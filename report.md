# 微信视频号文档收尾与端到端验收

Dispatch-Id：`dlg-20260901-125501-0dc5e2`（续 `dlg-20260901-123818-e4c7aa`，上一轮 SIGTERM / exit 143，零提交）
Executor：cursor / cursor-grok-4.6-high
Branch：`card/MediaResolverAPI-20260901-10`
Base：`b6fa3b8703ef044b140acd187956bb22ffc80ecc`

## 结论

文档已按卡面写入并先提交。HTTP 端到端对 `POST /api/resolve`、`GET /api/platforms`、抖音回归均通过。**流式 Range 路径实测与预期不符**：三次 `Range: bytes=0-131071` 均 HTTP 502，不是 206 / 131072 / `ftyp`。未改 `app/**`。

## 文档落点

- `README.md` 第 5 行：`risk-tier: internal`（简介下、代码块外，可 grep，与 `.github/workflows/gate.yml` 的 `tier: internal` 一致）
- 平台表增加微信视频号 / 短链解析 ✅ / TikHub
- 降级链说明增加 `wechat_channels` 单源单端点、链内不判终态
- 新节 **「视频号的下载方式与它和其他平台的差别」**（平台表后、快速开始前）
- `data.platform` 枚举、`video_url` / `view_count` 字段说明、`GET /api/platforms` 示例补 `"wechat_channels": ["tikhub"]`
- 新接口节 **GET /api/stream/wechat_channels/{object_id}**（`/api/platforms` 之后）
- `docs/generic-fallback-engine.md` 链配置总表与超时表各加一行（单端 25 / 总预算 30）
- `CHANGELOG.md` `[Unreleased] Added` 一条

`docs/generic-fallback-engine.md` 第 5 节 `URL_FALLBACK_PLATFORMS` 仍只写 kuaishou/instagram，代码已含 `wechat_channels`。本卡只要求补两张表，未改该节，交给主脑。

## 回归

```
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m pytest tests/ -q
235 passed, 107 warnings in 2.37s
```

## 端到端实测

服务：`/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`
凭据：`.env` 单键 grep，`API_KEY` 长度 43、`TIKHUB_API_KEY` 长度 60。所有 `curl` 均 `-o` 文件。产物目录 `/tmp/wechat-e2e-dlg-20260901-125501-0dc5e2/`。

### 1. POST /api/resolve（视频号）— 通过

`curl -o resolve.json` body `{"url":"https://weixin.qq.com/sph/AOzokRxWHz","translate":false}`，HTTP 200。

| 断言 | 实测 |
|------|------|
| success true | true |
| platform `wechat_channels` | `wechat_channels` |
| author_name 晓辉博士 | 晓辉博士 |
| view_count null | null |
| video_url 含 `/api/stream/wechat_channels/` | `http://127.0.0.1:8000/api/stream/wechat_channels/14998022876670594427` |

其余：title「对谈张笑宇：AI重塑组织与生活方式」，like 42 / comment 8 / share 171 / collect 61，provider tikhub。日志：`sph` 短链无 object_id，走 by_url 链命中。

### 2. GET Range bytes=0-131071 — **实测与预期不符**

预期：HTTP 206、恰好 131072 字节、偏移 4..8 为 `ftyp`。
实测三次（间隔 2s，每次内部 media 查找已 3 次 retry）：

| 次 | HTTP | 字节 | detail（已打码） |
|----|------|------|------------------|
| 1 | 502 | 148 | `WechatChannels all endpoints failed for '[TOKEN]' [attempts=[{'endpoint': 'fetch_video_detail', 'decision': 'retryable'}]]` |
| 2 | 502 | 148 | 同上 |
| 3 | 502 | 148 | 同上 |

服务日志每次两次 `wechat_channels media lookup retryable, retrying` 后 502。流式端点按 object_id 重查 TikHub，与 resolve 用 share_url 命中不是同一条取数路径。代码注释写明 object_id 查询会偶发微信错误包，重试 3 次仍失败。

**上一轮 SIGTERM 前同一样本的额外证据**（未提交，但产物仍在 `/tmp/wechat-e2e-dlg-20260901-123818-e4c7aa/`）：media 查找一旦成功，Range 仍 502，detail 为 `CDN 206 response has complete length 435768323, expected 2450521066`。即 TikHub `file_size`（2450521066）与 CDN `Content-Range` 总长（435768323）对不上，`_reconcile_cdn_offset` 拒绝转发。本轮三次都卡在更早的 object_id 查找，没有再次打到这条。

未改实现、未改文档去迁就。建议主脑拆卡：object_id 查找失败率、以及 file_size 与 CDN 完整长度不一致。此两条 **不是** issue #11（500 逃逸 / 重复构造 httpx）。

### 3. 不带 Range 的 GET，取前约 2MB — 部分通过

`curl -o full-prefix.mp4`，约 2MB 后 SIGTERM。HTTP 200，`Content-Type: video/mp4`，`Accept-Ranges: bytes`，**`Content-Length: 2450521066`**（与 TikHub file_size 相同）。落盘 3670016 字节。

文件头 12 字节：`00 00 00 20 66 74 79 70 69 73 6f 6d`（isom，符合卡面备用断言）。
本机有 ffprobe：2MB 前缀与 3.6MB 截断均 `Invalid data found when processing input`（moov 未完整落入前缀）。

上一轮未及中断、CDN 实际发完的 435768323 字节文件（恰为上面 complete length）：

```
ffprobe rc=0 format=mov,mp4,m4a,3gp,3g2,mj2 duration=7352.213
streams: h264 + aac
```

解密转发本身能产出可识别 mp4。`Content-Length` 写成 2450521066 而 CDN 体约 415.6MiB，也属 **实测与预期不符**（文档写无 Range 返回完整文件；客户端会按错误长度等待）。

### 4. GET /api/platforms — 通过

HTTP 200，含 `"wechat_channels": ["tikhub"]`。九个平台键齐全。

### 5. 抖音回归 — 通过

README 示例 `https://v.douyin.com/xxxxx/` 是占位符。改用 fixture 样本 `https://v.douyin.com/-Q8et5ToUhs/`。
HTTP 200，success true，platform `douyin`，provider tikhub，author「程优秀🔆」，`video_url` 主机 `v5-dy-ov-experiment.zjcdn.com`，**不是**本服务 `/api/stream/`。

## 文档自洽

照 README 走了一遍：路径、字段名、鉴权头与实现一致。Range 的 206 契约是代码意图，实测未达标，文档仍按锁定决策写预期行为。

## Git

文档提交（先提交、后实测）：

```
$ git log --oneline -1
a4d3f3b docs: document wechat_channels streaming and risk-tier

$ git show --stat --format= HEAD
 CHANGELOG.md                    |  1 +
 README.md                       | 68 ++++++++++++++++++++++++++++++++++++++---
 docs/generic-fallback-engine.md |  3 ++
 3 files changed, 68 insertions(+), 4 deletions(-)
```

本 `report.md` 随后续提交入库。未跟踪 `.venv/`（软链到主仓 venv）未加入版本库。

## 状态

DONE（文档入库 + 实测留档）。Range 与 Content-Length 两条实测与预期不符，交主脑拆修复卡。
---
# 本卡执行报告：下载端点改用 sph 短码查询

Dispatch-Id：`dlg-20260901-132447-fb24a8`
Branch：`card/MediaResolverAPI-20260901-11`
Base：`c8c9092`

## 结论

代码修复、单元测试和全量回归均通过。真实环境的三次 Range 端到端验收仍未通过，失败点已从原来的 object_id 查询转移到既有的 CDN 完整长度对账：TikHub 返回 `2450521066`，CDN 返回 `435768323`。按任务卡要求，已停止继续改动，没有修改断言、解密逻辑或对账逻辑。

## 实现落点

- `app/services/platforms/wechat_channels.py`
  - 从 TikHub 响应的 `params.share_url` 严格识别 `https://weixin.qq.com/sph/<code>`。
  - 仅接受 1–64 位字母数字短码；字段缺失或形态不符时记录 ERROR 并返回不可解析结果。
  - `VideoInfo.video_id` 仍使用 `data.id` object_id；`video_url` 改为短码路径。
- `app/services/providers/tikhub.py`
  - 下载媒体查询由短码拼成 `https://weixin.qq.com/sph/<code>`。
  - 对 TikHub 的逐字段请求断言为 `{"share_url": "...", "raw": False}`，不再发送 `object_id`。
  - 每次请求仍现取当次 `full_url` 与 `decode_key`，没有新增跨请求状态。
- `app/api/stream.py`
  - 路由由 `/api/stream/wechat_channels/{object_id}` 改为 `/{sph_code}`。
  - 外部路径输入白名单为 1–64 位字母数字；非法字符、超长和空路径均 4xx，媒体/CDN 请求均不发生。
  - Range、Content-Range 对账、取消清理、429 和客户端断开逻辑未改。
- `README.md`：仅同步视频号流式端点的短码路径、示例和参数说明。
- 变更未触及禁止目录或其他平台实现。

## 测试与编译

基线定向回归：`64 passed, 39 warnings`。

实现后的定向回归：

```
71 passed, 42 warnings in 1.05s
```

全量命令：

```
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m pytest tests/ -q
```

结果：

```
242 passed, 110 warnings in 2.19s
```

```
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m py_compile app/api/stream.py app/api/resolve.py app/services/platforms/wechat_channels.py app/services/providers/tikhub.py
```

编译检查通过；`git diff --check` 通过。

## 红验

1. 短码校验红验：临时将 `app/api/stream.py` 的校验行改为 `_SPH_CODE_PATTERN = re.compile(r"^.*$")`。失败测试为：
   - `test_invalid_sph_code_rejected_without_external_request[bad-code]`
   - `test_invalid_sph_code_rejected_without_external_request[xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx]`

   统计：`2 failed, 1 passed, 34 deselected, 4 warnings`。两项失败均实际放行到 200，并记录了媒体查询调用。随后已恢复白名单校验。

2. TikHub 入参红验：临时将 `app/services/providers/tikhub.py` 的
   `data = await self._fetch_wechat_channels("", share_url)`
   改为 `data = await self._fetch_wechat_channels(sph_code, "")`。失败测试为：
   - `test_download_media_uses_sph_share_url`
   - `test_fetch_wechat_channels_media_reads_fixture_and_is_uncached`

   统计：`2 failed, 2 passed, 63 deselected, 1 warning`；失败断言实际看到 `object_id: AOzokRxWHz` 而非 share_url。随后已恢复 share_url 调用。

## 真实环境端到端

服务按卡面命令启动：

```
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

API key 从 `.env` 单键读取，未打印值。服务已在验收后正常停止。

视频号 `POST /api/resolve`：

```
resolve_http=200
video_url=http://127.0.0.1:8000/api/stream/wechat_channels/AOzokRxWHz
resolve_success=True platform=wechat_channels video_id=14998022876670594427
resolve_video_url_shape=True
```

三次 Range 请求均失败：

```
range_1_http=502
range_2_http=502
range_3_http=502
range-1.bin: bytes=80 ftyp=False
range-2.bin: bytes=80 ftyp=False
range-3.bin: bytes=80 ftyp=False
```

三次响应体前缀完全一致：

```
b'{"detail":"CDN 206 response has complete length 435768323, expected 2450521066"}'
```

因此本卡核心端到端判据为**实测未通过**；短码 URL 形态与 TikHub share_url 查询路径已验证，当前阻塞是卡面非目标的既有 CDN 长度不一致。

抖音回归：

```
douyin_http=200
douyin_success=True platform=douyin provider=tikhub
```

## 提交证据

报告提交前最后一个代码/文档提交的实际输出：

```
$ git log --oneline -1
1451f79 docs: document wechat stream short code

$ git show --stat --format= HEAD
 README.md                                     | 12 ++++++------
 tests/test_tikhub_provider_wechat_channels.py |  2 +-
 2 files changed, 7 insertions(+), 7 deletions(-)
```

本卡实现分三次提交，依次为：

- `25dd978 fix: derive wechat stream path from sph share code`
- `8608070 fix: validate wechat stream share codes`
- `1451f79 docs: document wechat stream short code`
# 本卡执行报告：以 CDN 响应作为视频文件大小事实源

Dispatch-Id：`dlg-20260901-140945-bb4e71`
Branch：`card/MediaResolverAPI-20260901-14`
Base：`5df01b0ec3f24824aa156c46cdaf981f742f09a2`
阶段：repairing

## 结论

代码修复、测试轴、红验、全量回归和编译检查均完成并提交。实现已不再使用 TikHub 的 `media.file_size` 推导 Range 终点、响应长度或 Content-Range；测试夹具明确模拟 TikHub 元数据大小 `2450521066` 与 CDN 实际大小 `600000` 的偏差。

决定性真实环境实测**未通过**：`Range: bytes=0-` 原样转发后，实际 CDN 返回 HTTP 200 而不是 206，服务按既有契约返回 502 `CDN ignored Range request`。按任务卡要求，观察到该失败后停止后续端到端形态，不修改断言迁就。

## 实现落点

- `app/api/stream.py`
  - `parse_byte_range` 只校验单个 Range 语法并提取客户端显式起点，不接收或使用文件大小。
  - 客户端带 Range 时把请求头原样传给 CDN，不再调用按 `file_size` 补终点的 `_range_header_for`。
  - CDN 206 的 `Content-Range` 作为真实起止区间；保留语法、`last >= first`、显式请求起点和区间长度/Content-Length 对账。
  - 移除 CDN 声明终点与本地 `expected_end` 的比较；CDN 返回更短终点时按 CDN 区间解密和响应。
  - 无 Range 的 CDN 200 以 CDN `Content-Length` 设置解密终点和对外长度；CDN 没有长度时不声明 `Content-Length`，流到上游结束。
  - 对外 `Content-Range` 和 `Content-Length` 均由 CDN 响应推导；保留上游异常在响应头发出前转为 5xx JSON、取消释放、429、客户端断开、解密偏移和短码校验。
- `tests/test_stream_wechat_channels.py`
  - 测试元数据大小固定为 `2450521066`，CDN 模拟实际大小为 `600000`。
  - 覆盖无 Range、`bytes=0-`、`bytes=N-`、闭区间、CDN 截短闭区间、起点错位、缺失/畸形 Content-Range、长度对账和无 CDN 长度。
  - 密文仍由 `generate_keystream` 直接异或构造，没有用 `xor_chunk` 生成。
- `README.md`
  - 说明 Range 原样转发 CDN，开放范围和超出末尾时以 CDN 实际终点为准，并修正 416 说明。

## 对账维度改动前后对照

| 对账维度 | 改动前 | 改动后 | 原因 |
|---|---|---|---|
| Range 解析/转发 | 用 TikHub `file_size` 计算并补齐终点，再发给 CDN | 只做语法解析；原始 Range 原样发给 CDN | 本地没有 CDN 真实总长，不能预先补终点 |
| `Content-Range` 语法 | 保留 | 保留 | 仍需拒绝缺失或畸形 CDN 元数据 |
| `last-byte-pos >= first-byte-pos` | 保留 | 保留 | 区间自身合法性不依赖 TikHub 大小 |
| `first-byte-pos ==` 请求起点 | 保留 | 保留；显式起点继续对账；无 Range 按隐含起点 0 对账；suffix 起点未知时不伪造本地起点 | 起点来自客户端，是仍可信的本地量 |
| CDN 终点 == 本地 `expected_end` | 要求相等 | 移除 | `expected_end` 由不可信 `file_size` 推导；CDN 可合法返回更短终点 |
| CDN `complete_length == file_size` | 上一版已移除 | 继续移除 | TikHub 总长与 CDN 总长可相差数倍 |
| 声明区间长度 == CDN `Content-Length` | 有长度时保留 | 有长度时保留；无长度时不声明对外长度 | 有 CDN 长度就对账，没有就不能凭本地量补写 |
| 对外 `Content-Length` | 用 `end - start + 1`，间接受 `file_size` 影响 | 直接采用 CDN `Content-Length`；缺失则省略 | CDN 是实际传输大小的权威 |
| 对外 `Content-Range` | 用本地 `start/end/file_size` 重构 | 透传已验证的 CDN `Content-Range` | 响应区间和总长必须来自 CDN |

## 改完后 `file_size` 的使用点审计

- `app/api/stream.py`：无残留使用点。流式范围计算、解密边界、响应长度、响应 Content-Range 和对账均不读取该字段。
- `app/services/providers/tikhub.py`：仍在既有元数据提供层读取、校验并返回 `file_size`，作为 TikHub 元数据展示字段；本卡禁止修改该目录，且该值不再进入流式路由的任何对外承诺。
- `tests/test_stream_wechat_channels.py`：保留 provider 元数据测试对 `2450521066` 的断言，并用同一差异构造证明流式端点不使用它；测试夹具中的 `file_size` 仅模拟元数据输入。
- 未新增配置、缓存或跨请求状态，未改其他平台。

## 测试与编译

定向流式测试：

```
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m pytest tests/test_stream_wechat_channels.py -q
39 passed, 33 warnings in 1.11s
```

全量测试：

```
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m pytest tests/ -q
244 passed, 112 warnings in 2.63s
```

编译：

```
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m py_compile app/api/stream.py tests/test_stream_wechat_channels.py
```

通过；`git diff --check` 通过。

## 红验

### 1. 错误恢复为 TikHub `file_size` 推导

临时注入的两行：

```python
end = int(media["file_size"]) - 1  # RED INJECTION: derive endpoint from TikHub
content_length = int(media["file_size"])  # RED INJECTION: derive length from TikHub
```

执行：

```
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m pytest -q \
  'tests/test_stream_wechat_channels.py::test_range_matches_full_download_slice[no_range]' \
  'tests/test_stream_wechat_channels.py::test_range_matches_full_download_slice[bytes_0_open]'
```

失败测试：

- `test_range_matches_full_download_slice[no_range]`
- `test_range_matches_full_download_slice[bytes_0_open]`

统计行：

```
2 failed, 3 warnings in 0.21s
```

两项均因 CDN 实际流在 600000 字节结束，而临时注入把终点/长度推到 TikHub 的 2450521066，断言正文不一致。注入随后已恢复。

### 2. 起点对账改为放行

临时注入行：

```python
if expected_offset is not None and declared_start == expected_offset:  # RED INJECTION: disable start mismatch rejection
```

执行：

```
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m pytest -q \
  tests/test_stream_wechat_channels.py::test_initial_range_mismatch_fails_before_streaming
```

失败测试：

- `test_initial_range_mismatch_fails_before_streaming`

统计行：

```
1 failed, 2 warnings in 0.11s
```

错误注入时错位 CDN 区间被放行并返回 206，而测试要求响应头发出前返回 502。注入随后已恢复为：

```python
if expected_offset is not None and declared_start != expected_offset:
```

## 真实环境端到端

服务命令：

```
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

API key 从 `.env` 使用单键 `grep '^API_KEY='` 读取，仅记录长度 43，未打印值。所有 curl 均显式使用 `-o`，产物目录：

```
/tmp/wechat-e2e-dlg-20260901-140945-bb4e71.Wx7gsF/
```

解析阶段：

```
health_http=200
resolve_http=200
resolve_success=True
resolve_platform=wechat_channels
stream_path=/api/stream/wechat_channels/AOzokRxWHz
```

已完成的第一种形态：

- 请求：`Range: bytes=0-131071`
- 服务响应：HTTP 206
- 文件：恰好 131072 字节
- `Content-Range: bytes 0-131071/435768323`
- `Content-Length: 131072`
- 偏移 4..8：`ftyp`
- 结论：通过；其中 CDN 实际完整长度为 435768323，已证明不是 TikHub 的 2450521066。

未通过的第二种形态：

- 请求：`Range: bytes=0-`
- Range 已原样转发给 CDN
- 服务响应：HTTP 502
- 响应体文件大小：38 字节
- 响应体前缀：`b'{"detail":"CDN ignored Range request"}'`
- CDN 对该 Range 返回 HTTP 200，未提供 206 Content-Range；服务按既有“CDN 未按 Range 返回则 5xx JSON”契约在响应体发出前拦截。
- 结论：**实测未通过**。

按任务卡“任一形态未通过即记录并停下”执行，后两种形态不作为通过项：

- 无 Range：已启动过请求，服务观察到 HTTP 200，响应头中的 CDN `Content-Length: 435768323` 已写入；因批处理在第二种形态失败后终止，下载仅落盘 182190080 字节，不计作完整通过。
- `Range: bytes=200000-300000`：未发起。
- 未修改断言或文档去掩盖上述实测失败，服务已停止。

## Git

代码与测试提交后、写本报告前的实际输出：

```
$ git log --oneline -1
a63fd4b fix: derive wechat stream ranges from CDN

$ git show --stat --format= HEAD
 README.md                            |   4 +-
 app/api/stream.py                    | 119 +++++++++++++++++------------------
 tests/test_stream_wechat_channels.py |  75 +++++++++++++++++++---
 3 files changed, 124 insertions(+), 74 deletions(-)
```

实现提交：`a63fd4bbe9a13d3557cca03b83e8f49b5e8e0990`。
报告写入后将另有报告提交；上述 Git 证据对应实际代码/测试/README 提交。

## 状态

代码目标已按 CDN 权威完成，单元和静态验证通过；决定性真实环境端到端因上游 CDN 不响应开放 Range，按卡面判据标记为**实测未通过**，交主脑处理该上游行为与既有 5xx 契约的冲突。
+# 执行报告：客户端 Range × CDN 响应矩阵收口

Dispatch-Id: dlg-20260901-145118-6db013
Branch: card/MediaResolverAPI-20260901-13
Base: 21c75a89a0bf8f58b6d225489a41885306fb6b7a
阶段: repairing

## 结论

已在 app/api/stream.py 补齐 CDN 206 完整性和 CDN 416 状态映射，并在 tests/test_stream_wechat_channels.py 增加一个参数化测试覆盖 R0-R5 × C1-C8 的全部 48 格；每格都校验客户端实际正文，成功格校验解密后的完整字节，失败格校验 JSON、Content-Length 与未消费上游。README.md 已同步无 Range 短 206、无 Content-Length 的 200、以及 416 行为。

本地验证通过：

~~~
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m py_compile app/api/stream.py tests/test_stream_wechat_channels.py
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m pytest tests/ -q
298 passed, 166 warnings in 3.42s
~~~

真实端到端的五种形态中，无 Range 请求返回 200 且 Content-Length 正确，但在 60 秒有界传输内只收到了 358612992/435768323 字节；按任务卡要求记为“实测未通过”，没有修改断言、重试或改实现迁就。

## 矩阵约定

测试文件中的矩阵使用 FILE_SIZE=L=600000；R3 为 bytes=0-99999，R4 为 bytes=200000-299999，R5 为 bytes=-100000。C3/C4 的成功 206 以 CDN 的合法 Content-Range 为实际区间；因此例如 R3×C3 会转发 CDN 声明的完整 0-L-1 区间。失败格的 Content-Length 是 JSON 响应正文的实际字节数，实际发出字节数同为该 JSON 正文长度；成功格的实际发出字节数是客户端收到的解密正文长度。

R6 不与 CDN 轴组合：parse_byte_range 在出站请求前返回 416，三个畸形/多区间/非 bytes 用例均断言 CDN 未被调用。

## 48 格矩阵

| 客户端 × CDN | 对外状态码 | 对外 Content-Length | 对外 Content-Range | 实际发出字节数 |
|---|---:|---:|---|---:|
| R0 × C1 | 200 | 600000 | 不发送 | 600000 |
| R0 × C2 | 200 | 不发送 | 不发送 | 600000 |
| R0 × C3 | 200 | 600000 | 不发送 | 600000 |
| R0 × C4 | 502 | 64（JSON） | 不发送 | 64（JSON） |
| R0 × C5 | 502 | 53（JSON） | 不发送 | 53（JSON） |
| R0 × C6 | 502 | 57（JSON） | 不发送 | 57（JSON） |
| R0 × C7 | 416 | 29（JSON） | bytes */600000 | 29（JSON） |
| R0 × C8 | 502 | 29（JSON） | 不发送 | 29（JSON） |
| R1 × C1 | 206 | 600000 | bytes 0-599999/600000 | 600000 |
| R1 × C2 | 502 | 70（JSON） | 不发送 | 70（JSON） |
| R1 × C3 | 206 | 600000 | bytes 0-599999/600000 | 600000 |
| R1 × C4 | 206 | 599999 | bytes 0-599998/600000 | 599999 |
| R1 × C5 | 502 | 53（JSON） | 不发送 | 53（JSON） |
| R1 × C6 | 502 | 57（JSON） | 不发送 | 57（JSON） |
| R1 × C7 | 416 | 29（JSON） | bytes */600000 | 29（JSON） |
| R1 × C8 | 502 | 29（JSON） | 不发送 | 29（JSON） |
| R2 × C1 | 502 | 38（JSON） | 不发送 | 38（JSON） |
| R2 × C2 | 502 | 38（JSON） | 不发送 | 38（JSON） |
| R2 × C3 | 206 | 400000 | bytes 200000-599999/600000 | 400000 |
| R2 × C4 | 206 | 399999 | bytes 200000-599998/600000 | 399999 |
| R2 × C5 | 502 | 63（JSON） | 不发送 | 63（JSON） |
| R2 × C6 | 502 | 57（JSON） | 不发送 | 57（JSON） |
| R2 × C7 | 416 | 29（JSON） | bytes */600000 | 29（JSON） |
| R2 × C8 | 502 | 29（JSON） | 不发送 | 29（JSON） |
| R3 × C1 | 206 | 100000 | bytes 0-99999/600000 | 100000 |
| R3 × C2 | 206 | 不发送 | bytes 0-99999/* | 100000 |
| R3 × C3 | 206 | 100000 | bytes 0-99999/600000 | 100000 |
| R3 × C4 | 206 | 100000 | bytes 0-99999/600000 | 100000 |
| R3 × C5 | 502 | 53（JSON） | 不发送 | 53（JSON） |
| R3 × C6 | 502 | 57（JSON） | 不发送 | 57（JSON） |
| R3 × C7 | 416 | 29（JSON） | bytes */600000 | 29（JSON） |
| R3 × C8 | 502 | 29（JSON） | 不发送 | 29（JSON） |
| R4 × C1 | 502 | 38（JSON） | 不发送 | 38（JSON） |
| R4 × C2 | 502 | 38（JSON） | 不发送 | 38（JSON） |
| R4 × C3 | 206 | 100000 | bytes 200000-299999/600000 | 100000 |
| R4 × C4 | 206 | 100000 | bytes 200000-299999/600000 | 100000 |
| R4 × C5 | 502 | 63（JSON） | 不发送 | 63（JSON） |
| R4 × C6 | 502 | 57（JSON） | 不发送 | 57（JSON） |
| R4 × C7 | 416 | 29（JSON） | bytes */600000 | 29（JSON） |
| R4 × C8 | 502 | 29（JSON） | 不发送 | 29（JSON） |
| R5 × C1 | 502 | 38（JSON） | 不发送 | 38（JSON） |
| R5 × C2 | 502 | 38（JSON） | 不发送 | 38（JSON） |
| R5 × C3 | 206 | 100000 | bytes 500000-599999/600000 | 100000 |
| R5 × C4 | 206 | 99999 | bytes 500000-599998/600000 | 99999 |
| R5 × C5 | 502 | 63（JSON） | 不发送 | 63（JSON） |
| R5 × C6 | 502 | 57（JSON） | 不发送 | 57（JSON） |
| R5 × C7 | 416 | 29（JSON） | bytes */600000 | 29（JSON） |
| R5 × C8 | 502 | 29（JSON） | 不发送 | 29（JSON） |

矩阵测试实际统计：

~~~
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m pytest tests/test_stream_wechat_channels.py -q -k 'matrix or malformed_range'
51 passed, 42 deselected, 52 warnings in 1.54s
~~~

## 实现与不变式落点

- INV-1：app/api/stream.py:430-445 只在已知长度时声明 Content-Length；206 metadata 的 Content-Length 在 app/api/stream.py:235-245 对账；矩阵测试逐格比较响应头长度与客户端实际正文长度。
- INV-2：app/api/stream.py:399-404 要求无 Range 的 206 具备数字 complete-length 且 declared_end == complete_length - 1；R0×C4 测试断言 502、JSON 且 aiter_calls == 0。无 Range 的 206 完整格输出 200 和总长 L。
- INV-3：app/api/stream.py:436-445 原样使用已校验的 CDN 206 Content-Range，或为 200 响应按实际可知的区间构造；矩阵逐格断言 Content-Range。
- INV-4：app/api/stream.py:291-302 继续以 absolute offset 调用 xor_chunk；密文由测试中的 generate_keystream 逐字节异或构造，未使用 xor_chunk 生成密文；矩阵以及现有跨 131072 边界用例断言实际明文。
- INV-5：C5/C6/C7/C8 和 R0×C4 的响应形态错误在 StreamingResponse 创建前转换为 JSON/416；对应矩阵格均断言上游 aiter_calls == 0。既有客户端断开/中途上游断开契约未改动，仍由现有 test_upstream_disconnect_terminates_response_with_error 覆盖。

## 三项红验

1. R0×C4 失败判定注入：
   - 注入行：app/api/stream.py 第 399 行，sed 输出为
     if False and not is_partial:
   - 测试：test_client_range_cdn_response_matrix[R0xC4]
   - 统计：1 failed, 92 deselected, 2 warnings；实际状态码 200，期望 502。
   - 已恢复为 if not is_partial:。

2. 416 透传注入：
   - 注入行：app/api/stream.py 第 368 行，sed 输出为
     if first_stream.status_code == 416 and False:
   - 测试：test_client_range_cdn_response_matrix[R0xC7]
   - 统计：1 failed, 92 deselected, 2 warnings；实际状态码 502，期望 416。
   - 已恢复为 if first_stream.status_code == 416:。

3. R3×C1 截断注入：
   - 注入行：app/api/stream.py 第 296 行，sed 输出为
     if False and end is not None:
   - 测试：test_client_range_cdn_response_matrix[R3xC1]
   - 统计：1 failed, 92 deselected, 2 warnings；响应正文不等于 100000 字节目标。
   - 已恢复为 if end is not None:。

## 真实端到端实测

服务命令：

~~~
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
~~~

API key 由 grep '^API_KEY=' .env 单键读取，未打印值。POST 得到 data.video_url，长度 59。五个 GET 均显式使用独立 -o 文件，产物目录为 /tmp/wechat-e2e-dlg-20260901-145118-rEfY51。

| 形态 | HTTP | 落盘字节 | 头部/判定 |
|---|---:|---:|---|
| bytes=0-131071 | 206 | 131072 | Content-Length=131072，Content-Range=bytes 0-131071/435768323，偏移 4..8 为 ftyp |
| bytes=0- | 206 | 116457472 | Content-Length=435768323，Range 头正确；20 秒超时中止，允许作为有界截断 |
| 无 Range | 200 | 358612992 | Content-Length=435768323；60 秒超时中止，未收完整文件，实测未通过 |
| bytes=200000-300000 | 206 | 100001 | Content-Length=100001，Content-Range=bytes 200000-300000/435768323 |
| bytes=200000- | 206 | 116588544 | Content-Length=435568323，Content-Range=bytes 200000-435768322/435768323，起点正确 |

字节交叉校验：

~~~
A_eq_B_prefix=True
D_eq_B_slice=True
E_overlap_bytes=116257472 E_eq_B_overlap=True
B_cross_boundary_eq_C=True
A_len_ok=True D_len_ok=True ftyp_ok=True
full_C_header=435768323
~~~

无 Range 响应体前缀同样为合法 mp4 头，body[4:8] == b'ftyp'；失败原因是传输超时导致落盘文件短于其声明长度，不是 5xx 或头部矩阵错误。端到端按要求在此停止，未重试。

## 提交证据

代码/测试提交：

~~~
$ git log --oneline -1
6a88228 fix: close wechat stream range response matrix

$ git show --stat --format= HEAD
 app/api/stream.py                    |  54 +++++++++--
 tests/test_stream_wechat_channels.py | 167 +++++++++++++++++++++++++++++++++++
 2 files changed, 215 insertions(+), 6 deletions(-)
~~~

README 提交：

~~~
$ git log --oneline -1
79a1794 docs: document range response matrix edge cases

$ git show --stat --format= HEAD
 README.md | 7 ++++---
 1 file changed, 4 insertions(+), 3 deletions(-)
~~~

本报告写入前工作树在 79a1794 后无代码/文档未提交改动；报告本身随后单独提交。
+
## 收尾审查

按 review-discipline 先做了 OCR 前置扫描；ocr-review 主腿约 70 秒无输出，未返回 reviewed/skipped envelope，随后中止，未将其当作通过，也未得到可判定 finding。人工审查仅发现矩阵失败格需要明确校验 JSON detail，已增加该断言；未发现新的 P1/P2。

最终收尾验证：

~~~
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m pytest tests/ -q
298 passed, 166 warnings in 3.52s
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m py_compile app/api/stream.py tests/test_stream_wechat_channels.py
git diff --check
~~~

收尾测试提交：

~~~
$ git log --oneline -1
2acc1b7 test: assert matrix failures return detail JSON

$ git show --stat --format= HEAD
 tests/test_stream_wechat_channels.py | 1 +
 1 file changed, 1 insertion(+)
~~~

## 本次收口修复

收口验收发现 R3 × C3 与 R4 × C3 的 CDN 206 终点超过客户端明确终点。app/api/stream.py 在 206 分支增加了单一终点收紧：仅当 requested_end 存在且 CDN declared_end 更大时取客户端终点；Content-Range 的 complete-length 仍使用 CDN 值，C4 的 CDN 短终点不变，绝对解密偏移不变。

矩阵表已更新两格：

| 格 | 修复前 | 修复后 |
|---|---|---|
| R3 × C3 | 206 / 600000 / bytes 0-599999/600000 / 600000 字节 | 206 / 100000 / bytes 0-99999/600000 / 100000 字节 |
| R4 × C3 | 206 / 400000 / bytes 200000-599999/600000 / 400000 字节 | 206 / 100000 / bytes 200000-299999/600000 / 100000 字节 |

红验：临时把 app/api/stream.py:394 的 if requested_end is not None and end > requested_end: 改为 if False and requested_end is not None and end > requested_end:，并用 sed 确认：`sed -n '394p' app/api/stream.py` 输出 `if False and requested_end is not None and end > requested_end:`。随后 R3×C3 与 R4×C3 均失败；统计为 2 failed, 91 deselected, 3 warnings。注入已恢复。

最终验证：

~~~
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m pytest tests/test_stream_wechat_channels.py -q -k 'client_range_cdn_response_matrix'
48 passed, 45 deselected, 49 warnings in 1.47s
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m pytest tests/ -q
298 passed, 166 warnings in 4.16s
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m py_compile app/api/stream.py tests/test_stream_wechat_channels.py
git diff --check
~~~

本次代码/测试提交：

~~~
$ git log --oneline -1
6aafdd2 fix: clamp bounded cdn ranges to client end

$ git show --stat --format= HEAD
 app/api/stream.py                    | 14 +++++++++++++-
 tests/test_stream_wechat_channels.py |  3 +++
 2 files changed, 16 insertions(+), 1 deletion(-)
~~~
