"""Colab VM 上で、ComfyUI の環境を Google ドライブへ保存し、復元し、ComfyUI を更新する。

環境は部品ごとの書庫に分けて保存する。部品は ComfyUI 本体、カスタムノードごと、ユーザーデータ、
パッケージの記録の 4 種類で、変わった部品だけを送る。

    python3 comfy_env.py restore      Drive の latest.json が指す環境を BASE/environment に展開する
    python3 comfy_env.py save [force] 変わった部品だけを Drive へ保存する
    python3 comfy_env.py loop 秒数     一定の間隔で save を続ける
    python3 comfy_env.py prepare      モデルなどのフォルダーを用意し、保存したデータベースを戻す
    python3 comfy_env.py lock         インストール済みのパッケージを requirements.lock.txt に書く
    python3 comfy_env.py update 版     ComfyUI を指定の版に更新する。latest なら最新のリリースタグ

場所は環境変数で受け取る: COMFY_BASE, COMFY_STATE, COMFY_PYTHON, MSG_LANG と、
Drive の保存先の COMFY_STORAGE。COMFY_STORAGE が空なら保存も復元もしない。
"""

import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time

BASE = Path(os.environ.get("COMFY_BASE", "/content/comfy-session"))
ENVDIR = BASE / "environment"
REPO = ENVDIR / "ComfyUI"
VENV_PY = BASE / ".venv/bin/python"
DATABASE = BASE / "database/comfyui.db"
STATE = Path(os.environ.get("COMFY_STATE", "/content/.comfy-colab-cli/state"))
STORAGE = Path(os.environ["COMFY_STORAGE"]) if os.environ.get("COMFY_STORAGE") else None

# ===== 保存の形式を決める値。変えると、これまでの保存を読めなくなる =====
EXCLUDED = {"models", "output", "temp", "input"}
GENERATIONS = 3
PART_NAME = re.compile(r"[a-f0-9]{64}\.tar\.gz")
GENERATION_NAME = re.compile(r"\d{8}T\d{6}Z-[a-f0-9]{12}\.json")
WHOLE_ARCHIVE_NAME = re.compile(r"\d{8}T\d{6}Z-[a-f0-9]{12}\.tar\.gz")
LOCK_LINE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(==[A-Za-z0-9._+!-]+| @ (git\+)?https://\S+)")
TORCH_INDEX = re.compile(r"https://download\.pytorch\.org/whl/cu\d+")
MODEL_FOLDERS = ("checkpoints", "diffusion_models", "unet", "text_encoders", "clip", "clip_vision", "vae",
                 "loras", "controlnet", "upscale_models", "embeddings", "audio_encoders", "model_patches")

TEXTS = {
    "ja": {
        "restored": "Google ドライブに保存した環境を復元しました。保存日時: {saved_at}",
        "restored_nodes": "復元したカスタムノード: {nodes}",
        "no_save": "Google ドライブに保存した環境はありません。新しく作ります。",
        "python_changed": "保存した環境は Python {saved} 用ですが、この VM は Python {current} です。"
                          "config.env の RESTORE_ENVIRONMENT=0 で新しい環境を作るか、同じ Python の VM を使ってください。",
        "bad_name": "保存した環境の一覧に、想定外のファイル名があります。復元を中止します。",
        "sha_mismatch": "保存した環境の部品 {name} の SHA256 が一覧と一致しません。復元を中止します。",
        "special_files": "保存した環境にリンクや特殊なファイルが含まれています。復元を中止します。",
        "unsafe_path": "保存した環境に、フォルダーの外を指すパスがあります: {path}",
        "unpinned_lock": "requirements.lock.txt に、版を固定していない行や、ローカルのパスを使う行があります。"
                         "次の VM で同じものを入れられないので保存しません: {line}",
        "busy": "保存している間に環境が変わりました。次の保存でやり直します。",
        "saved": "環境を Google ドライブに保存しました。送った部品: {sent}",
        "unchanged": "環境に変更はありません。",
        "save_failed": "環境の保存に失敗しました: {error}",
        "apt_hint": "保存した環境では次の apt パッケージも使われていました。必要なら config.env の APT_PACKAGES に足してください: {names}",
        "update_incomplete": "ComfyUI の更新が途中で止まっています。update.bat か ./update.sh をもう一度実行すると、元に戻してから更新します。",
        "no_release": "ComfyUI のリリースタグが見つかりません。",
        "unknown_ref": "ComfyUI の版 {ref} が見つかりません。v0.3.60 のようなタグか、ブランチ名を指定してください。",
        "update_resuming": "前回の更新が途中で止まっています。先に元の版に戻します。",
        "update_already": "ComfyUI は既に {version} です。",
        "update_starting": "ComfyUI を {previous} から {ref} に更新します。",
        "update_done": "ComfyUI を {previous} から {version} に更新しました。",
        "update_undone": "更新できなかったので、{version} に戻しました。Google ドライブには保存していません。",
        "update_undo_failed": "更新できず、元の版にも戻せませんでした。update.bat か ./update.sh をもう一度実行してください。それまで環境は保存しません。",
        "none": "なし",
    },
    "en": {
        "restored": "Restored the environment saved on Google Drive. Saved at: {saved_at}",
        "restored_nodes": "Restored custom nodes: {nodes}",
        "no_save": "No environment is saved on Google Drive. Creating a new one.",
        "python_changed": "The saved environment is for Python {saved}, but this VM has Python {current}. "
                          "Set RESTORE_ENVIRONMENT=0 in config.env to create a new one, or use a VM with the same Python.",
        "bad_name": "The list of the saved environment has an unexpected file name. Restoring is aborted.",
        "sha_mismatch": "The SHA256 of part {name} of the saved environment does not match its list. Restoring is aborted.",
        "special_files": "The saved environment contains links or special files. Restoring is aborted.",
        "unsafe_path": "The saved environment has a path that leads outside its folder: {path}",
        "unpinned_lock": "requirements.lock.txt has a line without a pinned version or with a local path. "
                         "The next VM could not install the same, so nothing is saved: {line}",
        "busy": "The environment changed while it was being saved. The next save tries again.",
        "saved": "Saved the environment to Google Drive. Parts sent: {sent}",
        "unchanged": "The environment has not changed.",
        "save_failed": "Saving the environment failed: {error}",
        "apt_hint": "The saved environment also used these apt packages. Add them to APT_PACKAGES in config.env if needed: {names}",
        "update_incomplete": "An update of ComfyUI stopped halfway. Run update.bat or ./update.sh again: it goes back first, then updates.",
        "no_release": "No release tag of ComfyUI was found.",
        "unknown_ref": "ComfyUI version {ref} was not found. Give a tag such as v0.3.60 or a branch name.",
        "update_resuming": "The previous update stopped halfway. Going back to the earlier version first.",
        "update_already": "ComfyUI is already at {version}.",
        "update_starting": "Updating ComfyUI from {previous} to {ref}.",
        "update_done": "Updated ComfyUI from {previous} to {version}.",
        "update_undone": "The update failed, so ComfyUI is back at {version}. Nothing was saved to Google Drive.",
        "update_undo_failed": "The update failed and the earlier version could not be restored either. Run update.bat or ./update.sh again. "
                              "The environment is not saved until then.",
        "none": "none",
    },
}


def t(key, **values):
    lang = "ja" if os.environ.get("MSG_LANG") == "ja" else "en"
    return TEXTS[lang][key].format(**values)


def say(text):
    print("[remote] " + text, flush=True)


# ---- ファイル操作 ----

def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative(value):
    p = PurePosixPath(value)
    if (not value or "\\" in value or ":" in value or "\x00" in value or p.is_absolute()
            or any(x in {"..", "."} for x in value.split("/"))):
        raise ValueError(t("unsafe_path", path=value))
    return str(p)


def contained_path(root, relative):
    root = Path(root)
    rel = safe_relative(relative)
    target = root / rel
    current = root
    for part in PurePosixPath(rel).parts:
        current /= part
        if current.is_symlink():
            raise ValueError(t("unsafe_path", path=relative))
    if root.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(t("unsafe_path", path=relative))
    return target


def atomic_copy(source, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".comfy-", suffix=".part", dir=destination.parent)
    os.close(fd)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def private_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


# ---- 部品への分け方と書庫 ----

def excluded_path(parts):
    return "__pycache__" in parts or ".venv" in parts or (
        len(parts) >= 2 and parts[0] == "ComfyUI" and parts[1] in EXCLUDED)


def archive_filter(member):
    parts = PurePosixPath(member.name).parts
    if excluded_path(parts) or any(p.endswith(".pyc") for p in parts):
        return None
    # ほかの場所を指すリンクは保存しない
    if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
        return None
    return member


def component_of(parts):
    """ファイルが属する部品: ComfyUI 本体、カスタムノードごと、ユーザーデータ、ComfyUI の横に置くファイル。"""
    if parts[0] != "ComfyUI":
        return "state"
    if len(parts) >= 3 and parts[1] == "user":
        return "user"
    if len(parts) >= 4 and parts[1] == "custom_nodes":
        return "node/" + parts[2]
    return "core"


def component_fingerprints(root):
    """{部品: ファイルの名前・サイズ・更新時刻から作った指紋}"""
    entries = {}
    for parent, dirs, files in os.walk(root, followlinks=False):
        relative = Path(parent).relative_to(root)
        dirs[:] = sorted(d for d in dirs if not excluded_path((relative / d).parts)
                         and not (Path(parent) / d).is_symlink())
        for name in sorted(files):
            p = Path(parent) / name
            if p.is_symlink() or name.endswith(".pyc"):
                continue
            try:
                st = p.stat()
            except FileNotFoundError:
                continue
            path = relative / name
            entries.setdefault(component_of(path.parts), []).append((path.as_posix(), st.st_size, st.st_mtime_ns))
    return {name: hashlib.sha256(json.dumps(found).encode()).hexdigest() for name, found in entries.items()}


def write_archive(component, sink):
    """部品を 1 つ tar.gz にする。圧縮した時刻を入れないので、同じファイルからは同じバイト列になる。"""
    def without_other_parts(member):
        parts = PurePosixPath(member.name).parts
        if parts[:2] == ("ComfyUI", "user") or (member.isdir() and len(parts) == 3 and parts[1] == "custom_nodes"):
            return None
        return archive_filter(member)
    with gzip.GzipFile(fileobj=sink, mode="wb", compresslevel=1, mtime=0) as packed, tarfile.open(
            fileobj=packed, mode="w|") as tf:
        if component == "core":
            tf.add(REPO, arcname="ComfyUI", filter=without_other_parts)
        elif component == "user":
            tf.add(REPO / "user", arcname="ComfyUI/user", filter=archive_filter)
        elif component == "state":
            for child in sorted(ENVDIR.iterdir()):
                if child.name != "ComfyUI":
                    tf.add(child, arcname=child.name, filter=archive_filter)
        elif component.startswith("node/") and component[5:] not in ("", ".", "..") and "/" not in component[5:]:
            tf.add(REPO / "custom_nodes" / component[5:], arcname="ComfyUI/custom_nodes/" + component[5:],
                   filter=archive_filter)
        else:
            raise ValueError(component)


def restore_archive(archive, dest):
    with tarfile.open(archive, "r:gz") as tf:
        for member in tf.getmembers():
            safe_relative(member.name)
            if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                raise ValueError(t("special_files"))
        tf.extractall(dest, filter="data")


# ---- 保存 ----

def python_version():
    """ComfyUI の venv の元になる Python の版。保存した環境は、同じ版の Python でだけ復元する。"""
    py = os.environ.get("COMFY_PYTHON") or str(VENV_PY)
    out = subprocess.run([py, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
                         capture_output=True, text=True, check=True).stdout
    return out.strip()


def checked_lock(text):
    """次の VM でも同じものを入れられるよう、版を固定した行と https の URL の行だけを受け付ける。"""
    for line in text.splitlines():
        if line.strip() and not LOCK_LINE.fullmatch(line):
            raise RuntimeError(t("unpinned_lock", line=line))
    return text


def saved_torch_index():
    """記録した PyTorch の取得元。PyTorch の公式の CUDA 版の索引だけを受け付ける。"""
    index = json.loads((ENVDIR / "runtime.json").read_text()).get("torch_index_url", "")
    if not TORCH_INDEX.fullmatch(str(index)):
        raise RuntimeError("invalid torch index: " + str(index))
    return index


def write_lock():
    packages = checked_lock(subprocess.run([str(VENV_PY), "-m", "pip", "freeze"], capture_output=True, text=True,
                                           check=True, timeout=300).stdout)
    lock = ENVDIR / "requirements.lock.txt"
    if not lock.exists() or lock.read_text() != packages:
        fd, temporary = tempfile.mkstemp(prefix=".comfy-", suffix=".part", dir=ENVDIR)
        with os.fdopen(fd, "w") as file:
            file.write(packages)
        os.replace(temporary, lock)


def backup_database(force):
    """ComfyUI のデータベースを ComfyUI/user/comfyui.db に写す。WAL の確定済みの変更も含めて一貫した写しになる。"""
    if not DATABASE.exists():
        return
    signature = json.dumps([(p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in
                            (DATABASE, DATABASE.with_name(DATABASE.name + "-wal")) if p.exists()])
    marker = STATE / "database-signature"
    if not force and marker.exists() and marker.read_text() == signature:
        return
    with sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True) as database:
        with sqlite3.connect(REPO / "user/comfyui.db") as target:
            database.backup(target)
    private_write(marker, signature)


def discard_old_saves(storage):
    """新しい保存を残し、部品はそのどれかが使うものだけを残す。消すのはこの形式の名前のファイルだけ。"""
    def named(folder, pattern):
        return sorted((item for item in folder.iterdir() if not item.is_symlink() and pattern.fullmatch(item.name)),
                      key=lambda item: item.name, reverse=True)
    try:
        current = (storage / "latest.json").read_text()
        saves = {save: save.read_text() for save in named(storage / "generations", GENERATION_NAME)}
        # latest.json が指す保存は、名前の新しさに関係なく残す
        kept = sorted(saves, key=lambda save: saves[save] != current)[:GENERATIONS]
        lists = [json.loads(current), *(json.loads(saves[save]) for save in kept)]
        needed = {part["file"] for save in lists for part in save.get("parts", {}).values()}
        needed |= {save["archive"] for save in lists if "archive" in save}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return  # 読めない保存がある: それが使うかもしれないので何も消さない
    for old in set(saves) - set(kept):
        old.unlink()
    for part in named(storage / "parts", PART_NAME):
        if part.name not in needed:
            part.unlink()
    # 部品に分ける前の、環境全体の書庫は古い保存として数える
    for old in named(storage, WHOLE_ARCHIVE_NAME)[max(0, GENERATIONS - len(kept)):]:
        if old.name not in needed:
            old.unlink()


def checkpoint(force=False):
    """変わった部品だけを保存する。force なら、記録にある部品が Drive に残っているかも確かめる。"""
    if STORAGE is None or not REPO.exists():
        return False
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "checkpoint.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        refuse_unfinished_update()
        for folder in (STORAGE, STORAGE / "parts", STORAGE / "generations"):
            folder.mkdir(parents=True, exist_ok=True)
        backup_database(force)
        write_lock()
        record = STATE / "saved-parts.json"
        saved = json.loads(record.read_text()) if record.exists() else {}
        before = component_fingerprints(ENVDIR)
        parts, sent = {}, []
        staging = STATE / "part.tar.gz"
        for name in sorted(before):
            known = saved.get(name)
            if known and known["fingerprint"] == before[name] and (
                    not force or contained_path(STORAGE / "parts", known["file"]).exists()):
                parts[name] = known
                continue
            try:
                with staging.open("wb") as file:
                    write_archive(name, file)
                digest = file_hash(staging)
                atomic_copy(staging, contained_path(STORAGE / "parts", digest + ".tar.gz"))
            finally:
                staging.unlink(missing_ok=True)
            parts[name] = {"fingerprint": before[name], "file": digest + ".tar.gz", "sha256": digest}
            sent.append(name)
        if not sent and set(parts) == set(saved):
            return False
        after = component_fingerprints(ENVDIR)
        if any(after.get(name) != before[name] for name in sent):
            raise RuntimeError(t("busy"))
        # 一覧は最後に書く。それまでは前回の保存が指されたまま
        save = {"format": 2, "python": python_version(),
                "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "parts": {name: {"file": part["file"], "sha256": part["sha256"]} for name, part in parts.items()}}
        text = json.dumps(save, sort_keys=True)
        name = (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-"
                + hashlib.sha256(text.encode()).hexdigest()[:12] + ".json")
        private_write(STATE / "latest.json", text)
        atomic_copy(STATE / "latest.json", contained_path(STORAGE / "generations", name))
        atomic_copy(STATE / "latest.json", contained_path(STORAGE, "latest.json"))
        private_write(record, json.dumps(parts))
        private_write(STATE / "checkpoint-status.json", json.dumps(
            {"ok": True, "saved_at": save["saved_at"], "parts": len(parts), "sent": sent}))
        discard_old_saves(STORAGE)
        say(t("saved", sent=", ".join(sent) if sent else t("none")))
        return True


# ---- 復元 ----

def restore():
    """Drive の latest.json が指す環境を展開する。保存が無ければ 3 を返す。"""
    pointer_path = STORAGE / "latest.json" if STORAGE else None
    if pointer_path is None or not pointer_path.exists():
        say(t("no_save"))
        return 3
    save = json.loads(pointer_path.read_text())
    current = python_version()
    if save["python"] != current:
        raise RuntimeError(t("python_changed", saved=save["python"], current=current))
    STATE.mkdir(parents=True, exist_ok=True)
    ENVDIR.mkdir(parents=True, exist_ok=True)
    archive = STATE / "restore.tar.gz"

    def bring(folder, name, digest):
        atomic_copy(contained_path(folder, name), archive)
        if file_hash(archive) != digest:
            raise RuntimeError(t("sha_mismatch", name=name))
        restore_archive(archive, ENVDIR)
        archive.unlink()

    if "parts" not in save:
        # 部品に分ける前の形式: 環境全体が 1 つの書庫
        name = safe_relative(save["archive"])
        if "/" in name or not name.endswith(".tar.gz"):
            raise RuntimeError(t("bad_name"))
        bring(STORAGE, name, save["sha256"])
        restored = {}
    else:
        parts = save["parts"]
        for part in parts.values():
            if not PART_NAME.fullmatch(str(part["file"])) or part["file"] != str(part["sha256"]) + ".tar.gz":
                raise RuntimeError(t("bad_name"))
        for name in sorted(parts, key=lambda name: (name != "core", name)):  # 本体を先に
            bring(STORAGE / "parts", parts[name]["file"], parts[name]["sha256"])
        fingerprints = component_fingerprints(ENVDIR)
        restored = {name: {"fingerprint": fingerprints[name], "file": parts[name]["file"],
                           "sha256": parts[name]["sha256"]} for name in parts if name in fingerprints}
    private_write(STATE / "saved-parts.json", json.dumps(restored))
    say(t("restored", saved_at=save.get("saved_at", "-")))
    nodes = sorted(item.name for item in (REPO / "custom_nodes").glob("*")
                   if item.is_dir() and item.name != "__pycache__")
    say(t("restored_nodes", nodes=", ".join(nodes) if nodes else t("none")))
    return 0


def prepare():
    """ComfyUI の外に置くフォルダーを作り、保存したデータベースを戻す。"""
    for name in ("models", "output", "temp", "input", "home", "database"):
        (BASE / name).mkdir(parents=True, exist_ok=True)
    for name in MODEL_FOLDERS:
        (BASE / "models" / name).mkdir(parents=True, exist_ok=True)
    repo_models = REPO / "models"
    if repo_models.exists() and not repo_models.is_symlink():
        # ComfyUI に付いてくる models の中身は、既にあるものを上書きせずに移す
        shutil.copytree(repo_models, BASE / "models", dirs_exist_ok=True,
                        copy_function=lambda source, target: os.path.lexists(target) or shutil.copy2(source, target))
        shutil.rmtree(repo_models)
    if not repo_models.exists():
        repo_models.symlink_to(BASE / "models", target_is_directory=True)
    for name in ("user", "custom_nodes"):
        (REPO / name).mkdir(exist_ok=True)
    if (REPO / "user/comfyui.db").exists() and not DATABASE.exists():
        shutil.copyfile(REPO / "user/comfyui.db", DATABASE)


# ---- ComfyUI の更新 ----

UPDATE_MARKER = STATE / "update-incomplete.json"
REF_PATTERN = re.compile(r"[A-Za-z0-9._/-]+")
RELEASE_TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)")


def refuse_unfinished_update():
    """本体とパッケージの版が食い違った環境は、保存も起動もしない。"""
    if UPDATE_MARKER.exists():
        raise RuntimeError(t("update_incomplete"))


def git(*args, timeout=600):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True,
                          timeout=timeout).stdout.strip()


def latest_release():
    """取得元の vX.Y.Z の形のタグのうち、数値として最大のもの。"""
    listing = git("ls-remote", "--tags", "--refs", "origin", "v*", timeout=120)
    tags = [line.rsplit("refs/tags/", 1)[-1] for line in listing.splitlines()]
    releases = [tag for tag in tags if RELEASE_TAG.fullmatch(tag)]
    if not releases:
        raise RuntimeError(t("no_release"))
    return max(releases, key=lambda tag: tuple(map(int, RELEASE_TAG.fullmatch(tag).groups())))


def install_packages(*args):
    subprocess.run([str(VENV_PY), "-m", "uv", "pip", "install", "--python", str(VENV_PY), "-q", *args], check=True)


def update_needed(ref):
    """ref に切り替える必要があるか。止まった更新が残っていれば、戻すために必要とみなす。
    ref が見つからなければ、ComfyUI を止める前にここで断る。"""
    if UPDATE_MARKER.exists():
        return True
    if not REF_PATTERN.fullmatch(ref) or ref.startswith("-"):
        raise ValueError(t("unknown_ref", ref=ref))
    git("fetch", "--tags", "--force", "origin")
    if ref == "latest":
        ref = latest_release()
    for name in ("refs/tags/" + ref, "refs/remotes/origin/" + ref, ref):
        try:
            target = git("rev-parse", "--verify", "--quiet", name + "^{commit}")
        except subprocess.CalledProcessError:
            continue
        if target == git("rev-parse", "--verify", "HEAD^{commit}"):
            say(t("update_already", version=git("describe", "--tags", "--always")[:80]))
            return False
        return True
    raise ValueError(t("unknown_ref", ref=ref))


def update(ref):
    """ComfyUI を別の版に切り替え、その版が求めるパッケージを入れる。カスタムノード・設定・ワークフロー・
    データベースは ComfyUI のリポジトリのファイルではないので、そのまま残る。失敗したら元に戻し、Drive には保存しない。"""
    if not REF_PATTERN.fullmatch(ref) or ref.startswith("-"):
        raise ValueError(t("unknown_ref", ref=ref))
    lock = ENVDIR / "requirements.lock.txt"

    def commit(name):
        # リポジトリはカスタムノードからも書き換えられるので、コミットの ID 以外は受け取らない
        try:
            found = git("rev-parse", "--verify", "--quiet", name + "^{commit}")
        except subprocess.CalledProcessError:
            return ""
        return found if re.fullmatch(r"[0-9a-f]{40,64}", found) else ""

    def switch(target):
        git("checkout", "--force", "--detach", target)
        prepare()  # 切り替えで ComfyUI に付いてくる models フォルダーが戻るので、外へ移し直す

    def package_names(listing):
        return {re.split(r"==| @ ", line)[0] for line in listing.splitlines() if LOCK_LINE.fullmatch(line)}

    def undo(state):
        """更新を始める前の版とパッケージに戻す。"""
        packages = checked_lock(lock.read_text())
        index = saved_torch_index()
        switch(state["commit"])
        install_packages("--index-strategy", "unsafe-best-match", "--extra-index-url", index, "-r", str(lock))
        frozen = subprocess.run([str(VENV_PY), "-m", "pip", "freeze"], capture_output=True, text=True,
                                check=True, timeout=300).stdout
        added = sorted(package_names(frozen) - package_names(packages))
        if added:
            subprocess.run([str(VENV_PY), "-m", "uv", "pip", "uninstall", "--python", str(VENV_PY), *added], check=True)
        UPDATE_MARKER.unlink()

    if UPDATE_MARKER.exists():
        # 前の更新が途中で止まり、そのときは戻せなかった
        say(t("update_resuming"))
        undo(json.loads(UPDATE_MARKER.read_text()))
    git("fetch", "--tags", "--force", "origin")
    if ref == "latest":
        ref = latest_release()
    before = {"commit": commit("HEAD"), "version": git("describe", "--tags", "--always")[:80]}
    target = next(filter(None, map(commit, ("refs/tags/" + ref, "refs/remotes/origin/" + ref, ref))), "")
    if not before["commit"] or not target:
        raise ValueError(t("unknown_ref", ref=ref))
    if target == before["commit"]:
        say(t("update_already", version=before["version"]))
        return False
    say(t("update_starting", previous=before["version"], ref=ref))
    write_lock()
    private_write(UPDATE_MARKER, json.dumps(before))
    try:
        switch(target)
        install_packages(*(argument for name in ("requirements.txt", "manager_requirements.txt")
                           if (REPO / name).exists() for argument in ("-r", str(REPO / name))))
        subprocess.run([str(VENV_PY), "-m", "pip", "check"], check=True)
    except Exception:
        try:
            undo(before)
        except Exception:
            say(t("update_undo_failed"))
            raise
        say(t("update_undone", version=before["version"]))
        raise
    UPDATE_MARKER.unlink()
    say(t("update_done", previous=before["version"], version=git("describe", "--tags", "--always")[:80]))
    if STORAGE is not None:
        checkpoint(force=True)
    return True


def saved_apt_packages():
    """保存した環境が記録している apt パッケージを表示する。入れるかどうかは利用者が決める。"""
    try:
        meta = json.loads((ENVDIR / "runtime.json").read_text())
    except (OSError, ValueError):
        return
    names = sorted({n for n in meta.get("apt_packages", []) if isinstance(n, str)
                    and re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", n)} - set(os.environ.get("APT_PACKAGES", "").split()))
    if names:
        say(t("apt_hint", names=" ".join(names)))


def main(argv):
    command = argv[1] if len(argv) > 1 else ""
    if command == "restore":
        return restore()
    if command == "save":
        if STORAGE is not None and not checkpoint(force=len(argv) > 2 and argv[2] == "force"):
            say(t("unchanged"))
        return 0
    if command == "loop":
        seconds = max(60, int(argv[2]))
        while True:
            time.sleep(seconds)
            try:
                checkpoint()
            except Exception as failure:
                private_write(STATE / "checkpoint-status.json", json.dumps({"ok": False, "error": str(failure)}))
                say(t("save_failed", error=failure))
    if command == "prepare":
        prepare()
        return 0
    if command == "update":
        update(argv[2] if len(argv) > 2 else "latest")
        return 0
    if command == "update-needed":
        return 0 if update_needed(argv[2] if len(argv) > 2 else "latest") else 3
    if command == "refuse-unfinished-update":
        refuse_unfinished_update()
        return 0
    if command == "lock":
        write_lock()
        return 0
    if command == "check-lock":
        checked_lock((ENVDIR / "requirements.lock.txt").read_text())
        return 0
    if command == "apt-hint":
        saved_apt_packages()
        return 0
    if command == "torch-index":
        print(saved_torch_index())
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as failure:
        print(f"[remote] {failure}", file=sys.stderr, flush=True)
        sys.exit(1)
