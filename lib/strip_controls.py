"""Colab VM から届いた文字を、手元の端末を操作できない形にして書き出す。手元の WSL で動く。

VM のログや準備の出力には、ComfyUI やカスタムノードのように信頼しないプログラムが書いた文字が混じる。
制御文字をそのまま端末に出すと、クリップボードを書き換えられたり、前に出した行や問い合わせを上書きされたりする。
そこで、文字の色を変える SGR の並びと、タブ・改行・行頭復帰だけを残し、ほかの制御文字と制御の並びを取り除く。
行頭復帰は、進み具合の表示が同じ行を書き直すのに使う。戻れるのは今の行の頭までで、前の行には届かない。
読んだ分はすぐに書き出すので、改行の無い進み具合の表示も、これまでどおりその場で見える。

    ssh ... | python3 strip_controls.py
"""

import codecs
import os
import re
import signal

# 1 つ目の組に当たる SGR は残し、ほかに当たったものは取り除く。取り除くのは、SGR のほかの CSI の並びと途中で終わった CSI の並び、
# タブ・改行・行頭復帰のほかの C0 制御文字、DEL、C1 制御文字。ESC で始まるほかの並びは ESC だけを取り除き、残りはただの文字として見せる
CONTROLS = re.compile(r"(\x1b\[[0-9;:]*m)|\x1b\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]?|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
# 読んだ分の終わりで途切れているかもしれない並び。次に読んだ分とつなげてから調べる
UNFINISHED = re.compile(r"\x1b(?:\[[\x30-\x3f]*[\x20-\x2f]*)?\Z")
# 途切れた並びの続きを待つのは、この文字数まで
HOLD_MAX = 256


def strip_controls(text):
    return CONTROLS.sub(lambda m: m.group(1) or "", text)


def main():
    # Ctrl+C で止めたときや、読む側が先に終わったときは、tail などと同じように黙って終わる
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    # UTF-8 の文字に直してから調べる。取り除いた跡で前後のバイトがつながり、C1 制御文字にならないようにするため。
    # 読んだ分の境目で切れた文字はつなげ、UTF-8 として正しくない並びは置き換え文字にする
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    rest = ""
    while True:
        chunk = os.read(0, 65536)
        text = rest + decoder.decode(chunk, final=not chunk)
        rest = ""
        if chunk:
            found = UNFINISHED.search(text, max(0, len(text) - HOLD_MAX))
            if found:
                text, rest = text[:found.start()], text[found.start():]
        data = strip_controls(text).encode("utf-8")
        while data:
            data = data[os.write(1, data):]
        if not chunk:
            return


if __name__ == "__main__":
    main()
