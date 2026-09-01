#!/usr/bin/env python3
"""WeChat Channels (视频号) 文件头密钥流：decode_key → ISAAC64 → 131072 字节。

纯标准库。同 decode_key 恒定，可按 key 缓存。算法细节见同目录 README.md。
"""

from __future__ import annotations

KEYSTREAM_SIZE: int = 131072

_MASK = (1 << 64) - 1
_RANDSIZL = 8
_RANDSIZ = 1 << _RANDSIZL  # 256
_GOLDEN = 0x9E3779B97F4A7C13
_NWORDS = KEYSTREAM_SIZE // 8  # 16384


def _mix(a: int, b: int, c: int, d: int, e: int, f: int, g: int, h: int) -> tuple[int, ...]:
    a = (a - e) & _MASK
    f ^= h >> 9
    h = (h + a) & _MASK
    b = (b - f) & _MASK
    g ^= (a << 9) & _MASK
    a = (a + b) & _MASK
    c = (c - g) & _MASK
    h ^= b >> 23
    b = (b + c) & _MASK
    d = (d - h) & _MASK
    a ^= (c << 15) & _MASK
    c = (c + d) & _MASK
    e = (e - a) & _MASK
    b ^= d >> 14
    d = (d + e) & _MASK
    f = (f - b) & _MASK
    c ^= (e << 20) & _MASK
    e = (e + f) & _MASK
    g = (g - c) & _MASK
    d ^= f >> 17
    f = (f + g) & _MASK
    h = (h - d) & _MASK
    e ^= (g << 14) & _MASK
    g = (g + h) & _MASK
    return a, b, c, d, e, f, g, h


def _parse_decode_key(decode_key: int | str) -> int:
    """与 WxIsaac64(std::string) → stoull 对齐：十进制字符串或非负整数。"""
    if isinstance(decode_key, bool):
        raise TypeError("decode_key must be int or decimal str, not bool")
    if isinstance(decode_key, int):
        if decode_key < 0:
            raise ValueError("decode_key must be non-negative")
        return decode_key
    text = str(decode_key).strip()
    if not text or not text.isdigit():
        raise ValueError(f"decode_key is not a decimal integer: {decode_key!r}")
    return int(text, 10)


class _Isaac64:
    """Bob Jenkins ISAAC64，ind() 用原版字节偏移寻址。"""

    def __init__(self, seed: int) -> None:
        self.mm = [0] * _RANDSIZ
        self.randrsl = [0] * _RANDSIZ
        self.aa = 0
        self.bb = 0
        self.cc = 0
        self.randrsl[0] = seed & _MASK
        self._randinit()

    def _ind(self, x: int) -> int:
        return self.mm[(x & ((_RANDSIZ - 1) << 3)) >> 3]

    def isaac64(self) -> None:
        mm = self.mm
        rsl = self.randrsl
        a = self.aa
        self.cc = (self.cc + 1) & _MASK
        b = (self.bb + self.cc) & _MASK

        def step(mixval: int, m: int, m2: int, ri: int) -> tuple[int, int]:
            nonlocal a, b
            x = mm[m]
            a = (mixval + mm[m2]) & _MASK
            y = (self._ind(x) + a + b) & _MASK
            mm[m] = y
            b = (self._ind(y >> _RANDSIZL) + x) & _MASK
            rsl[ri] = b
            return a, b

        m = 0
        m2 = _RANDSIZ // 2
        ri = 0
        half = _RANDSIZ // 2
        while m < half:
            step((~(a ^ ((a << 21) & _MASK))) & _MASK, m, m2, ri)
            m += 1
            m2 += 1
            ri += 1
            step((a ^ (a >> 5)) & _MASK, m, m2, ri)
            m += 1
            m2 += 1
            ri += 1
            step((a ^ ((a << 12) & _MASK)) & _MASK, m, m2, ri)
            m += 1
            m2 += 1
            ri += 1
            step((a ^ (a >> 33)) & _MASK, m, m2, ri)
            m += 1
            m2 += 1
            ri += 1
        m2 = 0
        while m < _RANDSIZ:
            step((~(a ^ ((a << 21) & _MASK))) & _MASK, m, m2, ri)
            m += 1
            m2 += 1
            ri += 1
            step((a ^ (a >> 5)) & _MASK, m, m2, ri)
            m += 1
            m2 += 1
            ri += 1
            step((a ^ ((a << 12) & _MASK)) & _MASK, m, m2, ri)
            m += 1
            m2 += 1
            ri += 1
            step((a ^ (a >> 33)) & _MASK, m, m2, ri)
            m += 1
            m2 += 1
            ri += 1
        self.aa = a
        self.bb = b

    def _randinit(self) -> None:
        self.aa = self.bb = self.cc = 0
        a = b = c = d = e = f = g = h = _GOLDEN
        for _ in range(4):
            a, b, c, d, e, f, g, h = _mix(a, b, c, d, e, f, g, h)
        mm = self.mm
        rsl = self.randrsl
        for i in range(0, _RANDSIZ, 8):
            a = (a + rsl[i]) & _MASK
            b = (b + rsl[i + 1]) & _MASK
            c = (c + rsl[i + 2]) & _MASK
            d = (d + rsl[i + 3]) & _MASK
            e = (e + rsl[i + 4]) & _MASK
            f = (f + rsl[i + 5]) & _MASK
            g = (g + rsl[i + 6]) & _MASK
            h = (h + rsl[i + 7]) & _MASK
            a, b, c, d, e, f, g, h = _mix(a, b, c, d, e, f, g, h)
            mm[i : i + 8] = (a, b, c, d, e, f, g, h)
        for i in range(0, _RANDSIZ, 8):
            a = (a + mm[i]) & _MASK
            b = (b + mm[i + 1]) & _MASK
            c = (c + mm[i + 2]) & _MASK
            d = (d + mm[i + 3]) & _MASK
            e = (e + mm[i + 4]) & _MASK
            f = (f + mm[i + 5]) & _MASK
            g = (g + mm[i + 6]) & _MASK
            h = (h + mm[i + 7]) & _MASK
            a, b, c, d, e, f, g, h = _mix(a, b, c, d, e, f, g, h)
            mm[i : i + 8] = (a, b, c, d, e, f, g, h)
        self.isaac64()
        self.randcnt = _RANDSIZ


def generate_keystream(decode_key: int | str) -> bytes:
    """由 decode_key 生成恰好 KEYSTREAM_SIZE 字节的密钥流。纯函数，同 key 恒定。"""
    rng = _Isaac64(_parse_decode_key(decode_key))
    out = bytearray(_NWORDS * 8)
    pos = 0
    cnt = rng.randcnt
    rsl = rng.randrsl
    for _ in range(_NWORDS):
        if cnt == 0:
            rng.isaac64()
            cnt = _RANDSIZ
        cnt -= 1
        out[pos : pos + 8] = rsl[cnt].to_bytes(8, "big")
        pos += 8
    rng.randcnt = cnt
    return bytes(out)


def decrypt_head(encrypted_head: bytes, decode_key: str | int) -> bytes:
    """对文件开头部分做 XOR 还原；只处理前 KEYSTREAM_SIZE 字节，超出部分原样返回。
    入参允许短于 KEYSTREAM_SIZE（流式场景会分块喂进来）。"""
    data = bytes(encrypted_head)
    n = min(len(data), KEYSTREAM_SIZE)
    if n == 0:
        return data
    ks = generate_keystream(decode_key)
    head = bytes(data[i] ^ ks[i] for i in range(n))
    if len(data) <= KEYSTREAM_SIZE:
        return head
    return head + data[KEYSTREAM_SIZE:]
