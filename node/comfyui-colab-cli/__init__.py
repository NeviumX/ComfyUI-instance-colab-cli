"""comfyui-instance-colab-cli の停止ボタン。

ComfyUI の画面に「停止」ボタンを足し、押されたことを手元の start.bat に伝える。
ランタイムの削除は手元の colab CLI にしかできないので、このノードは要求を記録するだけで、
start.bat のウィンドウがトンネル越しに GET /colab_cli/stop を数秒ごとに確かめて実行する。
ボタンのダイアログからは、ComfyUI の出力をまとめて ZIP でダウンロードすることもできる。
「モデル」ボタンからは、Hugging Face と Civitai のモデルをランタイムか Google ドライブにダウンロードできる。
ランタイムに写しが無いモデルは、ノードで使われたときにランタイムへ取ってくる。取り方は model_sources.py に書いてある。
"""

import asyncio
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import zipfile

from aiohttp import web
from server import PromptServer

WEB_DIRECTORY = "./web"
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

STOP_PATH = "/colab_cli/stop"
# 手元の lib/common.sh の button_request は、この並びとこの区切りの JSON を前提にしている
STATE = {"state": "idle", "delete_runtime": False}


def _answer(data, status=200):
    return web.json_response(data, status=status, headers={"Cache-Control": "no-store"},
                             dumps=lambda value: json.dumps(value, separators=(",", ":")))


routes = PromptServer.instance.routes


@routes.get("/colab_cli/config")
async def colab_cli_config(request):
    return _answer({"lang": os.environ.get("COMFY_COLAB_CLI_LANG", ""),
                    "pull_outputs": os.environ.get("COMFY_COLAB_CLI_PULL") == "1",
                    "save_environment": os.environ.get("COMFY_COLAB_CLI_SAVE") == "1"})


@routes.get(STOP_PATH)
async def colab_cli_stop_state(request):
    return _answer(STATE)


@routes.post(STOP_PATH)
async def colab_cli_stop(request):
    # JSON に限ることで、ほかのサイトのフォームからは送れないようにする
    if request.content_type != "application/json":
        return _answer({"error": "expected application/json"}, 415)
    try:
        wanted = await request.json()
    except ValueError:
        return _answer({"error": "invalid json"}, 400)
    if not isinstance(wanted, dict):
        return _answer({"error": "invalid request"}, 400)
    STATE.update(state="pending", delete_runtime=wanted.get("delete_runtime") is True)
    return _answer(STATE)


@routes.post(STOP_PATH + "/ack")
async def colab_cli_stop_ack(request):
    """start.bat のウィンドウが要求を受け取ったときに呼ぶ。"""
    if request.content_type != "application/json":
        return _answer({"error": "expected application/json"}, 415)
    if STATE["state"] == "pending":
        STATE["state"] = "accepted"
    return _answer(STATE)


@routes.post(STOP_PATH + "/fail")
async def colab_cli_stop_fail(request):
    """start.bat のウィンドウが、環境を保存できずにランタイムを削除しなかったときに呼ぶ。"""
    if request.content_type != "application/json":
        return _answer({"error": "expected application/json"}, 415)
    STATE["state"] = "failed"
    return _answer(STATE)


@routes.post(STOP_PATH + "/reset")
async def colab_cli_stop_reset(request):
    """start.bat のウィンドウがトンネルを張ったときに呼ぶ。前の接続で残った要求を消す。"""
    if request.content_type != "application/json":
        return _answer({"error": "expected application/json"}, 415)
    STATE.update(state="idle", delete_runtime=False)
    return _answer(STATE)


# ---- モデルのダウンロード ----
# ダウンロードは、手元から送った model_download.py を別のプロセスで動かす。ComfyUI を止めずに中止できるようにするため

STAGE = Path(os.environ.get("COMFY_COLAB_CLI_STAGE", "/content/.comfy-colab-cli"))
DOWNLOADER = STAGE / "model_download.py"
DOWNLOAD_PROGRESS = STAGE / "state/download-progress.json"
DOWNLOAD_JOB = {"process": None}


def _downloader_args(command):
    args = [sys.executable, str(DOWNLOADER), command, "--models", os.environ.get("COMFY_COLAB_CLI_MODELS", "")]
    drive_models = os.environ.get("COMFY_COLAB_CLI_DRIVE_MODELS", "")
    if drive_models:
        args += ["--drive-models", drive_models]
    if command == "download" and os.environ.get("COMFY_COLAB_CLI_LEDGER"):
        args += ["--ledger", os.environ["COMFY_COLAB_CLI_LEDGER"]]
    return args


async def _json_body(request):
    if request.content_type != "application/json":
        return None, _answer({"error": "expected application/json"}, 415)
    try:
        body = await request.json()
    except ValueError:
        return None, _answer({"error": "invalid json"}, 400)
    if not isinstance(body, dict):
        return None, _answer({"error": "invalid request"}, 400)
    return body, None


@routes.get("/colab_cli/models/folders")
async def colab_cli_model_folders(request):
    if not DOWNLOADER.exists():
        return _answer({"error": "downloader missing"}, 503)
    process = await asyncio.create_subprocess_exec(*_downloader_args("folders"), stdout=asyncio.subprocess.PIPE)
    out, _ = await process.communicate()
    try:
        return _answer(json.loads(out))
    except ValueError:
        return _answer({"error": "could not list folders"}, 500)


@routes.post("/colab_cli/models/download")
async def colab_cli_model_download(request):
    body, refused = await _json_body(request)
    if refused:
        return refused
    process = DOWNLOAD_JOB["process"]
    if process is not None and process.returncode is None:
        return _answer({"error": "busy"}, 409)
    DOWNLOAD_PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_PROGRESS.unlink(missing_ok=True)
    args = _downloader_args("download") + [
        "--state", str(STAGE / "state"), "--tokens-file", str(STAGE / "state/tokens.json"),
        "--progress-file", str(DOWNLOAD_PROGRESS),
        "--url", str(body.get("url", "")), "--folder", str(body.get("folder", "")),
        "--filename", str(body.get("filename", ""))]
    if body.get("to_drive") is True:
        args.append("--to-drive")
    DOWNLOAD_JOB["process"] = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    return _answer({"state": "running"})


@routes.get("/colab_cli/models/download")
async def colab_cli_model_download_state(request):
    process = DOWNLOAD_JOB["process"]
    try:
        progress = json.loads(DOWNLOAD_PROGRESS.read_text())
    except (OSError, ValueError):
        progress = {"state": "running" if process is not None and process.returncode is None else "idle"}
    # 進み具合を書けずに終わったときも、画面が待ち続けないようにする
    if progress.get("state") == "running" and process is not None and process.returncode is not None:
        progress["state"] = "failed"
    return _answer(progress)


@routes.post("/colab_cli/models/dismiss")
async def colab_cli_model_dismiss(request):
    """ダイアログを閉じたときに呼ぶ。終わったダウンロードの結果を消し、次に開いたときに出さない。"""
    _, refused = await _json_body(request)
    if refused:
        return refused
    process = DOWNLOAD_JOB["process"]
    if process is None or process.returncode is not None:
        DOWNLOAD_PROGRESS.unlink(missing_ok=True)
    return _answer({"state": "idle"})


@routes.post("/colab_cli/models/cancel")
async def colab_cli_model_cancel(request):
    _, refused = await _json_body(request)
    if refused:
        return refused
    process = DOWNLOAD_JOB["process"]
    if process is not None and process.returncode is None:
        process.terminate()
    return _answer({"state": "cancelling"})


# ---- 台帳とモデルの取得 ----
# 台帳にしかないモデルも一覧に出し、ノードで使われたときにランタイムへ取ってくる。
# 一覧と場所を返す ComfyUI の関数を包む。包めなかったときは、今までどおりドライブから直接読む。

RUNTIME_MODELS = os.environ.get("COMFY_COLAB_CLI_MODELS", "")
DRIVE_MODELS = os.environ.get("COMFY_COLAB_CLI_DRIVE_MODELS", "")
LEDGER = None
FETCHER = None


def _notify(data):
    try:
        PromptServer.instance.send_sync("colab_cli.model_fetch", data)
    except Exception:
        pass


def _interrupted():
    try:
        import comfy.model_management
        return comfy.model_management.processing_interrupted()
    except Exception:
        return False


def _executing():
    """ノードの実行中か。一覧の表示やメタデータの問い合わせでは、大きなダウンロードを始めないため。"""
    try:
        from comfy_execution.utils import get_executing_context
        if get_executing_context() is not None:
            return True
    except Exception:
        pass
    return "prompt_worker" in threading.current_thread().name


def _inside(path, root):
    try:
        return Path(os.path.realpath(path)).relative_to(os.path.realpath(root)).as_posix()
    except ValueError:
        return None


def _model_tops(folder_paths, folder_name):
    """ComfyUI の種類が読むフォルダーのうち、ランタイムとドライブの models の下にあるものの相対パス。"""
    tops = set()
    for path in folder_paths.folder_names_and_paths.get(folder_paths.map_legacy(folder_name), ([], set()))[0]:
        for root in (RUNTIME_MODELS, DRIVE_MODELS):
            relative = _inside(path, root) if root else None
            if relative and relative != ".":
                tops.add(relative)
    return tops


def _install_model_hooks():
    global LEDGER, FETCHER
    if not RUNTIME_MODELS:
        return
    import folder_paths
    sys.path.insert(0, str(STAGE))
    import model_sources
    state = STAGE / "state"
    LEDGER = model_sources.Ledger(os.environ.get("COMFY_COLAB_CLI_LEDGER", ""), state / "ledger.lock")
    FETCHER = model_sources.Fetcher(
        RUNTIME_MODELS, DRIVE_MODELS, LEDGER, os.environ.get("COMFY_COLAB_CLI_SOURCE", "drive"),
        tokens_file=str(state / "tokens.json"), system_python=os.environ.get("COMFY_COLAB_CLI_SYSPY", ""),
        state_dir=str(state), notify=_notify, interrupted=_interrupted)
    original_full_path = folder_paths.get_full_path
    original_list = folder_paths.get_filename_list

    def ledger_key(folder_name, filename):
        entries = LEDGER.entries()
        for top in _model_tops(folder_paths, folder_name):
            key = f"{top}/{filename}"
            if key in entries:
                return key
        return None

    def get_full_path(folder_name, filename):
        path = original_full_path(folder_name, filename)
        if not _executing():
            return path
        try:
            if path is not None:
                key = _inside(path, DRIVE_MODELS) if DRIVE_MODELS else None
                if not key or not FETCHER.drive_ready():
                    return path
                local = FETCHER.ensure_local(key, Path(path))
                return str(local) if local else path
            key = ledger_key(folder_name, filename)
            if key:
                return str(FETCHER.ensure_local(key))
        except model_sources.Stopped:
            import comfy.model_management
            raise comfy.model_management.InterruptProcessingException()
        except model_sources.Failure as failure:
            if path is None:
                raise FileNotFoundError(str(failure)) from None
        except Exception as error:  # 取れなくても、今までどおりの場所を返す
            print(f"[comfyui-colab-cli] model fetch: {type(error).__name__}: {error}", flush=True)
        return path

    def get_filename_list(folder_name):
        names = original_list(folder_name)
        try:
            tops = _model_tops(folder_paths, folder_name)
            extensions = folder_paths.folder_names_and_paths[folder_paths.map_legacy(folder_name)][1]
            extra = set()
            for key in LEDGER.entries():
                for top in tops:
                    if key.startswith(top + "/"):
                        extra.add(key[len(top) + 1:])
            extra = set(folder_paths.filter_files_extensions(extra, extensions)) - set(names)
            if extra:
                return sorted(set(names) | extra)
        except Exception as error:
            print(f"[comfyui-colab-cli] model list: {type(error).__name__}: {error}", flush=True)
        return names

    folder_paths.get_full_path = get_full_path
    folder_paths.get_filename_list = get_filename_list


def _backup_loop():
    """ComfyUI-Manager などがランタイムに入れたモデルを、あとからドライブにも写す。
    台帳にあるものは、ランタイムだけと選ばれたものか、元の URL から取ってきた写しなので写さない。"""
    import model_sources
    started = time.time()
    seen = {}
    runtime, drive = Path(RUNTIME_MODELS), Path(DRIVE_MODELS)
    while True:
        time.sleep(30)
        try:
            if not (drive.parent.is_dir() and runtime.is_dir()):
                continue
            known = set(LEDGER.all()) if LEDGER else set()
            for key, path, size, mtime in model_sources.backup_candidates(runtime, drive, known, started):
                # 書き終わったものだけを写す。続けて 2 回、同じ大きさと時刻だったら書き終わったとみなす
                if seen.get(key) != (size, mtime) or time.time() - mtime < 60:
                    seen[key] = (size, mtime)
                    continue
                if model_sources.copy_to_drive(path, drive / key):
                    print(f"[comfyui-colab-cli] copied to Google Drive: {key}", flush=True)
                    _notify({"state": "backed_up", "name": key, "message": model_sources.t("backed_up", name=key)})
                seen.pop(key, None)
        except Exception as error:
            print(f"[comfyui-colab-cli] Drive copy: {type(error).__name__}: {error}", flush=True)


try:
    _install_model_hooks()
    if FETCHER and DRIVE_MODELS:
        threading.Thread(target=_backup_loop, name="colab-cli-drive-copy", daemon=True).start()
except Exception as error:
    print(f"[comfyui-colab-cli] model hooks are off: {type(error).__name__}: {error}", flush=True)


@routes.get("/colab_cli/models/ledger")
async def colab_cli_ledger(request):
    """台帳の中身。raw=1 なら外した記録も含めてそのまま返す。手元の WSL へ写すときに使う。"""
    if LEDGER is None:
        return _answer({"models": [], "available": False})
    models = await asyncio.get_running_loop().run_in_executor(None, LEDGER.all)
    if request.query.get("raw") == "1":
        return _answer({"version": 1, "models": models})
    listed = [{"key": key, "page": entry.get("page", ""), "size": entry.get("size", 0)}
              for key, entry in sorted(models.items()) if not entry.get("removed")]
    return _answer({"models": listed, "available": True})


@routes.post("/colab_cli/models/ledger/remove")
async def colab_cli_ledger_remove(request):
    """台帳から外す。ファイルは消さない。"""
    body, refused = await _json_body(request)
    if refused:
        return refused
    key = body.get("key")
    if LEDGER is None or not isinstance(key, str) or key not in LEDGER.entries():
        return _answer({"error": "not in the ledger"}, 404)
    await asyncio.get_running_loop().run_in_executor(None, LEDGER.remove, key)
    return _answer({"removed": key})


# ---- 出力の ZIP ----

def _output_files():
    """ComfyUI の output フォルダーの通常のファイル。リンクはたどらない。[(相対パス, 絶対パス, サイズ)]"""
    import folder_paths
    root = Path(folder_paths.get_output_directory())
    found = []
    for parent, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not (Path(parent) / d).is_symlink())
        for name in sorted(files):
            path = Path(parent) / name
            if path.is_symlink() or not path.is_file() or name == "_output_images_will_be_put_here":
                continue
            found.append((path.relative_to(root).as_posix(), path, path.stat().st_size))
    return found


@routes.get("/colab_cli/outputs")
async def colab_cli_outputs(request):
    """ZIP にする前に、件数と合計の大きさを知らせる。"""
    files = await asyncio.get_running_loop().run_in_executor(None, _output_files)
    return _answer({"files": len(files), "bytes": sum(size for _, _, size in files)})


class _QueueWriter:
    """zipfile が書いたバイト列を、1 MiB ずつまとめて送信する側へ渡す。
    seek を持たないので、zipfile は巻き戻さずに書く形式で ZIP を作る。"""

    CHUNK = 1024 * 1024

    def __init__(self, chunks):
        self.chunks = chunks
        self.buffer = bytearray()
        self.written = 0

    def write(self, data):
        self.buffer += data
        self.written += len(data)
        if len(self.buffer) >= self.CHUNK:
            self.flush()
        return len(data)

    def tell(self):
        return self.written

    def flush(self):
        if self.buffer:
            self.chunks.put(bytes(self.buffer))
            self.buffer = bytearray()


@routes.get("/colab_cli/outputs.zip")
async def colab_cli_outputs_zip(request):
    """output フォルダーを ZIP にして送る。画像や動画は圧縮済みなので、ZIP では圧縮し直さない。"""
    # ほかのサイトのリンクからは、ダウンロードを始めさせない
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        return _answer({"error": "cross-site request"}, 403)
    loop = asyncio.get_running_loop()
    files = await loop.run_in_executor(None, _output_files)
    if not files:
        return _answer({"error": "no outputs"}, 404)
    name = time.strftime("comfyui-outputs-%Y%m%d-%H%M%S.zip")
    response = web.StreamResponse(headers={
        "Content-Type": "application/zip",
        "Content-Disposition": f'attachment; filename="{name}"',
        "Cache-Control": "no-store",
    })
    await response.prepare(request)
    chunks = queue.Queue(maxsize=16)
    done = object()

    def pack():
        writer = _QueueWriter(chunks)
        try:
            with zipfile.ZipFile(writer, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                for relative, path, _ in files:
                    try:
                        archive.write(path, relative)
                    except FileNotFoundError:
                        continue  # 一覧を作った後に消されたもの
            writer.flush()
        finally:
            chunks.put(done)

    worker = loop.run_in_executor(None, pack)
    try:
        while True:
            chunk = await loop.run_in_executor(None, chunks.get)
            if chunk is done:
                break
            await response.write(chunk)
    except (ConnectionResetError, asyncio.CancelledError):
        # 受け取る側が途中でやめた: 詰める側が止まらないよう、残りを読み捨てる
        def drain():
            while chunks.get() is not done:
                pass
        loop.run_in_executor(None, drain)
        raise
    await worker
    await response.write_eof()
    return response
