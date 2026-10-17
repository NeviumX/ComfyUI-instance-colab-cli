#!/usr/bin/env bash
# Colab VM 上で実行される。手元の update.sh が lib/messages.sh と一緒に ssh 経由で標準入力から流し込む。
# ComfyUI と定期保存を止めて、comfy_env.py update で更新する。起動し直すのは update.sh が remote-setup.sh で行う。
# 受け取る変数: COMFY_BASE STAGE_DIR TMUX_SESSION SAVE_SESSION MSG_LANG
#               UPDATE_REF: 更新先の版。latest なら最新のリリースタグ
set -euo pipefail

COMFY_DIR="$COMFY_BASE/environment/ComfyUI"
STATE="$STAGE_DIR/state"

rmsg() { echo "[remote] $(msg "$@")"; }

if [[ ! -f "$STATE/setup-done" || ! -f "$STAGE_DIR/env.sh" || ! -d "$COMFY_DIR" ]]; then
    rmsg u_not_ready
    exit 1
fi

# remote-setup.sh と同じロックで、起動や復旧と同時に進まないようにする
exec 9>"$STATE/prepare.lock"
flock -w 1800 9 || { rmsg r_lock_timeout "$STATE/prepare.lock"; exit 1; }

# shellcheck disable=SC1091
source "$STAGE_DIR/env.sh"
# 既にその版なら、ComfyUI を止めずに終える。終了コード 3 で update.sh に伝える
code=0
"$COMFY_PYTHON" "$STAGE_DIR/comfy_env.py" update-needed "$UPDATE_REF" || code=$?
[[ "$code" == 0 ]] || exit "$code"

rmsg u_stopping
tmux kill-session -t "$SAVE_SESSION" 2>/dev/null || true
tmux kill-session -t "$TMUX_SESSION" 2>/dev/null || true
for _ in $(seq 1 15); do
    pids=""
    for d in /proc/[0-9]*; do
        c="$(readlink "$d/cwd" 2>/dev/null)" || continue
        case "$c" in "$COMFY_DIR"|"$COMFY_DIR"/*) pids+="${d#/proc/} " ;; esac
    done
    [[ -z "$pids" ]] && break
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    sleep 1
done

"$COMFY_PYTHON" "$STAGE_DIR/comfy_env.py" update "$UPDATE_REF"
