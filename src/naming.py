# -*- coding: utf-8 -*-
"""文件名生成规则（已按用户确认的规格实现）。

格式：{YYmmdd}_{文字}_{NN}.{ext}

文字清洗顺序：
  1. emoji / 不可显示字符 → □
  2. 换行、制表符 → 空格
  3. Windows 非法字符 \\ / : * ? " < > | → _
  4. 连续空格合并
  5. 去掉首尾空格和点
  6. 超过 maxlen → 截断
  7. 清洗后没有有效字符（汉字/字母/数字）→ 用「无文字」

重名：同一人内两条动态算出同一个名字时，统一在文字段后加 -<snsId md5 前4位>。
"""
import hashlib
import re
import unicodedata

BOX = "□"
NO_TEXT = "无文字"

_INVALID = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_WS = re.compile(r"\s+")

# emoji 与常见「显示不出来」的符号区段
_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),   # emoji 主区
    (0x1F1E6, 0x1F1FF),   # 区域指示符（国旗）
    (0x2600, 0x27BF),     # 杂项符号 / dingbats
    (0x2B00, 0x2BFF),     # 杂项符号箭头
    (0xFE00, 0xFE0F),     # 变体选择符
    (0xE0020, 0xE007F),   # 标签字符
    (0x2190, 0x21FF),     # 箭头
    (0x2300, 0x23FF),     # 杂项技术符号
    (0x25A0, 0x25FF),     # 几何图形
)


def _is_emoji(ch):
    cp = ord(ch)
    if cp in (0x200D, 0x20E3):      # 零宽连接符 / 组合用键帽
        return True
    return any(a <= cp <= b for a, b in _EMOJI_RANGES)


def _meaningful(ch):
    if ch.isalnum():
        return True
    return "\u4e00" <= ch <= "\u9fff" or "\u3400" <= ch <= "\u4dbf"


def clean_text(s, maxlen=40):
    out = []
    for ch in (s or ""):
        if ch in "\r\n\t":
            out.append(" ")
        elif _is_emoji(ch):
            out.append(BOX)
        elif unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn"):
            out.append(BOX)
        else:
            out.append(ch)
    t = "".join(out)
    t = _INVALID.sub("_", t)
    t = _WS.sub(" ", t).strip(" .")
    if len(t) > maxlen:
        t = t[:maxlen].rstrip(" .")
    if not any(_meaningful(c) for c in t):
        return ""
    return t


def maxlen_for(folder):
    """按 Windows 全路径 260 上限，算文字段最多能放几个字。"""
    # 目录\ + YYmmdd(6) + _ + 文字 + _ + NN(2) + .jpg(4)
    fixed = len(str(folder)) + 1 + 6 + 1 + 1 + 2 + 4
    return max(6, min(40, 250 - fixed))


def plan_texts(posts, maxlen=40):
    """给每条动态算文字段，并处理同人内重名。"""
    base = [clean_text(p.get("text", ""), maxlen) or NO_TEXT for p in posts]

    groups = {}
    for i, p in enumerate(posts):
        groups.setdefault((p["time"].strftime("%y%m%d"), base[i]), []).append(i)

    texts = []
    for i, p in enumerate(posts):
        t = base[i]
        if len(groups[(p["time"].strftime("%y%m%d"), t)]) > 1:
            h = hashlib.md5(str(p.get("snsId", "")).encode()).hexdigest()[:4]
            t = "%s-%s" % (t, h)
        texts.append(t)
    return texts


def stem(post, text, idx):
    """不带扩展名的文件名主体。"""
    return "%s_%s_%02d" % (post["time"].strftime("%y%m%d"), text, idx)


def filename(post, text, idx, ext="jpg"):
    return "%s.%s" % (stem(post, text, idx), ext)
