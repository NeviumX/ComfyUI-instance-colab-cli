"""ComfyUI とカスタムノードを、root ではない利用者 comfy で動かすための道具。Colab VM 上で root が使う。

ComfyUI の作業場所 COMFY_BASE はその利用者のもので、そこでの操作もその利用者として行う。
root が作業場所の中のリンクをたどると、ドライブや鍵やシステムのファイルを書き換えさせられるため。
ノートブック版の runtime/commands.py と runtime/drive.py と同じ考え方。

    python3 app_user.py run [--cwd DIR] [--set NAME=VALUE]... -- 命令...   その利用者として実行する
    python3 app_user.py ensure-user     利用者を作り、GPU のデバイスを使えるようにする
    python3 app_user.py create-tree     作業場所のフォルダーを作り、作ったものだけをその利用者に渡す
    python3 app_user.py stop            その利用者のプロセスをすべて止める
    python3 app_user.py show-view 元 見え方   ドライブの models を、読み取り専用で見せる
    python3 app_user.py hide-view 見え方      見え方を外す

分けるのは COMFY_ISOLATE=1 で root として動いているときだけ。それ以外では、命令をそのまま実行する。
"""

import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time

APP_USER = "comfy"
SETPRIV = "/usr/bin/setpriv"
ENV = "/usr/bin/env"
APP_TIMEOUT = 2 * 3600
OUTPUT_LIMIT = 16 * 1024 ** 2

GPU_ENVIRONMENT_KEYS = (
    "LD_LIBRARY_PATH", "LD_PRELOAD", "CUDA_VISIBLE_DEVICES", "CUDA_HOME", "CUDA_PATH",
    "CUDA_MODULE_LOADING", "CUDA_DEVICE_ORDER", "CUDA_CACHE_PATH",
    "NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES", "PYTORCH_NVML_BASED_CUDA_CHECK",
)


def base():
    return Path(os.environ.get("COMFY_BASE", "/content/comfy-session"))


def isolated():
    return os.environ.get("COMFY_ISOLATE") == "1" and sys.platform == "linux" and os.geteuid() == 0


def app_ids():
    """その利用者の uid と gid。分けていないときは None。"""
    if not isolated():
        return None
    import pwd
    account = pwd.getpwnam(APP_USER)
    return account.pw_uid, account.pw_gid


def application_environment(extra=None):
    """その利用者に渡す環境変数。root の環境は渡さず、GPU に要るものだけを写す。"""
    env = {"PATH": f"{base() / '.venv/bin'}:/usr/local/bin:/usr/bin:/bin",
           "HOME": str(base() / "home"), "LANG": "C.UTF-8", "PYTHONUNBUFFERED": "1",
           "VIRTUAL_ENV": str(base() / ".venv"), "PIP_DISABLE_PIP_VERSION_CHECK": "1",
           # Colab の /etc/pip.conf は、root しか書けない /var/log/pip.log に書こうとする
           "PIP_CONFIG_FILE": "/dev/null"}
    for name in GPU_ENVIRONMENT_KEYS:
        if name in os.environ:
            env[name] = os.environ[name]
    if "LD_LIBRARY_PATH" in env:
        # 空の要素や相対パスは今いるフォルダーを指し、その利用者が書ける場所になる
        env["LD_LIBRARY_PATH"] = ":".join(p for p in env["LD_LIBRARY_PATH"].split(":") if p.startswith("/"))
    # 偽の VM のテストだけが使う。uv の取得済みのパッケージを、テストのたびに取り直さないため
    if os.environ.get("COMFY_APP_UV_CACHE"):
        env["UV_CACHE_DIR"] = os.environ["COMFY_APP_UV_CACHE"]
    env.update(extra or {})
    return env


def app_command(args):
    """その利用者として args を実行する命令。setuid のプログラムでも root には戻れない。"""
    args = list(map(str, args))
    if not isolated():
        return args
    return [SETPRIV, "--reuid", APP_USER, "--regid", APP_USER, "--init-groups", "--no-new-privs", "--", *args]


def app_cwd(cwd):
    """その利用者として動かすときの、今いるフォルダー。指定が無ければ作業場所にする。
    root の今いるフォルダーは /root のことが多く、その利用者は入れない。uv などは今いるフォルダーから上の設定を探すので、読めずに失敗する。"""
    if cwd or not isolated():
        return cwd
    return str(base())


def launch(args, cwd=None):
    """その利用者として args を実行する命令と、root がそれを始める前に移るフォルダー。
    分けているときは root は移らず、その利用者になってから env が今いるフォルダーへ移る。root が移ると、
    その利用者が作業場所に置いたリンクを root としてたどり、PRIVATE_DIR の中のドライブのような、
    その利用者が自分では入れない場所を今いるフォルダーとして渡してしまうため。"""
    cwd = app_cwd(cwd)
    if not isolated():
        return app_command(args), cwd
    return app_command([ENV, "--chdir", cwd, "--", *args]), None


def app_env(extra=None):
    if isolated():
        return application_environment(extra)
    env = os.environ.copy()
    env.update(extra or {})
    return env


def run(args, *, cwd=None, extra=None, timeout=APP_TIMEOUT, stdin=None):
    """その利用者として実行し、出力はそのまま流す。失敗したら CalledProcessError。"""
    command, cwd = launch(args, cwd)
    subprocess.run(command, env=app_env(extra), cwd=cwd, check=True, timeout=timeout, stdin=stdin)


def bounded(command, *, env, limit, timeout, stdin=None, sink=None, cwd=None):
    """その利用者が動かす命令を実行し、出力を集める。止まったり出力し続けたりしても、
    root を timeout より長く待たせず、limit より多くは受け取らない。(終了コード, 出力, 標準エラーの最後)"""
    import select
    with tempfile.TemporaryFile() as spool:
        if isinstance(stdin, bytes):
            spool.write(stdin)
            spool.seek(0)
            stdin = spool
        process = subprocess.Popen(command, env=env, cwd=cwd, stdin=stdin or subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + timeout
    streams = {process.stdout.fileno(): True, process.stderr.fileno(): False}
    output, errors, total, failure = [], b"", 0, None
    try:
        while streams and not failure:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = "timed out"
                break
            ready, _, _ = select.select(list(streams), [], [], min(remaining, 0.5))
            for fd in ready:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    del streams[fd]
                elif not streams[fd]:
                    errors = (errors + chunk)[-64 * 1024:]
                elif total + len(chunk) > limit:
                    failure = "too much output"
                    break
                else:
                    total += len(chunk)
                    sink.write(chunk) if sink else output.append(chunk)
            if not ready and process.poll() is not None:
                break  # 終わった。パイプを持ったまま残った子のプロセスは待たない
        if not failure:
            try:
                process.wait(timeout=max(deadline - time.monotonic(), 0))
            except subprocess.TimeoutExpired:
                failure = "timed out"
    finally:
        if process.poll() is None:
            process.kill()
        process.stdout.close()
        process.stderr.close()
        process.wait()
    if failure:
        raise RuntimeError(failure)
    return process.returncode, b"".join(output), errors


def capture(args, *, cwd=None, extra=None, limit=OUTPUT_LIMIT, timeout=APP_TIMEOUT, stdin=None, sink=None):
    """その利用者として実行し、出力を受け取る。失敗したら RuntimeError。"""
    command, cwd = launch(args, cwd)
    code, out, err = bounded(command, env=app_env(extra), limit=limit, timeout=timeout,
                             stdin=stdin, sink=sink, cwd=cwd)
    if code:
        detail = err.decode(errors="replace").strip().splitlines()[-3:]
        raise RuntimeError(f"{Path(str(args[0])).name} exited with {code}: " + " / ".join(detail))
    return out


# ---- 利用者と作業場所 ----

def ensure_user():
    """ComfyUI と pip とカスタムノードを動かす利用者を作る。"""
    if not Path(SETPRIV).is_file():
        raise RuntimeError(f"{SETPRIV} is missing")
    if subprocess.run(["id", "-u", APP_USER], capture_output=True).returncode:
        subprocess.run(["useradd", "--system", "--user-group", "--no-create-home", "--home-dir", str(base() / "home"),
                        "--shell", "/usr/sbin/nologin", APP_USER], check=True)
    # GPU のデバイスファイルはたいてい誰でも開ける。そうでないものは、その group に入れる
    for device in [*Path("/dev").glob("nvidia*"), *Path("/dev/dri").glob("*")]:
        try:
            info = device.stat()
        except OSError:
            continue
        if stat.S_ISCHR(info.st_mode) and info.st_mode & 0o060 and not info.st_mode & 0o006 and info.st_gid:
            if subprocess.run(["getent", "group", str(info.st_gid)], capture_output=True).returncode:
                subprocess.run(["groupadd", "--gid", str(info.st_gid), f"{APP_USER}-device-{info.st_gid}"], check=True)
            subprocess.run(["usermod", "--append", "--groups", str(info.st_gid), APP_USER], check=True)


TREE = ("environment", "models", "output", "temp", "input", "home", "database")


def create_tree():
    """作業場所のフォルダーを作る。渡すのは今作ったものだけで、リンクはたどらない。
    失敗した後のやり直しでは、その利用者のコードが置いたものが残っているかもしれないため。"""
    ids = app_ids()
    created = []
    for directory in (base(), *(base() / name for name in TREE)):
        try:
            os.mkdir(directory, 0o755)
        except FileExistsError:
            continue
        created.append(directory)
    if ids:
        for directory in reversed(created):  # BASE は最後に渡す。その間に中身を差し替えられないように
            os.chown(directory, *ids, follow_symlinks=False)


def stop_processes():
    """その利用者が動かしているものをすべて止める。カスタムノードが ComfyUI から切り離して残したものも止まる。"""
    ids = app_ids()
    if not ids:
        return
    uid = ids[0]
    # その利用者として、送れる相手すべてに送る。自分のプロセスすべてに一度に届く
    subprocess.run(app_command(["/usr/bin/kill", "-KILL", "-1"]), env={}, capture_output=True)
    for _ in range(50):
        alive = False
        for entry in Path("/proc").iterdir():
            try:
                if entry.name.isdigit() and entry.stat().st_uid == uid:
                    alive = True
                    os.kill(int(entry.name), signal.SIGKILL)
            except (FileNotFoundError, ProcessLookupError):
                pass
        if not alive:
            return
        time.sleep(0.1)
    raise RuntimeError(f"processes of {APP_USER} remain")


# ---- ドライブの models の見え方 ----

def view_shown(view):
    return os.path.ismount(view)


def show_view(source, view):
    """ドライブの models だけを、その利用者のファイルとして読み取り専用で見せる。
    元のドライブのマウントは root だけが入れる場所に置いたまま。見え方の場所は作業場所の外に置く。"""
    source, view = Path(source), Path(view)
    if view_shown(view):
        return
    try:
        if view.is_symlink():
            raise RuntimeError(f"{view} is a link")
        view.mkdir(mode=0o755, exist_ok=True)
        if view.stat().st_uid != os.geteuid() or any(view.iterdir()):
            raise RuntimeError(f"{view} is not an empty folder of root")
        subprocess.run(["bindfs", "-r", f"--force-user={APP_USER}", f"--force-group={APP_USER}", "--perms=a=rX",
                        "-o", "allow_other", str(source), str(view)], check=True, capture_output=True, timeout=120)
        capture(["ls", str(view)])  # その利用者が本当に中を見られるか
    except (RuntimeError, OSError, subprocess.SubprocessError) as failure:
        hide_view(view)
        detail = getattr(failure, "stderr", b"") or b""
        raise RuntimeError(f"{failure} {detail.decode(errors='replace').strip()}".strip()) from None


def hide_view(view):
    """見え方を外す。ドライブは、何かが読んでいる間はアンマウントできないため。"""
    for command in (["umount", str(view)], ["fusermount", "-u", str(view)], ["umount", "-l", str(view)]):
        if not view_shown(view):
            return
        try:
            subprocess.run(command, capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            pass


def main(argv):
    command = argv[1] if len(argv) > 1 else ""
    if command == "run":
        cwd, extra, rest = None, {}, argv[2:]
        while rest and rest[0] != "--":
            if rest[0] == "--cwd" and len(rest) > 1:
                cwd, rest = rest[1], rest[2:]
            elif rest[0] == "--set" and len(rest) > 1 and "=" in rest[1]:
                name, value = rest[1].split("=", 1)
                extra[name], rest = value, rest[2:]
            else:
                print(__doc__, file=sys.stderr)
                return 2
        args = rest[1:]
        if not args:
            print(__doc__, file=sys.stderr)
            return 2
        command_line, cwd = launch(args, cwd)
        if cwd:
            os.chdir(cwd)
        os.execvpe(command_line[0], command_line, app_env(extra))
    if command == "ensure-user":
        if isolated():
            ensure_user()
        return 0
    if command == "create-tree":
        create_tree()
        return 0
    if command == "stop":
        stop_processes()
        return 0
    if command == "show-view" and len(argv) == 4:
        show_view(argv[2], argv[3])
        return 0
    if command == "hide-view" and len(argv) == 3:
        hide_view(Path(argv[2]))
        return 0
    if command == "isolated":
        return 0 if isolated() else 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as failure:
        print(f"[remote] {failure}", file=sys.stderr, flush=True)
        sys.exit(1)
