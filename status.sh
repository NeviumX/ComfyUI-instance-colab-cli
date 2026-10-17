#!/usr/bin/env bash
# ランタイム・ComfyUI・Drive・トンネルの状態をまとめて表示する。
source "$(dirname "$0")/lib/common.sh"

require_cmds colab ssh curl

msg st_runtime "$COLAB_SESSION"; echo
if session_exists; then
    colab status -s "$COLAB_SESSION" | sed 's/^/    /'

    msg st_vm; echo
    rssh "nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader; free -g | sed -n 2p; df -h /content | tail -n 1" \
        2>/dev/null | sed 's/^/    /' || echo "    $(msg st_ssh_fail)"
    if remote_healthy; then
        echo "    $(msg st_comfy_up)"
    else
        echo "    $(msg st_comfy_down)"
    fi
    if [[ "$USE_DRIVE" == 1 ]]; then
        if drive_mounted; then echo "    $(msg st_drive_on)"; else echo "    $(msg st_drive_off)"; fi
    fi
    if [[ "$SAVE_ENVIRONMENT" == 1 ]]; then
        echo "    $(msg st_env "$(rssh "cat $STAGE_DIR/state/checkpoint-status.json 2>/dev/null" 2>/dev/null || true)")"
    fi
else
    echo "    $(msg st_none)"
fi

msg st_tunnel "$LOCAL_URL"; echo
if local_healthy; then echo "    $(msg st_connected)"; else echo "    $(msg st_disconnected)"; fi

msg st_units; echo
colab usage | sed 's/^/    /'
