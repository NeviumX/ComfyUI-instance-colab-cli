"""Colab VM の ComfyUI の output を、標準入力の tar から手元のフォルダーへ取り出す。手元の WSL で動く。

書庫は VM の ComfyUI の利用者が作るので、中身はカスタムノードが決められる。
通常のファイルとフォルダーだけを取り出し、リンクや特殊なファイル、フォルダーの外を指す名前は捨てる。
手元に同じ名前のファイルがあれば上書きしない。

    ssh ... tar -cf - . | python3 untar_outputs.py 取り出し先
"""

import os
from pathlib import Path, PurePosixPath
import sys
import tarfile


def safe_parts(name):
    path = PurePosixPath(name)
    parts = [p for p in path.parts if p not in ("", ".")]
    if path.is_absolute() or not parts or any(p == ".." or "\\" in p or "\x00" in p for p in parts):
        return None
    return parts


def main(argv):
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    dest = Path(argv[1]).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r|") as archive:
        for member in archive:
            parts = safe_parts(member.name)
            if parts is None or not (member.isfile() or member.isdir()):
                continue
            target = dest.joinpath(*parts)
            # 途中にリンクがあれば、その先には書かない
            current = dest
            unsafe = False
            for part in parts[:-1]:
                current = current / part
                if current.is_symlink() or (current.exists() and not current.is_dir()):
                    unsafe = True
                    break
            if unsafe or target.is_symlink():
                continue
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            partial = target.with_name(".comfy-pull-" + target.name + ".part")
            try:
                with partial.open("wb") as sink:
                    while True:
                        chunk = source.read(4 * 1024 * 1024)
                        if not chunk:
                            break
                        sink.write(chunk)
                os.utime(partial, (member.mtime, member.mtime))
                if not target.exists():
                    os.replace(partial, target)
            finally:
                partial.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
