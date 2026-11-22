"""モデルの台帳と、ランタイムに写しが無いモデルの取得。

台帳には、このツールでダウンロードしたモデルの保存先とダウンロード元を記録する。
台帳にあるモデルは、ランタイムにもドライブにも無くても ComfyUI の一覧に出し、ノードで使われたときにダウンロードする。

ランタイムに写しが無いモデルがノードで使われたときは、次の順に試してランタイムへ取ってくる。
    MODEL_SOURCE=drive: Drive API の並列ダウンロード → ドライブからの順番どおりのコピー → 台帳の元 URL
    MODEL_SOURCE=url:   台帳の元 URL → Drive API の並列ダウンロード → ドライブからの順番どおりのコピー
どれも失敗したら、ドライブにあるモデルはドライブから直接読ませる。

ノードからは同じプロセスで読み込み、手元の WSL からはコマンドとして使う。
    python3 model_sources.py merge --into FILE --from FILE [--lock FILE]
"""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
import urllib.request

VERSION = 1
CHUNK = 64 * 1024 * 1024
WORKERS = 8
SPARE = 1024 ** 3  # 取ってきた後もランタイムのディスクに残しておく空き

TEXTS = {
    "ja": {
        "source_url": "元の URL からダウンロード",
        "source_drive_api": "Google ドライブから並列ダウンロード",
        "source_drive_copy": "Google ドライブからコピー",
        "no_space": "ランタイムのディスクの空きが足りないので、ランタイムには写しません。必要 {need}、空き {free}。",
        "direct": "{reason} Google ドライブから直接読み込みます。",
        "all_failed": "モデルを取得できませんでした: {name}。{reason}",
        "no_source": "台帳に元の URL がありません。",
        "size_mismatch": "大きさが台帳と違います。",
        "hash_mismatch": "SHA256 が台帳と違います。",
        "no_drive_token": "Drive API の認証情報がありません。",
        "drive_not_found": "Drive API でファイルが見つかりません。",
        "range": "Drive API が範囲を指定した読み込みに応じませんでした。",
        "backed_up": "Google ドライブにもコピーしました: {name}",
        "source_to_drive": "Google ドライブへコピー",
        "backup_failed": "Google ドライブにコピーできませんでした: {name}。{reason}",
        "drive_exists": "ドライブに同じ名前のファイルがあります。",
    },
    "en": {
        "source_url": "Downloading from the original URL",
        "source_drive_api": "Downloading from Google Drive in parallel",
        "source_drive_copy": "Copying from Google Drive",
        "no_space": "Not enough free disk space on the runtime, so no copy is made there. Needed {need}, free {free}.",
        "direct": "{reason} Reading it from Google Drive directly.",
        "all_failed": "Could not get the model {name}. {reason}",
        "no_source": "The ledger has no original URL for it.",
        "size_mismatch": "The size differs from the ledger.",
        "hash_mismatch": "The SHA256 differs from the ledger.",
        "no_drive_token": "No credentials for the Drive API.",
        "drive_not_found": "The Drive API cannot find the file.",
        "range": "The Drive API did not answer a ranged read.",
        "backed_up": "Also copied to Google Drive: {name}",
        "source_to_drive": "Copying to Google Drive",
        "backup_failed": "Could not copy {name} to Google Drive. {reason}",
        "drive_exists": "A file with the same name is already on Drive.",
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


# ---- 台帳 ----

def valid_key(key):
    """台帳のキーは models から見た相対パス。フォルダー/ファイル名 の形に限る。"""
    if not isinstance(key, str) or "\\" in key or "\x00" in key or ":" in key:
        return False
    parts = key.split("/")
    return len(parts) >= 2 and all(p and p not in {".", ".."} and not p.startswith(".") for p in parts)


def read_ledger(path):
    """台帳のモデルの記録。キーから記録への辞書で、外したものも含む。読めなければ空。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, dict):
        return {}
    return {k: v for k, v in models.items() if valid_key(k) and isinstance(v, dict)}


def write_ledger(path, models):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + secrets.token_hex(4) + ".tmp")
    try:
        temporary.write_text(json.dumps({"version": VERSION, "models": models}, ensure_ascii=False, indent=1,
                                        sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def merge_models(base, other):
    """2 つの台帳を合わせる。同じキーは、後で変えたほうを残す。外した記録も、後のほうなら残す。"""
    merged = dict(base)
    for key, entry in other.items():
        mine = merged.get(key)
        if mine is None or float(entry.get("updated", 0)) > float(mine.get("updated", 0)):
            merged[key] = entry
    return merged


@contextlib.contextmanager
def locked(lock_path):
    """台帳を書き換える間の排他。台帳がドライブにあるときも、ロックはランタイムのディスクに置く。"""
    if not lock_path:
        yield
        return
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


class Ledger:
    def __init__(self, path, lock_path=None):
        self.path = Path(path) if path else None
        self.lock_path = lock_path
        self._cache = (0.0, None, {})  # 読んだ時刻、更新時刻、内容

    def all(self):
        """外したものも含む記録。ComfyUI は一覧を何度も作るので、2 秒の間は読み直さない。"""
        if not self.path:
            return {}
        read_at, mtime, models = self._cache
        now = time.monotonic()
        if now - read_at < 2:
            return models
        try:
            current = self.path.stat().st_mtime
        except OSError:
            current = None
        if current != mtime or current is None:
            models = read_ledger(self.path) if current is not None else {}
        self._cache = (now, current, models)
        return models

    def entries(self):
        return {k: v for k, v in self.all().items() if not v.get("removed")}

    def change(self, update):
        if not self.path:
            return
        with locked(self.lock_path):
            models = read_ledger(self.path)
            update(models)
            write_ledger(self.path, models)
        self._cache = (0.0, None, {})

    def record(self, key, url, page, sha256, size):
        def update(models):
            models[key] = {"url": url, "page": page, "sha256": sha256, "size": size, "updated": time.time()}
        self.change(update)

    def remove(self, key):
        def update(models):
            entry = dict(models.get(key) or {})
            entry.update(removed=True, updated=time.time())
            models[key] = entry
        self.change(update)


# ---- 進み具合 ----

class Report:
    """取得の進み具合を、ComfyUI の画面の右下の通知へ送る。"""

    def __init__(self, notify, name):
        self.notify, self.name = notify or (lambda data: None), name
        self.lock = threading.Lock()
        self.source, self.done, self.total = "", 0, 0
        self.started = self.last = time.monotonic()

    def send(self, state, message="", force=False):
        now = time.monotonic()
        if not force and now - self.last < 0.5:
            return
        self.last = now
        speed = self.done / max(now - self.started, 0.001)
        self.notify({"state": state, "name": self.name, "source": self.source, "received": self.done,
                     "total": self.total, "speed": speed, "message": message})

    def start(self, source, total):
        with self.lock:
            self.source, self.done, self.total = source, 0, total
            self.started = time.monotonic()
        self.send("running", t("source_" + source), force=True)

    def advance(self, amount):
        with self.lock:
            self.done += amount
        self.send("running", t("source_" + self.source))


# ---- 取得 ----

class Stopped(Exception):
    """ComfyUI の中断で、取得をやめた。"""


def parallel_fill(target, total, read_range, report, interrupted, workers):
    """target に total バイトを、CHUNK ごとに分けて workers 本で同時に書く。read_range(start, end) はバイト列を順に返す。"""
    from concurrent.futures import ThreadPoolExecutor, FIRST_EXCEPTION, wait
    stop = threading.Event()
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        if total:
            os.ftruncate(fd, total)

        def one(start):
            end = min(start + CHUNK, total)
            position = start
            for block in read_range(start, end):
                if stop.is_set():
                    raise Stopped()
                if position + len(block) > end:
                    raise Failure(t("size_mismatch"))
                os.pwrite(fd, block, position)
                position += len(block)
                report.advance(len(block))
            if position != end:
                raise Failure(t("size_mismatch"))

        with ThreadPoolExecutor(workers) as pool:
            pending = {pool.submit(one, start) for start in range(0, total, CHUNK)}
            while pending:
                finished, pending = wait(pending, timeout=0.5, return_when=FIRST_EXCEPTION)
                for future in finished:
                    if future.exception():
                        stop.set()
                        for other in pending:
                            other.cancel()
                        raise future.exception()
                if interrupted():
                    stop.set()
                    for other in pending:
                        other.cancel()
                    raise Stopped()
    finally:
        os.close(fd)


class DriveApi:
    """Colab の認証情報で Drive API を使う。使えなければ、しばらく試し直さない。"""

    BASE = "https://www.googleapis.com/drive/v3/files"

    def __init__(self, system_python, mydrive, token_file=None):
        self.system_python, self.mydrive = system_python, Path(mydrive) if mydrive else None
        self.token_file = Path(token_file) if token_file else None
        self.token, self.expires, self.failed_until = "", 0.0, 0.0
        self.rejected = ""  # Google に断られた、手元から届いたトークン
        self.ids = {}
        self.lock = threading.Lock()

    def sent_token(self, now):
        """手元の WSL が送ってきたアクセストークン。自分の OAuth クライアントで、ドライブを読むことだけを許可したもの。"""
        if not self.token_file:
            return ""
        try:
            data = json.loads(self.token_file.read_text())
            token, expiry = data["token"], float(data["expiry"])
        except (OSError, ValueError, KeyError, TypeError):
            return ""
        if expiry < now + 60 or token == self.rejected or not re.fullmatch(r"[A-Za-z0-9._~+/=-]{20,4096}", token):
            return ""
        return token

    def access_token(self):
        with self.lock:
            now = time.time()
            sent = self.sent_token(now)
            if sent:
                return sent
            if self.token and now < self.expires - 300:
                return self.token
            if now < self.failed_until:
                raise Failure(t("no_drive_token"))
            # ComfyUI の venv には google-auth が無いことがあるので、Colab の Python に頼む
            script = ("import json, google.auth\nfrom google.auth.transport.requests import Request\n"
                      "c, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/drive.readonly'])\n"
                      "c.refresh(Request())\n"
                      "print(json.dumps({'token': c.token, 'expiry': c.expiry.timestamp() if c.expiry else 0}))\n")
            attempts = []
            if self.system_python:
                attempts.append([self.system_python, "-c", script])
            # Colab の gcloud は、SSH のシェルの PATH に入っていない場所にある
            gcloud = shutil.which("gcloud") or next(
                (p for p in ("/tools/google-cloud-sdk/bin/gcloud",) if os.access(p, os.X_OK)), "")
            if gcloud:
                attempts.append([gcloud, "auth", "print-access-token"])
            for command in attempts:
                try:
                    out = subprocess.run(command, capture_output=True, text=True, timeout=60).stdout.strip()
                except (OSError, subprocess.TimeoutExpired):
                    continue
                if not out:
                    continue
                try:
                    data = json.loads(out.splitlines()[-1])
                    token, expiry = data["token"], float(data.get("expiry") or 0)
                except (ValueError, KeyError, TypeError):
                    token, expiry = out.splitlines()[-1], 0
                if re.fullmatch(r"[A-Za-z0-9._~+/=-]{20,}", token or ""):
                    self.token = token
                    self.expires = expiry if expiry > now else now + 1800
                    return token
            self.failed_until = now + 600
            raise Failure(t("no_drive_token"))

    def give_up(self, token):
        with self.lock:
            if token == self.sent_token(time.time()):
                self.rejected = token  # 次に手元から届くトークンは、また試す
            else:
                self.token, self.failed_until = "", time.time() + 600

    def call(self, url, headers=None, timeout=60):
        request = urllib.request.Request(url, headers={"User-Agent": "comfyui-instance-colab-cli/1", **(headers or {})})
        token = self.access_token()
        # トークンは転送先へ持ち出さない
        request.add_unredirected_header("Authorization", "Bearer " + token)
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except HTTPError as error:
            if error.code == 401:
                self.give_up(token)
            raise

    def lookup(self, path):
        """マウントしたドライブのパスから、ファイルの ID と大きさを調べる。"""
        if not self.mydrive:
            raise Failure(t("drive_not_found"))
        relative = Path(path).relative_to(self.mydrive)
        parent = "root"
        found = None
        for depth, part in enumerate(relative.parts):
            key = (parent, part)
            last = depth == len(relative.parts) - 1
            if key in self.ids and not last:
                parent = self.ids[key]
                continue
            name = part.replace("\\", "\\\\").replace("'", "\\'")
            query = urlencode({"q": f"name = '{name}' and '{parent}' in parents and trashed = false",
                               "fields": "files(id,size,mimeType)", "pageSize": "10"})
            with self.call(self.BASE + "?" + query) as response:
                files = json.loads(response.read()).get("files") or []
            wanted = [f for f in files if (f.get("mimeType") == "application/vnd.google-apps.folder") != last]
            if not wanted:
                raise Failure(t("drive_not_found"))
            found = wanted[0]
            parent = self.ids[key] = found["id"]
        return found["id"], int(found.get("size") or 0)

    def read_range(self, file_id, interrupted):
        url = f"{self.BASE}/{quote(file_id)}?alt=media"

        def read(start, end):
            with self.call(url, {"Range": f"bytes={start}-{end - 1}"}, timeout=120) as response:
                if response.status != 206 and not (start == 0 and response.status == 200):
                    raise Failure(t("range"))
                while True:
                    block = response.read(4 * 1024 * 1024)
                    if not block:
                        return
                    yield block
        return read


class Fetcher:
    """ランタイムに写しが無いモデルを取ってくる。"""

    def __init__(self, runtime_models, drive_models, ledger, mode="drive", tokens_file="", system_python="",
                 state_dir="", notify=None, interrupted=None):
        self.runtime = Path(runtime_models)
        self.drive = Path(drive_models) if drive_models else None
        self.ledger, self.mode, self.tokens_file = ledger, mode, tokens_file
        self.state = Path(state_dir) if state_dir else self.runtime
        self.notify = notify
        self.interrupted = interrupted or (lambda: False)
        mydrive = None
        if self.drive:
            for parent in [self.drive] + list(self.drive.parents):
                if parent.name == "MyDrive":
                    mydrive = parent
                    break
        self.api = DriveApi(system_python, mydrive, self.state / "drive-token.json")

    def drive_ready(self):
        return bool(self.drive and self.drive.parent.is_dir())

    def tokens(self):
        try:
            return json.loads(Path(self.tokens_file).read_text()) if self.tokens_file else {}
        except (OSError, ValueError):
            return {}

    def ensure_local(self, key, drive_path=None):
        """key のモデルをランタイムに用意してそのパスを返す。ドライブから直接読むべきなら None を返す。
        ドライブに無く、どこからも取れなければ Failure を投げる。"""
        target = self.runtime / key
        if target.is_file():
            return target
        entry = self.ledger.entries().get(key) if self.ledger else None
        report = Report(self.notify, key)
        drive_size = drive_path.stat().st_size if drive_path else 0
        size = drive_size or int((entry or {}).get("size") or 0)
        target.parent.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(target.parent).free
        if size and size + SPARE > free:
            reason = t("no_space", need=size_text(size), free=size_text(free))
            return self._give_up(report, key, reason, drive_path)

        lock_name = "fetch-" + hashlib.sha1(key.encode()).hexdigest()[:16] + ".lock"
        with locked(self.state / lock_name):
            if target.is_file():  # 同じモデルを待っていた間に、ほかの呼び出しが取ってきた
                return target
            # 台帳の URL は、ドライブにも同じ名前のモデルがあるときは、同じ大きさのときだけ使う
            url_ok = bool(entry and entry.get("url")) and (not drive_path or int(entry.get("size") or 0) == drive_size)
            drive_sources = ["drive_api", "drive_copy"] if drive_path else []
            url_sources = ["url"] if url_ok else []
            sources = url_sources + drive_sources if self.mode == "url" else drive_sources + url_sources
            reason = t("no_source")
            for source in sources:
                partial = target.with_name(".comfy-" + secrets.token_hex(8) + ".part")
                try:
                    getattr(self, "_from_" + source)(key, entry, drive_path, partial, report)
                    os.replace(partial, target)
                    report.send("done", "", force=True)
                    return target
                except Stopped:
                    report.send("cancelled", "", force=True)
                    raise
                except (Failure, OSError, HTTPError, URLError, ValueError) as error:
                    reason = str(error) or type(error).__name__
                    print(f"[comfyui-colab-cli] {key}: {source}: {reason}", flush=True)
                finally:
                    partial.unlink(missing_ok=True)
            return self._give_up(report, key, reason, drive_path)

    def _give_up(self, report, key, reason, drive_path):
        if drive_path:
            report.send("fallback", t("direct", reason=reason), force=True)
            return None
        message = t("all_failed", name=key, reason=reason)
        report.send("failed", message, force=True)
        raise Failure(message)

    def _from_url(self, key, entry, drive_path, partial, report):
        import model_download  # 同じフォルダーにある。転送のたどり方と、内部のアドレスを断る仕組みを使う
        url = entry["url"]
        host = (urlsplit(url).hostname or "").removeprefix("www.")
        token_name = {"huggingface.co": "HF_TOKEN", "civitai.com": "CIVITAI_TOKEN"}.get(host, "")
        response = model_download.open_download(url, self.tokens().get(token_name, ""))
        expected = int(entry.get("size") or 0)
        digest = hashlib.sha256()
        with response, partial.open("wb") as file:
            length = response.headers.get("Content-Length", "")
            report.start("url", int(length) if length.isdigit() else expected)
            for block in iter(lambda: response.read(4 * 1024 * 1024), b""):
                if self.interrupted():
                    raise Stopped()
                file.write(block)
                digest.update(block)
                report.advance(len(block))
        if expected and partial.stat().st_size != expected:
            raise Failure(t("size_mismatch"))
        if entry.get("sha256") and digest.hexdigest() != entry["sha256"]:
            raise Failure(t("hash_mismatch"))

    def _from_drive_api(self, key, entry, drive_path, partial, report):
        file_id, size = self.api.lookup(drive_path)
        if size != drive_path.stat().st_size:
            raise Failure(t("size_mismatch"))
        report.start("drive_api", size)
        parallel_fill(partial, size, self.api.read_range(file_id, self.interrupted), report, self.interrupted, WORKERS)

    def _from_drive_copy(self, key, entry, drive_path, partial, report):
        size = drive_path.stat().st_size
        report.start("drive_copy", size)
        source = os.open(drive_path, os.O_RDONLY)
        try:
            def read(start, end):
                position = start
                while position < end:
                    block = os.pread(source, min(8 * 1024 * 1024, end - position), position)
                    if not block:
                        return
                    position += len(block)
                    yield block
            parallel_fill(partial, size, read, report, self.interrupted, 4)
        finally:
            os.close(source)


# ---- ドライブへの写し ----

def backup_candidates(runtime, drive, known, since):
    """ランタイムにだけある、ComfyUI の起動後に増えたモデル。台帳にあるものと書きかけは除く。"""
    found = []
    for parent, dirs, files in os.walk(runtime):
        dirs[:] = [d for d in dirs if not d.startswith(".") and not (Path(parent) / d).is_symlink()]
        relative_parent = Path(parent).relative_to(runtime)
        if relative_parent.parts[:1] == ("configs",):
            continue
        for name in files:
            path = Path(parent) / name
            if name.startswith((".", "put_")) or name.endswith((".part", ".tmp")) or path.is_symlink():
                continue
            key = (relative_parent / name).as_posix()
            if not valid_key(key) or key in known:
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            if st.st_mtime < since or (drive / key).exists():
                continue
            found.append((key, path, st.st_size, st.st_mtime))
    return found


def copy_to_drive(path, target, report=None):
    """ランタイムのモデルをドライブに写す。report があれば、進み具合を右下の通知に送る。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(".comfy-" + secrets.token_hex(8) + ".part")
    try:
        if report:
            report.start("to_drive", path.stat().st_size)
        with path.open("rb") as source, temporary.open("wb") as sink:
            for chunk in iter(lambda: source.read(16 * 1024 * 1024), b""):
                sink.write(chunk)
                if report:
                    report.advance(len(chunk))
        if target.exists():
            return False
        os.replace(temporary, target)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["merge"])
    parser.add_argument("--into", required=True)
    parser.add_argument("--from", dest="source", required=True)
    parser.add_argument("--lock", default="")
    args = parser.parse_args()
    if not Path(args.source).is_file():
        return 0
    other = read_ledger(args.source)
    with locked(args.lock):
        merged = merge_models(read_ledger(args.into), other)
        if merged != read_ledger(args.into) or not Path(args.into).exists():
            write_ledger(args.into, merged)
    return 0


if __name__ == "__main__":
    sys.exit(main())
