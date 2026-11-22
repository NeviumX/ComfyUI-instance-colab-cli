#!/usr/bin/env bash
# URL を Windows の既定のブラウザで開く。colab drivemount に BROWSER=open-url.sh として渡す。
# Python の webbrowser は BROWSER を引数に分けないので、このフォルダーを PATH の先頭に足して名前で呼ばせる。
# WSL の Python は既定で gio を使うが、WSL では開けずに URL 入りのエラーを表示するだけになる。
url="$1"
[[ -n "$url" ]] || exit 1
if command -v cmd.exe >/dev/null 2>&1; then
    # start の引数の & を cmd に解釈させないよう、^ でよける
    cd /mnt/c 2>/dev/null || true
    # cmd.exe は標準入力を読むので、利用者が押す Enter を取られないよう切り離す
    exec cmd.exe /c start "" "${url//&/^&}" </dev/null >/dev/null 2>&1
fi
if command -v xdg-open >/dev/null 2>&1; then
    exec xdg-open "$url" </dev/null >/dev/null 2>&1
fi
exit 1
