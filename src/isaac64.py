# -*- coding: utf-8 -*-
"""ISAAC-64 解密 + 图片格式嗅探。

算法与参数来自已验证实现（sns_decrypt_final.py）：
  ISAAC-64，初始常量 0x9E3779B97F4A7C13
  种子 = 媒体项的 key(uint64)，按 8 字节小端放进 randrsl[0]
  密钥流 = 每个 64 位输出字按大端摊成 8 字节，逐字节 XOR
"""

MASK = (1 << 64) - 1
CONST = 0x9E3779B97F4A7C13


class Isaac64:
    def __init__(self, seed_bytes=b""):
        self.a = self.b = self.c = 0
        self.m = [0] * 256
        self.r = [0] * 256
        self.n = 0
        self._reseed(seed_bytes)

    @staticmethod
    def _mix(x):
        SH = (9, 9, 23, 15, 14, 20, 17, 14)
        i = 0
        while i < 8:
            x[i] = (x[i] - x[(i + 4) & 7]) & MASK
            x[(i + 5) & 7] ^= x[(i + 7) & 7] >> SH[i]
            x[(i + 7) & 7] = (x[(i + 7) & 7] + x[i]) & MASK
            i += 1
            x[i] = (x[i] - x[(i + 4) & 7]) & MASK
            x[(i + 5) & 7] ^= (x[(i + 7) & 7] << SH[i]) & MASK
            x[(i + 7) & 7] = (x[(i + 7) & 7] + x[i]) & MASK
            i += 1

    def _reseed(self, sb):
        n = len(sb)
        i = 0
        while i < (n >> 3):
            self.r[i] ^= int.from_bytes(sb[i * 8:i * 8 + 8], "little")
            i += 1
        rem = n - (i << 3)
        if rem > 0:
            v = sb[i * 8]
            for j in range(1, rem):
                v |= sb[i * 8 + j] << (j << 3)
            self.r[i] ^= v
        x = [CONST] * 8
        for _ in range(4):
            self._mix(x)
        for k in range(0, 256, 8):
            for j in range(8):
                x[j] = (x[j] + self.r[k + j]) & MASK
            self._mix(x)
            self.m[k:k + 8] = x[:]
        for k in range(0, 256, 8):
            for j in range(8):
                x[j] = (x[j] + self.m[k + j]) & MASK
            self._mix(x)
            self.m[k:k + 8] = x[:]
        self._upd()

    def _upd(self):
        a = self.a
        self.c = (self.c + 1) & MASK
        b = (self.b + self.c) & MASK
        m, r = self.m, self.r
        for i in range(256):
            x = m[i]
            q = i & 3
            if q == 0:
                a = (~(a ^ ((a << 21) & MASK))) & MASK
            elif q == 1:
                a ^= a >> 5
            elif q == 2:
                a ^= (a << 12) & MASK
            else:
                a ^= a >> 33
            a = (a + m[(i + 128) & 255]) & MASK
            y = (m[(x >> 3) & 255] + a + b) & MASK
            m[i] = y
            b = (m[(y >> 11) & 255] + x) & MASK
            r[i] = b
        self.a, self.b, self.n = a, b, 256

    def nxt(self):
        if self.n == 0:
            self._upd()
        self.n -= 1
        return self.r[self.n]

    def keystream(self, size):
        out = bytearray()
        for _ in range((size + 7) // 8):
            out += self.nxt().to_bytes(8, "big")
        return bytes(out[:size])


def decrypt(enc, key_int):
    ks = Isaac64(key_int.to_bytes(8, "little")).keystream(len(enc))
    return bytes(a ^ b for a, b in zip(enc, ks))


def sniff(d):
    """看解密后的头几字节判断图片格式。"""
    if not d:
        return None
    if d[:3] == b"\xff\xd8\xff":
        return "jpg"
    if d[:4] == b"\x89PNG":
        return "png"
    if d[:4] == b"RIFF" and d[8:12] == b"WEBP":
        return "webp"
    if d[:4] == b"GIF8":
        return "gif"
    return None
