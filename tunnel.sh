#!/usr/bin/env bash
# Colab のランタイムが動いている状態で、トンネルを張り直す。
# ComfyUI が落ちていたら、Colab 側で起動し直してからつなぐ。
source "$(dirname "$0")/lib/common.sh"

require_cmds colab ssh curl ss tar
session_exists || die "$(msg runtime_missing_start "$COLAB_SESSION")"
rm -f "$STOPPING_FILE"
detect_drive
if ! remote_healthy; then
    warn "$(msg comfy_unresponsive_redo)"
    remote_start 0
    wait_healthy
fi
run_tunnel_foreground
