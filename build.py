#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建离线发行包（自带嵌入式 Python，解压双击即用）。

用法
----
    python build.py --version 1.0.0
    python build.py --version 1.0.0 --proxy http://127.0.0.1:7897

产物
----
    dist/wechat-moments-exporter-v<版本>.zip
    终端会打印体积与 SHA256，可直接贴到 Release notes。

依赖
----
    仅 Python 标准库（3.8+）。首次运行会下载嵌入式 Python 和 pycryptodome 轮子，
    之后从 .build-cache/ 复用。
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
THIRD = ROOT / "third_party" / "kobackup-decrypt"

PY_URL = "https://www.python.org/ftp/python/{v}/python-{v}-embed-amd64.zip"
PYPI_JSON = "https://pypi.org/pypi/pycryptodome/json"
PYPI_VER = "https://pypi.org/pypi/pycryptodome/{v}/json"

APP_DIR = "微信朋友圈导出"
# 源码里绝不允许出现的本地路径 / 个人信息特征
LEAK_PATTERNS = ("C:\\", "D:\\", "E:\\", "Desktop\\", "wxid_")


def log(msg=""):
    print(msg, flush=True)


def make_opener(proxy):
    if proxy:
        h = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    else:
        h = urllib.request.ProxyHandler({})      # 默认不走代理，避免死代理卡住
    return urllib.request.build_opener(h)


def download(url, dst, proxy=None, timeout=180):
    if dst.exists() and dst.stat().st_size > 0:
        log("  复用缓存 %s (%.1f MB)" % (dst.name, dst.stat().st_size / 1048576))
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    log("  下载 %s" % url)
    req = urllib.request.Request(url, headers={"User-Agent": "build.py"})
    with make_opener(proxy).open(req, timeout=timeout) as r, dst.open("wb") as f:
        shutil.copyfileobj(r, f)
    log("  完成 %s (%.1f MB)" % (dst.name, dst.stat().st_size / 1048576))
    return dst


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pick_wheel(version, proxy=None):
    """从 PyPI 取 win_amd64 + abi3 轮子（abi3 兼容 3.8+，无需按小版本匹配）。"""
    url = PYPI_JSON if version == "latest" else PYPI_VER.format(v=version)
    req = urllib.request.Request(url, headers={"User-Agent": "build.py"})
    data = json.loads(make_opener(proxy).open(req, timeout=60).read())
    cands = [u for u in data["urls"]
             if "win_amd64" in u["filename"] and "abi3" in u["filename"]]
    if not cands:
        raise SystemExit("PyPI 上找不到 win_amd64 + abi3 的 pycryptodome 轮子")
    c = cands[0]
    return c["filename"], c["url"]


def check_leaks(tool_dir):
    bad = []
    for f in sorted(Path(tool_dir).glob("*.py")):
        txt = f.read_text(encoding="utf-8", errors="replace")
        for pat in LEAK_PATTERNS:
            if pat in txt:
                bad.append("%s 含有 %r" % (f.name, pat))
    if bad:
        raise SystemExit("源码里发现本地路径 / 个人信息，已中止：\n  " + "\n  ".join(bad))


def write_launcher(stage):
    (stage / "双击运行.bat").write_bytes((
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "setlocal\r\n"
        "cd /d \"%~dp0\"\r\n"
        "\"%~dp0vendor\\python\\python.exe\" \"%~dp0tool\\main.py\"\r\n"
        "echo.\r\n"
        "pause\r\n"
    ).encode("utf-8"))


def self_test(stage):
    py = stage / "vendor" / "python" / "python.exe"
    tool = stage / "tool"
    checks = [
        "import sqlite3",
        "import Crypto.Cipher.AES",
        "import sys; sys.path.insert(0, %r); import naming, sns_db, ios_find, "
        "downloader, isaac64, android_prep, ui" % str(tool),
    ]
    for code in checks:
        r = subprocess.run([str(py), "-c", code], capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit("运行时自检失败：%s\n%s" % (code, (r.stderr or "")[-600:]))
    log("  运行时自检通过")


def main():
    ap = argparse.ArgumentParser(description="构建离线发行包")
    ap.add_argument("--version", default="dev", help="版本号，用于产物文件名")
    ap.add_argument("--python", default="3.12.10", help="嵌入式 Python 版本")
    ap.add_argument("--pycryptodome", default="4.0.1", help="pycryptodome 版本，或 latest")
    ap.add_argument("--out", default="dist", help="产物目录")
    ap.add_argument("--cache", default=".build-cache", help="下载缓存目录")
    ap.add_argument("--proxy", default=None, help="下载代理，例如 http://127.0.0.1:7897")
    args = ap.parse_args()

    cache = ROOT / args.cache
    out = ROOT / args.out
    stage = cache / "stage" / APP_DIR

    log("== 1/6 准备目录 ==")
    if stage.exists():
        shutil.rmtree(stage)
    for sub in ("tool", "vendor/python", "vendor/pylibs", "vendor/kobackup-decrypt"):
        (stage / sub).mkdir(parents=True, exist_ok=True)
    log("  %s" % stage)

    log("== 2/6 嵌入式 Python %s ==" % args.python)
    pyzip = download(PY_URL.format(v=args.python),
                     cache / ("python-%s-embed-amd64.zip" % args.python), args.proxy)
    with zipfile.ZipFile(pyzip) as z:
        z.extractall(stage / "vendor" / "python")

    py_dir = stage / "vendor" / "python"
    stdlib = sorted(py_dir.glob("python*.zip"))
    pths = sorted(py_dir.glob("python*._pth"))
    if not stdlib or not pths:
        raise SystemExit("嵌入式 Python 结构异常：找不到 python*.zip 或 ._pth")
    # 让包内 Python 能找到 pylibs 和 tool
    pths[0].write_text("%s\n.\n../pylibs\n../../tool\n" % stdlib[0].name, encoding="utf-8")
    log("  已配置 %s" % pths[0].name)

    log("== 3/6 pycryptodome %s ==" % args.pycryptodome)
    fname, furl = pick_wheel(args.pycryptodome, args.proxy)
    whl = download(furl, cache / fname, args.proxy)
    with zipfile.ZipFile(whl) as z:
        z.extractall(stage / "vendor" / "pylibs")
    log("  %s" % fname)

    log("== 4/6 复制源码并检查泄露 ==")
    n = 0
    for f in sorted(SRC.glob("*.py")):
        shutil.copy2(f, stage / "tool" / f.name)
        n += 1
    check_leaks(stage / "tool")
    log("  %d 个模块，未发现本地路径 / wxid" % n)

    log("== 5/6 第三方解密工具 ==")
    dec = THIRD / "kobackup_decrypt.py"
    if not dec.exists():
        raise SystemExit("缺少 %s\n（MIT 许可的第三方工具，需放在 third_party/kobackup-decrypt/）" % dec)
    shutil.copy2(dec, stage / "vendor" / "kobackup-decrypt" / "kobackup_decrypt.py")
    lic = THIRD / "LICENSE"
    if lic.exists():
        shutil.copy2(lic, stage / "vendor" / "kobackup-decrypt" / "LICENSE")
    log("  kobackup_decrypt.py%s" % (" + LICENSE" if lic.exists() else ""))

    write_launcher(stage)

    log("== 6/6 自检 + 打包 ==")
    self_test(stage)

    for p in stage.rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)
    for p in stage.rglob("*.pyc"):
        try:
            p.unlink()
        except OSError:
            pass

    out.mkdir(parents=True, exist_ok=True)
    zp = out / ("wechat-moments-exporter-v%s.zip" % args.version)
    if zp.exists():
        zp.unlink()
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(stage.rglob("*")):
            if f.is_file():
                z.write(f, Path(APP_DIR) / f.relative_to(stage))

    log()
    log("产物   %s" % zp)
    log("大小   %.1f MB（解压 %.1f MB）" % (
        zp.stat().st_size / 1048576,
        sum(f.stat().st_size for f in stage.rglob("*") if f.is_file()) / 1048576))
    log("SHA256 %s" % sha256(zp))
    log()
    log("把 SHA256 贴到 Release notes 里，方便别人校验。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
