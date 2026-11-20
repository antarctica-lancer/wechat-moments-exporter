# -*- coding: utf-8 -*-
"""华为 HiSuite 备份 → 解密 → 只抽出 SnsMicroMsg.db（22 MB，不整包解压 8.5 GB）。"""
import os
import subprocess
import sys
import tarfile
from pathlib import Path


def find_decryptor():
    here = Path(__file__).resolve().parent
    cands = []
    env = os.environ.get("WXTOOL_DECRYPTOR")      # 开发调试用，正式包不需要
    if env:
        cands.append(Path(env))
    cands += [
        here / "vendor" / "kobackup-decrypt" / "kobackup_decrypt.py",
        here.parent / "vendor" / "kobackup-decrypt" / "kobackup_decrypt.py",
        here.parent / "third_party" / "kobackup-decrypt" / "kobackup_decrypt.py",
    ]
    for c in cands:
        if c.exists():
            return c
    hits = list(here.rglob("kobackup_decrypt.py"))
    return hits[0] if hits else None


def validate_backup(root):
    root = Path(root)
    if not root.is_dir():
        return False, "这个路径不是文件夹"
    if not (root / "info.xml").exists():
        return False, "没找到 info.xml —— 这看起来不是 HiSuite 备份目录"
    tars = list(root.rglob("com.tencent.mm*.tar"))
    if not tars:
        return False, "没找到 com.tencent.mm*.tar —— 备份时可能没勾「应用数据」"
    return True, "看起来是 HiSuite 备份（%d 个数据分卷）" % len(tars)


def decrypt_and_extract(backup_dir, password, workdir, on_line=None):
    """解密并只抽出 SnsMicroMsg.db。返回 db 路径。"""
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    tool = find_decryptor()
    if not tool:
        raise RuntimeError("找不到 kobackup_decrypt.py（解密工具没随包带上）")

    out = work / "dec"
    out.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(tool), str(backup_dir), password, "-o", str(out), "--verify"]

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1)
    lines = []
    for line in proc.stdout:
        line = line.rstrip()
        lines.append(line)
        if on_line:
            on_line(line)
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("解密失败（退出码 %d）\n%s" % (proc.returncode, "\n".join(lines[-6:])))

    tars = sorted(out.rglob("com.tencent.mm*.tar"), key=lambda p: p.stat().st_size, reverse=True)
    if not tars:
        tars = sorted(out.rglob("*.tar"), key=lambda p: p.stat().st_size, reverse=True)
    if not tars:
        raise RuntimeError("解密完成，但没找到 .tar 产物")
    tar = tars[0]

    db_dir = work / "sns"
    db_dir.mkdir(parents=True, exist_ok=True)
    found_dbs = []
    with tarfile.open(tar, "r:") as tf:
        for m in tf:
            if not m.isfile():
                continue
            n = m.name.replace("\\", "/")
            parts = [p for p in n.split("/") if p]
            if not parts:
                continue
            base = parts[-1]
            if base in ("SnsMicroMsg.db", "SnsMicroMsg.db-wal", "SnsMicroMsg.db-shm"):
                acct = parts[-2] if len(parts) >= 2 else "default"
                acct_dir = db_dir / acct
                acct_dir.mkdir(parents=True, exist_ok=True)
                fobj = tf.extractfile(m)
                if fobj is None:
                    continue
                dst = acct_dir / base
                dst.write_bytes(fobj.read())
                if base == "SnsMicroMsg.db":
                    found_dbs.append(dst)
    if not found_dbs:
        raise RuntimeError("tar 里没找到 SnsMicroMsg.db")
    found_dbs.sort(key=lambda p: p.stat().st_size, reverse=True)
    return found_dbs[0]
