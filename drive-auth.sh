#!/usr/bin/env bash
# 自分の OAuth クライアントで Drive API を認可し直す。取り消すこともできる。
#   ./drive-auth.sh          ブラウザで許可して、トークンを手元に保存する。ランタイムが動いていれば VM にも送る
#   ./drive-auth.sh revoke   Google に取り消しを頼み、手元のトークンを消す
# 最初の認可は start.bat が行うので、ふつうは実行しなくてよい。
source "$(dirname "$0")/lib/common.sh"

require_cmds python3
if [[ "${1:-}" == revoke ]]; then
    drive_oauth revoke || exit 1
    if session_exists; then clear_drive_token; fi
    exit 0
fi

[[ -f "$(drive_client_file)" ]] || die "$(msg drive_api_no_client "$DRIVE_OAUTH_CLIENT")"
log "$(msg drive_api_authorize)"
drive_oauth authorize || die "$(msg drive_api_auth_failed)"
if session_exists && { detect_drive; [[ "$DRIVE_READY" == 1 ]]; }; then
    if send_drive_token; then log "$(msg drive_api_sent)"; fi
else
    log "$(msg drive_api_not_running)"
fi
