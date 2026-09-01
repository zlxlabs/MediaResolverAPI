# 视频号流式下载端点：降层审查 verdict

审查范围：`b6fa3b8703ef044b140acd187956bb22ffc80ecc..54ee346e47bd81aa9510dc705701c0752dfacc15`（H0 固定）

项目风险等级：`internal`。本轮视角是部署形态、不可逆流式动作、资源账本和出站行为；不是重复逐格审查 Range 矩阵。

## Verdict

**fail**：发现 1 条 P1。另有 2 条 P2，均不单独阻塞但需要主脑决定是否拆修复卡。

## 本轮新证据

- 指定解释器全量回归：`298 passed, 166 warnings in 4.13s`。
- 真实本地 Uvicorn + 实际视频号短 Range：`206`、32 字节、`Content-Range: bytes 0-31/435768323`。
- 用真实 `httpx.AsyncClient` 访问本地 HTTP chunked 上游、再走实际端点 handler：上游 200 无 `Content-Length`，只正常结束 5 字节时，端点完成为 `200`、不带 `Content-Length`、客户端收到 5 字节。
- 响应头后的受控探针：已知长度短流在已发送 `200` 头后抛 `UpstreamDisconnected`；解密函数异常在已发送 `200` 头后抛异常；取消中的生成器 `aclose_count=1`。
- 两个独立 Python 进程分别把同名 `stream_limiter.active` 设为 1，互不影响，证明不是跨进程共享计数器。
- OCR 前置扫描已启动但约 70 秒没有返回完整 envelope，随后停止；不能把它记为“扫描干净”。

## 降层三问

### 1. 终态成功前的不可逆动作，以及响应头后的失败

成功交接前，端点先在 `app/api/stream.py:365` 打开 CDN 流，再在 `app/api/stream.py:460-471` 把流所有权移交给 body iterator，并在 `app/api/stream.py:473-478` 创建 `StreamingResponse`。从此响应头和正文可能已经发送，外层 `finally` 只在尚未交接时关闭流（`app/api/stream.py:479-481`）。

响应头后可能发现的失败包括：

- 上游迭代抛出 `httpx.TransportError`/`httpx.StreamError`/`OSError`，或检测到已知长度的正文不足；捕获位置是 `app/api/stream.py:320-327`，动作是记录错误后重新抛出。客户端已经看到 200/206 头和部分正文，不会得到新的 JSON 502。
- 解密 `xor_chunk(...)` 在 `app/api/stream.py:300` 抛出未被 `_UPSTREAM_FAIL` 覆盖的异常；仍会经过 `app/api/stream.py:328-336` 的关闭逻辑，但响应状态无法改回 5xx。
- 对于无 `Content-Length` 的 200 流，`app/api/stream.py:411-439` 令 `end=None` 且不声明长度；`app/api/stream.py:304-305` 因 `end is None` 不检查正常 EOF。上游短流可以作为成功 200 完成，这是本轮 P1 finding F1。

客户端表现不是统一的：已知长度/异常抛出路径通常表现为 200/206 加短正文和传输/长度错误；无长度且上游以合法 chunked 终止的短流表现为成功 200 加短正文，HTTP 层没有错误。并发槽的生命周期独立于 CDN 流：`app/api/stream.py:267-276` 在请求依赖中持有槽，`app/api/stream.py:117-131` 的 `finally` 释放一次；现有阻塞流测试 `tests/test_stream_wechat_channels.py:981-1027` 证明流中途不会提前释放。

### 2. 并发守卫值是否在部署形态下唯一

计数器确实是模块级进程内存对象：`StreamLimiter.active` 在 `app/api/stream.py:103-109`，唯一模块实例在 `app/api/stream.py:143`；上限从同一进程内的 `settings.MAX_CONCURRENT_STREAMS` 读取（`app/api/stream.py:121-124`）。没有 Redis、数据库、文件或其他跨进程存储。

实际探针得到两个独立进程各自 `active_before=0`、各自设为 `active_after=1`。因此：

- 当前仓库展示的 Docker `CMD`（`docker/Dockerfile:33`）和 compose 服务（`docker/docker-compose.yml:1-17`、`docker/docker-compose.deploy.yml:3-23`）是单容器、默认单 Uvicorn worker；该默认形态下限制成立。
- 显式 `uvicorn --workers N` 时每个 worker 都有独立计数器；多副本时每个副本也独立计数。全局并发数可约为各进程/副本上限之和，README 的 `429` 说明（`README.md:40`、`README.md:293`）在这些形态下不准确。

### 3. 保护的是写入还是行为

`_SPH_CODE_PATTERN` 只在 handler 内检查路径短码（`app/api/stream.py:145`、`app/api/stream.py:346-347`），它阻止的是把非法字符串作为短码继续处理，并不是对出站请求目标做主机/协议约束。

实际出站值来自 provider 响应：provider 从外部数据取 `media.full_url`（`app/services/providers/tikhub.py:789-813`），handler 原样交给 `open_cdn_stream`（`app/api/stream.py:365-367`）；HTTP 客户端还开启了 `follow_redirects=True`（`app/api/stream.py:70-75`）。当前 handler 没有其他 query/body 直接传 URL 的路径；客户端只能传短码，`_fetch_media` 再按短码拼 TikHub share URL（`app/services/providers/tikhub.py:763-771`）。

所以短码校验没有覆盖 provider 输出和重定向行为；若 provider 响应或 CDN 重定向被操纵，服务会向任意外部地址发请求并把响应带回流。这个出站目标信任问题在基线 `b6fa3b8` 的 `app/api/stream.py:363-368` 已存在，本轮 diff 没有新增该行为，列入 backlog，不作为本轮 finding。

## 另外三个必查点

### 4. 资源生命周期

结论：正常客户端断开、上游异常和请求前取消路径均有关闭 CDN 流并释放槽的路径，未发现本次 diff 引入的双重释放。

- 客户端取消：`asyncio.CancelledError` 被显式记录并重新抛出（`app/api/stream.py:306-312`），随后 `finally` 关闭 `first_stream`（`app/api/stream.py:328-336`）；受控取消探针得到 `aclose_count=1`。
- 生成器关闭：`GeneratorExit` 路径同样经过上述 `finally`（`app/api/stream.py:313-319`）。现有测试 `tests/test_stream_wechat_channels.py:817-836` 验证生成器关闭会调用 `aclose`。
- 上游读异常/已知长度不足：`_UPSTREAM_FAIL` 在 `app/api/stream.py:28-34`，异常处理在 `app/api/stream.py:320-327`，关闭仍由 `finally` 完成；现有测试 `tests/test_stream_wechat_channels.py:511-527` 验证异常和关闭。
- 响应前异常：流还由 handler 持有时，`app/api/stream.py:344` 和 `app/api/stream.py:479-481` 负责关闭；请求前取消的槽释放由 `app/api/stream.py:129-131` 完成，现有测试 `tests/test_stream_wechat_channels.py:839-925` 覆盖。
- 交接后只有 `_iter_decrypted` 关闭 CDN，外层因 `first_stream=None` 不再重复关闭；槽只在 `_stream_slot` 的 context manager `finally` 中释放一次。

`finally` 内关闭异常只捕获 `Exception`（`app/api/stream.py:331`），不会吞掉 `CancelledError`；这使取消能继续传播，但如果关闭操作自身再次被取消，代码不能保证关闭完成。该边界未在本 diff 中新增为独立 P1。

### 5. 凭据泄漏面

未发现本次 diff 把 `decode_key`、`X-API-Key` 或 CDN 签名直链写入 stream 端点的日志或 JSON detail：

- stream 的 logger 调用只有 `app/api/stream.py:270-275`（短码、active）、`app/api/stream.py:307-312`（短码、offset）、`app/api/stream.py:314-319`（短码、offset）、`app/api/stream.py:321-327`（短码、offset、上游异常）和 `app/api/stream.py:331-336`（短码、关闭异常）。没有记录 `decode_key`、API Key 或 `full_url`。
- `_json_http_error` 的动态消息在 `app/api/stream.py:351-353`、`app/api/stream.py:382`、`app/api/stream.py:441`；当前 provider 的异常消息只含短码/端点/状态/区间元数据（`app/services/providers/tikhub.py:423-458`、`app/services/providers/tikhub.py:792-810`），不含三个凭据值。
- `app/api/stream.py:358` 明确返回 `Invalid decode_key`，而不是异常原文；Range 错误（`app/api/stream.py:164-195`）、429（`app/api/stream.py:275`）和 CDN 416（`app/api/stream.py:375-378`）也都是固定文案或数字状态。
- `verify_api_key` 只比较 Header、不记录值（`app/api/deps.py:15-20`）。

存量的全局异常日志 `app/main.py:116-123` 会记录任意异常文本；若未来 `httpx` 异常文本带出非法 provider URL，应另拆日志脱敏审查，本轮未将其归因给本 diff。

### 6. README 与实现逐条对照

| README 条款 | 实现 | 结论 |
|---|---|---|
| `README.md:274-276`：Range 原样转 CDN，实际终点依 CDN 响应 | `app/api/stream.py:360-367` 原样传有效 Range；206 对账在 `388-410`，客户端明确终点更大时收紧到客户端终点（`394-397`） | 正常合法 CDN 响应一致；异常超发时实现额外收紧，README 未明确该保护 |
| `README.md:276`：无 Range 收到不完整 206，响应头前 502 | `app/api/stream.py:405-410` 在响应创建前检查数字 total 与末字节 | 一致 |
| `README.md:281-283`：无 Range 返回完整文件；200 无 `Content-Length` 可 chunked | `app/api/stream.py:411-439` 允许无长度 200，且 `end=None` 时不验证正常 EOF | 条款组合有缺口：允许的 chunked 形态无法证明完整终态，触发 F1 |
| `README.md:287-294`：401/416/429/502 状态含义 | 401 由 `app/api/deps.py:15-20`；416 由 `app/api/stream.py:164-195`、`368-378`；429 由 `app/api/stream.py:121-131`、`267-276`；出站前的 502 由 `349-358`、`380-441` | 单进程且响应头前失败时一致；多进程/多副本的 429 不准确，响应头后的流失败不会变成 502（F2/F3） |

## Findings

### F1 — P1：无长度 chunked 短流以完整成功返回

- **触发路径**：客户端带有效短码、不带 Range；TikHub 返回 `(full_url, decode_key)`；CDN 返回 `200 Transfer-Encoding: chunked`、无 `Content-Length`，发送部分加密 mp4 后正常结束。`app/api/stream.py:386` 得到 `cdn_content_length=None`，`411-413` 保持 `end=None`，`427-439` 不声明长度，`463-478` 以 200 流出；`_iter_decrypted` 的 `304-305` 不会检查无界流的 EOF。
- **实际证据**：真实 `httpx.AsyncClient` + 本地 HTTP chunked 上游经实际端点 handler 复现：输出 `status 200`、`content_length_header False`、`body_bytes 5`，且 handler 正常完成；这不是只由代码形态推断。README 又明确允许无长度 200 chunked（`README.md:283`）。
- **后果**：客户端收到 HTTP 层成功、正常结束的短正文，可能把不可播放/损坏的 mp4 当作完整下载结果；该错误没有状态码或传输异常提示。
- **P1 两问**：①会在真实使用方式触发吗？**会**：它走的是 README 明确支持的 CDN 200 无长度 chunked 分支，受控真实 HTTP 上游已量到该路径的成功短流表现。②后果能接受吗？**不能**：终态是完整可播放 mp4，成功返回短文件属于静默错误，命中 internal 的 P1 红线。
- **代码位置**：`app/api/stream.py:386-439`、`app/api/stream.py:463-478`、`app/api/stream.py:304-305`；文档条款 `README.md:281-283`。

### F2 — P2：响应头后的上游/解密失败不能兑现 502 契约

- **触发路径**：流已由 `app/api/stream.py:473-478` 启动后，`aiter_bytes` 在 `293-305` 抛出上游异常，或 `xor_chunk` 在 `300` 抛出解密异常；`320-327` 只记录并重新抛出，`328-336` 关闭 CDN。客户端看到已经发送的 200/206 和部分正文，不会得到 JSON 502。
- **实际证据**：受控 handler 探针记录已发送 `start=200` 后，已知长度短流抛 `UpstreamDisconnected`；解密异常同样在 `start=200` 后抛出。现有真实路径测试 `tests/test_stream_wechat_channels.py:511-527` 也锁定上游断开为客户端 `httpx.ReadError`，而非 502。
- **后果**：客户端需依赖传输/长度错误判断失败；状态码仍是成功状态，重试/错误展示语义与 README 的“502 上游/CDN 失败”（`README.md:294`）不一致。
- **P1 两问**：①会触发吗？**会**：上游断流、读取超时和解密异常都发生在流式传输的真实时序中，现有测试和受控探针已走到该路径。②后果能接受吗？按 P1 红线**不成立**：已知长度或异常抛出路径通常会让客户端看到传输/长度错误，并非静默成功完整文件；因此降为 P2。无长度正常 EOF 的静默短流另由 F1 判 P1。
- **代码位置**：`app/api/stream.py:293-336`、`app/api/stream.py:460-481`；README `:294`。

### F3 — P2：多 worker/多副本时 429 不是全局并发限制

- **触发路径**：用 `uvicorn --workers N` 或启动多个副本；每个进程分别加载 `app/api/stream.py:143` 的 `StreamLimiter`，各自把 `active` 计到 `MAX_CONCURRENT_STREAMS` 后仍可继续接受其他进程/副本的槽位。
- **实际证据**：两个独立 Python 进程各自将模块对象从 0 设为 1，互不影响；默认 Docker 启动虽是单 worker、单副本，但代码/README 未限制部署者使用多 worker/副本。
- **后果**：全局并发可能超过 README 所理解的默认 4，CDN/本机带宽保护被放大；429 仍对单进程局部准确。
- **P1 两问**：①当前真实默认部署会触发吗？**不会**：仓库提供的 Docker CMD 未启用多 worker，compose 未声明 replicas。显式多 worker/多副本时会触发。②后果能接受吗？**本仓 P1 不成立**：这是未声明部署变体下的契约不精确，当前单进程真实形态不损坏数据、不造成静默文件错误，降为 P2。
- **代码位置**：`app/api/stream.py:103-143`、`app/api/stream.py:267-276`；Docker `docker/Dockerfile:33`；README `:40,293`。

## Backlog（存量，不阻塞本轮）

1. **P2 安全候选：provider 输出 URL 未限 host/协议，且启用跟随重定向。** `app/api/stream.py:365-367`、`:70-75` 与基线 `b6fa3b8` 的 `:363-368` 均存在；`media.full_url` 来自 provider 外部响应（`app/services/providers/tikhub.py:795-813`）。这不是本轮 diff 新增，不计入 verdict finding。
2. **资源存量：重复创建 `httpx.AsyncClient`。** `app/api/stream.py:71-76` 创建的 client 随即被 `:81-87` 的第二个 client 覆盖，未被关闭；基线相同，故不计入本轮 finding。
3. **日志存量：全局异常 handler 记录异常原文。** `app/main.py:116-123` 可能扩大异常文本泄漏面；本轮 stream diff 未新增该 handler。
4. 收件箱 issue #11（既有 500 逃逸/重复构造 httpx）不属于本次 `b6fa3b8..54ee346` 的新增行为，保持 backlog。

## 验证命令

```text
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m pytest tests/ -q
298 passed, 166 warnings in 4.13s
```

未修改 `app/**`、`tests/**`、`README.md` 或其他既有文件；仅新增本 verdict。
