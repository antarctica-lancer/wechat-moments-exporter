# -*- coding: utf-8 -*-
"""下载 + 解密 + 按规则重命名。

- 加密图（有 token+key）：URL?token=..&idx=1 → ISAAC-64 解密
- 公开图（无 token）：直接 GET，并把 /300 /1508 提升到 /0 取原图
- 429 指数退避；_manifest.json 断点续传；_failures.txt 记录失败
"""
import hashlib
import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

import isaac64
import naming

UA = "MicroMessenger Client"
MIN_GAP = 0.45
JITTER = 0.25
BACKOFF_BASE = 15
MAX_RETRY = 4


def _fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(req, timeout=timeout) as r:
        return r.read(), (r.headers.get("x-Enc") or "").strip()


def _promote(url):
    """把 /300 /1508 之类提升到 /0 取原图。"""
    return re.sub(r"/(\d+)(?=\?|$)", "/0", url)


def _stamp(path, ts):
    """把文件系统时间设成朋友圈发布时间（修改/访问时间 + Windows 创建时间）。"""
    try:
        os.utime(str(path), (ts, ts))
    except OSError:
        pass
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.HANDLE]
        k32.SetFileTime.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                    ctypes.c_void_p, ctypes.c_void_p]
        h = k32.CreateFileW(str(path), 0x100, 0, None, 3, 0x02000000, None)
        if not h or h in (-1, 0xFFFFFFFFFFFFFFFF):
            return
        ft = int((ts + 11644473600) * 10_000_000)     # Unix → FILETIME(1601)
        ftime = wintypes.FILETIME(ft & 0xFFFFFFFF, ft >> 32)
        k32.SetFileTime(h, ctypes.byref(ftime), None, None)
        k32.CloseHandle(h)
    except Exception:
        pass


def _save_manifest(man_p, manifest):
    """原子写入 manifest，防止中途中断导致文件损坏。"""
    tmp = man_p.with_name(man_p.name + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, man_p)


def download_person(posts, out_dir, maxlen=40, on_progress=None, stop=None):
    """下载一个人的全部图片。返回统计 dict。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    man_p = out_dir / "_manifest.json"
    fail_p = out_dir / "_failures.txt"

    manifest = {}
    if man_p.exists():
        try:
            manifest = json.loads(man_p.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}

    texts = naming.plan_texts(posts, maxlen)

    tasks = []
    for p, tx in zip(posts, texts):
        ds = p["time"].strftime("%y%m%d")
        ts = p["time"].timestamp()
        for idx, m in enumerate(p["media"], 1):
            # 存「不含后缀的主体」，等嗅探出真实格式后再拼 .jpg/.png/.webp/.gif
            tasks.append((("%s_%d" % (p["snsId"], idx)),
                          naming.stem(p, tx, idx), m, ds, idx, ts))

    total = len(tasks)
    done = skip = fail = 0
    nbytes = 0
    seen = {v["md5"] for v in manifest.values()
            if isinstance(v, dict) and v.get("md5")}
    failures = []
    last_req = [0.0]
    t0 = time.time()

    def gap():
        w = MIN_GAP - (time.time() - last_req[0])
        w += JITTER * (0.5 - random.random())
        if w > 0:
            time.sleep(w)
        last_req[0] = time.time()

    def tick():
        if on_progress:
            on_progress(done, total, nbytes, time.time() - t0, fail, skip)

    for key, stem_name, m, ds, idx, ts in tasks:
        if stop and stop():
            break
        entry = manifest.get(key)
        if entry is not None:
            fname = entry.get("file") if isinstance(entry, dict) else None
            if not fname or (out_dir / fname).exists():
                skip += 1
                done += 1
                tick()
                continue

        token = (m.get("token") or "").strip()
        keytxt = (m.get("key") or "").strip()
        enc = bool(m.get("enc")) and bool(token) and bool(keytxt)

        if enc:
            urls = [m["url"] + "?token=" + token + "&idx=1"]
        else:
            bases = []
            if m.get("url_thumb"):
                bases.append(m["url_thumb"])
            bases.append(m["url"])
            urls = []
            for b in bases:
                if token:
                    urls.append(b + "?token=" + token + "&idx=1")
                urls.append(_promote(b))
                urls.append(b)
            # 去重保序
            seen_u = set()
            uniq = []
            for u in urls:
                if u not in seen_u:
                    seen_u.add(u)
                    uniq.append(u)
            urls = uniq

        raw = None
        ext = None
        used = None
        for u in urls:
            for attempt in range(1, MAX_RETRY + 1):
                gap()
                try:
                    data, xenc = _fetch(u)
                except urllib.error.HTTPError as e:
                    if e.code == 429:
                        time.sleep(BACKOFF_BASE * attempt)
                        continue
                    if 500 <= e.code < 600 and attempt < MAX_RETRY:
                        time.sleep(2 * attempt)
                        continue
                    break
                except (urllib.error.URLError, TimeoutError, OSError):
                    if attempt < MAX_RETRY:
                        time.sleep(2 * attempt)
                        continue
                    break
                except Exception:
                    break

                ext = isaac64.sniff(data)
                if not ext and (enc or xenc == "1") and keytxt:
                    try:
                        data = isaac64.decrypt(data, int(keytxt))
                        ext = isaac64.sniff(data)
                    except Exception:
                        ext = None
                if ext:
                    raw = data
                    used = u
                    break
            if ext:
                break

        if not ext or not raw:
            fail += 1
            done += 1
            failures.append((key, urls[0] if urls else "", "all-fail"))
            tick()
            continue

        h = hashlib.md5(raw).hexdigest()
        if h in seen:
            manifest[key] = {"skip": "dup", "md5": h}
            skip += 1
            done += 1
            tick()
            continue
        seen.add(h)

        final = out_dir / ("%s.%s" % (stem_name, ext))
        if final.exists():
            final = out_dir / ("%s_%s.%s" % (stem_name, h[:4], ext))
        try:
            final.write_bytes(raw)
        except OSError:
            # 路径过长 / 文件名非法时，退化成短名（日期_哈希_编号）
            final = out_dir / ("%s_%s_%02d.%s" % (ds, h[:6], idx, ext))
            final.write_bytes(raw)
        _stamp(final, ts)

        nbytes += len(raw)
        manifest[key] = {"file": final.name, "size": len(raw), "md5": h, "url": used}
        done += 1
        if done % 10 == 0:
            _save_manifest(man_p, manifest)
        tick()

    _save_manifest(man_p, manifest)
    if failures:
        fail_p.write_text("\n".join("%s|%s|%s" % f for f in failures), encoding="utf-8")
    elif fail_p.exists():
        try:
            fail_p.unlink()
        except OSError:
            pass

    # 附带一份清单
    try:
        with (out_dir / "_list.csv").open("w", encoding="utf-8-sig", newline="") as f:
            f.write("时间,正文,图片数\n")
            for p, tx in zip(posts, texts):
                txt = (p.get("text") or "").replace("\n", " ").replace('"', '""')
                f.write('%s,"%s",%d\n' % (p["time"].strftime("%Y-%m-%d %H:%M"), txt, len(p["media"])))
    except OSError:
        pass

    return {
        "ok": done - skip - fail, "skip": skip, "fail": fail,
        "total": total, "bytes": nbytes, "seconds": time.time() - t0,
    }
