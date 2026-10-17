#!/usr/bin/env bash
# Colab VM 上の ComfyUI のログを表示し続ける。Ctrl+C で終了する。
#   ./logs.sh        ComfyUI のログ
#   ./logs.sh save   環境の定期保存のログ
source "$(dirname "$0")/lib/common.sh"

if [[ "${1:-}" == save ]]; then
    rssh "tail -n 50 -F $(printf '%q' "$STAGE_DIR/state/save.log")"
else
    rssh "tail -n 50 -F $(printf '%q' "$REMOTE_LOG")"
fi
