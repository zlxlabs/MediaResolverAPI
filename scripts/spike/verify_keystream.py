#!/usr/bin/env python3
"""对照 /tmp/sph-spike/ 固化样本验收 generate_keystream / xor_chunk。

判据失败或样本缺失时非零退出；不放宽为只比对前若干字节。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from wechat_keystream import KEYSTREAM_SIZE, generate_keystream, xor_chunk

SAMPLE_DIR = Path("/tmp/sph-spike")
REQUIRED = (
    "sample1.bin",
    "sample1.key",
    "sample2.bin",
    "sample2.key",
)


def _die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def _load_samples() -> tuple[bytes, int, bytes, int]:
    missing = [name for name in REQUIRED if not (SAMPLE_DIR / name).is_file()]
    if missing:
        _die("missing sample file(s): " + ", ".join(str(SAMPLE_DIR / n) for n in missing))
    e1 = (SAMPLE_DIR / "sample1.bin").read_bytes()
    e2 = (SAMPLE_DIR / "sample2.bin").read_bytes()
    k1 = (SAMPLE_DIR / "sample1.key").read_text().strip()
    k2 = (SAMPLE_DIR / "sample2.key").read_text().strip()
    if len(e1) != KEYSTREAM_SIZE:
        _die(f"sample1.bin length {len(e1)} != {KEYSTREAM_SIZE}")
    if len(e2) != KEYSTREAM_SIZE:
        _die(f"sample2.bin length {len(e2)} != {KEYSTREAM_SIZE}")
    return e1, int(k1, 10), e2, int(k2, 10)


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b, strict=True))


def main() -> int:
    e1, key1, e2, key2 = _load_samples()
    ks1 = generate_keystream(key1)
    ks2 = generate_keystream(key2)
    if len(ks1) != KEYSTREAM_SIZE or len(ks2) != KEYSTREAM_SIZE:
        _die(f"keystream length {len(ks1)}/{len(ks2)} != {KEYSTREAM_SIZE}")

    got = _xor(ks1, ks2)
    want = _xor(e1, e2)
    nmatch = sum(x == y for x, y in zip(got, want, strict=True))
    print(f"constraint_A: {nmatch}/{KEYSTREAM_SIZE} bytes match")
    ok_a = nmatch == KEYSTREAM_SIZE

    p1 = xor_chunk(e1, key1, 0)
    p2 = xor_chunk(e2, key2, 0)
    print(f"sample1_plain[:16] {p1[:16].hex()}")
    print(f"sample2_plain[:16] {p2[:16].hex()}")
    ok_b = p1[4:8] == b"ftyp" and p2[4:8] == b"ftyp"
    if not ok_b:
        print(
            f"constraint_B: ftyp check failed sample1={p1[4:8]!r} sample2={p2[4:8]!r}",
            file=sys.stderr,
        )
    else:
        print("constraint_B: sample1[4:8]==ftyp and sample2[4:8]==ftyp")

    chunk_size = 7000
    whole = xor_chunk(e1, key1, 0)
    parts = bytearray()
    off = 0
    while off < len(e1):
        piece = e1[off : off + chunk_size]
        parts.extend(xor_chunk(piece, key1, off))
        off += len(piece)
    if len(parts) != len(whole):
        print(
            f"constraint_C: length mismatch chunked={len(parts)} whole={len(whole)}",
            file=sys.stderr,
        )
        nmatch_c = 0
    else:
        nmatch_c = sum(a == b for a, b in zip(parts, whole, strict=True))
    print(f"constraint_C: {nmatch_c}/{len(whole)} bytes match (chunked, {chunk_size}B)")
    ok_c = nmatch_c == len(whole) == KEYSTREAM_SIZE

    if not ok_a or not ok_b or not ok_c:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
