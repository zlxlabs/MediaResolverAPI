# Content-Range 穷举式对账修复报告

## 1. Content-Range 对账维度枚举与处置

枚举方法：先按 `Content-Range: bytes <first-byte-pos>-<last-byte-pos>/<complete-length>` 的三个位置量逐项展开；对 `first-byte-pos` 和 `last-byte-pos` 分别列出语法值域与端点关系；对 `complete-length` 分开列出数值、未知值 `*` 及其与已知文件大小的关系。然后把调用方已有的本地期望偏移、客户端请求的 Range 终点、已知 `file_size`，以及上游 `Content-Length` 与声明区间长度可组成的直接比较逐项列出。这样清单可从 schema 的字段和调用方已知量机械重生成，不依赖当前 finding 的示例。

| 维度 | 与之比对的已知量 | 处置（校验/不校验） | 理由 | 锁死测试 |
| --- | --- | --- | --- | --- |
| `first-byte-pos` 的 unit、分隔符和非负十进制语法 | Content-Range schema | 校验；缺失或畸形返回 502 JSON | 无法解析位置就无法安全对账；正则只接受 `bytes N-N/N` 或 `bytes N-N/*` | `test_initial_range_missing_content_range_fails_before_streaming`；`test_initial_range_malformed_content_range_fails_before_streaming` |
| `last-byte-pos` 的非负十进制语法 | Content-Range schema | 校验 | 负数或非数字不是合法字节位置 | `test_initial_range_malformed_content_range_fails_before_streaming` |
| `complete-length` 的十进制或 `*` 语法 | Content-Range schema | 校验 | 数值总长或未知总长是 schema 允许的两种形式 | `test_initial_range_malformed_content_range_fails_before_streaming`；`test_initial_range_wildcard_complete_length_is_allowed` |
| `last-byte-pos >= first-byte-pos` | 同一 Content-Range 的两个位置量 | 校验；失败返回 502 JSON | 空区间不属于该 206 表示；这是既有实现，补测试锁死 | `test_initial_range_inverted_content_range_fails_before_streaming` |
| `first-byte-pos == expected_offset` | 本地解析出的客户端 Range 起点 | 校验；失败返回 502 JSON | 防止上游从错误绝对偏移开始，已有对账不变量 | `test_initial_range_mismatch_fails_before_streaming`；`test_no_range_206_nonzero_start_fails_before_streaming` |
| `last-byte-pos == expected_end` | 本地解析并发送给 CDN 的客户端 Range 终点 | 校验；失败返回 502 JSON | 起点正确但终点提前会造成响应头声明长度大于实际可读数据 | `test_initial_range_end_mismatch_fails_before_streaming` |
| 数值 `complete-length == file_size` | 本地已知 `file_size` | 校验；失败返回 502 JSON | 上游声明的完整资源长度与本地媒体元数据矛盾 | `test_initial_range_complete_length_mismatch_fails_before_streaming` |
| `complete-length == *` | 本地 `file_size` | 不校验 | `*` 按 HTTP 语义表示上游未提供该量；锁定决策要求未知量合法放行 | `test_initial_range_wildcard_complete_length_is_allowed` |
| 数值 `complete-length > last-byte-pos` | 数值总长与声明终点 | 不单独校验；由既有约束推出 | `expected_end` 来自 `parse_byte_range`，始终 `<= file_size - 1`；同时数值 `complete-length == file_size`，因此该关系已被终点对账与总长对账共同推出；`*` 时总长不可判定 | 终点、总长的两个失败测试及 `RANGE_CASES` 正常矩阵共同锁定这条传递不变量 |
| `last-byte-pos < file_size` | 本地已知 `file_size` | 不单独校验；由既有约束推出 | 与上行请求一致时，`expected_end` 已由 Range 解析裁剪到 `file_size - 1`，再由终点相等校验推出；不重复增加一条同义校验 | `test_initial_range_end_mismatch_fails_before_streaming`；全 Range 矩阵 |
| 声明区间长度 `last - first + 1 == Content-Length`（上游提供时） | 上游 `Content-Length` | 校验；失败返回 502 JSON | 这是“起点和终点都对但只收到 1 字节”这一缺陷的直接闸门，且必须在响应头前完成 | `test_initial_range_content_length_mismatch_fails_before_streaming` |
| 上游提供的 `Content-Length` 可解析为非负十进制 | `Content-Length` 字段语法 | 校验；失败返回 502 JSON | 非数值不能作为已知长度参与一致性判断，继续下发会把坏元数据带入响应 | `test_initial_range_invalid_content_length_fails_before_streaming` |
| 上游未提供 `Content-Length` | `Content-Length` 缺失 | 不校验 | HTTP 流式传输可不提供该量；未知量不能被臆造，其他 Content-Range 维度仍照常校验 | `test_initial_range_missing_content_length_is_allowed` |

所有“校验”行都在 `StreamingResponse` 构造之前执行；失败统一由既有 `UpstreamDisconnected` 转成既有风格的 502 JSON。没有恢复服务端续传，没有改 Range 首发解析、416、429、并发限制、解密算法或客户端断开清理分支。

## 2. 根因与实现

根因是 `_reconcile_cdn_offset` 只接收并校验 `expected_offset`，只解析了 `start/end` 却没有消费 `complete-length`，也没有读取上游 `Content-Length`。调用方随后直接按本地 `end - start + 1` 构造响应头，因此上游短区间可能在响应头发出后才在流迭代中失败。

本次改动：

- 在 `CdnResponse` 和 `_HttpxCdnStream` 中传递上游 `Content-Length`。
- 在同一个 `_reconcile_cdn_offset` 函数内校验 `last-byte-pos`、数值总长和区间长度；`*` 与缺失的 `Content-Length` 明确按未知量处理。
- 保留并补强 206 的缺失、畸形、倒置、起点不符场景，所有失败发生在响应头生成之前。
- 新增 7 个测试；密文构造仍只使用 `generate_keystream` 直接异或，未改用 `xor_chunk`。

## 3. 逐维度红验

以下注入均是逐条进行，确认失败测试后立即恢复；下列 `if not True` / `if True` 仅为临时注入，不在最终代码中。

### 终点对账

注入行：

```python
if not True:
```

失败统计：

```text
FAILED tests/test_stream_wechat_channels.py::test_initial_range_end_mismatch_fails_before_streaming
1 failed, 2 warnings in 0.11s
```

### 总长度对账

注入行：

```python
if complete_length != "*" and not True:
```

失败统计：

```text
FAILED tests/test_stream_wechat_channels.py::test_initial_range_complete_length_mismatch_fails_before_streaming
1 failed, 2 warnings in 0.11s
```

### 区间长度与 `Content-Length` 一致性

注入行：

```python
if not True:
```

失败统计：

```text
FAILED tests/test_stream_wechat_channels.py::test_initial_range_content_length_mismatch_fails_before_streaming
1 failed, 2 warnings in 0.11s
```

### `Content-Length` 语法

注入行：

```python
if False:
```

失败统计：

```text
FAILED tests/test_stream_wechat_channels.py::test_initial_range_invalid_content_length_fails_before_streaming
1 failed, 2 warnings in 0.13s
```

### 整体反向

在状态码判断之后临时加入：

```python
if True:
    raise UpstreamDisconnected("injected whole-reconciliation failure")
```

正常 Range 矩阵统计：

```text
FAILED tests/test_stream_wechat_channels.py::test_range_matches_full_download_slice[bytes_0_open]
FAILED tests/test_stream_wechat_channels.py::test_range_matches_full_download_slice[bytes_encrypted_exact]
FAILED tests/test_stream_wechat_channels.py::test_range_matches_full_download_slice[bytes_cross_boundary]
FAILED tests/test_stream_wechat_channels.py::test_range_matches_full_download_slice[bytes_straddle]
FAILED tests/test_stream_wechat_channels.py::test_range_matches_full_download_slice[bytes_plain_from_boundary]
FAILED tests/test_stream_wechat_channels.py::test_range_matches_full_download_slice[bytes_plain_window]
6 failed, 1 passed, 8 warnings in 0.28s
```

所有注入均已恢复；最终源文件没有恒真/恒假注入残留。

## 4. 验证

```text
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m pytest tests/ -q
232 passed, 107 warnings in 2.46s

/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m py_compile app/api/stream.py tests/test_stream_wechat_channels.py
通过
```

## 5. Git

实现与测试提交的实际输出（报告随后单独入库，避免把报告自身的提交统计混入实现统计）：

```text
$ git log --oneline -1
7376a43 fix: reconcile complete CDN byte ranges

$ git show --stat --format= HEAD
 app/api/stream.py                    |  43 ++++++++++-
 tests/test_stream_wechat_channels.py | 138 ++++++++++++++++++++++++++++++++++-
 2 files changed, 176 insertions(+), 5 deletions(-)
```

## 6. 现场

- Dispatch-Id：`dlg-20260901-115526-91a845`
- 分支：`card/MediaResolverAPI-20260901-08`
- 基线：`c7238a4abb327224aef48b0ce5c6d05aabb7db83`
- 允许修改范围内的实现与测试已提交；报告文件本次一并入库。
