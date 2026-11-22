"""手元の WSL で、自分の OAuth クライアントを使って Drive API を認可する。

長く使える更新用のトークンは手元にだけ置き、Colab の VM には 1 時間ほどで切れるアクセストークンだけを送る。
権限は drive.readonly に限る。ドライブを読むことしかできない。

    python3 drive_oauth.py authorize --client FILE --token FILE   ブラウザで許可して、更新用のトークンを保存する
    python3 drive_oauth.py access    --client FILE --token FILE   アクセストークンを JSON で標準出力に書く
    python3 drive_oauth.py revoke    --client FILE --token FILE   Google に取り消しを頼み、保存したトークンを消す

終了コード: 0 成功 / 1 失敗 / 2 クライアントの設定かトークンが無い / 3 トークンが無効になった。認可し直す必要がある
"""

import argparse
import base64
import hashlib
import http.server
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
import urllib.request

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
SCOPE = "https://www.googleapis.com/auth/drive.readonly"
WAIT_SEC = 300

TEXTS = {
    "ja": {
        "no_client": "OAuth クライアントの設定ファイルがありません: {path}",
        "bad_client": "OAuth クライアントの設定ファイルを読めません。Google Cloud で「デスクトップ アプリ」として作った JSON を置いてください: {path}",
        "open": "ブラウザで次の URL を開き、Google アカウントで許可してください。ブラウザが開かないときは、URL をコピーしてください。",
        "paste": "許可した後のページが開けないときは、そのページのアドレスをここに貼り付けて Enter を押してください。",
        "waiting": "許可を待っています。{sec} 秒で打ち切ります。",
        "timeout": "許可を待つ時間が過ぎました。",
        "denied": "許可されませんでした: {reason}",
        "state": "許可の応答が、この認可のものではありませんでした。",
        "no_refresh": "更新用のトークンが返ってきませんでした。Google アカウントの「サードパーティ製のアプリとサービス」からこのアプリへのアクセスを削除して、もう一度試してください。",
        "saved": "Drive API を認可しました。トークンは手元にだけ保存します: {path}",
        "page_ok": "認可しました。このタブを閉じて、ウィンドウに戻ってください。",
        "page_ng": "認可できませんでした。ウィンドウの表示を確かめてください。",
        "no_token": "Drive API のトークンがありません。",
        "invalid": "Drive API のトークンが無効になりました。認可し直してください。",
        "failed": "Google に接続できませんでした: {reason}",
        "revoked": "Drive API のトークンを取り消して、手元から消しました。",
    },
    "en": {
        "no_client": "The OAuth client file is missing: {path}",
        "bad_client": "Cannot read the OAuth client file. Put the JSON of a Desktop app client made in Google Cloud: {path}",
        "open": "Open the URL below in a browser and allow access with your Google account. If no browser opens, copy the URL.",
        "paste": "If the page after allowing does not open, paste its address here and press Enter.",
        "waiting": "Waiting for the authorization. Giving up in {sec} seconds.",
        "timeout": "Waited too long for the authorization.",
        "denied": "Access was not allowed: {reason}",
        "state": "The answer was not for this authorization.",
        "no_refresh": "No refresh token came back. Remove this app's access under Third-party apps & services in your Google account, then try again.",
        "saved": "The Drive API is authorized. The token is kept only on this PC: {path}",
        "page_ok": "Authorized. Close this tab and go back to the window.",
        "page_ng": "Authorization failed. Check the window.",
        "no_token": "There is no Drive API token.",
        "invalid": "The Drive API token is no longer valid. Authorize again.",
        "failed": "Could not reach Google: {reason}",
        "revoked": "Revoked the Drive API token and removed it from this PC.",
    },
}


def t(key, **values):
    lang = "ja" if os.environ.get("MSG_LANG") == "ja" else "en"
    return TEXTS[lang][key].format(**values)


class Failure(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def load_client(path):
    path = Path(path)
    if not path.is_file():
        raise Failure(t("no_client", path=path), 2)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        client = data.get("installed") or data
        client_id, secret = client["client_id"], client.get("client_secret", "")
    except (ValueError, KeyError, TypeError, AttributeError):
        raise Failure(t("bad_client", path=path)) from None
    if not re.fullmatch(r"[0-9A-Za-z._-]+\.apps\.googleusercontent\.com", client_id or ""):
        raise Failure(t("bad_client", path=path))
    return client_id, secret


def save_private(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(data, file)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def post(url, fields):
    request = urllib.request.Request(url, data=urlencode(fields).encode(), headers={
        "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "comfyui-instance-colab-cli/1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except HTTPError as error:
        try:
            body = json.loads(error.read() or b"{}")
        except ValueError:
            body = {}
        if body.get("error") in {"invalid_grant", "invalid_client", "unauthorized_client"}:
            raise Failure(t("invalid"), 3) from None
        raise Failure(t("failed", reason=f"HTTP {error.code} {body.get('error', '')}")) from None
    except (URLError, OSError) as error:
        raise Failure(t("failed", reason=getattr(error, "reason", error))) from None


def open_browser(url):
    """Windows のブラウザで開く。WSL の外へは cmd.exe の start で渡す。"""
    try:
        if shutil.which("cmd.exe"):
            # start の引数の & を cmd に解釈させないよう、^ でよける
            subprocess.Popen(["cmd.exe", "/c", "start", "", url.replace("&", "^&")], cwd="/mnt/c" if os.path.isdir("/mnt/c") else None, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def authorize(client_file, token_file, browser=True):
    client_id, secret = load_client(client_file)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    answer = {}
    done = threading.Event()

    def accept(query):
        """許可の応答を受け取る。ブラウザからでも、貼り付けたアドレスからでもよい。"""
        if done.is_set():
            return False
        values = {k: v[0] for k, v in parse_qs(query).items()}
        if "code" not in values and "error" not in values:
            return False
        answer.update(values)
        done.set()
        return True

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            ok = parsed.path == "/" and accept(parsed.query)
            page = t("page_ok") if ok and "code" in answer and answer.get("state") == state else t("page_ng")
            body = f"<!doctype html><meta charset=utf-8><title>comfyui-instance-colab-cli</title><p>{page}</p>".encode()
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def read_pasted():
        for line in sys.stdin:
            text = line.strip()
            if text and accept(urlsplit(text).query):
                return

    if sys.stdin and sys.stdin.isatty():
        threading.Thread(target=read_pasted, daemon=True).start()
    url = AUTH_URL + "?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
        "access_type": "offline", "prompt": "consent"})
    print(t("open"), flush=True)
    print("\n" + url + "\n", flush=True)
    if sys.stdin and sys.stdin.isatty():
        print(t("paste"), flush=True)
    print(t("waiting", sec=WAIT_SEC), flush=True)
    if browser:
        open_browser(url)
    try:
        if not done.wait(WAIT_SEC):
            raise Failure(t("timeout"))
    finally:
        server.shutdown()
        server.server_close()
    if answer.get("state") != state:
        raise Failure(t("state"))
    if "error" in answer:
        raise Failure(t("denied", reason=answer["error"]))
    fields = {"code": answer["code"], "client_id": client_id, "redirect_uri": redirect,
              "grant_type": "authorization_code", "code_verifier": verifier}
    if secret:
        fields["client_secret"] = secret
    result = post(TOKEN_URL, fields)
    if not result.get("refresh_token"):
        raise Failure(t("no_refresh"))
    save_private(token_file, {"client_id": client_id, "refresh_token": result["refresh_token"],
                              "scope": result.get("scope", SCOPE), "created": time.time()})
    print(t("saved", path=token_file), flush=True)


def access(client_file, token_file):
    client_id, secret = load_client(client_file)
    try:
        saved = json.loads(Path(token_file).read_text())
        refresh = saved["refresh_token"]
    except (OSError, ValueError, KeyError, TypeError):
        raise Failure(t("no_token"), 2) from None
    if saved.get("client_id") != client_id:
        raise Failure(t("invalid"), 3)
    fields = {"refresh_token": refresh, "client_id": client_id, "grant_type": "refresh_token"}
    if secret:
        fields["client_secret"] = secret
    result = post(TOKEN_URL, fields)
    token = result.get("access_token", "")
    if not re.fullmatch(r"[A-Za-z0-9._~+/=-]{20,4096}", token):
        raise Failure(t("failed", reason="no access token"))
    print(json.dumps({"token": token, "expiry": time.time() + int(result.get("expires_in") or 3600)}))


def revoke(client_file, token_file):
    try:
        saved = json.loads(Path(token_file).read_text())
        post(REVOKE_URL, {"token": saved["refresh_token"]})
    except (OSError, ValueError, KeyError, TypeError):
        pass
    except Failure:
        pass  # 既に無効なら取り消すものが無い
    Path(token_file).unlink(missing_ok=True)
    print(t("revoked"), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["authorize", "access", "revoke"])
    parser.add_argument("--client", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "authorize":
            authorize(args.client, args.token, browser=not args.no_browser)
        elif args.command == "access":
            access(args.client, args.token)
        else:
            revoke(args.client, args.token)
    except Failure as failure:
        print(failure, file=sys.stderr)
        return failure.code
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
