# 修复报告：微信视频号流式解密下载端点

- **Dispatch-Id**：dlg-20260901-100729-1be955
- **分支**：`card/MediaResolverAPI-20260901-04`
- **HEAD**：`cda33c7 feat: add wechat channels streaming decrypt endpoint`
- **结论**：`GET /api/stream/wechat_channels/{object_id}` 已落地。每次请求先向 TikHub 取当次配套的 (CDN 链接, decode_key)，成功后才开始 `StreamingResponse`；前 131072 字节边拉边 XOR，其余透传。客户端拿到的是可播 mp4。全量 220 passed。探活最终 `206` + 128KiB + 偏移 4..8 为 `ftyp`。

## 现场（pickup）

工作树 `MediaResolverAPI-20260901-04`，开工 HEAD 即卡面 Base `55a6313`。本工作区无交接单。同 dispatch id 的 systemd unit 是本派发现场，未当作他人占用。

- 存活探针不可用（`archive_orphan_debts.py`：`current session id missing`），改跑 `unaccepted_cards.py --all-repos`：跨仓汇总 6 仓 14 条。无法区分有主/无主，不拿总数冒充无主数。要处理请另开对话，不要在本次接手里顺手补账。
- 本仓 open issue：#8（快手/Instagram by_url，与本卡无关）。
- 已合并：#7 元数据解析层、#6 密钥流 spike。open PR #9 是设计文档分支 `feat/sph-design`。
- 默认分支是 `origin/master`（无 `origin/main`）。
- AGENTS.md / CLAUDE.md 在本工作树不存在（`wc` 合计 0）。

## 搬迁策略

选 **spike 薄转发**，不保留两份独立副本。

理由：算法已被两组真实样本全量验证，两份副本会在下次改 LRU / 偏移时漂移。`scripts/spike/wechat_keystream.py` 把仓根插入 `sys.path` 后 `from app.services.wechat_channels_crypto import KEYSTREAM_SIZE, generate_keystream, xor_chunk`。`verify_keystream.py` 与 `README.md` 未改。

离线验证（本机 `/tmp/sph-spike/` 样本仍在）实际输出：

```
constraint_A: 131072/131072 bytes match
sample1_plain[:16] 000000206674797069736f6d00000200
sample2_plain[:16] 000000206674797069736f6d00000200
constraint_B: sample1[4:8]==ftyp and sample2[4:8]==ftyp
constraint_C: 131072/131072 bytes match (chunked, 7000B)
EXIT:0
```

已知向量（硬编码进 `tests/test_wechat_channels_crypto.py`，不依赖 `/tmp`）：
`decode_key=55516695` 密钥流前 12 字节 `7769d98df51778766238a697`，与明文头 `000000206674797069736f6d` 异或可还原 `ftyp isom`。

## 行为

1. **无状态**：每次请求（含续传）都现调 `fetch_wechat_channels_media`；不缓存 (链接, key)。允许按 decode_key LRU 缓存密钥流。
2. **先取后流**：TikHub / 首次 CDN 打开失败时响应头未发出，返回 JSON 5xx/429。`VideoNotFoundError` 与 `ProviderError` 一律 502 JSON（卡面要求 TikHub 失败是 5xx，不是残缺字节流）。
3. **Range**：客户端 Range 按明文绝对偏移，CDN Range 相同。无 Range → 200；有 Range（含 `bytes=0-`）→ 206 + `Content-Range`。
4. **续传**：上游短读/传输错误时，以已转发绝对偏移 N 重新取 detail，带 `Range: bytes=N-…`。超过 `STREAM_RESUME_MAX_RETRIES` 打 ERROR 并让生成器抛错（此时不能改状态码）。
5. **客户端断开**：async generator `aclose` / `CancelledError` 不续传，finally 里 `aclose` 上游。
6. **并发**：非阻塞信号量，超限 429 JSON，不排队拖到超时。下载结束（含异常）释放名额。
7. **TikHub object_id 瞬态**：实测 `{"object_id", "raw": false}` 多数成功，但偶发 `data` 变成微信错误包（无 `object_type`），单端点链记 retryable 后立刻 `VideoNotFoundError`。新方法内对该瞬态最多再试 2 次并打 WARNING，耗尽仍抛给端点转 502。不静默当成功。既有 `_fetch_wechat_channels` 签名/行为未改。

## 卡面期望值里需要显式提出的点

提出不算抗命。均按卡面实现，没有调宽断言。

1. **Range 第 7 格**：卡面是 `bytes=200000-300000`（闭区间 100001 字节），设计不变式 2 写的是 `200000-`（开到文件尾）。语义都是「完全在明文区」。实现与测试跟卡面闭区间。
2. **`bytes=0-` vs 不带 Range**：体逐字节相同；状态码按 RFC 分别是 206 与 200。测试比的是体，不是状态码。
3. **续传上限之后**：响应头已发出，只能截断连接。调用方靠 `Content-Length` 对不上 / 连接提前关知道出事。ERROR 日志给运维。没有改成空 yield 或假装成功。
4. **客户端断开**：调用方自己取消的，WARNING + 关上游。若只关本地生成器、不 `aclose` CDN，才是静默漏连接。
5. **429**：JSON 错误体，不把第 N+1 路塞进队列慢慢耗。
6. **探活第一次/第二次 502**：不是端点把 TikHub 失败流成残缺 mp4；是 object_id 查询当时返回 retryable 信封，JSON 502。第三次同 URL 得到 206 + `ftyp`。见下方探活。这不是断言被调宽，是上游瞬态；代码侧加了有日志的重试。

## 验证

```
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m py_compile \
  app/services/wechat_channels_crypto.py app/api/stream.py app/main.py \
  app/core/config.py app/services/providers/tikhub.py \
  tests/test_stream_wechat_channels.py tests/test_wechat_channels_crypto.py
COMPILE_EXIT:0

/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m pytest tests/ -q
220 passed, 95 warnings in 2.22s
PYTEST_EXIT:0
```

时序窄范围连续 5 轮（resume 两格 + 并发 429）：每轮 exit 0，`FIVE_X_FAIL=0`。

## 探活

本地 `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`（venv 里 `uvicorn` 脚本 shebang 指向已不存在的旧路径，改用 `python -m uvicorn`）。

对 `https://weixin.qq.com/sph/AOzokRxWHz`：

| 步 | 结果（不回显响应体 / 不打印 .env） |
|---|---|
| `POST /api/resolve` | HTTP 200，`success=True`，`video_id=14998022876670594427`，`video_url=http://127.0.0.1:8000/api/stream/wechat_channels/14998022876670594427` |
| `curl -r 0-131071 -o <file>` 第 1、2 次 | HTTP 502，148 字节 JSON，`detail` 为 WechatChannels all endpoints failed / retryable（TikHub 当时返回无 object_type 的信封） |
| 同命令第 3 次 | HTTP **206**，**131072** 字节，MIME `video/mp4`，`head_hex8=0000002066747970`，`ftyp_ok=True` |

curl 均显式 `-o` 文件。TikHub 按次计费，本探活含 resolve + 若干次 stream 侧 detail。

## 不变式自检

| 不变式 | 代码 | 锁死测试 |
|---|---|---|
| 2 解密边界 | `xor_chunk` + stream 按绝对偏移 | Range 7 格，体与「整文件再切片」逐字节相同 |
| 7 上游断流可续 | `_iter_decrypted` 重取 media + Range 起点=已转发绝对偏移 | `test_resume_matches_oneshot…` 50000 / 500000；上限格 `resume_opens == STREAM_RESUME_MAX_RETRIES` + ERROR |
| 8 客户端断开关上游 | generator finally / aclose | `test_client_disconnect_acloses_upstream` |
| 9 并发上限 | `StreamLimiter.try_acquire` 非阻塞 | 占满后 429，释放后 200 |
| 1 配套性 | 每次 `_fetch_media`，续传再取 | `test_each_request_fetches_media_fresh`；resume 格 `media_calls==2` |
| 4 内存恒定 | 只用 `aiter_bytes`，源码禁止 `aread` / `.content` / `readall` | `test_memory_constant_no_full_body_read` 峰值块 ≤ `STREAM_CHUNK_SIZE` |

## git

```
cda33c7 feat: add wechat channels streaming decrypt endpoint
```

`git show --stat --format= HEAD`：

```
 app/api/stream.py                    | 388 +++++++++++++++++++++++++++++++
 app/main.py                          |   2 +
 tests/test_stream_wechat_channels.py | 434 +++++++++++++++++++++++++++++++++++
 3 files changed, 824 insertions(+)
```

本卡另外两笔（同一分支）：

- `60db2af` feat: move wechat channels keystream into the production module
- `d71729c` feat: add wechat channels media fetch and stream settings

相对 Base `55a6313` 的 `git diff --numstat`：+1187 / -193，合计 1380（target 1100 / hard 2000，超目标未超硬顶）。主要体积在 stream 实现与 Range/续传/429 测试，没有另造基线文件。

未 push（卡面要求提交到分配分支，未要求开 PR / 推远程）。
