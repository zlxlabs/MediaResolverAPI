"""Hard-coded known vectors for wechat_channels ISAAC64 keystream.

Does not touch /tmp/sph-spike/. Vectors exported from the production
generate_keystream implementation for decode_key=55516695.
"""

from app.services.wechat_channels_crypto import (
    KEYSTREAM_SIZE,
    _generate_keystream_cached,
    generate_keystream,
    xor_chunk,
)

# decode_key=55516695, first 12 keystream bytes (hex), exported from spike.
_KEY = 55516695
_KS12 = bytes.fromhex("7769d98df51778766238a697")
_FTYP_ISOM = bytes.fromhex("000000206674797069736f6d")  # 00 00 00 20 ftyp isom


def test_keystream_size_and_known_prefix():
    ks = generate_keystream(_KEY)
    assert len(ks) == KEYSTREAM_SIZE == 131072
    assert ks[:12] == _KS12


def test_int_and_decimal_str_share_cache_and_bytes():
    _generate_keystream_cached.cache_clear()
    a = generate_keystream(_KEY)
    b = generate_keystream("55516695")
    assert a == b
    assert a[:12] == _KS12
    info = _generate_keystream_cached.cache_info()
    assert info.hits >= 1
    assert info.currsize == 1


def test_xor_known_header_roundtrip():
    cipher12 = bytes(x ^ y for x, y in zip(_KS12, _FTYP_ISOM, strict=True))
    assert xor_chunk(cipher12, _KEY, 0) == _FTYP_ISOM
    assert xor_chunk(cipher12, _KEY, 0)[4:8] == b"ftyp"


def test_plaintext_region_is_identity():
    chunk = bytes(range(64))
    assert xor_chunk(chunk, _KEY, KEYSTREAM_SIZE) == chunk
    assert xor_chunk(chunk, _KEY, KEYSTREAM_SIZE + 10) == chunk


def test_boundary_crossing_chunk():
    # 2 bytes of ciphertext + 2 bytes of plaintext, starting at 131070.
    cipher_prefix = xor_chunk(b"\x00\x00", _KEY, KEYSTREAM_SIZE - 2)
    mixed = cipher_prefix + b"\xab\xcd"
    out = xor_chunk(mixed, _KEY, KEYSTREAM_SIZE - 2)
    assert out[:2] == b"\x00\x00"
    assert out[2:] == b"\xab\xcd"


def test_empty_chunk_noop():
    assert xor_chunk(b"", _KEY, 0) == b""
