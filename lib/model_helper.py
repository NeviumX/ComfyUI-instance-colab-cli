"""Colab VM の root で動き、ComfyUI のノードから頼まれたモデルの仕事をする。

ComfyUI を root ではない利用者で動かすと、ComfyUI からはドライブに書けず、トークンも読めない。
ドライブとトークンに触る仕事はここで行い、ノードには結果と進み具合だけを返す。
    download        画面の「モデル」ボタンのダウンロード。HF_TOKEN と CIVITAI_TOKEN を使う
    fetch           ランタイムに写しが無いモデルの取得。Drive API、ドライブからのコピー、台帳の元 URL
    ledger-remove   台帳から外す
    subscribe       バックグラウンドの仕事の知らせを受け取る。ドライブへのコピーの進み具合と終わり
ほかに、Manager などがランタイムに入れたモデルをドライブへ写し、台帳の写しを ComfyUI が読める場所に置く。

頼みは Unix ソケットに 1 行の JSON で受け取り、知らせを 1 行ずつの JSON で返す。最後の行は "end" を持つ。
相手の uid は SO_PEERCRED で確かめ、ComfyUI の利用者と root からだけ受け付ける。頼みを送った側が接続を閉じると中止する。

    python3 model_helper.py   設定は環境変数で受け取る。remote-setup.sh が tmux の中で起動する
"""

import argparse
import fcntl
import json
import os
from pathlib import Path
import socket
import socketserver
import struct
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app_user  # noqa: E402
import model_download  # noqa: E402
import model_sources  # noqa: E402

STATE = Path(os.environ.get("COMFY_STATE", "/content/.comfy-colab-cli/state"))
SOCKET = Path(os.environ.get("COMFY_HELPER_SOCKET", "/content/.comfy-colab-cli/run/helper.sock"))
RUNTIME_MODELS = Path(os.environ.get("COMFY_BASE", "/content/comfy-session")) / "models"
DRIVE_MODELS = os.environ.get("COMFY_DRIVE_MODELS", "")
TOKENS = STATE / "tokens.json"
RUNTIME_ONLY = STATE / "runtime-only.json"
REQUEST_LIMIT = 64 * 1024


class Gone(Exception):
    """頼みを送った側が接続を閉じた。"""


class Hub:
    """subscribe した接続へ、バックグラウンドの仕事の知らせを配る。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.listeners = set()

    def add(self, send):
        with self.lock:
            self.listeners.add(send)

    def remove(self, send):
        with self.lock:
            self.listeners.discard(send)

    def __call__(self, data):
        with self.lock:
            listeners = list(self.listeners)
        for send in listeners:
            try:
                send({"event": "notify", "data": data})
            except Gone:
                self.remove(send)


HUB = Hub()
OWNER = app_user.app_ids()
TREE = model_sources.AppTree(RUNTIME_MODELS, OWNER)
LEDGER = model_sources.Ledger(os.environ.get("COMFY_LEDGER", ""), STATE / "ledger.lock",
                              os.environ.get("COMFY_LEDGER_MIRROR") or None)
FETCHER = model_sources.Fetcher(
    RUNTIME_MODELS, DRIVE_MODELS, LEDGER, os.environ.get("COMFY_MODEL_SOURCE", "drive"),
    tokens_file=str(TOKENS), system_python=os.environ.get("COMFY_PYTHON", sys.executable),
    state_dir=str(STATE), owner=OWNER)
DOWNLOAD_LOCK = threading.Lock()


def allowed_uids():
    uids = {os.geteuid()}
    if OWNER:
        uids.add(OWNER[0])
    return uids


def tokens():
    try:
        data = json.loads(TOKENS.read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if k in {"HF_TOKEN", "CIVITAI_TOKEN"} and isinstance(v, str)} \
        if isinstance(data, dict) else {}


def drive_copy(key, size=None):
    """ランタイムのモデルをドライブへ写し、進み具合と結果を subscribe した接続へ配る。"""
    report = model_sources.Report(HUB, key)
    try:
        if model_sources.copy_to_drive(TREE, key, Path(DRIVE_MODELS) / key, report, size):
            print(f"[model-helper] copied to Google Drive: {key}", flush=True)
            HUB({"state": "backed_up", "name": key, "message": model_sources.t("backed_up", name=key)})
            return True
        HUB({"state": "backup_failed", "name": key,
             "message": model_sources.t("backup_failed", name=key, reason=model_sources.t("drive_exists"))})
    except Exception as error:
        print(f"[model-helper] Drive copy: {key}: {type(error).__name__}: {error}", flush=True)
        HUB({"state": "backup_failed", "name": key,
             "message": model_sources.t("backup_failed", name=key, reason=f"{type(error).__name__}: {error}")})
    return False


# ---- 頼みごと ----

def do_download(request, send, closed):
    where = request.get("where")
    if where not in {"runtime", "ledger", "drive"}:
        raise ValueError("where")
    values = {name: request.get(name, "") for name in ("url", "folder", "filename")}
    if not all(isinstance(v, str) and len(v) < 4096 for v in values.values()):
        raise ValueError("fields")
    argv = ["download", "--models", str(RUNTIME_MODELS), "--state", str(STATE),
            "--url", values["url"], "--folder", values["folder"], "--filename", values["filename"]]
    if DRIVE_MODELS:
        argv += ["--drive-models", DRIVE_MODELS]
    if where == "runtime":
        argv += ["--runtime-only-file", str(RUNTIME_ONLY)]
    elif LEDGER.path:
        argv += ["--ledger", str(LEDGER.path)]
        if LEDGER.mirror:
            argv += ["--ledger-mirror", str(LEDGER.mirror)]
    if where == "drive":
        argv.append("--drive-later")
    args = model_download.parse_args(argv)

    def sink(data):
        if closed.is_set():
            raise Gone()
        send({"event": "progress", "data": data})

    progress = model_download.Progress(None, False, "", sink=sink)
    if not DOWNLOAD_LOCK.acquire(blocking=False):
        send({"event": "end", "state": "failed", "message": model_download.t("busy"), "busy": True})
        return
    try:
        # ターミナルの model-download.sh と同じロック。同時に 2 つは進めない
        STATE.mkdir(parents=True, exist_ok=True)
        with (STATE / "download.lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                send({"event": "end", "state": "failed", "message": model_download.t("busy"), "busy": True})
                return
            try:
                message = model_download.download(args, tokens(), progress)
            except Gone:
                print("[model-helper] download cancelled", flush=True)
                return
            except model_download.Failure as failure:
                finish(progress, send, "failed", str(failure))
                return
            except Exception as failure:
                finish(progress, send, "failed", f"{type(failure).__name__}: {failure}")
                return
            finish(progress, send, "done", message + " " + model_download.t("refresh"))
    finally:
        DOWNLOAD_LOCK.release()
    key = progress.saved
    if where == "drive" and key and DRIVE_MODELS:
        threading.Thread(target=drive_copy, args=(key,), name="drive-copy-now", daemon=True).start()


def finish(progress, send, state, message):
    try:
        progress.finish(state, message)
    except Gone:
        return
    send({"event": "end", "state": state, "message": message, "path": progress.saved})


def do_fetch(request, send, closed):
    key = request.get("key")
    if not model_sources.valid_key(key):
        raise ValueError("key")

    def notify(data):
        try:
            send({"event": "notify", "data": data})
        except Gone:
            closed.set()

    try:
        found = FETCHER.ensure_local(key, FETCHER.drive_file(key), notify=notify, interrupted=closed.is_set)
    except model_sources.Stopped:
        send({"event": "end", "result": "cancelled"})
        return
    except model_sources.Failure as failure:
        send({"event": "end", "result": "failed", "message": str(failure)})
        return
    send({"event": "end", "result": "ok" if found else "fallback"})


def do_ledger_remove(request, send, closed):
    key = request.get("key")
    if not isinstance(key, str) or key not in LEDGER.entries():
        send({"event": "end", "result": "missing"})
        return
    LEDGER.remove(key)
    send({"event": "end", "result": "ok"})


def do_subscribe(request, send, closed):
    HUB.add(send)
    try:
        closed.wait()
    finally:
        HUB.remove(send)


OPERATIONS = {"download": do_download, "fetch": do_fetch, "ledger-remove": do_ledger_remove, "subscribe": do_subscribe}


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        pid, uid, gid = struct.unpack("3i", self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                                                    struct.calcsize("3i")))
        if uid not in allowed_uids():
            return
        line = self.rfile.readline(REQUEST_LIMIT)
        try:
            request = json.loads(line)
            operation = OPERATIONS[request["op"]]
        except (ValueError, KeyError, TypeError):
            return
        lock = threading.Lock()
        closed = threading.Event()

        def send(data):
            if closed.is_set():
                raise Gone()
            try:
                with lock:
                    self.wfile.write((json.dumps(data, ensure_ascii=False) + "\n").encode())
                    self.wfile.flush()
            except OSError:
                closed.set()
                raise Gone() from None

        def watch():
            # 頼みの後に届くものは無い。読めたら、相手が閉じたということ
            try:
                self.request.recv(1)
            except OSError:
                pass
            closed.set()

        threading.Thread(target=watch, daemon=True).start()
        try:
            operation(request, send, closed)
        except Gone:
            pass
        except Exception as error:
            print(f"[model-helper] {request.get('op')}: {type(error).__name__}: {error}", flush=True)
            try:
                send({"event": "end", "result": "error", "state": "failed", "message": f"{type(error).__name__}: {error}"})
            except Gone:
                pass


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


# ---- バックグラウンド ----

def background():
    """台帳の写しを新しく保ち、Manager などがランタイムに入れたモデルをドライブにも写す。
    台帳にあるものと、「ランタイムのみ」と選んだものは写さない。"""
    started = time.time()
    seen = {}
    published = None
    while True:
        try:
            current = LEDGER.path.stat().st_mtime if LEDGER.path and LEDGER.path.exists() else None
            if current != published:
                LEDGER.publish()
                published = current
        except Exception as error:
            print(f"[model-helper] ledger: {type(error).__name__}: {error}", flush=True)
        try:
            drive = Path(DRIVE_MODELS) if DRIVE_MODELS else None
            if drive and drive.parent.is_dir() and RUNTIME_MODELS.is_dir():
                known = set(LEDGER.all()) | model_sources.read_runtime_only(RUNTIME_ONLY)
                for key, size, mtime in model_sources.backup_candidates(TREE, drive, known, started):
                    # 書き終わったものだけを写す。続けて 2 回、同じ大きさと時刻だったら書き終わったとみなす
                    if seen.get(key) != (size, mtime) or time.time() - mtime < 60:
                        seen[key] = (size, mtime)
                        continue
                    drive_copy(key, size)
                    seen.pop(key, None)
        except Exception as error:
            print(f"[model-helper] Drive copy: {type(error).__name__}: {error}", flush=True)
        time.sleep(30)


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    SOCKET.parent.mkdir(parents=True, exist_ok=True)
    SOCKET.unlink(missing_ok=True)
    server = Server(str(SOCKET), Handler)
    # 誰でもつなげるが、相手の uid を確かめてから受け付ける
    os.chmod(SOCKET, 0o666)
    threading.Thread(target=background, name="background", daemon=True).start()
    print(f"[model-helper] listening on {SOCKET}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
