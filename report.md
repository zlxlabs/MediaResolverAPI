# 修复报告：解密边界测试换成独立参照系

- **Dispatch-Id**：dlg-20260901-103727-087afe
- **root_cause_group**：测试的参照物经由被测函数生成，加解密对称抵消，边界断言恒真。
- **introduced_by_commit**：`cda33c7`（`feat: add wechat channels streaming decrypt endpoint`）；判据缺陷源自主脑卡面，非执行器实现问题。
- **open_findings**：主脑红验抽查：`n_xor` 处注入 off-by-one 后 26 个测试全绿（应红）。
- **结论**：密文改由 `generate_keystream` 直接索引构造，不再经过 `xor_chunk`。另加硬编码单点：`offset=131071` 必须异或那一字节。实现未改。独立参照下实现仍绿，没有发现实现缺陷。

## 现场

沿用 `card/MediaResolverAPI-20260901-04`，开工 HEAD `e86022f`。工作区无交接单。同 dispatch id 的 unit 是本派发现场。存活探针不可用（`current session id missing`），无法区分有主/无主欠账；要处理请另开对话，不要在本次接手里顺手补账。

## 改了什么

- `tests/test_stream_wechat_channels.py` `_cipher`：`PLAIN[:128KiB] XOR keystream[i]`，明文区原样拼接。
- `tests/test_wechat_channels_crypto.py` `test_boundary_crossing_chunk`：前两字节同样按密钥流下标造密文。
- 新增 `test_last_encrypted_byte_xored_literal`：字面量 `cipher=0x8f` → `plain=0x5a`（`generate_keystream(55516695)[131071]==0xd5`）。

`app/` 恢复后 `git status --porcelain` 无 `app/` 行。

## 红验 1：`n_xor` off-by-one

注入行（`sed -n '209p'`）：

```
    n_xor = min(len(data), KEYSTREAM_SIZE - absolute_offset - 1)
```

```
FAILED tests/test_stream_wechat_channels.py::test_range_matches_full_download_slice[no_range]
FAILED tests/test_stream_wechat_channels.py::test_range_matches_full_download_slice[bytes_0_open]
FAILED tests/test_stream_wechat_channels.py::test_range_matches_full_download_slice[bytes_encrypted_exact]
FAILED tests/test_stream_wechat_channels.py::test_range_matches_full_download_slice[bytes_cross_boundary]
FAILED tests/test_stream_wechat_channels.py::test_range_matches_full_download_slice[bytes_straddle]
FAILED tests/test_stream_wechat_channels.py::test_resume_matches_oneshot_and_range_starts_at_forwarded[50000]
FAILED tests/test_stream_wechat_channels.py::test_resume_matches_oneshot_and_range_starts_at_forwarded[500000]
FAILED tests/test_stream_wechat_channels.py::test_concurrency_limit_429_and_release
FAILED tests/test_stream_wechat_channels.py::test_memory_constant_no_full_body_read
FAILED tests/test_wechat_channels_crypto.py::test_boundary_crossing_chunk
FAILED tests/test_wechat_channels_crypto.py::test_last_encrypted_byte_xored_literal
11 failed, 16 passed, 16 warnings in 0.66s
PYTEST_EXIT:1
```

典型失败：index 131071 仍是密文字节（`\xa1` / `\xd5` / `\x8f`），不是明文。纯明文区两格（`bytes=131072-`、`bytes=200000-300000`）仍绿——off-by-one 只伤加密区最后 1 字节，符合预期。

`git checkout -- app/services/wechat_channels_crypto.py` 后 porcelain 只有两个测试文件。

## 红验 2：`absolute_offset` 强制置 0

注入：

```
    if absolute_offset < 0:
        raise ValueError(f"absolute_offset must be >= 0, got {absolute_offset}")
    absolute_offset = 0
    data = bytes(chunk)
```

```
FAILED ...test_range_matches_full_download_slice[no_range]
FAILED ...[bytes_0_open]
FAILED ...[bytes_encrypted_exact]
FAILED ...[bytes_cross_boundary]
FAILED ...[bytes_straddle]
FAILED ...[bytes_plain_from_boundary]
FAILED ...[bytes_plain_window]
FAILED ...test_resume_matches_oneshot_and_range_starts_at_forwarded[50000]
FAILED ...test_resume_matches_oneshot_and_range_starts_at_forwarded[500000]
FAILED ...test_concurrency_limit_429_and_release
FAILED ...test_memory_constant_no_full_body_read
FAILED ...test_plaintext_region_is_identity
FAILED ...test_boundary_crossing_chunk
FAILED ...test_last_encrypted_byte_xored_literal
14 failed, 13 passed, 16 warnings in 0.91s
```

pytest 退出码 1。Range 7 格与续传 2 格均红。再次 `git checkout -- app/services/wechat_channels_crypto.py`。

## 恢复后

```
python -m pytest tests/ -q
221 passed, 95 warnings in 1.97s
```

（基线 220 + 新增字面量单点 1。）续传+并发窄范围 5 轮全绿。`py_compile` 所改测试文件通过。

## git

```
5518598 test: build wechat stream ciphertext from the keystream oracle
```

`git show --stat --format= HEAD`（测试提交）：

```
 tests/test_stream_wechat_channels.py | 12 ++++++++++--
 tests/test_wechat_channels_crypto.py | 19 +++++++++++++++++--
 2 files changed, 27 insertions(+), 4 deletions(-)
```
