# -*- coding: utf-8 -*-
"""微信朋友圈图片导出 —— 六步交互式工具（纯标准库）。"""
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import android_prep
import downloader
import ios_find
import naming
import sns_db
import ui

PAGE = 40


def _writable(d):
    try:
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".wxtest")
        os.close(fd)
        os.remove(tmp)
        return True
    except OSError:
        return False


def default_out_dir():
    """挑一个真的存在、能写、剩余空间最大的盘。返回 (路径, 剩余字节)。"""
    cands = []
    for letter in "DEFGHIJKC":
        root = letter + ":\\"
        if not os.path.isdir(root):
            continue
        try:
            free = shutil.disk_usage(root).free
        except OSError:
            continue
        if free < 5 * 1024 ** 3:
            continue
        cands.append((free, root))
    cands.sort(reverse=True)
    for free, root in cands:
        if _writable(root):
            return os.path.join(root, "朋友圈导出"), free
    home = os.path.join(os.path.expanduser("~"), "Desktop")
    if not os.path.isdir(home):
        home = os.path.expanduser("~")
    try:
        free = shutil.disk_usage(home).free
    except OSError:
        free = 0
    return os.path.join(home, "朋友圈导出"), free


def clean_path(s):
    s = (s or "").strip().strip('"').strip("'").strip()
    return s


_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


def safe_folder(name, fallback):
    n = re.sub(r'[\\/:*?"<>|]', "_", (name or "").strip()).strip(" .")
    n = n[:40].rstrip(" .")
    if not n:
        return fallback
    if n.upper() in _WIN_RESERVED:
        n += "_"
    return n


def build_rows(people, names):
    rows = []
    for w, posts in people.items():
        imgs = sum(len(p["media"]) for p in posts)
        if imgs == 0:
            continue
        info = names.get(w, {})
        rows.append({
            "wxid": w,
            "imgs": imgs,
            "posts": len(posts),
            "tmin": min(p["time"] for p in posts),
            "tmax": max(p["time"] for p in posts),
            "label": info.get("remark") or "",
            "nick": info.get("nick") or "",
            "friend": bool(info.get("friend")),
            "known": w in names,
        })
    rows.sort(key=lambda r: -r["imgs"])
    return rows


def render_list(rows, page, has_names, note, total_imgs):
    ui.clear()
    ui.title("第 4 步 / 共 6 步：扫描朋友圈")
    print("  %s" % note)
    print("  共 %d 个发布者 · %d 张图片" % (len(rows), total_imgs))
    if rows:
        print("  时间跨度 %s ~ %s" % (
            min(r["tmin"] for r in rows).strftime("%Y-%m-%d"),
            max(r["tmax"] for r in rows).strftime("%Y-%m-%d")))
    print()

    start = page * PAGE
    chunk = rows[start:start + PAGE]
    if has_names:
        ui.table(
            ["编号", "备注名", "昵称", "关系", "图片数", "时间跨度"],
            [(start + i + 1, ui.cut(r["label"], 14) or "（无备注）",
              ui.cut(r["nick"] or r["wxid"], 14),
              "好友" if r.get("friend") else "非好友",
              r["imgs"],
              "%s~%s" % (r["tmin"].strftime("%Y-%m"), r["tmax"].strftime("%Y-%m")))
             for i, r in enumerate(chunk)],
            aligns=["right", "left", "left", "left", "right", "left"])
    else:
        ui.table(
            ["编号", "wxid", "图片数", "帖数", "时间跨度"],
            [(start + i + 1, r["wxid"], r["imgs"], r["posts"],
              "%s~%s" % (r["tmin"].strftime("%Y-%m"), r["tmax"].strftime("%Y-%m")))
             for i, r in enumerate(chunk)],
            aligns=["right", "left", "right", "right", "left"])
    print()
    print(ui.c("  第 %d-%d 条 / 共 %d 条" % (start + 1, start + len(chunk), len(rows)), "90"))


def pick_person(rows, has_names, note, total_imgs):
    page = 0
    pages = max(1, (len(rows) + PAGE - 1) // PAGE)
    while True:
        render_list(rows, page, has_names, note, total_imgs)
        s = ui.ask("输入编号选择（回车下一页 / b 上一页 / q 退出）").strip().lower()
        if s == "q":
            return None
        if s == "":
            page = (page + 1) % pages
            continue
        if s in ("b", "p"):
            page = (page - 1) % pages
            continue
        if s.isdigit():
            n = int(s)
            if 1 <= n <= len(rows):
                return rows[n - 1]
            print(ui.c("  编号不在 1-%d 范围内" % len(rows), "33"))
        else:
            print(ui.c("  看不懂这个输入，可以输编号 / 回车 / b / q", "33"))
        ui.pause()


def confirm_person(row, people):
    posts = people[row["wxid"]]
    ui.clear()
    ui.title("第 5 步 / 共 6 步：确认这个人")
    print("  你选的是：%s" % (row["label"] or row["wxid"]))
    if row["label"]:
        print("  wxid：%s" % row["wxid"])
    print("  %d 条动态 · %d 张图 · %s ~ %s" % (
        len(posts), row["imgs"],
        row["tmin"].strftime("%Y-%m-%d"), row["tmax"].strftime("%Y-%m-%d")))
    print()
    print("  最近 20 条动态（帮你确认是不是这个人）：")
    recent = list(reversed(posts))[:20]
    for p in recent:
        t = (p.get("text") or "").replace("\n", " ").strip()
        if t:
            print("    [%s] %s" % (p["time"].strftime("%Y-%m-%d"), ui.cut(t, 42)))
        else:
            print("    [%s] （无文字，%d 张图）" % (
                p["time"].strftime("%Y-%m-%d"), len(p["media"])))
    if len(posts) > 20:
        print(ui.c("    …… 更早的 %d 条略" % (len(posts) - 20), "90"))
    print()
    return ui.ask_yn("确认导出这个人？", "y")


def do_download(row, people, out_root):
    posts = people[row["wxid"]]
    folder = safe_folder(row["label"] or row["nick"], row["wxid"])
    out_dir = Path(out_root) / folder
    maxlen = naming.maxlen_for(out_dir)

    if len(str(out_dir)) > 170:
        print(ui.c("  注意：保存路径较长，文件名里的文字会自动缩短", "33"))
    est = row["imgs"] * 170 * 1024
    try:
        free = shutil.disk_usage(str(out_dir.anchor or out_dir)).free
    except OSError:
        free = None
    if free is not None and free < est * 1.2:
        print(ui.c("  注意：%s 只剩 %.1f GB，这次预计需要 %.1f GB" % (
            out_dir.anchor, free / 1024 ** 3, est / 1024 ** 3), "33"))
        if not ui.ask_yn("  空间可能不够，还继续吗？", "n"):
            return

    print()
    print("  保存到：%s" % out_dir)
    print("  开始下载 %d 张…" % row["imgs"])
    print()

    def on_progress(done, total, nbytes, elapsed, fail, skip):
        now = elapsed
        speed = nbytes / now if now > 0.5 else 0
        eta = (total - done) * (now / done) if done else 0
        line = "%s %d/%d  %s/s  %s  剩 %s  失败%d" % (
            ui.bar(done, total), done, total, ui.human_size(speed),
            ui.human_time(now), ui.human_time(eta), fail)
        sys.stdout.write("\r" + line + "   ")
        sys.stdout.flush()

    r = downloader.download_person(posts, out_dir, maxlen=maxlen, on_progress=on_progress)
    print()
    print()
    print(ui.c("  完成！成功 %d 张 · 跳过 %d · 失败 %d" % (r["ok"], r["skip"], r["fail"]), "1;32"))
    print("  用时 %s · 共 %s" % (ui.human_time(r["seconds"]), ui.human_size(r["bytes"])))
    print("  → %s" % out_dir)
    if r["fail"]:
        print(ui.c("  失败清单：%s" % (out_dir / "_failures.txt"), "33"))


def scan_source(kind, src, password, out_root):
    if kind == "1":
        accts = ios_find.locate_ios(src)
        if not accts:
            raise RuntimeError("没在这个备份里找到微信朋友圈数据库。\n"
                               "可能原因：备份加密了 / 备份里没有微信 / 选错了文件夹。")
        acct = accts[0]
        if len(accts) > 1:
            print()
            print("  这个备份里有 %d 个微信账号：" % len(accts))
            for i, a in enumerate(accts, 1):
                print("    %d) 账号 %s…  %d 条记录" % (i, a["label"][:14], a["timeline"]))
            pick = ui.ask("  选哪个", "1")
            try:
                idx = int(pick)
                acct = accts[idx - 1] if 1 <= idx <= len(accts) else accts[0]
            except Exception:
                acct = accts[0]
        people = sns_db.scan_ios(acct["db"])
        # 合并所有账号的通讯录：只要在任一账号里是好友，就算好友
        names = {}
        for a in accts:
            if not a.get("contact"):
                continue
            try:
                for k, v in sns_db.load_ios_contacts(a["contact"]).items():
                    old = names.get(k)
                    if (old is None
                            or (not old.get("friend") and v.get("friend"))
                            or (not old.get("remark") and v.get("remark"))):
                        names[k] = v
            except Exception:
                pass
        note = "来源：iOS 备份 · 账号 %s…" % acct["label"][:14]
        return people, names, note

    if kind == "2":
        print()
        print("  开始解密华为备份（首次会比较慢，8.5 GB 要解一会）…")
        print()
        cnt = [0]

        def on_line(s):
            cnt[0] += 1
            low = s.lower()
            if cnt[0] % 10 == 0 or "merged" in low or "error" in low or "fail" in low:
                print("    " + s[:100])

        db = android_prep.decrypt_and_extract(
            src, password, Path(out_root) / "_work", on_line=on_line)
        people = sns_db.scan_android(db)
        return people, {}, "来源：华为备份（通讯录加密，无法区分好友）"

    k = sns_db.detect_kind(src)
    if k == "android_db":
        return sns_db.scan_android(src), {}, "来源：安卓朋友圈数据库（无通讯录，无法区分好友）"
    if k == "ios_db":
        names = {}
        cdb = sns_db.find_ios_contact_db(src)
        if cdb:
            names = sns_db.load_ios_contacts(cdb)
        return sns_db.scan_ios(src), names, "来源：iOS 朋友圈数据库"
    raise RuntimeError("认不出这个文件是什么库（既不像安卓也不像 iOS）")


def choose_group(rows):
    """让用户选择要看哪一组：全部 / 只好友 / 只非好友。"""
    fr = [r for r in rows if r.get("friend")]
    non = [r for r in rows if not r.get("friend")]
    if not non:
        return rows                      # 全是好友，不用问

    print()
    print("  按关系分两组：")
    print("    好友      %3d 人 · %d 张" % (len(fr), sum(r["imgs"] for r in fr)))
    print("    非好友    %3d 人 · %d 张" % (len(non), sum(r["imgs"] for r in non)))
    print(ui.c("    （非好友 = 你只是看过他/她的朋友圈，并不是好友）", "90"))
    print()
    print("  看哪一组？")
    print("    1) 全部")
    print("    2) 只看好友")
    print("    3) 只看非好友")
    print()
    c = ui.ask("输入编号", "1")
    if c == "2":
        return fr
    if c == "3":
        return non
    return rows


def run():
    ui.clear()
    ui.title("微信朋友圈图片导出")
    print("  只读取你自己的备份，不修改任何原始文件。")
    print("  按 Ctrl+C 可随时退出。")

    # 第 1 步
    ui.title("第 1 步 / 共 6 步：选择备份类型")
    print("  1) 苹果 iPhone 备份（iTunes / iMazing / 爱思 等，文件夹或 zip 都行）")
    print("  2) 华为备份（HiSuite / EMUI / HarmonyOS 2-4）")
    print(ui.c("     ※ 不支持 HarmonyOS NEXT（纯血鸿蒙）", "90"))
    print("  3) 我已经有朋友圈数据库文件（.db）")
    print()
    kind = ui.ask_choice("输入编号回车", ("1", "2", "3"))

    # 第 2 步
    ui.title("第 2 步 / 共 6 步：指定备份位置")
    if kind == "3":
        print("  请把 .db 文件拖进来（或粘贴完整路径）")
        print(ui.c("  支持 SnsMicroMsg.db（安卓）和 wc005_008.db（iOS）", "90"))
    else:
        print("  请把备份文件夹拖进来（或粘贴完整路径）")
        print(ui.c("  zip 文件也可以直接拖", "90"))
    print()
    src = clean_path(ui.ask(">"))
    if not src or not Path(src).exists():
        raise RuntimeError("这个路径不存在：%s" % src)

    password = ""
    if kind == "2":
        ok, msg = android_prep.validate_backup(src)
        print("  " + (ui.c("✓ ", "32") + msg if ok else ui.c("✗ " + msg, "31")))
        if not ok:
            raise RuntimeError("备份目录不对")
        password = ui.ask_password("  这个备份的密码")

    # 第 3 步
    ui.title("第 3 步 / 共 6 步：选择下载位置")
    dflt, dflt_free = default_out_dir()
    print("  图片保存到哪里？直接回车用默认。")
    print(ui.c("  默认：%s（该盘剩余 %.0f GB）" % (dflt, dflt_free / 1024 ** 3), "90"))
    print()
    out_root = clean_path(ui.ask(">")) or dflt
    try:
        Path(out_root).mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RuntimeError("这个目录建不了：%s\n%s" % (out_root, e))
    print("  " + ui.c("✓ ", "32") + "保存到 %s" % out_root)

    # 第 4 步（扫描）
    people, names, note = scan_source(kind, src, password, out_root)
    rows = build_rows(people, names)
    if not rows:
        raise RuntimeError("扫描完成，但没有找到任何带图片的朋友圈")
    if names:
        rows = choose_group(rows)
        if not rows:
            raise RuntimeError("这一组里没有人")
    total_imgs = sum(r["imgs"] for r in rows)
    has_names = bool(names)

    # 选人 → 确认 → 下载（可循环）
    while True:
        row = pick_person(rows, has_names, note, total_imgs)
        if row is None:
            print("\n  已退出。")
            return
        if not confirm_person(row, people):
            continue
        do_download(row, people, out_root)
        print()
        if not ui.ask_yn("继续导出下一个？", "n"):
            print("\n  全部完成。")
            return


def main():
    ui.enable_ansi()
    try:
        run()
    except KeyboardInterrupt:
        print("\n\n  已取消。")
    except EOFError:
        print("\n\n  输入结束，已退出。")
    except Exception as e:
        print("\n\n" + ui.c("  出错了：", "31") + str(e))
        try:
            input("  按回车退出")
        except Exception:
            pass


if __name__ == "__main__":
    main()
