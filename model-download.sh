#!/usr/bin/env bash
# 動いているランタイムに、Hugging Face か Civitai のモデルをダウンロードする。
# 保存先のフォルダーを番号で選び、URL を貼り付け、保存する場所を選ぶ。
#   1) ランタイムのみ: 台帳に記録しない  2) ランタイムのみで台帳に記録  3) ランタイムとGoogleドライブ: 台帳に記録してドライブにも写す
# 3 のドライブへのコピーは、ダウンロードに続けてこのウィンドウで進み具合を出しながら行う。
#   ./model-download.sh
# 成功したら 0 で終わる。model-download.bat は、失敗したときだけウィンドウを残す。
source "$(dirname "$0")/lib/common.sh"

# ssh は標準入力を読んで VM へ送るので、入力した答えを横取りしないよう、手元の入力は記述子 3 に退避して
# ほかのコマンドの標準入力は空にしておく
exec 3<&0 </dev/null

require_cmds colab ssh tar
session_exists || die "$(msg runtime_missing_start "$COLAB_SESSION")"
rssh "test -f $STAGE_DIR/state/setup-done" 2>/dev/null || die "$(msg u_not_ready)"
detect_drive
# 最新の model_download.py とトークンを送っておく
upload_files

drive_arg=""
[[ "$DRIVE_READY" == 1 ]] && drive_arg="--drive-models $(printf '%q' "$DRIVE_MODELS")"
# root で動かすので、ComfyUI の venv の Python ではなく、VM の Python を使う。venv はその利用者が書き換えられる
runner="source $(printf '%q' "$STAGE_DIR/env.sh") && \"\$COMFY_PYTHON\" $(printf '%q' "$STAGE_DIR/model_download.py")"
models_arg="--models $(printf '%q' "$COMFY_BASE/models") $drive_arg"

# ---- 保存先のフォルダー ----
listing="$(rssh "$runner folders $models_arg")" || die "$(msg md_list_failed)"
mapfile -t folder_list < <(python3 -c 'import json, sys; print("\n".join(json.load(sys.stdin)["folders"]))' <<<"$listing")
(( ${#folder_list[@]} > 0 )) || die "$(msg md_list_failed)"
echo
msg md_folders; echo
default_index=1
for i in "${!folder_list[@]}"; do
    printf '  %2d) %s\n' "$((i + 1))" "${folder_list[$i]}"
    [[ "${folder_list[$i]}" == checkpoints ]] && default_index=$((i + 1))
done
while true; do
    read -r -u 3 -p "$(msg md_pick_folder "$default_index")" pick || exit 1
    pick="${pick:-$default_index}"
    if [[ "$pick" =~ ^[0-9]+$ ]] && (( pick >= 1 && pick <= ${#folder_list[@]} )); then
        folder="${folder_list[$((pick - 1))]}"
        break
    fi
    warn "$(msg md_bad_number)"
done

# ---- URL ----
while true; do
    read -r -u 3 -p "$(msg md_url)" url || exit 1
    url="${url//[[:space:]]/}"
    [[ -n "$url" ]] && break
done
read -r -u 3 -p "$(msg md_filename)" filename || exit 1

# ---- 保存する場所 ----
echo
msg md_where; echo
last=3
if [[ "$DRIVE_READY" != 1 ]]; then
    log "$(msg md_no_drive)"
    last=2
fi
while true; do
    read -r -u 3 -p "$(msg md_where_prompt "$last")" where || exit 1
    where="${where:-$last}"
    [[ "$where" =~ ^[1-9]$ ]] && (( where <= last )) && break
    warn "$(msg md_bad_number)"
done
# 2 と 3 は台帳に記録し、ComfyUI が読む写しも書き直す。1 はドライブへ自動で写さないものとして記録する
ledger_arg="--runtime-only-file \"\$COMFY_RUNTIME_ONLY\""
if (( where >= 2 )); then
    ledger_arg="--ledger \"\$COMFY_LEDGER\" --ledger-mirror \"\$COMFY_LEDGER_MIRROR\""
fi
to_drive=""
(( where == 3 )) && to_drive="--to-drive"

# ---- ダウンロード ----
echo
log "$(msg md_starting "$folder")"
# トークンは標準入力で渡す。進み具合のバーは ssh 越しに 1 行で書き換わる
code=0
tokens_json | rssh "MSG_LANG=$MSG_LANG $runner download $models_arg --state $(printf '%q' "$STAGE_DIR/state") \
    --url $(printf '%q' "$url") --folder $(printf '%q' "$folder") --filename $(printf '%q' "$filename") \
    $ledger_arg $to_drive --tokens-stdin --bar" || code=$?
if [[ "$code" != 0 ]]; then
    die "$(msg md_failed)"
fi
pull_ledger
log "$(msg md_done)"
