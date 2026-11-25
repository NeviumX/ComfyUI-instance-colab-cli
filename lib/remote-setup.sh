#!/usr/bin/env bash
# Colab VM 上で実行される。手元の lib/common.sh が lib/messages.sh と一緒に ssh 経由で標準入力から流し込む。
# 配置:
#   COMFY_BASE/environment/ComfyUI   ComfyUI 本体。カスタムノードと user もこの中
#   COMFY_BASE/environment/requirements.lock.txt, runtime.json   次の VM で同じパッケージを入れるための記録
#   COMFY_BASE/.venv                 ComfyUI の Python
#   COMFY_BASE/models, output, input, temp, database
#   STAGE_DIR                        手元から送ったファイル。root のもので、ComfyUI からは読めるが書けない
#   STAGE_DIR/state                  root だけが読める状態のファイル。トークン、台帳の場所、ロック
#
# ISOLATE_COMFY=1 なら、ComfyUI と準備の手順を root ではない利用者 comfy で動かす。作業場所 COMFY_BASE はその利用者のもの。
# ドライブは root だけが入れる PRIVATE_DIR にマウントし、models だけを DRIVE_VIEW に読み取り専用で見せる。
# ドライブへの書き込みとトークンを使う仕事は、root で動く model_helper.py が受け持つ。
#
# 受け取る変数: COMFY_REPO COMFY_REF COMFY_BASE REMOTE_PORT INSTALL_MANAGER CUSTOM_NODES COMFY_EXTRA_ARGS
#               APT_PACKAGES REMOTE_LOG MSG_LANG
#               DRIVE_READY: 1 なら DRIVE_MODELS のモデルを使い、DRIVE_STORAGE に環境を保存し、DRIVE_LEDGER を台帳にする
#               MODEL_SOURCE: ランタイムに写しが無いモデルを、drive と url のどちらから先に取るか
#               SAVE_ENVIRONMENT / RESTORE_ENVIRONMENT / SAVE_INTERVAL_SEC: 環境の保存と復元
#               AUTO_UPDATE: 1 なら、新しいリリースがあれば起動の前に更新する
#               USE_SAGE_ATTENTION: 1 なら SageAttention を入れて試し、使えれば --use-sage-attention で起動する
#               PULL_OUTPUTS_ON_STOP: 停止ボタンの説明を切り替えるために ComfyUI へ渡す
#               STAGE_DIR / NODE_NAME: 手元から送ったファイルの置き場所と、停止ボタンのノードの名前
#               TMUX_SESSION / SAVE_SESSION / HELPER_SESSION: ComfyUI と定期保存と model_helper.py を動かす tmux のセッション名
#               ISOLATE_COMFY / PRIVATE_DIR / DRIVE_VIEW: 利用者を分けるか、ドライブのマウントの親、models の見え方の場所
#               COMFY_APP_UV_CACHE: 偽の VM のテストだけが使う、その利用者の uv のキャッシュ
#               FORCE_RESTART: 1 なら進行中でも止めてやり直す
set -euo pipefail

EXIT_MARK='[comfyui-colab-cli] ComfyUI exited'
PORT_ERR_RE='already in use|address in use|eaddrinuse'
HEALTH_PATH=/system_stats
FORCE_RESTART="${FORCE_RESTART:-0}"
ENVDIR="$COMFY_BASE/environment"
COMFY_DIR="$ENVDIR/ComfyUI"
VENV_PY="$COMFY_BASE/.venv/bin/python"
STATE="$STAGE_DIR/state"
HELPER_SOCKET="$STAGE_DIR/run/helper.sock"
LEDGER_MIRROR="$STAGE_DIR/shared/model-sources.json"
# STAGE_DIR と送ったファイルは root のもので、ComfyUI の利用者は読めても書けない。state は root だけ
mkdir -p "$STAGE_DIR" "$STATE" "$STAGE_DIR/run" "$STAGE_DIR/shared"
chmod 755 "$STAGE_DIR" "$STAGE_DIR/run" "$STAGE_DIR/shared"
chmod 700 "$STATE"
rmsg() { echo "[remote] $(msg "$@")"; }

# 準備が同時に進まないよう順番に待つ。このロックはファイル記述子 9 で持つ。ここから起動する tmux などに
# 引き継ぐと、ComfyUI が動いている間ロックが外れず、次の準備が止まったままになるので、長く残るプロセスは new_session で 9 を閉じて起動する。
exec 9>"$STATE/prepare.lock"
flock -w 1800 9 || { rmsg r_lock_timeout "$STATE/prepare.lock"; exit 1; }
new_session() { tmux new-session "$@" 9>&-; }

healthy() { curl -sf -m 5 "http://127.0.0.1:$REMOTE_PORT$HEALTH_PATH" >/dev/null 2>&1; }

# ---- Python ----
# Colab の Python には CUDA 版の PyTorch が入っている。torch を読み込める Python から venv を作る。
find_py() {
    local c
    for c in $(type -ap python3) /usr/local/bin/python3 /usr/bin/python3; do
        [[ -x "$c" ]] && "$c" -c 'import torch' 2>/dev/null && { echo "$c"; return 0; }
    done
    return 1
}
PY="$(find_py)" || PY="$(command -v python3)"

# ---- 利用者を分けるか ----
ISOLATE=0
if [[ "${ISOLATE_COMFY:-1}" == 1 ]]; then
    if [[ "$(id -u)" == 0 ]]; then ISOLATE=1; else rmsg r_not_root; fi
fi
DRIVE_FOR_COMFY="$DRIVE_MODELS"
[[ "$ISOLATE" == 1 ]] && DRIVE_FOR_COMFY="$DRIVE_VIEW"

# 環境の保存と復元は comfy_env.py が行う。手元からの最後の保存にも使うので、設定をファイルに残す
STORAGE=""
if [[ "$DRIVE_READY" == 1 && ( "$SAVE_ENVIRONMENT" == 1 || "$RESTORE_ENVIRONMENT" == 1 ) ]]; then
    STORAGE="$DRIVE_STORAGE"
fi
# モデルの台帳。ドライブがあればドライブに置き、無ければ VM の中に置いて手元と行き来させる。
if [[ "$DRIVE_READY" == 1 ]]; then LEDGER="$DRIVE_LEDGER"; else LEDGER="$STATE/model-sources.json"; fi
printf '%s\n' "$LEDGER" > "$STATE/ledger-path"
[[ "${MODEL_SOURCE:-drive}" == url ]] || MODEL_SOURCE=drive
drive_real=""
[[ "$DRIVE_READY" == 1 ]] && drive_real="$DRIVE_MODELS"
q() { printf '%q' "$1"; }
cat > "$STAGE_DIR/env.sh" <<EOF
export COMFY_BASE=$(q "$COMFY_BASE") COMFY_STATE=$(q "$STATE") COMFY_PYTHON=$(q "$PY")
export COMFY_STORAGE=$(q "$([[ "$SAVE_ENVIRONMENT" == 1 ]] && echo "$STORAGE")") MSG_LANG=$(q "$MSG_LANG")
export APT_PACKAGES=$(q "$APT_PACKAGES") COMFY_ISOLATE=$ISOLATE COMFY_APP_UV_CACHE=$(q "${COMFY_APP_UV_CACHE:-}")
export COMFY_DRIVE_MODELS=$(q "$drive_real") COMFY_LEDGER=$(q "$LEDGER") COMFY_LEDGER_MIRROR=$(q "$LEDGER_MIRROR")
export COMFY_MODEL_SOURCE=$(q "$MODEL_SOURCE") COMFY_HELPER_SOCKET=$(q "$HELPER_SOCKET")
export COMFY_DRIVE_VIEW=$(q "$DRIVE_VIEW") COMFY_RUNTIME_ONLY=$(q "$STATE/runtime-only.json")
EOF
chmod 644 "$STAGE_DIR/env.sh"
cat > "$STAGE_DIR/save.sh" <<EOF
#!/usr/bin/env bash
# 手元の down.sh と停止ボタンが、ランタイムを削除する前に呼ぶ。引数 force で全部品を確かめる
source $(q "$STAGE_DIR/env.sh")
exec $(q "$PY") $(q "$STAGE_DIR/comfy_env.py") save "\$@"
EOF
# shellcheck disable=SC1091
source "$STAGE_DIR/env.sh"
cenv() { COMFY_STORAGE="$STORAGE" "$PY" "$STAGE_DIR/comfy_env.py" "$@"; }
# 保存してよいときだけ Drive に書く。SAVE_ENVIRONMENT=0 で復元だけを使う場合に、更新の後で保存しないため
cenv_saving() { "$PY" "$STAGE_DIR/comfy_env.py" "$@"; }
# ComfyUI の利用者として実行する。分けていないときは、そのまま実行する
# root が開いたロックの記述子 9 は渡さない
app() { "$PY" "$STAGE_DIR/app_user.py" run -- "$@" 9>&-; }
app_in() { local dir="$1"; shift; "$PY" "$STAGE_DIR/app_user.py" run --cwd "$dir" -- "$@" 9>&-; }

"$PY" "$STAGE_DIR/model_sources.py" merge --into "$LEDGER" --from "$STATE/model-sources.local.json" \
    --lock "$STATE/ledger.lock" --mirror "$LEDGER_MIRROR" || rmsg r_ledger_merge_failed

# ComfyUI の新しいリリースがあれば、NEWER_RELEASE にそのタグを、CURRENT_VERSION に今の版を入れる
NEWER_RELEASE="" CURRENT_VERSION=""
find_newer_release() {
    NEWER_RELEASE=""
    [[ -d "$COMFY_DIR/.git" ]] || return 0
    local latest
    # リポジトリの設定はその利用者が書き換えられるので、git はその利用者として動かす
    CURRENT_VERSION="$(app_in "$COMFY_DIR" git describe --tags --always 2>/dev/null)" || return 0
    latest="$(git ls-remote --tags --refs "$COMFY_REPO" 'v*' 2>/dev/null |
              sed -n 's#.*refs/tags/\(v[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)$#\1#p' | sort -V | tail -n 1)"
    [[ -n "$latest" && "$latest" != "$CURRENT_VERSION" ]] || return 0
    # v0.41.3-2-gabcdef のようにタグより先のコミットなら、そのタグは新しくない
    [[ "$(printf '%s\n%s\n' "$CURRENT_VERSION" "$latest" | sort -V | tail -n 1)" == "$latest" ]] || return 0
    NEWER_RELEASE="$latest"
}

start_saver() {
    [[ "$SAVE_ENVIRONMENT" == 1 && -n "$STORAGE" ]] || return 0
    tmux has-session -t "=$SAVE_SESSION" 2>/dev/null && return 0
    new_session -d -s "$SAVE_SESSION" -c "$STAGE_DIR" \
        "source $(q "$STAGE_DIR/env.sh"); $(q "$PY") comfy_env.py loop $SAVE_INTERVAL_SEC >> $(q "$STATE/save.log") 2>&1"
}

# root で動き、ドライブとトークンを使う仕事を ComfyUI から受ける。引数 restart なら、送り直したファイルで起動し直す
start_helper() {
    if [[ "${1:-}" != restart ]] && tmux has-session -t "=$HELPER_SESSION" 2>/dev/null && [[ -S "$HELPER_SOCKET" ]]; then
        return 0
    fi
    tmux kill-session -t "=$HELPER_SESSION" 2>/dev/null || true
    rm -f "$HELPER_SOCKET"
    new_session -d -s "$HELPER_SESSION" -c "$STAGE_DIR" \
        "source $(q "$STAGE_DIR/env.sh"); $(q "$PY") model_helper.py >> $(q "$STATE/helper.log") 2>&1"
    local _
    for _ in $(seq 1 30); do [[ -S "$HELPER_SOCKET" ]] && return 0; sleep 0.5; done
    rmsg r_helper_failed "$STATE/helper.log"
    tail -n 5 "$STATE/helper.log" 2>/dev/null || true
    exit 1
}

if healthy; then
    start_helper
    start_saver
    rmsg r_already_running
    # 動いている ComfyUI は止めないので、AUTO_UPDATE=1 でも知らせるだけにする
    find_newer_release
    [[ -z "$NEWER_RELEASE" ]] || rmsg r_update_available "$NEWER_RELEASE" "$CURRENT_VERSION"
    exit 0
fi

if command -v tmux >/dev/null 2>&1 && tmux has-session -t "=$TMUX_SESSION" 2>/dev/null; then
    if [[ "$FORCE_RESTART" == 1 ]]; then
        rmsg r_redo
    elif tail -n 5 "$REMOTE_LOG" 2>/dev/null | grep -qF "$EXIT_MARK"; then
        rmsg r_prev_exited
    elif tail -n 40 "$REMOTE_LOG" 2>/dev/null | grep -qiE "$PORT_ERR_RE"; then
        rmsg r_port_fail_restart "$REMOTE_PORT"
    else
        rmsg r_in_progress
        exit 0
    fi
fi

# ---- apt ----
# 足りないものだけを入れる。Colab にはたいてい入っている
# 別の名前のパッケージが提供していることもある。Ubuntu 24.04 の libglib2.0-0 は libglib2.0-0t64 が提供する。
# grep -q を dpkg-query に直接つなぐと、pipefail のもとでは書き込みの途中で失敗扱いになることがあるので、先に読んでおく
# 端末が無いので、debconf に質問させない
export DEBIAN_FRONTEND=noninteractive
provided="$(dpkg-query -W -f '${Provides}\n' 2>/dev/null || true)"
missing=()
for p in tmux iproute2 git ffmpeg libgl1 libglib2.0-0 $APT_PACKAGES; do
    dpkg -s "$p" >/dev/null 2>&1 || grep -qw -- "$p" <<<"$provided" || missing+=("$p")
done
apt_updated=0
if (( ${#missing[@]} > 0 )); then
    rmsg r_installing "${missing[*]}"
    apt-get -qq update >/dev/null
    apt_updated=1
    apt-get -qq install -y "${missing[@]}" >/dev/null
fi
# bindfs は推奨パッケージを入れない。ドライブのマウントに使う FUSE の道具が入れ替わることがあるため
if [[ "$ISOLATE" == 1 && "$DRIVE_READY" == 1 ]] && ! command -v bindfs >/dev/null 2>&1; then
    rmsg r_installing bindfs
    [[ "$apt_updated" == 1 ]] || apt-get -qq update >/dev/null
    apt-get -qq install -y --no-install-recommends bindfs >/dev/null
fi

# ---- 利用者と作業場所 ----
if [[ "$ISOLATE" == 1 ]]; then
    # ドライブが root だけの場所の外にもマウントされていると、ComfyUI から読み書きできてしまう
    while read -r _ mountpoint fstype _; do
        if [[ "$fstype" == fuse.drive && "$mountpoint" != "$PRIVATE_DIR"/* ]]; then
            rmsg r_drive_elsewhere "$mountpoint"
            exit 1
        fi
    done < /proc/mounts
    if [[ -d "$PRIVATE_DIR" ]]; then
        chmod 700 "$PRIVATE_DIR"
        [[ "$(stat -c %u "$PRIVATE_DIR")" == 0 && ! -L "$PRIVATE_DIR" ]] || { rmsg r_private_owner "$PRIVATE_DIR"; exit 1; }
    fi
    "$PY" "$STAGE_DIR/app_user.py" ensure-user
    rmsg r_isolated comfy
fi
"$PY" "$STAGE_DIR/app_user.py" create-tree

# ---- 前回の ComfyUI を片付けて、ポートを空ける ----
# 分けているときは、その利用者のプロセスをすべて止める。分けていないときは、作業フォルダーか実行ファイルが
# ComfyUI のフォルダーの中にあるプロセスだけを止める。
under_comfy() {
    [[ -n "$1" ]] || return 1
    [[ "$1" == "$COMFY_DIR" || "$1" == "$COMFY_DIR"/* ]]
}
app_uid="$(id -u comfy 2>/dev/null || true)"
is_comfy_pid() {
    local cwd exe
    if [[ "$ISOLATE" == 1 ]]; then
        [[ "$(stat -c %u "/proc/$1" 2>/dev/null)" == "$app_uid" ]]
        return
    fi
    cwd="$(readlink "/proc/$1/cwd" 2>/dev/null)" || return 1
    exe="$(readlink "/proc/$1/exe" 2>/dev/null)" || true
    under_comfy "$cwd" || under_comfy "$exe"
}
comfy_pids() {
    local d p
    for d in /proc/[0-9]*; do
        p="${d#/proc/}"
        [[ "$p" == "$$" || "$p" == "$PPID" ]] && continue
        is_comfy_pid "$p" && echo "$p"
    done
    return 0
}
port_pids() {
    ss -ltnpH "sport = :$REMOTE_PORT" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u
}
kill_and_wait() {
    local p alive
    kill "$@" 2>/dev/null || true
    for _ in $(seq 1 10); do
        alive=""
        for p in "$@"; do kill -0 "$p" 2>/dev/null && alive+="$p "; done
        [[ -z "$alive" ]] && return 0
        sleep 1
    done
    # shellcheck disable=SC2086
    kill -9 $alive 2>/dev/null || true
    sleep 1
}

tmux kill-session -t "=$TMUX_SESSION" 2>/dev/null || true
if [[ "$ISOLATE" == 1 ]]; then
    "$PY" "$STAGE_DIR/app_user.py" stop
else
    mapfile -t old < <(comfy_pids)
    if (( ${#old[@]} > 0 )); then
        rmsg r_stop_old "${old[*]}"
        kill_and_wait "${old[@]}"
    fi
fi

mapfile -t holders < <(port_pids)
if (( ${#holders[@]} > 0 )); then
    others=""
    for p in "${holders[@]}"; do
        if is_comfy_pid "$p"; then
            kill_and_wait "$p"
        else
            others+="  pid $p: $(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null | cut -c1-120)"$'\n'
        fi
    done
    if [[ -n "$others" ]]; then
        rmsg r_port_other "$REMOTE_PORT"
        printf '%s' "$others"
        rmsg r_port_other_hint
        exit 3
    fi
fi

uv_install() { app "$VENV_PY" -m uv pip install --python "$VENV_PY" -q "$@"; }

latest_tag() {
    git ls-remote --tags --refs "$COMFY_REPO" 'v*' 2>/dev/null |
        sed -n 's#.*refs/tags/\(v[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)$#\1#p' | sort -V | tail -n 1
}

# ---- 初回の準備。同じ VM で 2 回目以降は飛ばす ----
if [[ ! -f "$STATE/setup-done" ]]; then
    rmsg r_py "$PY" "$("$PY" -c 'import sys; print(sys.version.split()[0])')" \
        "$("$PY" -c 'import torch; print(torch.__version__)' 2>/dev/null || echo -)"

    # 途中で止まった復元は、やり直す。中身を消すのはその利用者
    if [[ -f "$STATE/restoring" ]]; then
        app rm -rf "$ENVDIR"
        "$PY" "$STAGE_DIR/app_user.py" create-tree
        rm -f "$STATE/restoring"
    fi

    # 1. Google ドライブに保存した環境
    if [[ ! -d "$COMFY_DIR" ]]; then
        if [[ "$DRIVE_READY" != 1 ]]; then
            [[ "$SAVE_ENVIRONMENT" == 1 || "$RESTORE_ENVIRONMENT" == 1 ]] && rmsg r_env_no_drive
        elif [[ "$RESTORE_ENVIRONMENT" == 1 ]]; then
            touch "$STATE/restoring"
            code=0
            cenv restore || code=$?
            case "$code" in
                0) cenv apt-hint ;;
                3) ;;
                *) app rm -rf "$ENVDIR"; "$PY" "$STAGE_DIR/app_user.py" create-tree
                   rm -f "$STATE/restoring"; rmsg r_restore_failed; exit 1 ;;
            esac
            rm -f "$STATE/restoring"
        else
            rmsg r_env_fresh
        fi
    fi

    # 2. ComfyUI 本体。復元しなかったときだけ取得する
    if [[ ! -d "$COMFY_DIR" ]]; then
        ref="$COMFY_REF"
        if [[ "$ref" == latest ]]; then
            ref="$(latest_tag)"
            [[ -n "$ref" ]] || { rmsg r_tag_failed; ref=master; }
        fi
        rmsg r_cloning "$COMFY_REPO" "$ref"
        app rm -rf "$COMFY_DIR.part"
        app git clone -q "$COMFY_REPO" "$COMFY_DIR.part"
        app_in "$COMFY_DIR.part" git -c advice.detachedHead=false checkout -q "$ref"
        app mv "$COMFY_DIR.part" "$COMFY_DIR"
    fi
    [[ -f "$COMFY_DIR/main.py" ]] || { rmsg r_source_incomplete; exit 1; }
    rmsg r_comfy_version "$(app_in "$COMFY_DIR" git describe --tags --always 2>/dev/null || echo -)"
    cenv prepare

    # 3. venv。pip を入れずに作り、pip と uv は外から入れる。ensurepip の無い Python でも作れるようにするため
    if [[ ! -x "$VENV_PY" ]]; then
        rmsg r_venv
        app "$PY" -m venv --without-pip "$COMFY_BASE/.venv"
        if ! app "$PY" -m pip --python "$VENV_PY" install -q pip uv 2>/dev/null &&
           ! { command -v uv >/dev/null 2>&1 && app "$(command -v uv)" pip install --python "$VENV_PY" -q pip uv; }; then
            app "$PY" -m venv "$COMFY_BASE/.venv"
            app "$VENV_PY" -m pip install -q uv
        fi
    fi

    # 4. パッケージ。保存した環境なら記録どおりに、新しい環境なら Colab と同じ CUDA 版の PyTorch から入れる
    lock_ok=0
    if [[ -f "$ENVDIR/requirements.lock.txt" ]]; then
        code=0
        cenv check-lock || code=$?
        case "$code" in
            0) lock_ok=1 ;;
            3) rmsg r_lock_without_torch ;;
            *) exit 1 ;;
        esac
    fi
    if [[ "$lock_ok" == 1 ]]; then
        torch_index="$(cenv torch-index)"
        rmsg r_requirements "requirements.lock.txt"
        uv_install --index-strategy unsafe-best-match --extra-index-url "$torch_index" -r "$ENVDIR/requirements.lock.txt"
    else
        cuda="$("$PY" -c 'import torch; print(torch.version.cuda or "")' 2>/dev/null || true)"
        [[ "$cuda" =~ ^[0-9]+\.[0-9]+$ ]] || { rmsg r_no_torch; exit 1; }
        torch_index="https://download.pytorch.org/whl/cu${cuda//./}"
        torch_pins="$("$PY" -c 'import importlib.metadata as m; print("torch==" + m.version("torch"), "torchvision==" + m.version("torchvision"))')"
        rmsg r_requirements "$torch_pins"
        # shellcheck disable=SC2086
        uv_install --index-url "$torch_index" $torch_pins
        reqs=(-r "$COMFY_DIR/requirements.txt")
        [[ -f "$COMFY_DIR/manager_requirements.txt" ]] && reqs+=(-r "$COMFY_DIR/manager_requirements.txt")
        rmsg r_requirements "requirements.txt"
        uv_install "${reqs[@]}"
        for req in "$COMFY_DIR"/custom_nodes/*/requirements.txt; do
            [[ -f "$req" ]] && uv_install -r "$req"
        done
    fi
    py_minor="$("$PY" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")"
    app "$PY" - "$ENVDIR/runtime.json" "$py_minor" "$torch_index" "$APT_PACKAGES" <<'PYEOF'
import json, sys
path, version, index, apt = sys.argv[1:5]
with open(path, "w") as file:
    json.dump({"apt_packages": sorted(apt.split()), "python": version, "torch_index_url": index}, file)
PYEOF
    touch "$STATE/setup-done"
    fresh_setup=1
fi

# ---- ComfyUI の更新 ----
# 前の更新が途中で止まっていれば、本体とパッケージの版が食い違っているので起動しない。AUTO_UPDATE=1 なら戻してから更新し直す
unfinished=0
cenv_saving refuse-unfinished-update 2>/dev/null || unfinished=1
find_newer_release
if [[ "$AUTO_UPDATE" == 1 && ( -n "$NEWER_RELEASE" || "$unfinished" == 1 ) ]]; then
    rmsg r_auto_updating "${NEWER_RELEASE:-latest}"
    # 失敗したら元の版に戻るので、そのまま起動する。戻せなかったときだけ止める
    cenv_saving update "${NEWER_RELEASE:-latest}" || rmsg r_auto_update_failed
    cenv_saving refuse-unfinished-update || exit 1
elif [[ "$unfinished" == 1 ]]; then
    cenv_saving refuse-unfinished-update || exit 1
elif [[ -n "$NEWER_RELEASE" ]]; then
    rmsg r_update_available "$NEWER_RELEASE" "$CURRENT_VERSION"
fi

# ---- ComfyUI-Manager と、config.env の CUSTOM_NODES ----
# ComfyUI に manager_requirements.txt があれば、pip の comfyui-manager を --enable-manager で使う。
# 古い ComfyUI では custom_nodes に ComfyUI-Manager を取得する。
manager_args=()
# shellcheck disable=SC2206
nodes=($CUSTOM_NODES)
if [[ "$INSTALL_MANAGER" == 1 ]]; then
    if [[ -f "$COMFY_DIR/manager_requirements.txt" ]]; then
        manager_args=(--enable-manager)
    else
        nodes=(https://github.com/Comfy-Org/ComfyUI-Manager.git "${nodes[@]}")
    fi
fi
for url in "${nodes[@]}"; do
    name="$(basename "${url%/}" .git)"
    [[ "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { rmsg r_node_failed "$url"; continue; }
    dir="$COMFY_DIR/custom_nodes/$name"
    [[ -e "$dir" || -L "$dir" ]] && continue
    rmsg r_cloning_node "$url"
    if app git clone -q --depth 1 "$url" "$dir"; then
        [[ -f "$dir/requirements.txt" ]] && { uv_install -r "$dir/requirements.txt" || rmsg r_node_failed "$url"; }
    else
        rmsg r_node_failed "$url"
    fi
done

# ---- SageAttention ----
# --use-sage-attention を付けたのにパッケージが無いと、ComfyUI は起動時に終了する。カーネルをコンパイルできないと、
# attention を呼ぶたびにエラーを記録して標準の attention に切り替わる。どちらも避けるため、入れたうえで GPU で試し、
# 通ったときだけ引数を付ける。1.0.6 は Python だけでできた版で、カーネルは PyTorch に付いてくる Triton が実行時に作る。
SAGE_ATTENTION_PACKAGE=sageattention==1.0.6
SAGE_ATTENTION_PROBE='
import torch
from sageattention import sageattn
q, k, v = (torch.randn(1, 2, 256, 64, device="cuda", dtype=torch.float16) for _ in range(3))
out = sageattn(q, k, v, is_causal=False, tensor_layout="HND", sm_scale=None, smooth_k=False)
assert out.shape == q.shape and bool(torch.isfinite(out).all()), "SageAttention returned an unusable result"
'
sage_args=()
if [[ "$USE_SAGE_ATTENTION" == 1 ]]; then
    # 試した結果はこの VM の間だけ覚えておく。GPU もパッケージも変わらないため
    if [[ ! -f "$STATE/sage-attention" ]]; then
        if ! app "$VENV_PY" -c 'import importlib.util, sys; sys.exit(0 if importlib.util.find_spec("sageattention") else 1)'; then
            # 保存した環境に入っていれば、その版のまま使う
            rmsg r_sage_installing "$SAGE_ATTENTION_PACKAGE"
            uv_install "$SAGE_ATTENTION_PACKAGE" || true
        fi
        # Triton がカーネルを置くキャッシュは、その利用者の HOME に置かれる
        if probe="$(timeout 600 "$PY" "$STAGE_DIR/app_user.py" run -- "$VENV_PY" -c "$SAGE_ATTENTION_PROBE" 2>&1 9>&-)"; then
            echo ok > "$STATE/sage-attention"
        else
            printf '%s\n' "$probe" | tail -n 15
            echo failed > "$STATE/sage-attention"
        fi
    fi
    if [[ "$(cat "$STATE/sage-attention")" == ok ]]; then
        rmsg r_sage_enabled
        [[ " $COMFY_EXTRA_ARGS " == *" --use-sage-attention "* ]] || sage_args=(--use-sage-attention)
    else
        rmsg r_sage_unavailable
    fi
fi

if ! app "$VENV_PY" -m pip check >/dev/null 2>&1; then
    app "$VENV_PY" -m pip check || true
    rmsg r_pip_check_failed
    exit 1
fi

# ---- ComfyUI の外から読み込ませるもの ----
# 停止ボタンのノードと Drive のモデルは、保存する環境に入れない。このツールの外で環境を復元したときに残らないようにするため
EXTRA_PATHS="$STAGE_DIR/extra_model_paths.yaml"
{
    echo "# comfyui-instance-colab-cli が起動のたびに書き直す"
    echo "comfyui_colab_cli:"
    echo "  base_path: \"$STAGE_DIR\""
    echo "  custom_nodes: custom_nodes"
    if [[ "$DRIVE_READY" == 1 ]]; then
        # ランタイムの models のフォルダーを、ドライブにも作る。名前はその利用者が決めるので、普通の名前だけを使う
        for d in "$COMFY_BASE"/models/*/; do
            n="$(basename "$d")"
            [[ "$n" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] && mkdir -p "$DRIVE_MODELS/$n"
        done
        # ランタイムの models の後ろに足す。ランタイムに写しがあれば、そちらを先に読む。
        # Manager などがダウンロードしたモデルはランタイムに入り、model_helper.py があとからドライブにも写す
        echo "colab_drive:"
        echo "  base_path: \"$DRIVE_FOR_COMFY\""
        for d in "$DRIVE_MODELS"/*/; do
            [[ -d "$d" ]] || continue
            n="$(basename "$d")"
            [[ "$n" =~ ^[A-Za-z0-9][A-Za-z0-9._\ -]*$ ]] || continue
            # custom_nodes に登録した場所からは ComfyUI がノードのコードを読み込み、base_path と is_default は設定として読まれる。
            # どれもモデルの種類ではないので、ドライブに同じ名前のフォルダーがあっても書かない
            case "$n" in custom_nodes|base_path|is_default) continue ;; esac
            echo "  \"$n\": \"$n\""
        done
    fi
} > "$EXTRA_PATHS"
chmod 644 "$EXTRA_PATHS"
if [[ "$ISOLATE" == 1 && "$DRIVE_READY" == 1 ]]; then
    "$PY" "$STAGE_DIR/app_user.py" show-view "$DRIVE_MODELS" "$DRIVE_VIEW" || { rmsg r_view_failed "$DRIVE_VIEW"; exit 1; }
fi
[[ "$DRIVE_READY" == 1 ]] && rmsg r_drive_models "$DRIVE_MODELS"

# 新しく準備した環境は、すぐ Drive に保存しておく
if [[ "${fresh_setup:-0}" == 1 && "$SAVE_ENVIRONMENT" == 1 && -n "$STORAGE" ]]; then
    rmsg r_saving
    bash "$STAGE_DIR/save.sh" force || rmsg r_save_failed
fi

start_helper restart

# ---- 起動 ----
args=("$COMFY_DIR/main.py" --listen 127.0.0.1 --port "$REMOTE_PORT" "${manager_args[@]}"
      --base-directory "$COMFY_DIR" --user-directory "$COMFY_DIR/user"
      --input-directory "$COMFY_BASE/input" --output-directory "$COMFY_BASE/output" --temp-directory "$COMFY_BASE/temp"
      --database-url "sqlite:///$COMFY_BASE/database/comfyui.db"
      --extra-model-paths-config "$EXTRA_PATHS" "${sage_args[@]}")
# COMFY_EXTRA_ARGS は空白で区切って渡す。--fast のように後ろの語を値として取る引数があるので、最後に置く
# shellcheck disable=SC2206
[[ -n "$COMFY_EXTRA_ARGS" ]] && args+=($COMFY_EXTRA_ARGS)

# 前回のログは .prev に残す
[[ -f "$REMOTE_LOG" ]] && mv -f "$REMOTE_LOG" "$REMOTE_LOG.prev"

saving=0
[[ "$SAVE_ENVIRONMENT" == 1 && -n "$STORAGE" ]] && saving=1
drive_shown=""
[[ "$DRIVE_READY" == 1 ]] && drive_shown="$DRIVE_FOR_COMFY"
# ComfyUI に渡す設定。トークンや台帳そのものの場所は渡さない。モデルの仕事は model_helper.py に頼む
settings=(COMFY_COLAB_CLI_LANG="$MSG_LANG" COMFY_COLAB_CLI_PULL="$PULL_OUTPUTS_ON_STOP" COMFY_COLAB_CLI_SAVE="$saving"
          COMFY_COLAB_CLI_STAGE="$STAGE_DIR" COMFY_COLAB_CLI_MODELS="$COMFY_BASE/models"
          COMFY_COLAB_CLI_DRIVE_MODELS="$drive_shown"
          COMFY_COLAB_CLI_LEDGER="$LEDGER_MIRROR" COMFY_COLAB_CLI_HELPER="$HELPER_SOCKET")
cmd="cd $(q "$STAGE_DIR") && $(q "$PY") $(q "$STAGE_DIR/app_user.py") run --cwd $(q "$COMFY_DIR")"
for s in "${settings[@]}"; do cmd+=" --set $(q "$s")"; done
# 出力は root の tee が書く。ComfyUI からはログを書き換えられない
cmd+=" -- $(q "$VENV_PY") $(printf '%q ' "${args[@]}") 2>&1 | tee -a $(q "$REMOTE_LOG")"
cmd+="; echo $(q "$EXIT_MARK") | tee -a $(q "$REMOTE_LOG"); sleep infinity"

# tmux の中で動かすので、SSH が切れても ComfyUI は止まらない。tmux のサーバーは root のまま
new_session -d -s "$TMUX_SESSION" bash -c "$cmd"
start_saver
rmsg r_started "$REMOTE_PORT"
