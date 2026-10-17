#!/usr/bin/env bash
# 各スクリプト共通の設定読み込みと関数。source して使う。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="$ROOT/.state"
TUNNEL_PID_FILE="$STATE_DIR/tunnel.pid"
mkdir -p "$STATE_DIR"

# shellcheck source=../config.env
source "$ROOT/config.env"
if [[ -f "$ROOT/config.local.env" ]]; then
    # shellcheck disable=SC1091
    source "$ROOT/config.local.env"
fi
# shellcheck source=messages.sh
source "$ROOT/lib/messages.sh"

# ssh_config が $COLAB_SESSION を参照する
export COLAB_SESSION
command -v colab >/dev/null 2>&1 || export PATH="$HOME/.local/bin:$PATH"

SSH_HOST="${SSH_HOST:-colab-comfyui}"
SSH_OPTS=(-F "$ROOT/ssh_config")
# 共有 SSH 接続のソケットの置き場所
mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"

# Colab VM 側の場所。Drive は colab drivemount の既定の場所にマウントする
DRIVE_MOUNT="${DRIVE_MOUNT:-/content/drive}"
# shellcheck disable=SC2034  # remote_env で Colab VM に渡す
DRIVE_MODELS="$DRIVE_MOUNT/MyDrive/$DRIVE_FOLDER/models"
# shellcheck disable=SC2034  # remote_env で Colab VM に渡す
DRIVE_STORAGE="$DRIVE_MOUNT/MyDrive/$DRIVE_FOLDER/environment"
# 手元から送るファイルと、保存の状態の置き場所
STAGE_DIR="${STAGE_DIR:-/content/.comfy-colab-cli}"
COMFY_DIR="$COMFY_BASE/environment/ComfyUI"
OUTPUT_REMOTE="$COMFY_BASE/output"
TMUX_SESSION=comfyui
# shellcheck disable=SC2034  # remote_env で Colab VM に渡す
SAVE_SESSION=comfyui-save
NODE_NAME=comfyui-colab-cli
LOCAL_URL="http://localhost:$LOCAL_PORT"

log()  { printf '\033[36m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { printf '\033[33m[%s] %s\033[0m\n' "$(date +%H:%M:%S)" "$*" >&2; }
die()  { printf '\033[31m[%s] %s\033[0m\n' "$(date +%H:%M:%S)" "$*" >&2; exit 1; }

require_cmds() {
    local c
    for c in "$@"; do
        command -v "$c" >/dev/null 2>&1 || die "$(msg cmd_missing "$c")"
    done
}

# ランタイムがあるかを colab status の出力で判定する
session_exists() {
    local out
    out="$(colab status -s "$COLAB_SESSION" 2>&1)" || return 1
    ! grep -qi "not found" <<<"$out"
}

# ssh_config の ControlMaster で作った共有 SSH 接続への操作。新しい接続は作らない
ctl() { ssh "${SSH_OPTS[@]}" -O "$1" "${@:2}" "$SSH_HOST"; }

# 共有 SSH 接続を用意する。Colab のランタイムには同時に 1 本しか SSH をつなげないため、
# すべての ssh はこの 1 本に相乗りする。
ensure_master() {
    ctl check >/dev/null 2>&1 && return 0
    (
        flock 9
        ctl check >/dev/null 2>&1 && exit 0
        setsid -w ssh "${SSH_OPTS[@]}" -o ControlMaster=yes -N -f "$SSH_HOST"
    ) 9>"$HOME/.ssh/colab-cm-$COLAB_SESSION.lock"
}

# Colab VM 上でコマンドを実行する。
# colab ssh はランタイムが無いと新しく作るので、先に存在を確かめる。
rssh() {
    if ! session_exists; then
        warn "$(msg no_runtime_skip_ssh "$COLAB_SESSION")"
        return 255
    fi
    ensure_master || return 255
    ssh "${SSH_OPTS[@]}" "$SSH_HOST" "$@"
}

# 指定した変数を「NAME=値 ...」の形にして、リモートのコマンドの前に付けられるようにする
remote_env() {
    local v out=""
    for v in "$@"; do out+="$v=$(printf '%q' "${!v}") "; done
    printf '%s' "$out"
}

HEALTH_PATH=/system_stats

remote_healthy() {
    rssh "curl -sf -m 5 http://127.0.0.1:$REMOTE_PORT$HEALTH_PATH >/dev/null" 2>/dev/null
}

# remote-setup.sh と同じ値にしておくこと
EXIT_MARK='[comfyui-colab-cli] ComfyUI exited'
PORT_ERR_RE='already in use|address in use|eaddrinuse'

# ---- Google ドライブ ----
DRIVE_READY=0

# マウントすると MyDrive が現れ、flush_and_unmount で消える
drive_mounted() { rssh "test -d $DRIVE_MOUNT/MyDrive" 2>/dev/null; }

# マウント済みかどうかだけを調べる。認可画面は出さない
detect_drive() {
    DRIVE_READY=0
    if [[ "$USE_DRIVE" == 1 ]] && drive_mounted; then DRIVE_READY=1; fi
}

# USE_DRIVE=1 なら Drive をマウントする。初回は Google の認可画面の URL が表示され、Enter を待つ。
# マウントできなくても起動は続け、モデルは VM の中だけに置く。
ensure_drive() {
    DRIVE_READY=0
    [[ "$USE_DRIVE" == 1 ]] || return 0
    if drive_mounted; then
        log "$(msg drive_already)"
        DRIVE_READY=1
        return 0
    fi
    log "$(msg drive_mounting)"
    colab drivemount -s "$COLAB_SESSION" "$DRIVE_MOUNT" || true
    if drive_mounted; then
        DRIVE_READY=1
        log "$(msg drive_ready "MyDrive/$DRIVE_FOLDER/models")"
    else
        warn "$(msg drive_failed)"
    fi
}

# Drive への書き込みを終わらせてからアンマウントする。ランタイムの削除前に呼ぶ。
flush_drive() {
    drive_mounted || return 0
    log "$(msg drive_flushing)"
    local out
    if out="$(printf 'from google.colab import drive\ndrive.flush_and_unmount()\nprint("FLUSHED")\n' |
              colab exec -s "$COLAB_SESSION" --timeout 300 2>&1)" && grep -q FLUSHED <<<"$out"; then
        log "$(msg drive_flushed)"
    else
        printf '%s\n' "$out" | tail -n 5 | sed 's/^/    /' >&2
        warn "$(msg drive_flush_failed)"
    fi
}

# ---- Colab VM 上の ComfyUI ----
# Colab VM で ComfyUI を準備して起動する。起動済み・起動中なら何もしない。引数 1 なら止めてからやり直す。
remote_start() {
    # shellcheck disable=SC2034  # remote_env で Colab VM に渡す
    local FORCE_RESTART="${1:-0}"
    upload_files
    # 表示する文章も一緒に送り、言語は手元で決めたものを使わせる
    cat "$ROOT/lib/messages.sh" "$ROOT/lib/remote-setup.sh" |
        rssh "$(remote_env COMFY_REPO COMFY_REF COMFY_BASE REMOTE_PORT INSTALL_MANAGER CUSTOM_NODES COMFY_EXTRA_ARGS \
                APT_PACKAGES REMOTE_LOG FORCE_RESTART MSG_LANG DRIVE_READY DRIVE_MODELS DRIVE_STORAGE \
                SAVE_ENVIRONMENT RESTORE_ENVIRONMENT SAVE_INTERVAL_SEC AUTO_UPDATE PULL_OUTPUTS_ON_STOP \
                STAGE_DIR TMUX_SESSION SAVE_SESSION NODE_NAME) bash -s"
}

# 停止ボタンのカスタムノードと、環境を保存・復元・更新する comfy_env.py を VM へ送る
upload_files() {
    tar -C "$ROOT" --exclude=__pycache__ --transform 's#^node/#custom_nodes/#;s#^lib/##' \
        -cf - "node/$NODE_NAME" lib/comfy_env.py |
        rssh "mkdir -p $STAGE_DIR && rm -rf $STAGE_DIR/custom_nodes/$NODE_NAME && tar -C $STAGE_DIR -xf -" ||
        die "$(msg node_upload_failed)"
}

# ComfyUI を指定の版に更新する。ComfyUI と定期保存を止めてから更新し、起動し直すのは呼び出し側
remote_update() {
    # shellcheck disable=SC2034  # remote_env で Colab VM に渡す
    local UPDATE_REF="$1"
    upload_files
    cat "$ROOT/lib/messages.sh" "$ROOT/lib/remote-update.sh" |
        rssh "$(remote_env COMFY_BASE STAGE_DIR TMUX_SESSION SAVE_SESSION MSG_LANG UPDATE_REF) bash -s"
}

# 環境を Google ドライブに保存する。ランタイムを削除する前に呼ぶ。保存しない設定なら何もしない
save_environment() {
    [[ "$USE_DRIVE" == 1 && "$SAVE_ENVIRONMENT" == 1 ]] || return 0
    rssh "test -f $STAGE_DIR/save.sh && test -d $COMFY_DIR" 2>/dev/null || return 0
    drive_mounted || return 0
    log "$(msg env_saving)"
    rssh "bash $STAGE_DIR/save.sh force"
}

# Colab 側の状態を 1 回の SSH で調べる: HEALTHY / PORT_IN_USE / EXITED / NOT_RUNNING / ログの最終行
remote_poll() {
    local q_log
    q_log="$(printf '%q' "$REMOTE_LOG")"
    rssh "if curl -sf -m 5 http://127.0.0.1:$REMOTE_PORT$HEALTH_PATH >/dev/null 2>&1; then echo HEALTHY;
          elif tail -n 40 $q_log 2>/dev/null | grep -qiE '$PORT_ERR_RE'; then echo PORT_IN_USE;
          elif tail -n 5 $q_log 2>/dev/null | grep -qF '$EXIT_MARK'; then echo EXITED;
          elif ! tmux has-session -t $TMUX_SESSION 2>/dev/null; then echo NOT_RUNNING;
          else tail -c 400 $q_log 2>/dev/null | tr '\r' '\n' | grep -v '^[[:space:]]*\$' | tail -n 1; fi" \
        2>/dev/null || msg ssh_no_response
}

# 起動完了を待つ。ポート使用中などで ComfyUI が落ちたら SETUP_RETRIES 回までやり直す。
wait_healthy() {
    local deadline=$((SECONDS + HEALTH_TIMEOUT_MIN * 60)) status why retries=0
    while true; do
        status="$(remote_poll)"
        case "$status" in
            HEALTHY) return 0 ;;
            PORT_IN_USE|EXITED|NOT_RUNNING)
                case "$status" in
                    PORT_IN_USE) why="$(msg why_port_in_use "$REMOTE_PORT")" ;;
                    EXITED)      why="$(msg why_exited)" ;;
                    *)           why="$(msg why_not_running)" ;;
                esac
                (( retries < SETUP_RETRIES )) ||
                    die "$(msg retry_exhausted "$why" "$SETUP_RETRIES")"
                retries=$((retries + 1))
                warn "$(msg retry_setup "$why" "$retries" "$SETUP_RETRIES")"
                remote_start 1
                deadline=$((SECONDS + HEALTH_TIMEOUT_MIN * 60))
                ;;
            *) if [[ -n "${status//[[:space:]]/}" ]]; then printf '    %s\n' "${status:0:160}"; fi ;;
        esac
        (( SECONDS < deadline )) || die "$(msg start_timeout "$HEALTH_TIMEOUT_MIN")"
        sleep "$POLL_SEC"
    done
}

# ComfyUI を止める。Drive のファイルを開いたままにしないよう、作業フォルダーが ComfyUI の中にあるプロセスが消えるまで待つ
remote_stop_comfy() {
    rssh "$(remote_env COMFY_DIR TMUX_SESSION SAVE_SESSION) bash -s" >/dev/null 2>&1 <<'EOF' || true
tmux kill-session -t "$TMUX_SESSION" 2>/dev/null
tmux kill-session -t "$SAVE_SESSION" 2>/dev/null
for _ in 1 2 3 4 5 6 7 8 9 10; do
    pids=""
    for d in /proc/[0-9]*; do
        c="$(readlink "$d/cwd" 2>/dev/null)" || continue
        case "$c" in "$COMFY_DIR"|"$COMFY_DIR"/*) pids+="${d#/proc/} " ;; esac
    done
    [ -z "$pids" ] && break
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null
    sleep 1
done
EOF
}

local_healthy() { curl -sf -m 5 "http://127.0.0.1:$LOCAL_PORT$HEALTH_PATH" >/dev/null 2>&1; }

local_port_in_use() { ss -ltnH "sport = :$LOCAL_PORT" 2>/dev/null | grep -q .; }

# SSH が通るまで待つ。VM の起動直後は少し時間がかかる
wait_ssh() {
    local i err
    for i in 1 2 3 4 5 6; do
        err="$(rssh true 2>&1 >/dev/null)" && return 0
        if grep -q "429" <<<"$err"; then
            warn "$(msg ssh_busy_429 "$i")"
        else
            warn "$(msg ssh_waiting "$i")"
        fi
        sleep 10
    done
    die "$(msg ssh_failed)"
}

# ---- 出力の回収 ----
output_dir() {
    case "$OUTPUT_DIR" in
        /*) printf '%s' "$OUTPUT_DIR" ;;
        *)  printf '%s' "$ROOT/$OUTPUT_DIR" ;;
    esac
}

# ComfyUI の output フォルダーを手元へコピーする。手元に同じ名前のファイルがあれば残す
pull_outputs() {
    local dest q_out before after
    dest="$(output_dir)"
    q_out="$(printf '%q' "$OUTPUT_REMOTE")"
    if ! rssh "test -d $q_out && find $q_out -type f ! -name '_output_images_will_be_put_here' -print -quit | grep -q ." 2>/dev/null; then
        log "$(msg pull_none)"
        return 0
    fi
    mkdir -p "$dest"
    before="$(find "$dest" -type f | wc -l)"
    log "$(msg pull_start "$dest")"
    rssh "tar -C $q_out --exclude=./_output_images_will_be_put_here -cf - ." |
        tar -C "$dest" --skip-old-files --no-same-owner --no-same-permissions -xf - || return 1
    after="$(find "$dest" -type f | wc -l)"
    log "$(msg pull_done "$((after - before))" "$dest")"
}

# ---- 共有 SSH 接続のトンネル ----
stop_master() { ctl exit >/dev/null 2>&1 || true; }

FWD_SPEC="127.0.0.1:$LOCAL_PORT:127.0.0.1:$REMOTE_PORT"
KEEPALIVE_PID=""

# 共有 SSH 接続にポート転送を足す。
# ウィンドウを開いている間は、接続を保つための何もしないセッションを 1 本つないでおく。
open_link() {
    rssh true || return 1
    if [[ -z "$KEEPALIVE_PID" ]] || ! kill -0 "$KEEPALIVE_PID" 2>/dev/null; then
        # shellcheck disable=SC2016
        ssh "${SSH_OPTS[@]}" "$SSH_HOST" 'while kill -0 $PPID 2>/dev/null; do sleep 30; done' \
            </dev/null >/dev/null 2>&1 &
        KEEPALIVE_PID=$!
    fi
    ctl forward -L "$FWD_SPEC" >/dev/null 2>&1
}

close_link() {
    if [[ -n "$KEEPALIVE_PID" ]]; then
        kill "$KEEPALIVE_PID" 2>/dev/null || true
        KEEPALIVE_PID=""
    fi
    ctl cancel -L "$FWD_SPEC" >/dev/null 2>&1 || true
}

open_browser() {
    if command -v cmd.exe >/dev/null 2>&1; then
        (cd /mnt/c 2>/dev/null || true; cmd.exe /c start "" "$LOCAL_URL" >/dev/null 2>&1) || true
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$LOCAL_URL" >/dev/null 2>&1 || true
    fi
}

print_endpoints() {
    echo
    echo "  $(msg ep_browser "$LOCAL_URL")"
    echo "  $(msg ep_desktop "$LOCAL_URL")"
    if [[ "$DRIVE_READY" == 1 ]]; then
        echo "  $(msg ep_models "MyDrive/$DRIVE_FOLDER/models")"
    fi
    echo
}

# 別のウィンドウでトンネルを保っている up.sh / tunnel.sh を止める。トンネルはその終了処理が閉じる
stop_tunnel() {
    local pid
    pid="$(cat "$TUNNEL_PID_FILE" 2>/dev/null)" || return 0
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        local i
        for i in 1 2 3 4 5; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    fi
    rm -f "$TUNNEL_PID_FILE"
}

# ランタイムを削除する。出力の回収 → 環境の保存 → ComfyUI の停止 → Drive の書き込み完了 → 接続を閉じる → colab stop の順。
# 環境を保存できなかったときは、引数が ask なら削除するか尋ね、それ以外なら削除せずに 1 を返す。
teardown_runtime() {
    local mode="${1:-ask}" ans
    if session_exists; then
        if [[ "$PULL_OUTPUTS_ON_STOP" == 1 ]]; then
            pull_outputs || warn "$(msg pull_failed)"
        fi
        if ! save_environment; then
            warn "$(msg env_save_failed)"
            ans=n
            [[ "$mode" == ask ]] && { read -r -p "$(msg env_delete_anyway)" ans || ans=n; }
            if [[ ! "$ans" =~ ^[yY] ]]; then
                warn "$(msg not_deleted)"
                return 1
            fi
        fi
        remote_stop_comfy
        flush_drive
        close_link
        stop_master
        log "$(msg stopping_runtime "$COLAB_SESSION")"
        colab stop -s "$COLAB_SESSION"
    else
        close_link
        stop_master
        log "$(msg runtime_already_gone "$COLAB_SESSION")"
    fi
    log "$(msg usage_after_stop)"
    colab usage | sed 's/^/    /'
}

# ---- ComfyUI の画面の停止ボタン ----
# 手元のトンネル越しにカスタムノードへ尋ねる。押されていれば delete か disconnect を返す
BUTTON_PATH=/colab_cli/stop

button_request() {
    local answer
    answer="$(curl -sf -m 3 "http://127.0.0.1:$LOCAL_PORT$BUTTON_PATH" 2>/dev/null)" || return 0
    case "$answer" in
        *'"state":"pending"'*'"delete_runtime":true'*) echo delete ;;
        *'"state":"pending"'*) echo disconnect ;;
    esac
}

# 要求を受け取ったことをページに伝える
button_accept() {
    curl -sf -m 3 -X POST -H 'Content-Type: application/json' -d '{}' \
        "http://127.0.0.1:$LOCAL_PORT$BUTTON_PATH/ack" >/dev/null 2>&1 || true
}

# 環境を保存できず、ランタイムを削除しなかったことをページに伝える
button_fail() {
    curl -sf -m 3 -X POST -H 'Content-Type: application/json' -d '{}'         "http://127.0.0.1:$LOCAL_PORT$BUTTON_PATH/fail" >/dev/null 2>&1 || true
}

# 前の接続で残った要求を消す。ComfyUI はトンネルを張り直しても動き続けているため
button_reset() {
    curl -sf -m 3 -X POST -H 'Content-Type: application/json' -d '{}' \
        "http://127.0.0.1:$LOCAL_PORT$BUTTON_PATH/reset" >/dev/null 2>&1 || true
}

TUNNEL_END=""   # トンネルを閉じたときの案内。空ならランタイムが残っている前提の注意を出す
# stop.bat がこのウィンドウを閉じさせるときに置く目印
STOPPING_FILE="$STATE_DIR/stopping"

on_tunnel_exit() {
    close_link
    # 転送の待ち受けを外しても、ブラウザがつないだままの接続は共有 SSH 接続の上に残る。
    # 画面からも確実に切れるよう、共有接続ごと閉じる
    stop_master
    rm -f "$TUNNEL_PID_FILE"
    echo
    [[ -z "$TUNNEL_END" && -f "$STOPPING_FILE" ]] && TUNNEL_END=stopping
    case "$TUNNEL_END" in
        stopping)     log "$(msg end_stopping)" ;;
        deleted)      log "$(msg end_deleted)" ;;
        disconnected) warn "$(msg end_disconnected)" ;;
        *)            warn "$(msg tunnel_closed)" ;;
    esac
}

# トンネルをこのプロセスで保持する。ウィンドウを閉じるか Ctrl+C でトンネルだけ切れる。
run_tunnel_foreground() {
    local other
    other="$(cat "$TUNNEL_PID_FILE" 2>/dev/null || true)"
    if [[ -n "$other" && "$other" != "$$" ]] && kill -0 "$other" 2>/dev/null && local_healthy; then
        log "$(msg tunnel_already)"
        [[ "$OPEN_BROWSER" == 1 ]] && open_browser
        print_endpoints
        return 0
    fi
    # 前回のウィンドウが残した転送があれば外してから、ポートが空いているか確かめる
    close_link
    if local_port_in_use; then
        die "$(msg local_port_busy "$LOCAL_PORT")"
    fi

    echo "$$" > "$TUNNEL_PID_FILE"
    trap on_tunnel_exit EXIT
    trap 'exit 130' INT TERM

    open_link || die "$(msg tunnel_failed)"
    local i
    for i in $(seq 1 30); do
        local_healthy && break
        sleep 2
    done
    local_healthy || die "$(msg tunnel_unreachable)"
    button_reset

    log "$(msg ready)"
    [[ "$OPEN_BROWSER" == 1 ]] && open_browser
    print_endpoints
    log "$(msg tunnel_hold)"
    watch_comfy
}

# トンネルを保っている間、停止ボタンと ComfyUI の応答を見張る。
# Colab 側で落ちていたら起動し直し、トンネルだけが切れていたら張り直す。
watch_comfy() {
    local fails=0 next_check=$((SECONDS + WATCH_SEC)) asked
    while true; do
        sleep "$BUTTON_POLL_SEC"
        asked="$(button_request)"
        case "$asked" in
            delete)
                log "$(msg button_delete)"
                button_accept
                if teardown_runtime button; then
                    TUNNEL_END=deleted
                    return 0
                fi
                button_fail
                continue
                ;;
            disconnect)
                log "$(msg button_disconnect)"
                button_accept
                # ページが受け取りを表示できるよう、少し待ってから切る
                sleep 3
                TUNNEL_END=disconnected
                return 0
                ;;
        esac

        (( SECONDS >= next_check )) || continue
        next_check=$((SECONDS + WATCH_SEC))
        if local_healthy; then
            fails=0
            continue
        fi
        fails=$((fails + 1))
        (( fails >= 2 )) || continue    # 一度だけの失敗は様子を見る
        fails=0

        session_exists || { warn "$(msg runtime_stopped "$COLAB_SESSION")"; TUNNEL_END=deleted; break; }
        if remote_healthy; then
            warn "$(msg tunnel_stuck)"
            open_link || warn "$(msg relink_retry_later)"
            continue
        fi
        warn "$(msg comfy_down_recovering)"
        remote_start 0
        wait_healthy
        open_link || warn "$(msg relink_retry_later)"
        log "$(msg recovered)"
    done
}
