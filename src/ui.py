# -*- coding: utf-8 -*-
"""终端界面小工具：清屏、彩色、表格（中文对齐）、进度条。纯标准库。"""
import os
import shutil
import sys
import unicodedata

_COLOR = True


def enable_ansi():
    """Windows 10+ 打开 ANSI 转义支持。"""
    global _COLOR
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetConsoleMode(k.GetStdHandle(-11), 7)
        except Exception:
            _COLOR = False
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def c(text, code):
    if not _COLOR:
        return text
    return "\033[%sm%s\033[0m" % (code, text)


def clear():
    if not sys.stdout.isatty():
        return
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def width(s):
    """显示宽度：中文/全角算 2。"""
    n = 0
    for ch in str(s):
        if unicodedata.combining(ch):
            continue
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return n


def cut(s, limit):
    out = []
    n = 0
    for ch in str(s):
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if n + cw > limit:
            break
        out.append(ch)
        n += cw
    return "".join(out)


def pad(s, limit, align="left"):
    s = str(s)
    gap = limit - width(s)
    if gap <= 0:
        return s
    if align == "right":
        return " " * gap + s
    if align == "center":
        return " " * (gap // 2) + s + " " * (gap - gap // 2)
    return s + " " * gap


def hr(ch="─"):
    return ch * min(shutil.get_terminal_size((80, 20)).columns - 1, 78)


def title(text):
    print()
    print(c(" " + text + " ", "1;36"))
    print(hr("═"))


def table(headers, rows, aligns=None, maxwidths=None):
    """打印带对齐的中文表格。rows 是 list of tuple/list。"""
    cols = len(headers)
    aligns = aligns or ["left"] * cols
    widths = [width(h) for h in headers]
    for r in rows:
        for i, v in enumerate(r):
            widths[i] = max(widths[i], width(v))
    if maxwidths:
        widths = [min(w, m) if m else w for w, m in zip(widths, maxwidths)]

    def line(vals, sep="  "):
        cells = []
        for i, v in enumerate(vals):
            cells.append(pad(cut(v, widths[i]), widths[i], aligns[i]))
        return sep.join(cells).rstrip()

    print(" " + c(line(headers), "1"))
    print(" " + c("  ".join("─" * w for w in widths), "90"))
    for r in rows:
        print(" " + line([str(x) for x in r]))


def bar(done, total, size=28):
    if total <= 0:
        return "[" + " " * size + "]"
    ratio = min(1.0, done / total)
    filled = int(size * ratio)
    if filled >= size:
        return "[" + "=" * size + "]"
    return "[" + "=" * filled + ">" + " " * (size - filled - 1) + "]"


def human_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d%s" % (n, unit)) if unit == "B" else ("%.1f%s" % (n, unit))
        n /= 1024.0
    return "%.1fGB" % n


def human_time(sec):
    sec = int(max(0, sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    if h:
        return "%d:%02d:%02d" % (h, m, s)
    return "%d:%02d" % (m, s)


def ask(prompt, default=None):
    hint = "（回车用 %s）" % default if default else ""
    sys.stdout.write("%s%s\n> " % (prompt, hint))
    sys.stdout.flush()
    raw = sys.stdin.readline()
    if raw == "":
        raise EOFError
    s = raw.strip()
    return s if s else (default or "")


def ask_password(prompt):
    """密码不回显。管道输入时自动退回普通读取。"""
    try:
        import getpass
        return getpass.getpass(prompt + ": ")
    except (EOFError, KeyboardInterrupt):
        raise
    except Exception:
        return ask(prompt)


def ask_choice(prompt, valid):
    while True:
        s = ask(prompt)
        if s in valid:
            return s
        print(c("  请输入 %s 之一" % "/".join(valid), "33"))


def ask_yn(prompt, default="n"):
    while True:
        s = ask(prompt + " (y/n)", default).lower()
        if s in ("y", "yes", "是"):
            return True
        if s in ("n", "no", "否"):
            return False


def pause(msg="按回车继续"):
    sys.stdout.write(msg + " ")
    sys.stdout.flush()
    sys.stdin.readline()
