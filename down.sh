#!/usr/bin/env bash
# トンネルを切り、Colab のランタイムを削除する。
# PULL_OUTPUTS_ON_STOP=1 なら、削除の前に ComfyUI の output フォルダーを手元にコピーする。
source "$(dirname "$0")/lib/common.sh"

require_cmds colab ssh tar

if [[ -f "$TUNNEL_PID_FILE" ]]; then
    log "$(msg closing_tunnel)"
    # 開いている start.bat のウィンドウに、停止のために閉じることを伝える
    touch "$STOPPING_FILE"
    stop_tunnel
fi

teardown_runtime
rm -f "$STOPPING_FILE"
