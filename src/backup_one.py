# -*- coding: utf-8 -*-
"""命令行单人物导出。

用法：
    python backup_one.py <wxid> <输出目录> <库路径>

库路径可以是安卓的 SnsMicroMsg.db，也可以是 iOS 的 wc005_*.db，自动识别。
"""
import sys
from pathlib import Path

import downloader
import naming
import sns_db


def main():
    if len(sys.argv) < 4:
        print("用法: backup_one.py <wxid> <输出目录> <库路径>")
        print("  库路径: 安卓 SnsMicroMsg.db，或 iOS wc005_*.db")
        return 1

    wxid = sys.argv[1]
    out = Path(sys.argv[2])
    db = sys.argv[3]

    kind = sns_db.detect_kind(db)
    if kind == "android_db":
        people = sns_db.scan_android(db)
    elif kind == "ios_db":
        people = sns_db.scan_ios(db)
    else:
        print("认不出这个库：%s" % db)
        return 1

    posts = people.get(wxid)
    if not posts:
        print("没有找到 %s 的动态" % wxid)
        return 1

    total = sum(len(p["media"]) for p in posts)
    print("%s: %d 帖 / %d 图 -> %s" % (wxid, len(posts), total, out))

    def prog(done, n, nb, el, fail, skip):
        sp = nb / el / 1024 if el > 0.3 else 0
        sys.stdout.write("\r  %d/%d  %.1fMB  %.0fKB/s  失败%d   " % (done, n, nb / 1048576, sp, fail))
        sys.stdout.flush()

    r = downloader.download_person(posts, out, maxlen=naming.maxlen_for(out), on_progress=prog)
    print("\n完成: 成功 %d / 跳过 %d / 失败 %d，共 %.1f MB，用时 %.0f 秒"
          % (r["ok"], r["skip"], r["fail"], r["bytes"] / 1048576, r["seconds"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
