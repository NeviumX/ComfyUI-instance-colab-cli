"""Colab VM 上で、Hugging Face と Civitai のモデルをダウンロードする。

ComfyUI の画面からはノードがこのファイルを別のプロセスで起動し、ターミナルからは model-download.sh が ssh 越しに起動する。

    python3 model_download.py folders --models DIR [--drive-models DIR]
    python3 model_download.py download --models DIR [--drive-models DIR] --state DIR
                                       --url URL --folder NAME [--filename NAME] [--sha256 HEX] [--to-drive]
                                       [--tokens-file FILE | --tokens-stdin] [--progress-file FILE] [--bar]

ランタイムの models に .part として書き、確かめてから正しい名前にする。--to-drive なら、続けて Google ドライブにもコピーする。
ランタイムのディスクに先に書くので、ComfyUI ではすぐに使える。ドライブへの書き込みは遅いことがあるため、この順にしている。
"""

import argparse
import fcntl
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import signal
import socket
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit
import urllib.request

TEXTS = {
    "ja": {
        "unsupported_url": "Hugging Face か Civitai の URL を指定してください。",
        "hf_url": "Hugging Face は、ファイルのページの URL を指定してください。例: https://huggingface.co/<作者>/<リポジトリ>/blob/main/<ファイル>",
        "civitai_url": "Civitai は、バージョンを選んだモデルのページの URL か、ダウンロードの URL を指定してください。",
        "bad_folder": "保存先のフォルダー名が正しくありません: {folder}",
        "bad_filename": "ファイル名にはフォルダーを含められません: {name}",
        "bad_sha256": "SHA256 は 16 進数 64 文字で指定してください。",
        "not_https": "HTTPS ではない転送先には接続しません。",
        "private": "内部のアドレスへの転送には接続しません。",
        "unresolved": "接続先の名前を解決できません: {host}",
        "too_many_redirects": "転送が多すぎます。",
        "http_error": "ダウンロードに失敗しました。HTTP {status}{hint}",
        "hint_auth": "。認証が必要なモデルなら、config.local.env に {token} を書いてください",
        "not_a_model": "モデルではなく、Web ページが返ってきました。URL を確かめてください。認証が必要なモデルかもしれません。",
        "empty": "ダウンロードしたファイルが空でした。",
        "no_filename": "ファイル名が分かりません。ファイル名を指定してください。",
        "exists": "同じ名前のファイルが既にあります。上書きはしません: {path}",
        "no_space": "ランタイムのディスクの空きが足りません。必要 {need}、空き {free}。",
        "sha_mismatch": "SHA256 が一致しません。ダウンロードしたファイルは消しました。",
        "busy": "ほかのダウンロードが進んでいます。終わってからもう一度実行してください。",
        "no_drive": "Google ドライブがマウントされていないので、ドライブには保存できません。",
        "connection": "接続できませんでした: {reason}",
        "cancelled": "ダウンロードを中止しました。",
        "downloading": "ダウンロード中",
        "copying": "ドライブへコピー中",
        "saved_runtime": "ランタイムに保存しました: {path}",
        "saved_drive": "ランタイムと Google ドライブに保存しました: {path}",
        "refresh": "ComfyUI の画面で R キーを押すと、モデルの一覧に出ます。",
    },
    "en": {
        "unsupported_url": "Give a Hugging Face or Civitai URL.",
        "hf_url": "For Hugging Face, give the URL of the file page, such as https://huggingface.co/<owner>/<repo>/blob/main/<file>",
        "civitai_url": "For Civitai, give the URL of a model page with a version selected, or a download URL.",
        "bad_folder": "The folder name is not valid: {folder}",
        "bad_filename": "The file name cannot contain a folder: {name}",
        "bad_sha256": "Give SHA256 as 64 hexadecimal characters.",
        "not_https": "A redirect to a non-HTTPS address is not followed.",
        "private": "A redirect to an internal address is not followed.",
        "unresolved": "Cannot resolve the host name: {host}",
        "too_many_redirects": "Too many redirects.",
        "http_error": "The download failed with HTTP {status}{hint}",
        "hint_auth": ". If the model needs authentication, put {token} in config.local.env",
        "not_a_model": "A web page came back instead of a model. Check the URL. The model may need authentication.",
        "empty": "The downloaded file was empty.",
        "no_filename": "The file name is unknown. Give a file name.",
        "exists": "A file with the same name already exists and is not overwritten: {path}",
        "no_space": "Not enough free disk space on the runtime. Needed {need}, free {free}.",
        "sha_mismatch": "SHA256 does not match. The downloaded file was deleted.",
        "busy": "Another download is in progress. Try again when it is done.",
        "no_drive": "Google Drive is not mounted, so the model cannot be saved there.",
        "connection": "Could not connect: {reason}",
        "cancelled": "The download was cancelled.",
        "downloading": "Downloading",
        "copying": "Copying to Drive",
        "saved_runtime": "Saved on the runtime: {path}",
        "saved_drive": "Saved on the runtime and on Google Drive: {path}",
        "refresh": "Press R in ComfyUI to see it in the model lists.",
    },
}


def t(key, **values):
    lang = "ja" if os.environ.get("MSG_LANG", os.environ.get("COMFY_COLAB_CLI_LANG")) == "ja" else "en"
    return TEXTS[lang][key].format(**values)


class Failure(Exception):
    """利用者に見せる理由のある失敗。"""


def size_text(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ---- URL ----

def parse_model_url(url):
    """ブラウザからコピーした URL から、ダウンロードの URL と、分かればファイル名を返す。"""
    parsed = urlsplit(url.strip())
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    host = (parsed.hostname or "").removeprefix("www.")
    if parsed.scheme == "https" and host == "huggingface.co":
        if len(parts) < 5 or parts[2] not in {"resolve", "blob"} or not re.fullmatch(r"[A-Za-z0-9_.-]+", parts[0] + parts[1]):
            raise Failure(t("hf_url"))
        file = "/".join(parts[4:])
        if any(p in {"", ".", ".."} for p in parts[4:]):
            raise Failure(t("hf_url"))
        return (f"https://huggingface.co/{parts[0]}/{parts[1]}/resolve/{quote(parts[3], safe='')}/{quote(file, safe='/')}",
                parts[-1], "HF_TOKEN")
    if parsed.scheme == "https" and host == "civitai.com":
        version = parse_qs(parsed.query).get("modelVersionId", [""])[0]
        if parts[:3] == ["api", "download", "models"] and len(parts) == 4:
            version = parts[3]
        elif parts[:1] != ["models"] or not version:
            raise Failure(t("civitai_url"))
        if not re.fullmatch(r"[1-9][0-9]*", version):
            raise Failure(t("civitai_url"))
        return "https://civitai.com/api/download/models/" + version, "", "CIVITAI_TOKEN"
    raise Failure(t("unsupported_url"))


def checked_https(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise Failure(t("not_https"))
    if parsed.hostname in {"localhost", "metadata.google.internal"}:
        raise Failure(t("private"))
    try:
        addresses = {ipaddress.ip_address(parsed.hostname)}
    except ValueError:
        # 公開されていそうな名前でも、VM の内部のサービスを指していることがある
        try:
            found = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        except OSError:
            raise Failure(t("unresolved", host=parsed.hostname)) from None
        addresses = {ipaddress.ip_address(item[4][0].split("%")[0]) for item in found}
    if any(not ip.is_global for ip in addresses):
        raise Failure(t("private"))
    return parsed


def served_filename(headers):
    """応答が示すファイル名。普通の名前でなければ空。"""
    from email.message import EmailMessage
    message = EmailMessage()
    try:
        message["Content-Disposition"] = headers.get("Content-Disposition") or "attachment"
        name = message.get_filename() or ""
    except (ValueError, TypeError, LookupError):
        return ""
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    return name if re.fullmatch(r"[^\x00-\x1f:/]{1,200}", name) and not name.startswith(".") else ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


# 環境変数のプロキシや .netrc は使わず、転送は自分でたどる
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def open_download(url, token):
    """転送をたどってダウンロードの応答を返す。トークンは最初の接続先にだけ送る。"""
    first_host = checked_https(url).hostname
    current = url
    for _ in range(11):
        parsed = checked_https(current)
        request = urllib.request.Request(current, headers={"User-Agent": "comfyui-instance-colab-cli/1"})
        if token and parsed.hostname == first_host:
            request.add_header("Authorization", "Bearer " + token)
        try:
            response = _OPENER.open(request, timeout=60)
        except HTTPError as error:
            if error.code in {301, 302, 303, 307, 308}:
                location = error.headers.get("Location")
                error.close()
                if not location:
                    raise Failure(t("too_many_redirects")) from None
                current = urljoin(current, location)
                continue
            raise
        content_type = (response.headers.get("Content-Type") or "").lower()
        if "text/html" in content_type or "application/json" in content_type:
            response.close()
            raise Failure(t("not_a_model"))
        return response
    raise Failure(t("too_many_redirects"))


# ---- 保存先 ----

def safe_folder(folder):
    value = folder.strip().strip("/")
    p = PurePosixPath(value)
    if (not value or "\\" in value or ":" in value or "\x00" in value or p.is_absolute()
            or any(x in {"", ".", ".."} for x in value.split("/")) or value.startswith(".")):
        raise Failure(t("bad_folder", folder=folder))
    return value


def safe_filename(name):
    if "/" in name or "\\" in name:
        raise Failure(t("bad_filename", name=name))
    if not re.fullmatch(r"[^\x00-\x1f:]{1,200}", name) or name.startswith("."):
        raise Failure(t("bad_filename", name=name))
    return name


def folders(models, drive_models):
    """選べるモデルのフォルダー。ランタイムと Drive の両方にあるものを合わせる。"""
    names = set()
    for root in (models, drive_models):
        if root and root.is_dir():
            names |= {d.name for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")}
    return sorted(names)


def inside(root, relative):
    target = root / relative
    if not target.resolve().is_relative_to(root.resolve()):
        raise Failure(t("bad_folder", folder=relative))
    return target


# ---- 進み具合 ----

class Progress:
    """進み具合を、画面用のファイルと、ターミナル用の 1 行のバーに出す。
    バーはメッセージと同じ標準出力に書く。ssh 越しだと標準出力と標準エラーは別々に届き、順番が入れ替わるため。"""

    def __init__(self, path, bar, name):
        self.path, self.bar, self.name = path, bar, name
        self.started = self.last = time.monotonic()
        self.phase, self.done, self.total = "download", 0, 0

    def write(self, state, message="", force=False):
        now = time.monotonic()
        if not force and now - self.last < 0.5:
            return
        self.last = now
        speed = self.done / max(now - self.started, 0.001)
        if self.path:
            data = {"state": state, "phase": self.phase, "name": self.name, "received": self.done,
                    "total": self.total, "speed": speed, "message": message}
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data))
            os.replace(temporary, self.path)
        if self.bar and state == "running":
            label = t("downloading") if self.phase == "download" else t("copying")
            if self.total:
                filled = int(30 * self.done / self.total)
                line = (f"{label} [{'#' * filled}{'.' * (30 - filled)}] {self.done * 100 / self.total:5.1f}% "
                        f"{size_text(self.done)} / {size_text(self.total)} {size_text(speed)}/s")
            else:
                line = f"{label} {size_text(self.done)} {size_text(speed)}/s"
            sys.stdout.write("\r" + line + " " * 4)
            sys.stdout.flush()

    def end_line(self):
        """バーの最後の状態を書いてから改行する。速く終わると、途中の表示が 0% のまま残るため。"""
        if self.bar and self.done:
            self.write("running", force=True)
            sys.stdout.write("\n")

    def start(self, phase, total):
        if self.phase != phase:
            self.end_line()
        self.phase, self.done, self.total = phase, 0, total
        self.started = time.monotonic()
        self.write("running", force=True)

    def advance(self, amount):
        self.done += amount
        self.write("running")

    def finish(self, state, message):
        if state == "done":
            self.end_line()
        elif self.bar and self.done:
            sys.stdout.write("\n")
        self.write(state, message, force=True)


# ---- ダウンロード ----

def copy_with_progress(source, target, progress):
    progress.start("copy", source.stat().st_size)
    partial = target.with_name(".comfy-" + secrets.token_hex(8) + ".part")
    try:
        with source.open("rb") as src, partial.open("wb") as dst:
            for chunk in iter(lambda: src.read(8 * 1024 * 1024), b""):
                dst.write(chunk)
                progress.advance(len(chunk))
        if target.exists():
            raise Failure(t("exists", path=target))
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)


def download(args, tokens, progress):
    url, suggested, token_name = parse_model_url(args.url)
    folder = safe_folder(args.folder)
    given = args.filename.strip() or suggested
    name = safe_filename(given) if given else ""
    sha256 = args.sha256.strip().lower()
    if sha256 and not re.fullmatch(r"[a-f0-9]{64}", sha256):
        raise Failure(t("bad_sha256"))
    models = Path(args.models)
    drive_models = Path(args.drive_models) if args.drive_models else None
    if args.to_drive and not (drive_models and drive_models.parent.is_dir()):
        raise Failure(t("no_drive"))

    def check_free(target_name):
        for root in [models] + ([drive_models] if args.to_drive else []):
            if (root / folder / target_name).exists():
                raise Failure(t("exists", path=f"{folder}/{target_name}"))

    if name:
        check_free(name)
    local_dir = inside(models, folder)
    local_dir.mkdir(parents=True, exist_ok=True)
    partial = local_dir / (".comfy-" + secrets.token_hex(8) + ".part")
    try:
        try:
            response = open_download(url, tokens.get(token_name, ""))
        except HTTPError as error:
            hint = t("hint_auth", token=token_name) if error.code in {401, 403} and not tokens.get(token_name) else ""
            raise Failure(t("http_error", status=error.code, hint=hint)) from None
        except URLError as error:
            raise Failure(t("connection", reason=error.reason)) from None
        with response:
            length = response.headers.get("Content-Length", "")
            total = int(length) if length.isdigit() else 0
            free = shutil.disk_usage(local_dir).free
            if total and total + 1024 ** 3 > free:
                raise Failure(t("no_space", need=size_text(total), free=size_text(free)))
            name = name or served_filename(response.headers)
            if not name:
                raise Failure(t("no_filename"))
            progress.name = name
            check_free(name)
            digest = hashlib.sha256()
            progress.start("download", total)
            with partial.open("wb") as file:
                for chunk in iter(lambda: response.read(4 * 1024 * 1024), b""):
                    file.write(chunk)
                    digest.update(chunk)
                    progress.advance(len(chunk))
        if not partial.stat().st_size:
            raise Failure(t("empty"))
        if sha256 and not hmac.compare_digest(digest.hexdigest(), sha256):
            raise Failure(t("sha_mismatch"))
        target = local_dir / name
        check_free(name)
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    relative = f"{folder}/{name}"
    if args.to_drive:
        drive_dir = inside(drive_models, folder)
        drive_dir.mkdir(parents=True, exist_ok=True)
        copy_with_progress(target, drive_dir / name, progress)
        return t("saved_drive", path=relative) + " SHA256: " + digest.hexdigest()
    return t("saved_runtime", path=relative) + " SHA256: " + digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["folders", "download"])
    parser.add_argument("--models", required=True)
    parser.add_argument("--drive-models", default="")
    parser.add_argument("--state", default="")
    parser.add_argument("--url", default="")
    parser.add_argument("--folder", default="")
    parser.add_argument("--filename", default="")
    parser.add_argument("--sha256", default="")
    parser.add_argument("--to-drive", action="store_true")
    parser.add_argument("--tokens-file", default="")
    parser.add_argument("--tokens-stdin", action="store_true")
    parser.add_argument("--progress-file", default="")
    parser.add_argument("--bar", action="store_true")
    args = parser.parse_args()

    drive_models = Path(args.drive_models) if args.drive_models else None
    if args.command == "folders":
        print(json.dumps({"folders": folders(Path(args.models), drive_models),
                          "drive": bool(drive_models and drive_models.parent.is_dir())}))
        return 0

    tokens = {}
    try:
        if args.tokens_stdin:
            tokens = json.loads(sys.stdin.read() or "{}")
        elif args.tokens_file and Path(args.tokens_file).exists():
            tokens = json.loads(Path(args.tokens_file).read_text())
    except ValueError:
        tokens = {}
    progress = Progress(Path(args.progress_file) if args.progress_file else None, args.bar, "")
    report = sys.stdout if args.bar else sys.stderr

    # 中止されたら、書きかけのファイルを消してから終わる
    def stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)

    state = Path(args.state or args.models)
    state.mkdir(parents=True, exist_ok=True)
    with (state / "download.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            progress.finish("failed", t("busy"))
            print(t("busy"), file=report)
            return 4
        try:
            message = download(args, tokens, progress)
        except Failure as failure:
            progress.finish("failed", str(failure))
            print(failure, file=report)
            return 1
        except KeyboardInterrupt:
            progress.finish("cancelled", t("cancelled"))
            print(t("cancelled"), file=report)
            return 130
        except Exception as failure:  # 想定していない失敗も、画面には理由を出す
            progress.finish("failed", f"{type(failure).__name__}: {failure}")
            print(f"{type(failure).__name__}: {failure}", file=report)
            return 1
        progress.finish("done", message + " " + t("refresh"))
        print(message)
        print(t("refresh"))
        return 0


if __name__ == "__main__":
    sys.exit(main())
