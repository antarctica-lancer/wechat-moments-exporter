# -*- coding: utf-8 -*-
"""iOS 备份定位：从五花八门的备份里找到微信朋友圈数据库。

支持的输入形态
--------------
  1. iTunes / Finder 备份目录（Manifest.db + 两位哈希文件名）
  2. 已解出的 App 容器目录（iMazing 导出 App 数据 / 爱思 / 越狱导出）
     形如 Documents/<账号hash>/wc/wc005_008.db
  3. 上面任一种的 zip 打包
  4. 直接给 .db 文件

核心思路
--------
  先按「结构」找（Manifest.db / 已知路径），
  再按「内容」兜底（SQLite 头 + 存在 MyWC* 表）——这样任何工具导出的都能认。
"""
import atexit
import plistlib
import re
import shutil
import sqlite3
import tarfile
import tempfile
import zipfile
from pathlib import Path

WECHAT_DOMAIN = "AppDomain-com.tencent.mm"
CONTACT_NAME = "WCDB_Contact.sqlite"

# 朋友圈库文件名随微信版本变化：wc005_008.db / wc005_006.db / wc007_*.db …
# 所以按模式匹配，绝不写死具体编号。
DB_RE = re.compile(r"^wc\d*_\d+\.db$", re.I)


def _is_moments_name(name):
    return bool(DB_RE.match(name))


def _is_moments_file(name):
    """含 -wal / -shm 后缀也算。"""
    low = name.lower()
    for suf in ("-wal", "-shm"):
        if low.endswith(suf):
            name = name[:-len(suf)]
            break
    return _is_moments_name(name)


def _is_contact_file(name):
    """含 -wal / -shm 后缀也算。"""
    low = name.lower()
    for suf in ("-wal", "-shm"):
        if low.endswith(suf):
            name = name[:-len(suf)]
            break
    return name == CONTACT_NAME


def _is_wanted_file(base):
    return _is_contact_file(base) or _is_moments_file(base)


def _ro_connect(p):
    return sqlite3.connect(Path(p).resolve().as_uri() + "?mode=ro", uri=True)

_TEMP_DIRS = []


def _cleanup_temps():
    for d in _TEMP_DIRS:
        try:
            shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass


atexit.register(_cleanup_temps)


# ------------------------------------------------------------------ 探测

def _is_sqlite(p):
    try:
        with open(p, "rb") as f:
            return f.read(16).startswith(b"SQLite format 3")
    except OSError:
        return False


def _has_mywc(p):
    if not _is_sqlite(p):
        return False
    try:
        con = _ro_connect(p)
        n = con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name LIKE 'MyWC%'"
        ).fetchone()[0]
        con.close()
        return n > 0
    except sqlite3.Error:
        return False


def _stats(db):
    try:
        con = _ro_connect(db)
        tabs = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        myc = sum(1 for t in tabs if t.startswith("MyWC01_"))
        tl = 0
        if "MyWC_Timeline01" in tabs:
            tl = con.execute("SELECT COUNT(*) FROM MyWC_Timeline01").fetchone()[0]
        con.close()
        return len(tabs), myc, tl
    except sqlite3.Error:
        return 0, 0, 0


def _copy_with_wal(src, dst_dir):
    """连 -wal / -shm 一起复制，否则会丢掉最新数据。"""
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    shutil.copy2(src, dst)
    for suf in ("-wal", "-shm"):
        s = Path(str(src) + suf)
        if s.exists():
            shutil.copy2(s, dst_dir / (src.name + suf))
    return dst


# ------------------------------------------------------------------ Manifest 备份

def _accounts_from_manifest(manifest_path, backup_root, out_root):
    plist = Path(backup_root) / "Manifest.plist"
    if plist.exists():
        try:
            info = plistlib.loads(plist.read_bytes())
        except Exception:
            info = {}
        if info.get("IsEncrypted"):
            raise RuntimeError(
                "这个 iOS 备份是【加密】的，工具读不了。\n"
                "请重新做一个不勾选「加密本地备份」的备份。")

    con = _ro_connect(manifest_path)
    try:
        rows = con.execute(
            "SELECT fileID, relativePath FROM Files WHERE domain=? AND "
            "(relativePath LIKE '%wc%.db%' OR relativePath LIKE ?)",
            (WECHAT_DOMAIN, "%" + CONTACT_NAME + "%")).fetchall()
    except sqlite3.Error as e:
        con.close()
        raise RuntimeError("这个 iOS 备份的 Manifest.db 结构不认识（可能版本太老）：%s" % e)
    con.close()

    accounts = {}
    for file_id, rel in rows:
        rel_p = Path(str(rel).replace("\\", "/"))
        base = rel_p.name
        if not _is_wanted_file(base):
            continue
        src = Path(backup_root) / file_id[:2] / file_id
        if not src.exists():
            continue
        parts = rel_p.parts
        acct = parts[1] if len(parts) > 2 and parts[0] == "Documents" else "unknown"
        dst_dir = Path(out_root) / "ios" / acct / rel_p.parent.name
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / base
        shutil.copy2(src, dst)
        a = accounts.setdefault(acct, {"label": acct, "db": None, "contact": None})
        if base == CONTACT_NAME:
            a["contact"] = dst
        elif _is_moments_name(base):
            a["db"] = dst
    return [a for a in accounts.values() if a["db"]]


# ------------------------------------------------------------------ App 容器

def _accounts_from_container(root, out_root=None):
    root = Path(root)
    accounts = {}
    cands = set()
    for pat in ("wc*_*.db", "wc*.db"):
        cands.update(root.rglob(pat))
    for db in sorted(cands):
        if not _is_moments_name(db.name):
            continue
        if not _has_mywc(db):
            continue
        acct = db.parent.parent.name if db.parent.name == "wc" else db.parent.name
        contact = None
        for cand in (db.parent.parent / "DB" / CONTACT_NAME,
                     db.parent.parent / CONTACT_NAME):
            if cand.exists():
                contact = cand
                break
        accounts[acct] = {"label": acct, "db": db, "contact": contact}

    # 名字找不到 → 按内容找（覆盖改了文件名的各种导出工具）
    if not accounts:
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in (".db", ".sqlite", ".sqlite3", ""):
                continue
            try:
                if p.stat().st_size > 600 * 1024 * 1024:
                    continue
            except OSError:
                continue
            if _has_mywc(p):
                acct = p.parent.parent.name if p.parent.name == "wc" else p.parent.name
                accounts[acct] = {"label": acct, "db": p, "contact": None}
    return list(accounts.values())


# ------------------------------------------------------------------ zip

def _accounts_from_zip(zpath, out_root):
    out = Path(out_root) / "unzip"
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()

        # 情形 A：zip 里是 iTunes 备份（含 Manifest.db）→ 只解微信那几个文件
        man = [n for n in names if str(n).replace("\\", "/").endswith("Manifest.db")]
        if man:
            man_name = man[0]
            prefix = str(man_name).replace("\\", "/")[:-len("Manifest.db")]
            plist_name = prefix + "Manifest.plist"
            if plist_name in names:
                try:
                    info = plistlib.loads(zf.read(plist_name))
                except Exception:
                    info = {}
                if info.get("IsEncrypted"):
                    raise RuntimeError(
                        "这个 iOS 备份是【加密】的，工具读不了。\n"
                        "请重新做一个不勾选「加密本地备份」的备份。")
            man_path = out / "_Manifest.db"
            man_path.write_bytes(zf.read(man_name))
            con = _ro_connect(man_path)
            try:
                rows = con.execute(
                    "SELECT fileID, relativePath FROM Files WHERE domain=? AND "
                    "(relativePath LIKE '%wc%.db%' OR relativePath LIKE ?)",
                    (WECHAT_DOMAIN, "%" + CONTACT_NAME + "%")).fetchall()
            except sqlite3.Error as e:
                con.close()
                raise RuntimeError("zip 里的 Manifest.db 结构不认识（可能版本太老）：%s" % e)
            con.close()
            accounts = {}
            for file_id, rel in rows:
                rel_p = Path(str(rel).replace("\\", "/"))
                if not _is_wanted_file(rel_p.name):
                    continue
                member = prefix + file_id[:2] + "/" + file_id
                try:
                    data = zf.read(member)
                except KeyError:
                    continue
                parts = rel_p.parts
                acct = parts[1] if len(parts) > 2 and parts[0] == "Documents" else "unknown"
                dst_dir = out / "ios" / acct / rel_p.parent.name
                dst_dir.mkdir(parents=True, exist_ok=True)
                dst = dst_dir / rel_p.name
                dst.write_bytes(data)
                a = accounts.setdefault(acct, {"label": acct, "db": None, "contact": None})
                if rel_p.name == CONTACT_NAME:
                    a["contact"] = dst
                elif _is_moments_name(rel_p.name):
                    a["db"] = dst
            return [a for a in accounts.values() if a["db"]]

        # 情形 B：zip 里直接是 App 容器 → 只解需要的
        targets = [n for n in names
                   if _is_wanted_file(str(n).replace("\\", "/").rsplit("/", 1)[-1])]
        if not targets:
            targets = names
        for n in targets:
            try:
                zf.extract(n, out)
            except Exception:
                pass
    return locate_ios(out, out_root)


def _is_tar(p):
    try:
        with open(p, "rb") as f:
            h = f.read(512)
    except OSError:
        return False
    return h[257:262] == b"ustar" or h[:2] == b"\x1f\x8b"


def _accounts_from_tar(tpath, out_root):
    out = Path(out_root) / "untar"
    out.mkdir(parents=True, exist_ok=True)
    accounts = {}
    with tarfile.open(tpath, "r:*") as tf:
        for m in tf:
            if not m.isfile():
                continue
            n = m.name.replace("\\", "/")
            base = n.rsplit("/", 1)[-1]
            if not _is_wanted_file(base):
                continue
            parts = Path(n).parts
            acct = parts[-3] if len(parts) >= 3 and parts[-2] in ("wc", "DB") else "unknown"
            sub = "wc" if _is_moments_file(base) else "DB"
            dst_dir = out / "ios" / acct / sub
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / base
            dst.write_bytes(tf.extractfile(m).read())
            a = accounts.setdefault(acct, {"label": acct, "db": None, "contact": None})
            if base == CONTACT_NAME:
                a["contact"] = dst
            elif _is_moments_name(base):
                a["db"] = dst
    return [a for a in accounts.values() if a["db"]]


# ------------------------------------------------------------------ 统一入口

def locate_ios(source, workdir=None):
    """返回账号列表（按 Timeline 行数降序，已剔除空账号）。

    每项：{label, db, contact, tables, mywc, timeline}
    """
    src = Path(source)
    tmp = None
    if workdir is None:
        tmp = tempfile.mkdtemp(prefix="wxios_")
        _TEMP_DIRS.append(tmp)
        workdir = tmp

    try:
        accounts = []
        if src.is_file():
            if zipfile.is_zipfile(src):
                accounts = _accounts_from_zip(src, workdir)
            elif _is_tar(src):
                accounts = _accounts_from_tar(src, workdir)
            elif src.suffix.lower() in (".db", ".sqlite", ".sqlite3"):
                if _has_mywc(src):
                    accounts = [{"label": src.stem, "db": src, "contact": None}]
                elif _is_sqlite(src):
                    raise RuntimeError(
                        "这个 .db 不是 iOS 朋友圈库（没有 MyWC_* 表）。\n"
                        "  如果它是安卓的 SnsMicroMsg.db，请在第 1 步选「华为」或「我有数据库」。")
                else:
                    accounts = []
        elif src.is_dir():
            manifests = sorted(src.rglob("Manifest.db"))
            if manifests:
                accounts = _accounts_from_manifest(manifests[0], manifests[0].parent, workdir)
            else:
                accounts = _accounts_from_container(src, workdir)
                if not accounts:
                    zips = [p for p in src.rglob("*.zip") if p.is_file()]
                    if zips:
                        accounts = _accounts_from_zip(zips[0], workdir)

        result = []
        for a in accounts:
            tables, myc, tl = _stats(a["db"])
            if tl == 0 and myc <= 1:
                continue                      # 空账号（例如全 0 哈希那个占位）
            a.update(tables=tables, mywc=myc, timeline=tl)
            result.append(a)
        result.sort(key=lambda a: -a["timeline"])
        return result
    finally:
        pass


def describe(accounts):
    """给 TUI 用的一句话摘要。"""
    if not accounts:
        return "没找到微信朋友圈数据库"
    if len(accounts) == 1:
        a = accounts[0]
        return "找到 1 个微信账号（%d 条朋友圈记录）" % a["timeline"]
    return "找到 %d 个微信账号（主号 %d 条，其余 %s）" % (
        len(accounts), accounts[0]["timeline"],
        "/".join(str(a["timeline"]) for a in accounts[1:]))


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    accts = locate_ios(sys.argv[1])
    print(describe(accts))
    for a in accts:
        print("  %-34s db=%s" % (a["label"], a["db"]))
        print("  %-34s contact=%s" % ("", a["contact"]))
