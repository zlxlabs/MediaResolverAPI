# wx-stream-window R2 verdict

verdict: pass

审查对象冻结：`d848c7c..62e1810`（全量）；增量 `fa9a5ce..62e1810`（R1-F1 修复）。审查期间的新提交不属于本轮。
本轮新证据（生成结论之后才拿到）：审查前 `pytest tests/ -q` **307 passed**；`TestClient`/ASGI 探针（误拒矩阵、槽位、内存峰值、xor 恒等、换对旧 key、断连日志、aclose 打 URL）；三处与 R1 不同的注入红验；OCR `status=reviewed`（minimax，3 条测试/可维护性意见）。不把 R1 verdict 当本轮证据。

## 增量审四问（`fa9a5ce..62e1810`）

1. **本轮是否只修登记在案的 finding（F1）？** 是。实现只在 `_read_window_retry` 换对处把 `_fetch_media` 的 `ProviderError` 收成 `UpstreamDisconnected`（`app/api/stream.py` 370-373）；另加两条锁定测试（发头前 502、发头后断连）。`VideoNotFoundError` 是 `ProviderError` 子类，同一 `except` 覆盖，无需第二条分支。
2. **是否新增未经批准的抽象？** 否。无新类型、无新模块、无新配置项。
3. **状态 / 事实源 / fallback 是否无依据增加？** 否。`state` 字段未增加；只是把已有异常转换成已有的 `UpstreamDisconnected`，与 F1 建议修法一致。
4. **是否留下双路径？** 否。发头前：换对失败与首取失败一样走 502 JSON；发头后：同一异常经 `_iter_windows` 断连。不是同一失败在两处用不同方式处理。

四问均为「是/否」中的合规答案，不计入新 P1。

## findings

| 编号 | P 级 | 违反的不变式/红线 | 文件与行号 | 触发路径 | 证据 | 建议修法 |
|---|---|---|---|---|---|---|
| F2 | P2 | spec 要点 6「单流内存上限为 2 个窗口」 | `app/api/stream.py` `_iter_windows` 440-454 + `_consume_cdn_body` 270-278 + `xor_chunk` 副本 | 任意多窗请求：yield 当前窗明文时预读下一窗，`b"".join(chunks)` 与 `xor_chunk` 副本叠在一起 | 探针 `test_probe_memory_peak_sync`：`STREAM_WINDOW_BYTES=65536` 时 `peak_bytes=262144`，`snap={'current': 65536, 'decrypted': 65536, 'chunks': 65536, 'join': 65536}`，**实际 4.00 个窗口**（spec 写 2） | 先把当前窗吐完再启动 prefetch，或 xor 原地/避免 join 双份；或把 spec 改成「可见副本 ≤4 窗」。不阻塞本轮 |

P1 两问（F2）：
1. 真实使用方式下会被触发吗？会。默认 4 MiB 窗口的完整下载只要超过一窗就会在 prefetch join 时碰到。
2. 触发了后果能否接受？能接受：单流约 16 MiB 对 8 MiB、4 路约 64 MiB 对 32 MiB，有界、不丢字节、不静默错、不崩溃。不是数据丢失/静默出错/崩溃/越权/损坏他人数据。故 **不是 P1**。

无 P1。OCR 三条均为测试/可维护性，P1 两问后全部 ≤P3，见下。

## 1. 误拒 / 假失败

每条都用 `TestClient` 探针实测（临时 `tests/_r2_review_probes.py`，已删除），不靠读代码推断。

| 输入 | 查了什么 | 结论 |
|---|---|---|
| `Range: bytes=200000-9999999`（M 超过文件末尾） | 状态码 / `Content-Range` / 正文 vs `PLAIN[200000:]` | **不误拒**：206，`bytes 200000-599999/600000`，`Content-Length=400000`，正文逐字节匹配 |
| `bytes=0-0` | 1 字节闭区间 | **不误拒**：206，`bytes 0-0/600000`，`Content-Length=1`，正文 `PLAIN[0:1]` |
| 文件恰好等于窗口（`STREAM_WINDOW_BYTES=FILE_SIZE`） | 无 Range 完整下载 | 200，1 次 CDN，正文完整 |
| 文件恰好等于窗口整数倍（窗 200000，文件 600000） | 开窗次数 | 200，3 次 CDN，正文完整 |
| 恰好 1 字节的文件 | 无 Range 与 `bytes=0-0`（CDN 按 key 配套 1 字节密文） | 200 / 206，正文为那 1 字节，`Content-Range: bytes 0-0/1` |
| `STREAM_WINDOW_BYTES=1024` < `STREAM_CHUNK_SIZE=65536` | 完整下载 | 200，586 窗，正文匹配 |
| `STREAM_WINDOW_BYTES=4096` < 131072 | `bytes=0-200000` | 206，正文匹配，49 窗 |
| Range 大小写与空白：`Bytes=` / `BYTES=` / ` bytes=0-0 ` / 头名 `RANGE`/`range` / `bytes= 0-0` / `bytes=0- 0` | 7 个变体 | 全部 206 + 1 字节。`parse_byte_range` 用 `strip` + `lower().startswith("bytes=")`，HTTP 头名由 Starlette 大小写不敏感 |

未发现合法输入被错误拒绝或截短。

## 2. 资源与并发（运行时视角）

部署事实：`MAX_CONCURRENT_STREAMS=4`、单 worker Docker。

**峰值内存（Python 可见副本，不含 httpx 内部缓冲）**

在 `_iter_windows` 吐当前窗期间，prefetch 的 `_consume_cdn_body` 会 `list` 攒块再 `join`。探针记账：

```
peak_bytes=262144 peak_at=consume-join
snap={'current': 65536, 'decrypted': 65536, 'chunks': 65536, 'join': 65536}
multiple=4.00  spec=2.0
```

即：**当前窗密文 + xor 明文副本 + 预读窗 chunks + join 临时量 = 4 个窗口**，是 spec 要点 6「2 个窗口」的 **2 倍**（4 窗 / 声称 2 窗）。默认 4 MiB 窗 → 单流约 16 MiB；×4 路约 64 MiB（spec 写 32 MiB）。见 F2。

**429 槽位**

`Depends(_stream_slot)` 是 yield 依赖。FastAPI 0.135.1 / Starlette 0.52.1 把 `AsyncExitStack` 挂到流式响应结束之后（`fastapi/routing.py` 与 `AsyncExitStackMiddleware`）。

- 在 `_iter_windows` **内部**采样 `stream_limiter.active`：`n=11 unique=[1]`，生成器跑完后 `active=0`。槽位在发头后、正文转发期间一直占用，不是「返回 `StreamingResponse` 就释放」。
- 连续 5 个中途断开（`httpx.stream` 读 ≥1 KiB 后退出）后第 6 个：**200**，每次 `enter → yielded active=1 → exit active=0`，**不是 429**。槽位在生成器结束/取消后释放。

## 3. 时间维度

读 `app/services/wechat_channels_crypto.py` 206-207：`absolute_offset >= KEYSTREAM_SIZE`（131072）时 `return data`，不生成密钥流。

探针：

- `xor_chunk(payload, KEY_A, 131072) is payload` 且 `== payload`；`offset=131073` 对 `KEY_B` 同样恒等；跨边界 `offset=131062` 只改前 10 字节、尾部恒等。
- 第二窗起 `start==131072` 回 403 触发换对后，`xor_chunk` 仍只用旧 `KEY_A`（`unique_keys=[55516695]`），正文仍等于 `PLAIN`。与环境事实「两 URL 偏移 ≥ 131072 逐字节相同」一致：预读已完成、客户端停顿超过 CDN URL 有效期再换对，旧 key 解密不会改字节。

## 4. 错误信息与日志

发头后失败只能断连：探针把第二窗 `disconnect_after=10`，`TestClient` 仍给 200、`body_len=0`（头已发出，正文不足）。`_iter_windows` 打了：

```
wechat stream upstream failed sph_code=AOzokRxWHz offset=0: upstream closed before range complete
```

含 `sph_code` 与 `offset`。`CancelledError` / `GeneratorExit` 走 `logger.warning` 同样带这两个字段。注意：prefetch 失败时日志里的 `offset` 是**当前正在 yield 的窗**，不是失败窗起点（本例失败窗 65536，日志仍是 0）——生产能定位请求，但不能直接看到失败窗，记 backlog。

`rg -n 'logger\.(error|warning|info|debug).*url|url=\{\}' app/api/stream.py` 命中唯一一处：

```
334: logger.error
335: "wechat stream failed to aclose upstream url={} start={}: {}"
```

探针让 `aclose` 抛 `RuntimeError("aclose boom")`：

```
wechat stream failed to aclose upstream url=https://cdn.test/v1 start=0: aclose boom
```

`log_has_url=True`，`log_has_sph=False`。生产上这里会是带 token 的 `full_url`。触发面是 aclose 失败（少见），internal 档 P1 红线不含凭据进日志；无法溯源到 spec 不变式，**降为 P3 backlog**，不进 findings 表。

断连主路径（`_iter_windows` 的 cancel / GeneratorExit / `_UPSTREAM_FAIL`）**没有**打 `full_url`。

## 5. 熵增审查

**`fa9a5ce..62e1810` 新增：** 5 行 `try/except ProviderError` + 两条测试。无新抽象、无转发-only 层、无第二消费者缺失的通用化。

**全量 `d848c7c..62e1810` 再扫一遍（R1 未当作本轮证据，本轮独立看）：**

| 抽象 | 消费者 | 裁决 |
|---|---|---|
| `CdnHttpError` | `_read_window` 产生；retry 与端点分叉 | 控制流，不是转发层 |
| `_read_window_retry` | 端点首窗/后缀探针 + `_iter_windows` prefetch | 两处调用 |
| `_iter_windows` | 仅 `body()` | 单消费者，但隔开发头前/后，必要 |
| `_cancel_task` | 仅 `_iter_windows finally` | 单消费者 helper，可内联，≤P3 味道 |
| `STREAM_WINDOW_BYTES` | 实现 / README / `.env.example` / 测试 | spec 要点 1，有第二消费者 |

未发现 R1 漏记的转发-only 层或无第二消费者通用化。不新增 P2。

## 6. 测试自身的可信度

三条锁核心不变式的测试，注入点与 R1（`_consume_cdn_body` 边读边 xor、`_read_window` 六路径）均不同。每次 `sed -n`/`rg` 确认哨兵，跑完 `git checkout` 还原。

| # | 锁的测试 | 注入点（确认行） | 结果 |
|---|---|---|---|
| 1 | `test_refresh_provider_error_before_headers_returns_502`（F1 / 不变式 4 发头前 502） | 去掉 370-373 的 `except ProviderError` 包装，改为直接 `_fetch_media`；`# RED-VERIFY r2-m1` 在 `stream.py:370` | **红** `assert 500 == 502`；日志 `Unhandled exception: tikhub down on refresh` |
| 2 | `test_range_matches_full_download_slice[bytes_plain_from_boundary-65536]`（要点 7 / 偏移 ≥131072 恒等） | `xor_chunk` 早退改为 `return b"\xff" * len(data)`；`# RED-VERIFY r2-m2` 在 `wechat_channels_crypto.py:207`。`--assert=plain`（pytest 改写大 bytes 不等会卡死） | **红** `assert resp.content == expected` |
| 3 | `test_client_disconnect_acloses_upstream`（不变式 6 取消清理） | `_cancel_task` 开头 `return`；`# RED-VERIFY r2-m3` 在 `stream.py:413` | **红** `assert all(...aclose_called)` 为 False；日志 `generator closed sph_code=AOzokRxWHz offset=0` |

三处都能被改坏实现打红。注入均已还原。

## OCR 前置（status=reviewed，不是 skipped）

`ocr-review --from d848c7c --to 62e1810`，`status=reviewed`，profile=minimax，3 条。外部 severity 只当输入。

| 工具标注 | 本仓判定 | 两问 |
|---|---|---|
| `_slice_plain` 与端点各自内联解析 `bytes=-N` / low | P3 | 不触发生产错误；与 R1 OCR 同形 |
| FakeCdn `aiter_in_progress` 依赖 aclose / low（复核 unverified） | P3 | 测试夹具味道，生产路径不走 FakeCdn |
| 不可换对的 `CdnHttpError` 仍扣 `state["budget"]` / low（复核 unverified） | P3 | 该分支随后立刻 raise，当前请求会中止，扣预算无观察后果 |

## backlog（存量 / P3，不计入 verdict）

- F2：峰值 4 窗 vs spec 2 窗；可改实现或改 spec。不阻塞。
- `_read_window` aclose 失败日志带 `full_url`（含 token），无 `sph_code`。
- 发头后 prefetch 失败时日志 `offset` 是当前窗不是失败窗。
- `_cancel_task` `except BaseException: pass` 过宽（R1 已测不吞外层取消）。
- 401/404/410 与 403 同分支，无单独测试。
- 发头后纯 `CancelledError`（非 `aclose`）无独立 ASGI 用例。
- OCR 三条测试/预算记账味道。
- 存量 issue #13/#11 的「无法验证却报成功」不在本 diff。

## 测试命令

```
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m pytest tests/ -q
```

审查前：307 passed（12.93s）。探针与三处注入全部还原后：307 passed（9.32s）。`git status` 在写入本文件前对实现文件干净。
