#!/usr/bin/env bash
# スクリプトが表示する文章を日本語と英語でまとめたファイル。
# `msg キー 引数...` で、選ばれた言語の文章を返す。文章は printf の書式で、%s に引数が入る。改行は付かない。
# 文章に % を書くときは %% にする。
#
# 言語は次の順で決める。
#   1. COMFY_COLAB_LANG が ja か en ならそれに従う
#   2. LC_ALL / LC_MESSAGES / LANG が ja で始まれば日本語、ほかの言語が指定されていれば英語
#   3. C や POSIX のように言語の指定がなく、WSL 上で動いているときは Windows の表示言語に合わせる
#   4. どれにも当てはまらなければ英語
# MSG_LANG がすでに決まっているときは判定しない。Colab VM 側にはこの値を渡している。

_detect_msg_lang() {
    local v ui
    case "${COMFY_COLAB_LANG:-}" in
        ja|en) echo "$COMFY_COLAB_LANG"; return ;;
    esac
    for v in "${LC_ALL:-}" "${LC_MESSAGES:-}" "${LANG:-}"; do
        [[ -n "$v" ]] || continue
        case "$v" in
            ja*) echo ja; return ;;
            C|C.*|POSIX|POSIX.*) break ;;
            *) echo en; return ;;
        esac
    done
    if [[ -n "${WSL_DISTRO_NAME:-}" ]] && command -v powershell.exe >/dev/null 2>&1; then
        ui="$(cd /mnt/c 2>/dev/null || true; powershell.exe -NoProfile -NonInteractive -Command \
            '[Globalization.CultureInfo]::CurrentUICulture.TwoLetterISOLanguageName' 2>/dev/null | tr -d '\r\n ')"
        if [[ "$ui" == ja ]]; then echo ja; return; fi
    fi
    echo en
}
MSG_LANG="${MSG_LANG:-$(_detect_msg_lang)}"

declare -A _MSG_JA=() _MSG_EN=()
# 文章を登録する: _m キー 日本語 英語
_m() { _MSG_JA[$1]="$2"; _MSG_EN[$1]="$3"; }

msg() {
    local key="$1" fmt
    shift
    if [[ "$MSG_LANG" == ja ]]; then fmt="${_MSG_JA[$key]-}"; else fmt="${_MSG_EN[$key]-}"; fi
    [[ -n "$fmt" ]] || fmt="[$key]"
    # shellcheck disable=SC2059
    printf "$fmt" "$@"
}

# ===== 共通 =====
_m cmd_missing \
    "%s が見つかりません。先に ./setup-wsl.sh を実行してください。" \
    "%s not found. Run ./setup-wsl.sh first."
_m no_runtime_skip_ssh \
    "ランタイム %s がないので、SSH は行いません。" \
    "Runtime %s does not exist, so SSH is skipped."
_m runtime_missing_start \
    "ランタイム %s がありません。start.bat か ./up.sh で起動してください。" \
    "Runtime %s does not exist. Start it with start.bat or ./up.sh."
_m ssh_no_response \
    "SSH の応答がありません。" \
    "No response over SSH."
_m ssh_busy_429 \
    "別の colab ssh がこのランタイムに接続中です。手動の colab ssh を閉じてください。%s/6 回目" \
    "Another colab ssh is connected to this runtime. Close your manual colab ssh. Attempt %s/6"
_m ssh_waiting \
    "SSH 接続を待っています。%s/6 回目" \
    "Waiting for SSH. Attempt %s/6"
_m ssh_failed \
    "SSH で接続できません。ssh_config を見直し、./ssh.sh -v true で詳細を確認してください。" \
    "Cannot connect over SSH. Check ssh_config and run ./ssh.sh -v true for details."
_m ssh_failed_429 \
    "SSH で接続できません。別の colab ssh が接続中なら閉じてください。HTTP 429 はそれが原因です。" \
    "Cannot connect over SSH. If another colab ssh is connected, close it. HTTP 429 means exactly that."
_m node_upload_failed \
    "停止ボタンのカスタムノードを Colab VM へ送れませんでした。" \
    "Could not send the stop-button custom node to the Colab VM."

# ===== Google ドライブ =====
_m drive_already \
    "Google ドライブはマウント済みです。" \
    "Google Drive is already mounted."
_m drive_mounting \
    "Google ドライブをマウントします。認可の URL が表示されたらブラウザで開いて許可し、このウィンドウで Enter を押してください。" \
    "Mounting Google Drive. If an authorization URL appears, open it in a browser, allow access, then press Enter in this window."
_m drive_ready \
    "Google ドライブの %s のモデルを使います。" \
    "Using the models in %s on Google Drive."
_m drive_failed \
    "Google ドライブをマウントできませんでした。モデルは Colab VM の中だけに置かれ、ランタイムの削除で消えます。" \
    "Could not mount Google Drive. Models stay only on the Colab VM and are deleted with the runtime."
_m drive_flushing \
    "Google ドライブへの書き込みを完了させて、アンマウントします。" \
    "Finishing writes to Google Drive and unmounting it."
_m drive_flushed \
    "Google ドライブをアンマウントしました。" \
    "Google Drive is unmounted."
_m drive_flush_failed \
    "Google ドライブのアンマウントを確認できませんでした。直前に Drive へ保存したモデルは、Drive の画面で確かめてください。" \
    "Could not confirm that Google Drive was unmounted. Check on Google Drive any model saved there just now."

# ===== 環境の保存 =====
_m env_saving \
    "環境を Google ドライブに保存します。" \
    "Saving the environment to Google Drive."
_m env_save_failed \
    "環境を Google ドライブに保存できませんでした。" \
    "Could not save the environment to Google Drive."
_m env_delete_anyway \
    "保存せずにランタイムを削除しますか？ 前回の保存より後の変更は失われます。 [y/N] " \
    "Delete the runtime without saving? Changes since the last save are lost. [y/N] "
_m not_deleted \
    "ランタイムは削除していません。課金は続いています。原因を直してから、もう一度削除してください。" \
    "The runtime was not deleted and is still being billed. Fix the cause, then delete it again."

# ===== update.sh =====
_m updating \
    "ComfyUI を %s に更新します。更新の間、ComfyUI は止まります。" \
    "Updating ComfyUI to %s. ComfyUI stops during the update."
_m restarting \
    "ComfyUI を起動し直します。" \
    "Starting ComfyUI again."
_m update_finished \
    "更新が終わりました。ブラウザや Comfy Desktop の画面を再読み込みしてください。" \
    "The update is done. Reload ComfyUI in your browser or in Comfy Desktop."
_m update_failed \
    "更新できませんでした。上の表示を確かめてください。ComfyUI は元の版で動いています。" \
    "The update failed. See the messages above. ComfyUI runs with the earlier version."
_m u_not_ready \
    "このランタイムでは、まだ ComfyUI の準備が終わっていません。先に start.bat で起動してください。" \
    "ComfyUI is not set up on this runtime yet. Start it with start.bat first."
_m u_stopping \
    "ComfyUI と定期保存を止めます。" \
    "Stopping ComfyUI and the periodic save."

# ===== 起動待ちと復旧 =====
_m why_port_in_use \
    "ポート %s が使用中で ComfyUI が起動できませんでした" \
    "ComfyUI could not start because port %s is in use"
_m why_exited \
    "ComfyUI が途中で終了しました" \
    "ComfyUI exited before it was ready"
_m why_not_running \
    "Colab 側で ComfyUI が動いていません" \
    "ComfyUI is not running on Colab"
_m retry_setup \
    "%s。Colab 側で起動し直します。%s/%s 回目です。" \
    "%s. Starting it again on Colab. Attempt %s/%s."
_m retry_exhausted \
    "%s。%s 回やり直しても起動しませんでした。./logs.sh でログを確認してください。" \
    "%s. It did not start after %s retries. Check the log with ./logs.sh."
_m start_timeout \
    "%s 分待っても起動しませんでした。./logs.sh でログを確認してください。" \
    "ComfyUI did not start within %s minutes. Check the log with ./logs.sh."
_m comfy_unresponsive_redo \
    "Colab 上で ComfyUI が応答しません。起動し直します。" \
    "ComfyUI on Colab is not responding. Starting it again."

# ===== 出力の回収 =====
_m pull_none \
    "コピーする出力はありません。" \
    "There are no outputs to copy."
_m pull_start \
    "ComfyUI の出力を %s にコピーします。" \
    "Copying ComfyUI outputs to %s."
_m pull_done \
    "%s 個のファイルを %s にコピーしました。同じ名前のファイルが手元にあるものは残しています。" \
    "Copied %s files to %s. Files that already existed locally were kept."
_m pull_failed \
    "出力のコピーに失敗しました。" \
    "Copying the outputs failed."

# ===== トンネルと監視 =====
_m tunnel_already \
    "トンネルは別のウィンドウですでに有効です。" \
    "The tunnel is already up in another window."
_m local_port_busy \
    "手元のポート %s は別のプログラムが使っています。config.env の LOCAL_PORT を変えてください。" \
    "Local port %s is used by another program. Change LOCAL_PORT in config.env."
_m tunnel_closed \
    "トンネルを閉じました。ランタイムを削除していなければ課金が続くので、stop.bat か ./down.sh で削除してください。" \
    "Tunnel closed. Unless you deleted the runtime, it is still being billed. Delete it with stop.bat or ./down.sh."
_m tunnel_failed \
    "トンネルを張れませんでした。" \
    "Could not open the tunnel."
_m tunnel_unreachable \
    "トンネル経由で ComfyUI に接続できません。" \
    "Cannot reach ComfyUI through the tunnel."
_m ready \
    "準備ができました。" \
    "Ready."
_m tunnel_hold \
    "このウィンドウを開いている間トンネルが保たれます。Ctrl+C で切断します。ComfyUI の画面右上の「停止」からランタイムを削除できます。" \
    "The tunnel stays up while this window is open. Press Ctrl+C to disconnect. Use Stop at the top right of ComfyUI to delete the runtime."
_m runtime_stopped \
    "ランタイム %s が停止しました。" \
    "Runtime %s has stopped."
_m tunnel_stuck \
    "Colab 側の ComfyUI は動いていますが、トンネルが応答しません。張り直します。" \
    "ComfyUI is running on Colab, but the tunnel is not responding. Reopening it."
_m relink_retry_later \
    "トンネルを張り直せませんでした。次の確認でもう一度試します。" \
    "Could not reopen the tunnel. Will try again at the next check."
_m comfy_down_recovering \
    "Colab 側の ComfyUI が応答しません。復旧します。" \
    "ComfyUI on Colab is not responding. Recovering."
_m recovered \
    "ComfyUI が復旧しました。" \
    "ComfyUI has recovered."
_m button_delete \
    "ComfyUI の画面で「接続を解除してランタイムを削除」が選ばれました。" \
    "\"Disconnect and delete the runtime\" was chosen in ComfyUI."
_m button_disconnect \
    "ComfyUI の画面で「接続だけを解除」が選ばれました。" \
    "\"Disconnect only\" was chosen in ComfyUI."
_m end_deleted \
    "ランタイムはもうありません。コンピューティングユニットは消費されません。" \
    "The runtime is gone. No more compute units are used."
_m end_disconnected \
    "接続を解除しました。ランタイムは動き続けていて課金も続きます。再接続は start.bat、削除は stop.bat です。" \
    "Disconnected. The runtime keeps running and keeps being billed. Reconnect with start.bat, or delete it with stop.bat."
_m end_stopping \
    "stop.bat がランタイムを削除するので、トンネルを閉じました。進み具合は stop.bat のウィンドウに表示されます。" \
    "Closed the tunnel because stop.bat is deleting the runtime. Its window shows the progress."

# ===== 接続先の表示 =====
_m ep_browser "ブラウザ                       : %s" "Browser                         : %s"
_m ep_desktop "Comfy Desktop の Remote Connection : %s" "Comfy Desktop Remote Connection : %s"
_m ep_models  "モデルの置き場所               : %s" "Model folder                    : %s"

# ===== up.sh =====
_m units_left \
    "コンピューティングユニットの残り:" \
    "Compute units left:"
_m runtime_exists \
    "ランタイム %s は作成済みです。" \
    "Runtime %s already exists."
_m runtime_creating \
    "ランタイムを作成します: %s" \
    "Creating the runtime: %s"
_m ssh_connecting \
    "SSH で接続します。" \
    "Connecting over SSH."
_m gpu_line \
    "GPU: %s" \
    "GPU: %s"
_m gpu_unknown \
    "取得できませんでした" \
    "unknown"
_m gpu_mismatch \
    "期待した GPU %s ではありません。" \
    "This is not the expected GPU, %s."
_m gpu_continue_prompt \
    "このまま続けますか？ [y/N] " \
    "Continue anyway? [y/N] "
_m stopping_to_save \
    "コンピューティングユニットを節約するため、ランタイムを削除します。" \
    "Deleting the runtime to save compute units."
_m gpu_aborted \
    "中止しました。時間をおいて再実行するか、config.env の GPU を変えてください。" \
    "Aborted. Try again later or change GPU in config.env."
_m preparing \
    "ComfyUI を準備します。" \
    "Preparing ComfyUI."
_m waiting_start \
    "起動完了を待ちます。初回は ComfyUI の取得と依存関係のインストールで数分かかります。" \
    "Waiting for ComfyUI to start. The first run downloads ComfyUI and installs its dependencies, which takes a few minutes."
_m started \
    "ComfyUI が起動しました。" \
    "ComfyUI has started."

# ===== down.sh =====
_m closing_tunnel \
    "トンネルを閉じます。" \
    "Closing the tunnel."
_m stopping_runtime \
    "ランタイム %s を削除します。" \
    "Deleting runtime %s."
_m runtime_already_gone \
    "ランタイム %s は見つかりません。削除済みです。" \
    "Runtime %s was not found. It is already deleted."
_m usage_after_stop \
    "利用状況です。Active assignments: 0 ならランタイムは残っていません。" \
    "Usage. Active assignments: 0 means no runtime is left."

# ===== status.sh =====
_m st_runtime      "== ランタイム: %s ==" "== Runtime: %s =="
_m st_vm           "== Colab VM ==" "== Colab VM =="
_m st_ssh_fail     "SSH で接続できません" "Cannot connect over SSH"
_m st_comfy_up     "ComfyUI: 起動中" "ComfyUI: running"
_m st_comfy_down \
    "ComfyUI: 停止中または起動途中。./logs.sh で確認できます" \
    "ComfyUI: stopped or still starting. Check with ./logs.sh"
_m st_drive_on     "Google ドライブ: マウント済み" "Google Drive: mounted"
_m st_drive_off    "Google ドライブ: マウントされていません" "Google Drive: not mounted"
_m st_env         "最後の環境の保存: %s" "Last environment save: %s"
_m st_none         "ありません" "None"
_m st_tunnel       "== 手元のトンネル: %s ==" "== Local tunnel: %s =="
_m st_connected    "つながっています" "Connected"
_m st_disconnected "つながっていません" "Not connected"
_m st_units        "== コンピューティングユニット ==" "== Compute units =="

# ===== setup-wsl.sh =====
_m sw_apt          "== apt パッケージ: ssh, curl, iproute2, tar ==" "== apt packages: ssh, curl, iproute2, tar =="
_m sw_uv           "== uv ==" "== uv =="
_m sw_colab        "== Colab CLI ==" "== Colab CLI =="
_m sw_key          "== SSH 鍵: colab ssh がこの公開鍵を VM に送る ==" "== SSH key: colab ssh sends this public key to the VM =="
_m sw_key_exists   "既存の ~/.ssh/id_ed25519 を使います。" "Using the existing ~/.ssh/id_ed25519."
_m sw_done \
    "\n完了しました。次の手順:\n  1. colab usage を実行して Google アカウントの認証を済ませる。済んでいれば不要。\n     ブラウザが開かなければ、表示された URL を Windows のブラウザで開く。\n  2. 必要なら config.env を編集する。\n  3. start.bat をダブルクリックするか、./up.sh を実行する。\n" \
    "\nDone. Next steps:\n  1. Run colab usage to sign in to your Google account. Skip this if you already have.\n     If no browser opens, open the printed URL in a browser on Windows.\n  2. Edit config.env if needed.\n  3. Double-click start.bat or run ./up.sh.\n"

# ===== Colab VM 側: remote-setup.sh =====
_m r_already_running \
    "ComfyUI は既に起動しています。" \
    "ComfyUI is already running."
_m r_redo \
    "起動し直します。" \
    "Starting again."
_m r_prev_exited \
    "前回の ComfyUI は終了しています。起動し直します。" \
    "The previous ComfyUI has exited. Starting again."
_m r_port_fail_restart \
    "ポート %s が使用中で起動に失敗しています。起動し直します。" \
    "Startup failed because port %s is in use. Starting again."
_m r_in_progress \
    "準備または起動が進行中です。完了を待ちます。" \
    "Setup or startup is in progress. Waiting for it to finish."
_m r_installing \
    "%s をインストールします。" \
    "Installing %s."
_m r_stop_old \
    "前回の ComfyUI のプロセスを止めます: %s" \
    "Stopping processes left from the previous ComfyUI: %s"
_m r_port_other \
    "ポート %s は ComfyUI 以外のプロセスが使っています。そのプロセスは止めずに中止します:" \
    "Port %s is used by a process other than ComfyUI. Aborting without stopping it:"
_m r_port_other_hint \
    "config.env の REMOTE_PORT を別の番号に変えてから、もう一度実行してください。" \
    "Change REMOTE_PORT in config.env to another number and run again."
_m r_py \
    "ComfyUI 用の Python: %s %s、PyTorch %s" \
    "Python for ComfyUI: %s %s, PyTorch %s"
_m r_tag_failed \
    "最新のリリースタグを取得できませんでした。master を使います。" \
    "Could not get the latest release tag. Using master."
_m r_cloning \
    "ComfyUI を取得します: %s %s" \
    "Cloning ComfyUI: %s %s"
_m r_comfy_version \
    "ComfyUI の版: %s" \
    "ComfyUI version: %s"
_m r_requirements \
    "依存関係をインストールします: %s" \
    "Installing dependencies: %s"
_m r_cloning_node \
    "カスタムノードを取得します: %s" \
    "Cloning custom node: %s"
_m r_node_failed \
    "カスタムノード %s を入れられませんでした。ほかの準備は続けます。" \
    "Could not install custom node %s. Continuing with the rest."
_m r_update_available \
    "ComfyUI の新しい版 %s があります。今は %s です。update.bat か ./update.sh で更新できます。" \
    "A new version of ComfyUI, %s, is available. This one is %s. Update with update.bat or ./update.sh."
_m r_auto_updating \
    "AUTO_UPDATE=1 なので、起動の前に ComfyUI を %s に更新します。" \
    "AUTO_UPDATE=1, so ComfyUI is updated to %s before it starts."
_m r_auto_update_failed \
    "更新できなかったので、今の版のまま起動します。" \
    "The update failed, so ComfyUI starts with the current version."
_m r_sage_installing \
    "SageAttention を入れます: %s" \
    "Installing SageAttention: %s"
_m r_sage_enabled \
    "SageAttention を使います。" \
    "Using SageAttention."
_m r_sage_unavailable \
    "この環境では SageAttention を使えなかったので、標準の attention で起動します。理由は上の表示です。" \
    "SageAttention cannot be used here, so ComfyUI starts with the standard attention. The reason is shown above."
_m r_lock_timeout \
    "別の準備が 30 分たっても終わりません。./ssh.sh で入り、%s を持っているプロセスを確かめてください。" \
    "Another setup has not finished after 30 minutes. Open a shell with ./ssh.sh and check which process holds %s."
_m r_env_no_drive \
    "Google ドライブがマウントされていないので、環境の復元と保存はしません。" \
    "Google Drive is not mounted, so the environment is neither restored nor saved."
_m r_env_fresh \
    "RESTORE_ENVIRONMENT=0 なので、保存した環境は使わずに新しく作ります。" \
    "RESTORE_ENVIRONMENT=0, so a new environment is created instead of the saved one."
_m r_restore_failed \
    "保存した環境を復元できませんでした。上の理由を確かめてください。" \
    "Could not restore the saved environment. See the reason above."
_m r_source_incomplete \
    "ComfyUI の main.py がありません。保存した環境が壊れている可能性があります。" \
    "ComfyUI's main.py is missing. The saved environment may be damaged."
_m r_venv \
    "ComfyUI 用の venv を作ります。" \
    "Creating the venv for ComfyUI."
_m r_no_torch \
    "Colab の Python に CUDA 版の PyTorch が見つかりません。" \
    "No CUDA build of PyTorch was found in Colab's Python."
_m r_pip_check_failed \
    "pip check が失敗しました。パッケージの依存関係が食い違っています。食い違ったまま保存すると次のランタイムで復元できなくなるので、起動を中止します。" \
    "pip check failed: package dependencies conflict. Saving them like this would break the next restore, so startup is aborted."
_m r_saving \
    "準備した環境を Google ドライブに保存します。" \
    "Saving the prepared environment to Google Drive."
_m r_save_failed \
    "環境を保存できませんでした。起動は続けます。" \
    "Could not save the environment. Continuing startup."
_m r_drive_models \
    "Google ドライブのモデルを ComfyUI に追加しました: %s" \
    "Added the models on Google Drive to ComfyUI: %s"
_m r_started \
    "ComfyUI を起動しました。ポート %s" \
    "Started ComfyUI on port %s."
