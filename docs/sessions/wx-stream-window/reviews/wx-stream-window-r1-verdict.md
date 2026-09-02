# wx-stream-window R1 verdict

verdict: pass

审查对象：`d848c7c..ef9af01`（冻结；本文件所在提交不属于被审范围）。
本轮新证据：基线/还原后 `pytest tests/ -q` 均为 305 passed；`_consume_cdn_body` 边读边 xor 注入打红；`_read_window` 六条 return/raise 路径各注入一次；`_cancel_task` 在 venv Python 3.13.12 上的取消探针；发头前换对时 TikHub `ProviderError` 探针返回 500；OCR `status=reviewed`（minimax）5 条测试侧意见。

## findings

| 编号 | P 级 | 违反的不变式/红线 | 文件与行号 | 触发路径 | 证据 | 建议修法 |
|---|---|---|---|---|---|---|
| F1 | P2 | README 流式错误表「502 = 响应头发出前的上游 TikHub / CDN 失败」；spec 不变式 4「重试预算耗尽前响应头未发出 → 502」 | `app/api/stream.py` `_read_window_retry` 369-371；`stream_wechat_channels` 546-551 只捕获 `CdnHttpError` / `UpstreamDisconnected` | 客户端 `Range: bytes=200000-`（起点 ≥ 131072）→ 首窗 CDN 403 → `_fetch_media` 换对抛 `ProviderError` | 探针：`Range: bytes=200000-` + 第二次 `_fetch_media` raise `ProviderError("tikhub down on refresh")` → `status 500`，body `{"success": false, "error": "Internal server error"}`，`fetch_calls==2`。日志 `Unhandled exception: tikhub down on refresh`。默认窗口 4 MiB 的无 Range 请求首窗起点为 0、不会走换对，故日常完整下载不触发。 | 在 `_read_window_retry` 换对处把 `ProviderError`/`VideoNotFoundError` 收成 `UpstreamDisconnected`（或在端点 `except` 补这两类），发头前统一 502。 |

P1 两问（F1）：
1. 真实使用方式下会被触发吗？会，但只在「带 Range 且起点 ≥ 131072」且「这一窗 CDN 401/403/404/410」且「TikHub 重取失败」的交叉上。内部播放器拖拽/断点续传会发这类 Range；完整无 Range 下载默认 4 MiB 首窗起点为 0，走不到换对。
2. 触发了后果能否接受？不能当 P1：客户端拿到的是明确错误 JSON，不是错字节或假完整。不是数据丢失/静默出错/崩溃/越权/损坏他人数据。500 vs 文档承诺的 502 是契约偏差，故 P2。

无其他 P1/P2。

## 1. 不变式逐条核对

1. **CDN 响应在 yield 前被完整读完**  
   代码：`_consume_cdn_body`（`stream.py:270-278`）攒齐再返回；`_read_window` 在 `finally` 里 `aclose` 后才把 `raw` 交给 `_iter_windows`；`xor_chunk` 发生在 `await prefetch` 之后（或首窗已在内存）。  
   测试：`_assert_windows_fully_read_before_client_yield`（`xor_while_reading == []` 且每条流 `aclose_called`），由 `test_range_matches_full_download_slice` 等调用。  
   红验：见维度 2③，注入后该断言变红。

2. **字节正确性**  
   代码：`xor_chunk(current, key, offset)` 按绝对偏移；窗口大小来自 `STREAM_WINDOW_BYTES`。  
   测试：`test_range_matches_full_download_slice` 参数 `WINDOW_SIZES × RANGE_CASES`（含 65536 / 131072 / 200000 / 4 MiB / `FILE_SIZE+10`，以及跨 131072、后缀 Range）。拼接结果与独立密钥流加密的明文切片逐字节相同。

3. **响应头正确性**  
   代码：`stream_wechat_channels` 在首窗成功后写 `Content-Length: str(end-start+1)`；`is_partial` 时带 `Content-Range` 与 206，否则 200。后缀 Range 先 `bytes=0-0` 取总长再换算。  
   测试：同一矩阵断言 200/206、`Content-Length`、`Content-Range`；`test_initial_range_missing_content_length_still_sets_client_length`；后缀用例断言首个 CDN 请求为 `bytes=0-0`。

4. **窗口失败重试**  
   代码：`_read_window_retry` 每窗最多 3 次、`state["budget"]` 初值 20。发头前失败 → 端点转 502；发头后失败 → `_iter_windows` 抛错断连。  
   测试：`test_later_window_short_once_then_succeeds`、`test_later_window_connect_timeout_once_then_succeeds`、`test_same_window_three_failures_disconnects_after_headers`、`test_retry_budget_exhausted_stops_after_twenty_failures`、`test_first_window_short_body_returns_502_json`（3 次后 502）。

5. **换对规则**  
   代码：`_EXPIRED_STATUSES={401,403,404,410}`；`start >= 131072` 且 `not state["refreshed"]` 才 `_fetch_media` 一次。`_iter_windows` 的 `key` 在换对后仍用旧值，但 `xor_chunk` 在 offset≥131072 时原样返回（`wechat_channels_crypto.py:206-207`），与环境事实「两 URL 偏移 ≥ 131072 逐字节相同」一致。  
   测试：`test_window_403_at_keystream_boundary_refreshes_url`（正文仍等于 `PLAIN`）、`test_window_403_before_keystream_does_not_refresh`、`test_second_403_does_not_refresh_again`。401/404/410 与 403 同分支，无单独用例（backlog）。

6. **取消清理**  
   代码：`_read_window` `finally aclose`；`_iter_windows` `finally: await _cancel_task(prefetch)`。  
   测试：`test_client_disconnect_acloses_upstream`（`agen.aclose()` 后所有 `FakeCdn.aclose_called`，且无残留 `_read_window` 任务）；发头前取消见 `test_pre_response_cancel_during_cdn_open_closes_client`。发头后 `CancelledError`（非 `aclose`）无独立用例，见维度 4。

7. **416 语义**  
   代码：`_read_window` 对 416 抛 `CdnHttpError` 且不消费 body；`_416_from_cdn` 透传合法 `bytes */L`。  
   测试：`test_range_past_end_returns_416`、`test_read_window_416_does_not_consume_body`。

## 2. 降层审查三问

### ① 发头前不可逆动作；发头后会否静默错字节

发头前：TikHub `_fetch_media`、一至多窗 CDN GET、可能一次换对。这些对客户端不可见；失败走 JSON 4xx/5xx。

发头后：`StreamingResponse` 已带准确 `Content-Length`。后续窗失败只断连接（长度不足）。Spec 明文：这不算静默。

查过的错字节候选并排除：
- 换对后仍用旧 `decode_key`：只发生在 start≥131072，xor 为恒等，有字节测试锁死。
- 206 短 Content-Range 非 EOF：`declared_end < end and not EOF` 直接 `UpstreamDisconnected`，发头前 502。
- 206 声明长度与 body 不符：`len(raw) != expected_len` 失败。
- 200 且 CDN 忽略 Range：非「小文件例外」即失败，不丢弃前 N 字节。
- 发头后短读：`Content-Length` 仍是全长，客户端看见截断——按卡面定义不算静默。

未发现发头后「错字节但客户端以为完整」的路径。

### ② 守卫值是否唯一；`state` 并发；换对后的 key

`state` 是每次请求的局部 dict，不是跨请求/跨 worker 共享。部署形态（单进程或多 uvicorn worker）下每请求一份。

写入点：主协程（探针/首窗 `_read_window_retry`）与至多一个 prefetch task。asyncio 单线程；prefetch 在 `yield` 期间跑，主协程那时不写 `state`；`await prefetch` 时 prefetch 已结束。后缀探针与首窗串行。无并发写。

`media["decode_key"]`：`_iter_windows` 开头捕获一次。换对只更新 `state["media"]`。需要密钥的偏移只在前 131072 字节，而换对守卫禁止这些窗换对。结论：换对后旧 key 不会被用在需要密钥的偏移上。

### ③ 保护的是行为还是写法

断言看的是「`xor_chunk` 调用时目标窗 `aiter_in_progress` 是否仍为真」，不是 `_consume_cdn_body` 是否写成 `list.append`。

注入（已还原）：在 `_consume_cdn_body` 的 `async for` 内对每块调用 `xor_chunk`（`# RED-VERIFY stream-while-read`，`sed`/`grep` 确认第 276 行），跑：

```
pytest tests/test_stream_wechat_channels.py::test_range_matches_full_download_slice -k "65536 and no_range"
```

结果：`FAILED ...[no_range-65536]`（`xor_while_reading == []` 被打破）。**该断言能被边读边吐打红**，锁的是行为。

若只把 `_consume_cdn_body` 改成内部 yield 再 `b"".join`、xor 仍在 aiter 结束之后，断言不会红——这正说明它不锁函数形态。

## 3. 变异验证（`_read_window`）

每次注入后 `grep` 确认哨兵，跑完还原。`invalid window bounds`（`end < start`）调用方被 `parse_byte_range` 挡住，判**不可达**，未注入。

| 路径 | 注入 | 结果 | 归因 |
|---|---|---|---|
| 416 | `raise CdnHttpError(416)` → `pass` | `test_read_window_416_does_not_consume_body`、`test_range_past_end_returns_416` 均红 | 测试强 |
| 过期 | `raise CdnHttpError(expired)` → `pass` | `test_window_403_at_keystream_boundary_refreshes_url` 红；`test_window_403_before_keystream_does_not_refresh` 仍绿 | 换对依赖 `CdnHttpError`，测试强。不可换对的 403 会落到 `status != 206` → 仍 502，该用例不区分「过期」与「其它非 206」，**不是缺口**（失败仍 fail-loud） |
| 200 例外 | `if start==0 and CL<=window` → `if False and ...` | `test_read_window_status_200_whole_file_within_window`、`test_range_start_zero_accepts_cdn_full_response_when_file_fits_window` 均红 | 测试强 |
| 200 非例外 | 「CDN ignored Range」改为消费 body 并 return | 三条（`...larger_than_window_fails_without_read` / `...nonzero_start_rejects...` / `test_cdn_200_on_later_window_disconnects`）均红 | 测试强 |
| 206 短包 | 非 EOF 短 Range 的 raise → `pass` | `test_mid_file_short_content_range_fails_before_streaming` 红（日志随后出现 `expected 200` 的下一窗错位）；`test_read_window_eof_clip_returns_declared_range` 仍绿 | 非 EOF 短包测试强。EOF 短窗是另一条合法成功路径，该注入碰不到，**不是弱测试** |
| 206 正常 | `return raw` → `return raw[:-1]` | `test_read_window_sends_bounded_range_and_returns_exact_bytes` 红（逐字节 assert） | 测试强 |

六条指定路径均有至少一处变红。无「注入了但不红」需要补测的路径。

## 4. 取消与资源

- **客户端断开 / GeneratorExit**：`test_client_disconnect_acloses_upstream` 在首窗已读、第二窗 `hold.wait()` 时 `agen.aclose()`，断言所有 CDN `aclose_called`，且 `repr(coro)` 含 `_read_window` 的残留 task 为空。venv 3.13.12 上 `repr(get_coro())` 仍含函数名（OCR 所谓「3.12+ 不含名字」在本测试运行时上不成立）。
- **CancelledError**：`_iter_windows` `except CancelledError: log; raise`，然后 `finally _cancel_task`。发头前 ASGI 取消有槽位/CDN client 测试。发头后任务级取消没有单独 ASGI 用例，与 `aclose` 路径同用 `_cancel_task`。
- **`_cancel_task` 吞 `BaseException`**：venv 3.13.12 探针——外层 task 在 `await sleep` 时 cancel，`finally` 里 `_cancel_task` 等待仍在跑的 prefetch。结果：`cancelled as expected`，**没有把外层取消吞成正常结束**。原因：`except CancelledError: raise` 的 pending 异常在 finally 正常返回后继续传播；内层 `except BaseException: pass` 吞的是 prefetch 的 `CancelledError`。过度捕获 `KeyboardInterrupt`/`SystemExit` 仍是味道，见 backlog。

结论：当前与预读 CDN 在断开路径上会被 `aclose`；外层取消未被 `_cancel_task` 吞掉。

## 5. 熵增审查

| 抽象 | 消费者 | 裁决 |
|---|---|---|
| `CdnHttpError` | `_read_window` 产生；`_read_window_retry` 与端点区分 416 / 换对 / 传输失败 | 单实现但必要：控制流分叉，不是转发层 |
| `_read_window_retry` | 端点（探针+首窗）与 `_iter_windows` prefetch | 两个调用点，必要 |
| `_iter_windows` | 仅 `body()` | 单消费者，但把「已发头的生成器」与「发头前同步读」隔开，必要 |
| `_cancel_task` | 仅 `_iter_windows finally` | 单消费者 helper；10 行隔离取消等待。可内联，不值新抽象债。≤P3 |
| `STREAM_WINDOW_BYTES` | 实现、README、`.env.example`、测试 | spec 要点 1 要求，有第二消费者（文档/配置） |

未把「单实现接口 / 转发-only / 无消费者通用化」记成 P2。

## 6. 文档一致性

README 三处与代码一致：
1. 第 40 行：按 `STREAM_WINDOW_BYTES`（默认 4 MiB）分窗整块读完再转发。对应 `_read_window` + config 默认 `4194304`。
2. 第 66 行表格：窗口整块读完才吐。与不变式 1 一致。
3. 第 293-310 行：不把客户端 Range 原样转发；无 Range → 200 且 `Content-Length` 取自首窗 `Content-Range` 完整长度；有 Range → 206 + `Content-Range` + 对应 `Content-Length`；416 透传 `bytes */L`；502 = 发头前失败，发头后为断连。代码始终写入 `Content-Length`（通配 `complete_length=*` 在发头前失败，见 `test_wildcard_complete_length_fails_before_streaming`）。

偏差仅 F1：换对时 TikHub 失败发头前走全局 500，而不是表里的 502。`.env.example` 的 `STREAM_WINDOW_BYTES=4194304` 与代码默认一致。

## 7. 删改的旧测试

基线 `d848c7c` 有 37 个 `test_*`，本 diff 删除/重写 10 个名字不再出现的用例：

| 旧用例 | 原锁行为 | 新覆盖 |
|---|---|---|
| `test_client_range_cdn_response_matrix` | 客户端 Range **原样**转 CDN，组合 C1–C8 | 语义已否决（README「不原样转发」）。200/206/416/500/畸形 Content-Range 拆到新用例 |
| `test_range_start_zero_accepts_cdn_full_response` | 起点 0 的 200 整文件，可按请求 Range 截短 | `test_range_start_zero_accepts_cdn_full_response_when_file_fits_window` 只覆盖「文件 ≤ 本窗」的 `bytes=0-`。真实 CDN 对有界 Range 回 206，旧截短路径按环境事实不可达 |
| `test_upstream_disconnect_terminates_response_with_error` | 中途断流立即 `ReadError` | 现为重试后再失败。发头前：`test_first_window_short_body_returns_502_json`；发头后：`test_same_window_three_failures_disconnects_after_headers` |
| `test_initial_range_end_mismatch_uses_cdn_range` | 短 206 当成功、用 CDN 的 end | 现为失败：`test_mid_file_short_content_range_fails_before_streaming`（与 spec「非 EOF 短窗失败」一致） |
| `test_initial_range_missing_content_length_is_allowed` | 响应可以没有 `Content-Length` | `test_initial_range_missing_content_length_still_sets_client_length`（现始终有，符合要点 3） |
| `test_initial_range_wildcard_complete_length_is_allowed` | `bytes x-y/*` 成功 | `test_wildcard_complete_length_fails_before_streaming`（有意反转：没有总长就发不出准确 `Content-Length`） |
| `test_no_range_without_cdn_content_length_fails_before_streaming` | 无 Range 的 200 缺 CL → 502 | 现始终带 Range 窗；206 缺窗 CL 仍成功：`test_no_range_206_without_window_content_length_still_succeeds`。200 缺 CL：`test_bounded_range_cdn_200_without_content_length_is_ignored_range` |
| `test_bounded_range_accepts_cdn_full_response_without_content_length` | 200 无 CL 当成功 | 现失败，同上 |
| `test_bounded_range_cdn_full_response_early_end_raises` | 200 无 CL 短 body 抛未处理异常 | `test_bounded_range_cdn_200_without_content_length_early_end_is_502`（现 502） |
| `test_memory_constant_no_full_body_read` | xor 入参 ≤ `STREAM_CHUNK_SIZE` | `test_memory_bound_is_window_not_full_file`（现按窗 xor，上限是窗口而非 chunk） |

无「旧锁行为既未有意改掉、又无新用例」的漏项。

## OCR 前置（status=reviewed，不是 skipped）

`ocr-review --from d848c7c --to ef9af01`，`status=reviewed`，profile=minimax，5 条 finding，均为测试文件。P1 两问后全部 ≤P3：

| 工具标注 | 本仓判定 | 两问 |
|---|---|---|
| `_slice_plain` 重复解析 suffix / low | P3 | 不触发生产错误 |
| `tracking_xor` 上界用 `<=` / low | P3 | 断言仍能被 M0 打红 |
| `repr(coro)` 不含 `_read_window` / medium | 不成立（记 backlog） | venv 3.13.12：`repr` 含函数名 |
| `WINDOW_SIZES` 含 `FILE_SIZE+10` 依赖 harness clip / low | P3 | 参数化仍在测「窗比文件大」 |
| 403 换对测试未本地锁 at-most-once / low | 不成立 | 已有 `media_calls == [SPH_CODE, SPH_CODE]`；`test_second_403_does_not_refresh_again` 锁第二次 |

## backlog（存量 / P3，不计入 verdict）

- F1 的修复（发头前换对 `ProviderError` → 502）建议下一轮修，不阻塞本轮 pass。
- `_cancel_task` `except BaseException: pass` 过宽；实测不吞外层取消。
- 401/404/410 与 403 同分支，无单独测试。
- 发头后纯 `CancelledError`（非 `aclose`）无独立 ASGI 用例。
- spec 要点 4「窗口大小」口语上是 `STREAM_WINDOW_BYTES`，代码用 `end-start+1`（可能被客户端 Range 夹小）。真实 CDN 回 206，Q1 否。
- OCR 四条测试味道（suffix 双解析、tracking 上界、`FILE_SIZE+10`、coro repr 前瞻）。
- 存量 issue #13/#11 的「无法验证却报成功」不在本 diff。

## 测试命令

```
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m pytest tests/ -q
```

审查前：305 passed（12.66s）。变异全部还原后：305 passed（7.42s）。`git status` 在写入本文件前对实现文件干净。
