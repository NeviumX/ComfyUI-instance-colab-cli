#!/usr/bin/env bash
# ComfyUI の output フォルダーを手元の OUTPUT_DIR にコピーする。ランタイムは止めない。
source "$(dirname "$0")/lib/common.sh"

require_cmds colab ssh tar
session_exists || die "$(msg runtime_missing_start "$COLAB_SESSION")"
pull_outputs || die "$(msg pull_failed)"
