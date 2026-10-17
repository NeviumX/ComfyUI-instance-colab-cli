#!/usr/bin/env bash
# 動いているランタイムの ComfyUI を更新する。カスタムノード・設定・ワークフローはそのまま残る。
#   ./update.sh           最新のリリースタグに更新する
#   ./update.sh v0.3.60   指定の版にする。タグかブランチ名
# 更新できなかったときは元の版に戻し、Google ドライブには保存しない。
source "$(dirname "$0")/lib/common.sh"

require_cmds colab ssh curl tar
session_exists || die "$(msg runtime_missing_start "$COLAB_SESSION")"
detect_drive

ref="${1:-latest}"
log "$(msg updating "$ref")"
# 更新が失敗しても、元の版で起動し直す
code=0
remote_update "$ref" || code=$?
# 3 は既にその版だったという意味で、ComfyUI は止めていない
[[ "$code" == 3 ]] && exit 0
update_ok=1
[[ "$code" == 0 ]] || update_ok=0

log "$(msg restarting)"
# 失敗した更新を起動のときにもう一度試さないよう、ここでは自動更新を切る
# shellcheck disable=SC2034  # remote_env で Colab VM に渡す
AUTO_UPDATE=0
remote_start 0
wait_healthy
if [[ "$update_ok" == 1 ]]; then
    log "$(msg update_finished)"
else
    die "$(msg update_failed)"
fi
