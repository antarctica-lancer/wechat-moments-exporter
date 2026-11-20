# -*- coding: utf-8 -*-
"""朋友圈数据库扫描（安卓 SnsMicroMsg.db / iOS wc005_008.db）。

安卓：SnsInfo.content protobuf
  顶层 f2=wxid  f4=时间  f5=正文  f8=媒体容器
  f8 内 f5(repeated)=媒体项：f4=URL/0  f5=加密  f6=URL/1508  f27=token  f29=key

iOS：MyWC_Timeline01 / MyWC_DataItem01 的 Buffer
  顶层 field 2=Id 5=wxid 6=昵称 7=时间 23=正文
  媒体项用正则从原始字节抠（已验证实现）
"""
import datetime as dt
import re
import sqlite3
from pathlib import Path

TZ = dt.timezone(dt.timedelta(hours=8))


def _ro_connect(db_path):
    return sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)

# ---------------------------------------------------------------- protobuf

def _rv(buf, i):
    r = s = 0
    while True:
        if i >= len(buf):
            raise ValueError
        c = buf[i]
        i += 1
        r |= (c & 0x7F) << s
        if not c & 0x80:
            return r, i
        s += 7


def _parse(buf):
    out = []
    i = 0
    while i < len(buf):
        try:
            tag, i = _rv(buf, i)
            f, wt = tag >> 3, tag & 7
            if f == 0:
                break
            if wt == 0:
                v, i = _rv(buf, i)
            elif wt == 2:
                ln, i = _rv(buf, i)
                v = buf[i:i + ln]
                i += ln
            elif wt == 5:
                v = buf[i:i + 4]
                i += 4
            elif wt == 1:
                v = buf[i:i + 8]
                i += 8
            else:
                break
            out.append((f, wt, v))
        except (ValueError, IndexError):
            break
    return out


def _txt(b):
    try:
        return b.decode("utf-8", "replace")
    except Exception:
        return ""


# ---------------------------------------------------------------- 安卓

def scan_android(db_path):
    """返回 {wxid: [post, ...]}，post = {snsId,time,text,media[]}"""
    con = _ro_connect(db_path)
    rows = con.execute(
        "SELECT snsId, userName, createTime, content FROM SnsInfo "
        "WHERE content IS NOT NULL").fetchall()
    con.close()

    people = {}
    for sns_id, user, ctime, content in rows:
        top = _parse(bytes(content))
        if not user:
            for f, wt, v in top:
                if f == 2 and wt == 2:
                    user = _txt(v).strip()
                    break
        if not user:
            continue
        ts = ctime
        text = ""
        for f, wt, v in top:
            if f == 4 and wt == 0:
                ts = v
                break
        for f, wt, v in top:
            if f == 5 and wt == 2:
                text = _txt(v)
                break

        media = []
        f8 = [v for f, wt, v in top if f == 8 and wt == 2]
        if f8:
            for f, wt, v in _parse(f8[0]):
                if f != 5 or wt != 2:
                    continue
                fl = {}
                for ff, ww, vv in _parse(v):
                    fl.setdefault(ff, vv)
                url = fl.get(4)
                if not url:
                    continue
                u = _txt(url)
                if "qpic" not in u:
                    continue
                media.append({
                    "url": u,
                    "url_thumb": _txt(fl.get(6) or b""),
                    "token": _txt(fl.get(27) or b""),
                    "key": _txt(fl.get(29) or b""),
                    "enc": fl.get(5) == 1,
                })

        t = dt.datetime.fromtimestamp(ts, TZ) if ts else dt.datetime(1970, 1, 1, tzinfo=TZ)
        people.setdefault(user, []).append({
            "snsId": str(sns_id), "time": t, "text": text, "media": media,
        })

    for v in people.values():
        v.sort(key=lambda p: p["time"])
    return people


# ---------------------------------------------------------------- iOS

_URL_RE = re.compile(rb"(https?)://([a-z0-9]*)mmsns\.qpic\.cn/mmsns/([A-Za-z0-9_-]+)/(\d+)")
_MD5_RE = re.compile(rb"\x1a\x20([0-9a-f]{32})")


def _ios_media(buf):
    """从 iOS Buffer 抠媒体项。

    媒体项字段顺序（本库实测）：f1=url  f2=type  f3=md5(可能为空)  f4=token  f5=enc  f6=key
    注意：/0 原图嵌在 /150 那一项的 f13 里，所以 URL 正则能同时命中两条。
    关键：不能假设「md5 后紧跟 key」——必须从 URL 之后按 protobuf 顺序解析。
    """
    out = []
    for m in _URL_RE.finditer(buf):
        scheme = m.group(1).decode()
        host, fid, size = m.group(2).decode(), m.group(3).decode(), m.group(4).decode()
        tail = buf[m.end(): m.end() + 600]
        d = {}
        for f, wt, v in _parse(tail):
            if f == 1:          # 下一个媒体项的 url → 本项到此结束
                break
            d.setdefault(f, v)
        token = _txt(d.get(4) or b"")
        key = d.get(6)
        enc = d.get(5)
        # enc == 1 → 加密（token+key 解）；-1/0/缺失 → 公开图（token 多为空，裸 URL 直接取）
        out.append({
            "url": "%s://%smmsns.qpic.cn/mmsns/%s/%s" % (scheme, host, fid, size),
            "url_thumb": "",
            "token": token,
            "key": str(key) if isinstance(key, int) else "",
            "enc": (enc == 1) if isinstance(enc, int) else True,
            "size": size,
            "md5": _txt(d.get(3) or b""),
        })
    # 同一 fileid 优先 /0 原图
    best = {}
    for it in out:
        fid = it["url"].split("/mmsns/")[1].split("/")[0]
        if fid not in best or (it["size"] == "0" and best[fid]["size"] != "0"):
            best[fid] = it
    return list(best.values())


def _merge_media(old_media, new_media):
    """合并同一条动态在不同表里的媒体项（按 fileid 去重，优先 /0 原图与带 token 项）。"""
    best = {}
    for it in list(old_media) + list(new_media):
        u = it.get("url") or ""
        fid = u.split("/mmsns/")[1].split("/")[0] if "/mmsns/" in u else u
        cur = best.get(fid)
        if cur is None:
            best[fid] = it
        else:
            old_score = (cur.get("size") == "0", bool(cur.get("token")), bool(cur.get("key")))
            new_score = (it.get("size") == "0", bool(it.get("token")), bool(it.get("key")))
            if new_score > old_score:
                best[fid] = it
    return list(best.values())


def scan_ios(db_path):
    con = _ro_connect(db_path)
    tabs = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    posts = {}
    scan_tables = ["MyWC_Timeline01", "MyWC_DataItem01"] + \
                  sorted(t for t in tabs if t.startswith("MyWC01_"))
    for table in scan_tables:
        if table not in tabs:
            continue
        try:
            rows = con.execute('SELECT Id, Buffer FROM "%s"' % table).fetchall()
        except sqlite3.Error:
            continue
        for pid, buf in rows:
            if not buf:
                continue
            b = bytes(buf)
            top = _parse(b)
            d = {}
            for f, wt, v in top:
                d.setdefault(f, v)
            wxid = _txt(d.get(5) or b"")
            if not wxid:
                continue
            ts = d.get(7) if isinstance(d.get(7), int) else 0
            text = _txt(d.get(23) or b"") if isinstance(d.get(23), (bytes, bytearray)) else ""
            t = dt.datetime.fromtimestamp(ts, TZ) if ts else dt.datetime(1970, 1, 1, tzinfo=TZ)
            media = _ios_media(b)
            key = str(pid)
            old = posts.get(key)
            if old is None:
                posts[key] = {
                    "snsId": key, "time": t, "text": text,
                    "media": media, "wxid": wxid,
                }
            else:
                if old["time"].year == 1970 and ts:
                    old["time"] = t
                if len(text) > len(old["text"]):
                    old["text"] = text
                old["media"] = _merge_media(old["media"], media)
    con.close()

    people = {}
    for p in posts.values():
        people.setdefault(p["wxid"], []).append(p)
    for v in people.values():
        v.sort(key=lambda p: p["time"])
    return people


def load_ios_contacts(contact_db):
    """WCDB_Contact.sqlite -> {wxid: {remark, nick, py, wechat, type, friend}}

    好友判定：`type & 1`（微信自己的好友位）**或** 有备注名。
    实测：type=4 的记录数量最多、且几乎都没有备注，属于「只是看过朋友圈的陌生人」；
    真正的联系人（有备注或已互加好友）都是 type&1=1。
    """
    con = _ro_connect(contact_db)
    out = {}
    try:
        for un, blob, ctype in con.execute(
                "SELECT userName, dbContactRemark, type FROM Friend"):
            d = {}
            for f, wt, v in _parse(blob or b""):
                if wt == 2:
                    d.setdefault(f, _txt(v))
            remark = (d.get(3) or "").strip()
            out[un] = {
                "remark": remark,
                "nick": (d.get(1) or "").strip(),
                "py": (d.get(4) or d.get(6) or "").strip(),
                "wechat": (d.get(2) or "").strip(),
                "type": ctype,
                "friend": bool((ctype or 0) & 1) or bool(remark),
            }
    finally:
        con.close()
    return out


_IOS_DB_RE = re.compile(r"^wc\d*_\d+\.db$", re.I)


def find_ios_db(root):
    """在目录里递归找朋友圈库（不同微信版本编号不同），返回最大的那个。"""
    hits = []
    for pat in ("wc*_*.db", "wc*.db"):
        hits += [p for p in Path(root).rglob(pat) if p.is_file()]
    seen, uniq = set(), []
    for h in hits:
        if not _IOS_DB_RE.match(h.name):
            continue
        r = str(h.resolve())
        if r not in seen:
            seen.add(r)
            uniq.append(h)
    uniq.sort(key=lambda p: p.stat().st_size, reverse=True)
    return uniq[0] if uniq else None


def find_ios_contact_db(ios_db_path):
    """在 wc005_008.db 附近找 WCDB_Contact.sqlite。"""
    p = Path(ios_db_path).resolve()
    for up in (p.parent.parent / "DB" / "WCDB_Contact.sqlite",
               p.parent / "DB" / "WCDB_Contact.sqlite",
               p.parent / "WCDB_Contact.sqlite"):
        if up.exists():
            return up
    hits = list(p.parent.parent.rglob("WCDB_Contact.sqlite"))
    return hits[0] if hits else None


def find_contact_dbs(path):
    """找出路径下所有 iOS 通讯录库（多账号时会有多份，全部返回）。"""
    p = Path(path)
    if p.is_file():
        return [p] if p.name.lower() == "wcdb_contact.sqlite" else []
    if p.is_dir():
        return sorted(p.rglob("WCDB_Contact.sqlite"),
                      key=lambda x: x.stat().st_size, reverse=True)
    return []


def find_contact_db(path):
    """兼容用：只返回最大的一份。"""
    hits = find_contact_dbs(path)
    return hits[0] if hits else None


# ---------------------------------------------------------------- 类型识别

def detect_kind(path):
    """返回 'android_db' / 'ios_db' / 'hisuite' / 'ios_backup' / None"""
    p = Path(path)
    if p.is_file():
        if p.suffix.lower() in (".db", ".sqlite"):
            try:
                con = _ro_connect(p)
                tabs = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                con.close()
                if "SnsInfo" in tabs:
                    return "android_db"
                if any(t.startswith("MyWC") for t in tabs):
                    return "ios_db"
            except sqlite3.Error:
                return None
        return None
    if p.is_dir():
        if (p / "info.xml").exists() or (p / "backupinfo.ini").exists():
            return "hisuite"
        if find_ios_db(p) is not None:
            return "ios_backup"
    return None
