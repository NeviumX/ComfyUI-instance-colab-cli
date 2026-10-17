#!/usr/bin/env bash
# Colab にランタイムを作り、ComfyUI を起動して、手元のブラウザと Comfy Desktop からつなげるようにする。
# 途中で失敗しても、もう一度実行すれば済んだ手順は飛ばして続きから進む。
source "$(dirname "$0")/lib/common.sh"

check_gpu() {
    local gpu want="$EXPECT_GPU_REGEX"
    gpu="$(rssh "nvidia-smi --query-gpu=name,memory.total --format=csv,noheader" 2>/dev/null | head -n 1 || true)"
    log "$(msg gpu_line "${gpu:-$(msg gpu_unknown)}")"
    # 空なら GPU の種類名で判定する。G4 は表示名が分からないので確かめない
    if [[ -z "$want" ]]; then
        [[ "$GPU" == G4 ]] && return 0
        want="$GPU"
    fi
    [[ "$gpu" =~ $want ]] && return 0

    warn "$(msg gpu_mismatch "$want")"
    local ans
    case "$ON_GPU_MISMATCH" in
        continue) return 0 ;;
        stop) ans=n ;;
        *) read -r -p "$(msg gpu_continue_prompt)" ans || ans=n ;;
    esac
    [[ "$ans" =~ ^[yY] ]] && return 0

    log "$(msg stopping_to_save)"
    stop_master
    colab stop -s "$COLAB_SESSION" || true
    die "$(msg gpu_aborted)"
}

main() {
    require_cmds colab ssh curl ss tar
    rm -f "$STOPPING_FILE"

    log "$(msg units_left)"
    colab usage 2>/dev/null | sed 's/^/    /' || true

    # 1. ランタイム
    if session_exists; then
        log "$(msg runtime_exists "$COLAB_SESSION")"
    else
        local new_args=(-s "$COLAB_SESSION" --gpu "$GPU")
        [[ "$HIGH_MEM" == 1 ]] && new_args+=(--high-mem)
        # 前のランタイムへの共有 SSH 接続が残っていたら閉じる
        stop_master
        log "$(msg runtime_creating "colab new ${new_args[*]}")"
        colab new "${new_args[@]}"
    fi

    # 2. SSH と GPU
    log "$(msg ssh_connecting)"
    wait_ssh
    check_gpu

    # 3. Google ドライブ
    ensure_drive

    # 4. ComfyUI の準備と起動。VM 上の tmux で動かす
    log "$(msg preparing)"
    remote_start 0

    # 5. 起動完了を待つ。落ちたら Colab 側で自動でやり直す
    log "$(msg waiting_start)"
    wait_healthy
    log "$(msg started)"

    # 6. トンネル → ブラウザ。トンネルを保っている間、停止ボタンを見張る
    run_tunnel_foreground
}

main "$@"
