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
