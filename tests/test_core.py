# -*- coding: utf-8 -*-
"""核心模块单元测试（纯 Python 标准库）。"""
import datetime as dt
import json
import plistlib
import sqlite3
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import android_prep
import downloader
import ios_find
import isaac64
import main as main_mod
import naming
import sns_db


def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _pb_bytes(field, data):
    return _varint((field << 3) | 2) + _varint(len(data)) + data


def _pb_int(field, val):
    return _varint((field << 3) | 0) + _varint(val)


class TestIsaac64(unittest.TestCase):
    def test_decrypt_roundtrip_and_sniff(self):
        key = 12345678901234567
        plain_jpg = b"\xff\xd8\xff\xe0" + b"payload-bytes" * 4
        enc = isaac64.decrypt(plain_jpg, key)
        self.assertNotEqual(enc, plain_jpg)
        self.assertIsNone(isaac64.sniff(enc))
        dec = isaac64.decrypt(enc, key)
        self.assertEqual(dec, plain_jpg)
        self.assertEqual(isaac64.sniff(dec), "jpg")
        self.assertEqual(isaac64.sniff(b"\x89PNG\r\n\x1a\n"), "png")
        self.assertEqual(isaac64.sniff(b"RIFF\x00\x00\x00\x00WEBP"), "webp")
        self.assertEqual(isaac64.sniff(b"GIF89a"), "gif")


class TestNaming(unittest.TestCase):
    def test_emoji_truncation_to_empty(self):
        s = "\U0001F600" * 45 + "abc"
        self.assertEqual(naming.clean_text(s, maxlen=40), "")

    def test_safe_folder_windows_reserved(self):
        self.assertEqual(main_mod.safe_folder("CON", "fb"), "CON_")
        self.assertEqual(main_mod.safe_folder("nul", "fb"), "nul_")
        self.assertEqual(main_mod.safe_folder("正常名字", "fb"), "正常名字")
        self.assertEqual(main_mod.safe_folder("   ", "fb"), "fb")

    def test_plan_texts_disambiguates_same_day(self):
        t = dt.datetime(2025, 6, 1, 12, 0, tzinfo=sns_db.TZ)
        posts = [
            {"snsId": "101", "time": t, "text": "同一天"},
            {"snsId": "102", "time": t, "text": "同一天"},
        ]
        res = naming.plan_texts(posts, maxlen=40)
        self.assertEqual(len(set(res)), 2)
        self.assertTrue(res[0].startswith("同一天-"))


class TestIosFind(unittest.TestCase):
    def test_manifest_preserves_wal_and_rejects_encrypted_zip(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            src_db = td / "orig.db"
            con = sqlite3.connect(src_db)
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA wal_autocheckpoint=0")
            con.execute("CREATE TABLE MyWC_Timeline01 (Id INTEGER PRIMARY KEY, Buffer BLOB)")
            con.execute(
                "INSERT INTO MyWC_Timeline01 VALUES (1, ?)",
                (_pb_bytes(5, b"user_a") + _pb_int(7, 1700000000),),
            )
            con.commit()
            wal_path = Path(str(src_db) + "-wal")
            self.assertTrue(wal_path.exists() and wal_path.stat().st_size > 0)

            broot = td / "itunes_backup"
            fid_db = "aa" + "1" * 38
            fid_wal = "bb" + "2" * 38
            (broot / "aa").mkdir(parents=True)
            (broot / "bb").mkdir(parents=True)
            (broot / "aa" / fid_db).write_bytes(src_db.read_bytes())
            (broot / "bb" / fid_wal).write_bytes(wal_path.read_bytes())
            con.close()

            man = sqlite3.connect(broot / "Manifest.db")
            man.execute("CREATE TABLE Files (fileID TEXT, domain TEXT, relativePath TEXT)")
            man.execute(
                "INSERT INTO Files VALUES (?, ?, ?)",
                (fid_db, ios_find.WECHAT_DOMAIN, "Documents/acct123/wc/wc005_008.db"),
            )
            man.execute(
                "INSERT INTO Files VALUES (?, ?, ?)",
                (fid_wal, ios_find.WECHAT_DOMAIN, "Documents/acct123/wc/wc005_008.db-wal"),
            )
            man.commit()
            man.close()

            accts = ios_find.locate_ios(broot)
            self.assertEqual(len(accts), 1)
            self.assertEqual(accts[0]["timeline"], 1)
            self.assertEqual(accts[0]["db"].name, "wc005_008.db")
            self.assertTrue((accts[0]["db"].parent / "wc005_008.db-wal").exists())

            ezip = td / "enc.zip"
            with zipfile.ZipFile(ezip, "w") as zf:
                zf.write(broot / "Manifest.db", "backup/Manifest.db")
                zf.writestr("backup/Manifest.plist", plistlib.dumps({"IsEncrypted": True}))
            with self.assertRaises(RuntimeError) as ctx:
                ios_find.locate_ios(ezip)
            self.assertIn("加密", str(ctx.exception))

    def test_tar_matches_contact_db_to_account(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            dbp = td / "wc005_008.db"
            con = sqlite3.connect(dbp)
            con.execute("CREATE TABLE MyWC_Timeline01 (Id INTEGER PRIMARY KEY, Buffer BLOB)")
            con.execute("INSERT INTO MyWC_Timeline01 VALUES (1, ?)", (_pb_bytes(5, b"user_x"),))
            con.commit()
            con.close()

            cp = td / "WCDB_Contact.sqlite"
            con = sqlite3.connect(cp)
            con.execute("CREATE TABLE Friend (userName TEXT, dbContactRemark BLOB, type INTEGER)")
            con.commit()
            con.close()

            tp = td / "ios.tar"
            with tarfile.open(tp, "w") as tf:
                tf.add(dbp, arcname="Documents/acctXYZ/wc/wc005_008.db")
                tf.add(cp, arcname="Documents/acctXYZ/DB/WCDB_Contact.sqlite")

            accts = ios_find.locate_ios(tp)
            self.assertEqual(len(accts), 1)
            self.assertEqual(accts[0]["label"], "acctXYZ")
            self.assertIsNotNone(accts[0]["contact"])
            self.assertEqual(accts[0]["contact"].name, "WCDB_Contact.sqlite")


class TestAndroidPrep(unittest.TestCase):
    def test_find_decryptor_from_source_tree(self):
        p = android_prep.find_decryptor()
        self.assertIsNotNone(p)
        self.assertTrue(p.exists())
        self.assertEqual(p.name, "kobackup_decrypt.py")


class TestSnsDb(unittest.TestCase):
    def test_ios_merge_and_android_edge_cases_with_special_path(self):
        with tempfile.TemporaryDirectory() as td:
            special = Path(td) / "dir#1%test"
            special.mkdir()

            ios_db = special / "wc007_001.db"
            con = sqlite3.connect(ios_db)
            con.execute("CREATE TABLE MyWC_Timeline01 (Id INTEGER PRIMARY KEY, Buffer BLOB)")
            con.execute("CREATE TABLE MyWC_DataItem01 (Id INTEGER PRIMARY KEY, Buffer BLOB)")
            url = b"https://shmmsns.qpic.cn/mmsns/abc_123-XYZ/0"
            media_buf = _pb_bytes(1, url) + _pb_bytes(4, b"tok123") + _pb_int(5, 1) + _pb_int(6, 99999)
            rich = (
                _pb_bytes(5, b"user_u1")
                + _pb_int(7, 1700000000)
                + _pb_bytes(23, "完整正文".encode("utf-8"))
                + _pb_bytes(99, media_buf)
            )
            sparse = _pb_bytes(5, b"user_u1") + _pb_int(7, 0) + _pb_bytes(23, b"")
            con.execute("INSERT INTO MyWC_Timeline01 VALUES (100, ?)", (rich,))
            con.execute("INSERT INTO MyWC_DataItem01 VALUES (100, ?)", (sparse,))
            con.commit()
            con.close()

            self.assertEqual(sns_db.detect_kind(ios_db), "ios_db")
            self.assertEqual(sns_db.detect_kind(special), "ios_backup")
            people = sns_db.scan_ios(ios_db)
            post = people["user_u1"][0]
            self.assertEqual(post["text"], "完整正文")
            self.assertGreater(post["time"].year, 2000)
            self.assertEqual(len(post["media"]), 1)
            self.assertEqual(post["media"][0]["url"], "https://shmmsns.qpic.cn/mmsns/abc_123-XYZ/0")

            and_db = special / "SnsMicroMsg.db"
            con = sqlite3.connect(and_db)
            con.execute("CREATE TABLE SnsInfo (snsId INTEGER, userName TEXT, createTime INTEGER, content BLOB)")
            short_media = _pb_bytes(4, b"http://mmsns.qpic.cn/mmsns/short1/0")
            f8 = _pb_bytes(5, short_media)
            self.assertLess(len(short_media), 200)
            content = (
                _pb_bytes(2, b"user_fallback")
                + _pb_int(4, 1700000000)
                + _pb_bytes(5, "短图".encode("utf-8"))
                + _pb_bytes(8, f8)
            )
            con.execute("INSERT INTO SnsInfo VALUES (42, NULL, 1700000000, ?)", (content,))
            con.commit()
            con.close()

            self.assertEqual(sns_db.detect_kind(and_db), "android_db")
            apeople = sns_db.scan_android(and_db)
            self.assertIn("user_fallback", apeople)
            self.assertEqual(len(apeople["user_fallback"][0]["media"]), 1)


class TestDownloader(unittest.TestCase):
    def test_retry_atomic_manifest_and_cross_run_dedup(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out"
            out.mkdir()
            fail_p = out / "_failures.txt"
            fail_p.write_text("stale", encoding="utf-8")

            jpg = b"\xff\xd8\xff\xe0" + b"0" * 32
            calls = [0]
            orig_fetch = downloader._fetch
            orig_gap = downloader.MIN_GAP
            orig_jitter = downloader.JITTER
            downloader.MIN_GAP = 0.0
            downloader.JITTER = 0.0

            def fake_fetch(url, timeout=30):
                calls[0] += 1
                if calls[0] == 1:
                    raise TimeoutError("transient")
                return jpg, ""

            downloader._fetch = fake_fetch
            try:
                posts = [{
                    "snsId": "1",
                    "time": dt.datetime(2025, 6, 1, 12, 0, tzinfo=sns_db.TZ),
                    "text": "测试",
                    "media": [{"url": "https://mmsns.qpic.cn/mmsns/abc/0", "token": "", "key": "", "enc": False}],
                }]
                r = downloader.download_person(posts, out)
                self.assertEqual(r["ok"], 1)
                self.assertEqual(r["fail"], 0)
                self.assertEqual(calls[0], 2)
                self.assertFalse(fail_p.exists())
                man = json.loads((out / "_manifest.json").read_text(encoding="utf-8"))
                self.assertIn("md5", man["1_1"])
                self.assertFalse((out / "_manifest.json.tmp").exists())

                posts.append({
                    "snsId": "2",
                    "time": dt.datetime(2025, 6, 2, 12, 0, tzinfo=sns_db.TZ),
                    "text": "测试2",
                    "media": [{"url": "https://mmsns.qpic.cn/mmsns/def/0", "token": "", "key": "", "enc": False}],
                })
                r2 = downloader.download_person(posts, out)
                self.assertEqual(r2["skip"], 2)
                self.assertEqual(r2["ok"], 0)
            finally:
                downloader._fetch = orig_fetch
                downloader.MIN_GAP = orig_gap
                downloader.JITTER = orig_jitter


if __name__ == "__main__":
    unittest.main()
