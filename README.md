# MediaResolverAPI

社交媒体视频链接解析服务 —— 输入一个社交媒体视频 URL，返回无水印直链及视频元数据。

risk-tier: internal

## 支持平台

| 平台 | 短链解析 | 数据源 |
|------|---------|--------|
| 抖音 (Douyin) | ✅ | TikHub（多级端点降级） |
| TikTok | ✅ | TikHub（多级端点降级）→ Cobalt |
| 快手 (Kuaishou) | ✅ | TikHub（多级端点降级） |
| YouTube | ✅ | TikHub（多级端点降级）→ Cobalt |
| 小红书 (Xiaohongshu) | ✅ | TikHub（多级端点降级） |
| Instagram | ✅ | TikHub（多级端点降级）→ Cobalt |
| Pinterest | ✅ | Cobalt |
| Facebook | ✅ | Cobalt |
| 微信视频号 (WeChat Channels) | ✅ | TikHub |

> 含多个数据源的平台会按优先级依次尝试，前者失败自动降级到后者。
>
> **多级端点降级（通用引擎）**：六大平台均在 TikHub 内部按「同一 ID 串行打多个端点、命中即停、终态短路、总预算兜底」的统一引擎做多级降级，单端点被 TikHub 下线不再等于平台解析全挂。引擎设计与各平台链配置见 [docs/generic-fallback-engine.md](docs/generic-fallback-engine.md)。
>
> - **抖音**：`web/fetch_one_video → web/fetch_one_video_v2 → app/v3/fetch_one_video_v3`；私密/部分可见终态短路；短链展开或 ID 提取失败回退 `hybrid/video_data`。详见 [docs/douyin-fallback-design.md](docs/douyin-fallback-design.md)。
> - **小红书**：`app_v2/get_video_note_detail（仅 note_id）→ web_v3/fetch_note_detail（note_id + xsec_token）`；token 缺失时跳过 `web_v3`；图文/删除终态短路。TikHub 单源。详见 [docs/xiaohongshu-fallback-design.md](docs/xiaohongshu-fallback-design.md)。
> - **快手**：`web/fetch_one_video_v2（photo_id）→ web/fetch_one_video（share_text=url）`；解析器自适应 `data.photo` 与 `data[0]` 两种 schema。原为单源单端点、无 Cobalt 兜底，是唯一真单点故障，故优先消除。
> - **TikTok**：`app/v3/fetch_one_video → _v2 → _v3`（同 `data.aweme_detail` schema）。有 Cobalt 兜底，故链内不判终态（误判会跳过 Cobalt），链走完落 Cobalt。
> - **Instagram**：`v2/fetch_post_info（code_or_url）→ v1/fetch_post_by_url（post_url）`；非视频/轮播不判终态（子节点可能含视频），落 Cobalt。ID 提取失败时由路由层放行原始 url 兜底。
> - **YouTube**：`web/get_video_info（预解析直链）→ web/get_video_info_v2（streamingData 合流）`；解析器自适应两套 schema。有 Cobalt 兜底。
> - **微信视频号**：TikHub 单源单端点（`wechat_channels/v2/fetch_video_detail`），无 Cobalt 兜底，故链内不判终态。平台标识是 `wechat_channels`（不是 `wechat`）。

---

## 视频号的下载方式与它和其他平台的差别

其他平台的 `video_url` 是第三方 CDN 直链，客户端自己去拉。视频号不是这样。

1. **源站 mp4 是加密的。** `POST /api/resolve` 返回的 `video_url` 指向**本服务**的流式端点 `/api/stream/wechat_channels/{sph_code}`，而不是第三方 CDN。服务端边下边解密再转发，客户端拿到的是可直接播放的标准 mp4，**不需要客户端做任何解密**。
2. **下载流量经过本服务。** 并发下载数受环境变量 `MAX_CONCURRENT_STREAMS` 限制（默认 4），超限返回 `429 Too many concurrent streams`。部署在反向代理后面时，需要确认代理对长连接、大响应体的超时与缓冲设置（流式转发，响应体可达完整视频大小）。
3. **播放量拿不到。** `view_count` 恒为 `null`（TikHub 的 `read_count` 恒为 0，不是真实播放量），不要把它理解成「偶尔缺失」。其余统计数据（点赞 / 收藏 / 转发 / 评论）齐全。
4. **`video_url` 必须带 `X-API-Key` 才能访问。** 其他平台的 `video_url` 是第三方 CDN 直链，拿到即可直接下载或喂给播放器，无需任何认证。视频号的 `video_url` 指向本服务，因此请求时必须带 `X-API-Key`，否则返回 401。不要把这个 URL 直接丢给播放器或下载器——它们默认不会加这个请求头，播放/下载会失败。若服务端未配置 `API_KEY`（开发模式）则不校验，本地未设密钥时可以不带。

流式端点的路径、鉴权、Range 与状态码见下文 [GET /api/stream/wechat_channels/{sph_code}](#get-apistreamwechat_channelssph_code)。

---

## 快速开始

### 1. 环境准备

```bash
cp .env.example .env
# 编辑 .env，填入 API_KEY、TIKHUB_API_KEY 等配置
```

环境变量（默认值以 `app/core/config.py` 为准；完整清单见 `.env.example`）：

| 变量 | 作用 | 默认值 | 什么时候必须设 |
|------|------|--------|----------------|
| `API_KEY` | 服务自身的接入密钥。所有 `/api/*` 接口（含视频号的 `video_url`）用 `X-API-Key` 校验 | 空字符串 `""`（不校验，开发模式） | 对公网或非可信客户端提供服务时必须设；留空则任何人都能调接口 |
| `TIKHUB_API_KEY` | TikHub 数据源密钥，解析各平台元数据时使用 | 空字符串 `""` | 要解析视频（含视频号）时必须设，否则上游请求失败 |
| `TIKHUB_API_BASE` | TikHub API 基址 | `https://api.tikhub.io` | 一般不用改；只用自建/代理 TikHub 时才设 |
| `PUBLIC_BASE_URL` | 对外公开基址，决定 `POST /api/resolve` 返回的 `data.video_url` 前缀。显式配置了就用它，没配置就用当前请求的 `Host`（`request.base_url`）推导 | 空字符串 `""` | **反向代理后面且代理未透传正确 Host 时必须设置**，否则 `video_url` 会变成内网地址（如 `http://127.0.0.1:8000/api/stream/...`），下游拿到无法使用 |
| `MAX_CONCURRENT_STREAMS` | 视频号流式下载的**每进程**并发上限，超限返回 429，不会排队 | `4` | 一般不用改；单进程扛不住并发、或要用 `uvicorn --workers N` / 多副本时需按进程数自行核算（此项不是全局限制） |
| `STREAM_CHUNK_SIZE` | 流式转发的分块大小（字节） | `65536` | 一般不用改；要调整转发缓冲时才设 |
| `COBALT_API_BASE` | Cobalt 自建服务地址，作为部分平台的兜底数据源 | 空字符串 `""`（未配置则 Cobalt 不可用） | 需要 Cobalt 兜底时必须设为实际服务地址；视频号无 Cobalt 兜底，不设不影响视频号 |
| `HTTP_TIMEOUT_SECONDS` | 服务访问上游 HTTP 的超时（秒） | `30` | 一般不用改；上游较慢需要放宽超时时才设 |
| `TRANSLATION_ENABLED` | 是否翻译视频描述（影响响应里的 `translated_description`） | `True` | 不需要翻译时设为 `false`；为 True 且请求 `translate=true` 时还需配置 `OPENAI_API_KEY` |
| `OPENAI_API_KEY` | 翻译用的 OpenAI 兼容接口密钥 | 空字符串 `""` | 需要翻译描述时必须设；留空则即使 `TRANSLATION_ENABLED=True` 也不会翻译 |

### 2. 启动服务

```bash
# 本地启动
pip install -e .
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Docker 启动
docker compose up -d
```

### 3. 验证服务

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

---

## API 接入文档

### 认证

所有 `/api/*` 接口需要通过 `X-API-Key` Header 进行认证：

```
X-API-Key: your-api-key
```

如果服务端未配置 `API_KEY` 环境变量，则不校验认证（开发模式）。

---

### 跨域访问（CORS）

服务已开启 CORS，**允许任意来源的浏览器跨域调用**：

- `Access-Control-Allow-Origin: *`（不限来源）
- 允许全部方法（`GET/POST/PUT/PATCH/DELETE/OPTIONS` 等）与全部请求头，含 `X-API-Key`
- **不带凭据模式**（`Allow-Credentials: false`）：鉴权请把 API Key 放在 `X-API-Key` 请求头，**不要**依赖 cookie / HTTP Basic 等浏览器凭据——跨域携带凭据不被支持

因此前端（含跨域单页应用）可直接 `fetch`/`axios` 调用，无需代理。示例：

```javascript
// 浏览器中从任意域名跨域调用
const resp = await fetch("https://your-server:8000/api/resolve", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-API-Key": "your-api-key",
  },
  // 注意：不要设置 credentials: "include"，本服务为不带凭据模式
  body: JSON.stringify({ url: "https://v.douyin.com/xxxxx/" }),
});
```

> 服务端调用（Python `requests`、Node 后端、cURL 等）不经过浏览器 CORS，不受上述限制。

---

### POST /api/resolve

解析社交媒体视频 URL，返回无水印直链和视频元数据。

#### 请求

```bash
curl -X POST http://localhost:8000/api/resolve \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "url": "https://www.tiktok.com/@user/video/1234567890",
    "translate": true,
    "force_refresh": false
  }'
```

**请求参数：**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `url` | string | ✅ | - | 社交媒体视频 URL（支持短链） |
| `translate` | bool | - | `true` | 是否将视频描述翻译为中文 |
| `force_refresh` | bool | - | `false` | 跳过缓存，强制重新解析 |

#### 成功响应

```json
{
  "success": true,
  "data": {
    "platform": "tiktok",
    "video_id": "1234567890",
    "title": "Video Title",
    "description": "Original video description",
    "translated_description": "翻译后的视频描述",
    "author_name": "creator_name",
    "author_id": "creator_id",
    "video_url": "https://direct-download-link.mp4",
    "width": 1080,
    "height": 1920,
    "duration": 30,
    "quality": "1080p",
    "view_count": 100000,
    "like_count": 5000,
    "comment_count": 200,
    "share_count": 100,
    "collect_count": 300,
    "create_time": "2025-01-01T12:00:00",
    "publish_time": "2025-01-01",
    "provider": "tikhub"
  },
  "error": null
}
```

**响应字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `success` | bool | 是否解析成功 |
| `data.platform` | string | 平台标识：`douyin` `tiktok` `kuaishou` `youtube` `xiaohongshu` `instagram` `pinterest` `facebook` `wechat_channels` |
| `data.video_id` | string | 平台视频 ID |
| `data.title` | string | 视频标题 |
| `data.description` | string | 视频描述原文 |
| `data.translated_description` | string \| null | 翻译后的中文描述（仅当 `translate=true` 且原文非中文时） |
| `data.author_name` | string | 作者昵称 |
| `data.author_id` | string | 作者 ID |
| `data.video_url` | string | 视频无水印直链。其他平台为第三方 CDN；视频号指向本服务的流式端点（见「视频号的下载方式」） |
| `data.width` | int | 视频宽度（像素） |
| `data.height` | int | 视频高度（像素） |
| `data.duration` | int \| null | 视频时长（秒） |
| `data.quality` | string \| null | 视频质量 |
| `data.view_count` | int \| null | 播放量。视频号恒为 `null`（TikHub 的 `read_count` 恒为 0），不是偶尔缺失 |
| `data.like_count` | int \| null | 点赞数 |
| `data.comment_count` | int \| null | 评论数 |
| `data.share_count` | int \| null | 分享数 |
| `data.collect_count` | int \| null | 收藏数 |
| `data.create_time` | string \| null | 创建时间（ISO 8601） |
| `data.publish_time` | string \| null | 发布时间 |
| `data.provider` | string \| null | 实际使用的数据源：`tikhub` 或 `cobalt` |
| `error` | string \| null | 错误信息（`success=false` 时） |

> **注意：** 部分字段可能为 `null`，取决于平台和数据源的返回情况。通过 Cobalt 解析的视频通常只有基础信息（直链、标题），缺少统计数据。

#### 失败响应

```json
{
  "success": false,
  "data": null,
  "error": "Failed to resolve video from all providers. Platform: tiktok, Video ID: 123"
}
```

**HTTP 状态码：**

| 状态码 | 说明 |
|--------|------|
| 200 | 请求成功（检查 `success` 字段判断业务是否成功） |
| 400 | URL 无法识别或短链解析失败 |
| 401 | API Key 无效或缺失 |
| 500 | 服务端内部错误 |

---

### GET /api/platforms

查询当前支持的平台及其数据源。

#### 请求

```bash
curl http://localhost:8000/api/platforms \
  -H "X-API-Key: your-api-key"
```

#### 响应

```json
{
  "platforms": {
    "pinterest": ["cobalt"],
    "facebook": ["cobalt"],
    "tiktok": ["tikhub", "cobalt"],
    "instagram": ["tikhub", "cobalt"],
    "xiaohongshu": ["tikhub"],
    "youtube": ["tikhub", "cobalt"],
    "douyin": ["tikhub"],
    "kuaishou": ["tikhub"],
    "wechat_channels": ["tikhub"]
  }
}
```

---

### GET /api/stream/wechat_channels/{sph_code}

拉取视频号的解密后 mp4。`sph_code` 是视频号分享链接 `https://weixin.qq.com/sph/<sph_code>` 中的短码，由 `POST /api/resolve` 返回的 `data.video_url` 编码携带；`data.video_id` 仍是视频号对象 ID。源站文件加密，本服务边下边解密再转发；客户端按普通 mp4 处理即可。

#### 请求

```bash
curl http://localhost:8000/api/stream/wechat_channels/AOzokRxWHz \
  -H "X-API-Key: your-api-key" \
  -H "Range: bytes=0-131071" \
  -o video-partial.mp4
```

鉴权与其他 `/api/*` 接口相同：`X-API-Key` Header。服务端未配置 `API_KEY` 时不校验。

**请求参数：**

| 字段 | 位置 | 必填 | 说明 |
|------|------|------|------|
| `sph_code` | 路径 | ✅ | 视频号分享链接 `https://weixin.qq.com/sph/<sph_code>` 中的字母数字短码 |
| `Range` | Header | - | 标准字节范围，如 `bytes=0-131071` 或 `bytes=0-`。省略则返回完整文件 |

支持 `Range` 请求，可用于断点续传和播放器拖拽进度。客户端的 `Range` 会原样转发给 CDN，开放范围或超出文件末尾时的实际终点以 CDN 响应为准。响应带 `Accept-Ranges: bytes`。带 `Range` 时即使覆盖了整个文件也返回 206。无 `Range` 时语义是完整文件：若 CDN 返回 206，必须从字节 0 到 `Content-Range` 声明的末尾完整覆盖文件，否则在响应头发出前返回 502；服务端不会自动续拉。

#### 成功响应

- `Content-Type: video/mp4`
- 无 `Range`：HTTP 200，正文为完整解密后的 mp4
- 有 `Range`：HTTP 206，带 `Content-Range: bytes start-end/total`
- CDN 返回 200 且没有 `Content-Length` 时，只有客户端给出明确终点的 `Range` 请求可以不声明该响应头并使用分块传输；无 `Range` 或开放范围因无法验证完整性而在响应头发出前返回 502。

#### 失败响应 / HTTP 状态码

| 状态码 | 说明 |
|--------|------|
| 200 | 完整文件（未带 `Range`） |
| 206 | 部分内容（带 `Range`） |
| 401 | API Key 无效或缺失 |
| 416 | `Range` 格式错误，或 CDN 返回 416；若 CDN 提供合法的 `Content-Range: bytes */L` 则透传 |
| 429 | 每进程内并发流超过 `MAX_CONCURRENT_STREAMS`（默认 4），不会排队；默认 Docker 单 worker 单副本时限制成立，使用 `uvicorn --workers N` 或多副本时不是全局限制 |
| 502 | 响应头发出前的上游 TikHub / CDN 失败、解密密钥无效、CDN 未按 Range 返回，或无 Range 收到不完整的 206；响应头发出后的上游断流/解密异常表现为连接中断或正文长度不足 |

---

### GET /health

健康检查，无需认证。

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

---

## 运维仪表盘 API

服务内置一组运维统计接口，前缀 `/api/dashboard`，**全部需要 `X-API-Key` 认证**。同时在 `/dashboard/` 提供一个基于这些接口的静态 Web 仪表盘页面。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/dashboard/stats/overview?period=7d` | 用量总览：请求数、成功/失败、缓存命中率、平均耗时、平台/数据源分布、按日计数。`period` 取值 `24h` `7d`（默认）`30d` |
| GET | `/api/dashboard/stats/recent?limit=20` | 最近的解析请求列表。`limit` 范围 1–100，默认 20 |
| GET | `/api/dashboard/stats/provider-health` | 各数据源健康度：调用数、成功率、平均耗时、状态（`healthy`/`degraded`/`down`/`unknown`） |
| GET | `/api/dashboard/cache/stats` | 缓存统计：已缓存总数、过期数、平台分布 |
| POST | `/api/dashboard/cache/clear-expired` | 清理已过期的缓存条目 |
| DELETE | `/api/dashboard/cache/{platform}/{video_id}` | 删除指定缓存条目 |

```bash
curl "http://localhost:8000/api/dashboard/stats/overview?period=24h" \
  -H "X-API-Key: your-api-key"
```

> Web 仪表盘地址：`http://localhost:8000/dashboard/`（默认每 30s 前端轮询刷新）。

---

## 交互式文档

服务启动后可访问自动生成的交互式 API 文档：

- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`

---

## 接入示例

### Python

```python
import requests

resp = requests.post(
    "http://localhost:8000/api/resolve",
    headers={"X-API-Key": "your-api-key"},
    json={"url": "https://v.douyin.com/xxxxx/"},
)
data = resp.json()
if data["success"]:
    print(data["data"]["video_url"])
```

视频号是唯一需要两步的平台：先 `POST /api/resolve` 拿到 `video_url`，再带 `X-API-Key` 请求该 URL 下载 mp4。不要把 `video_url` 直接丢给播放器。

```python
import requests

headers = {"X-API-Key": "your-api-key"}
resp = requests.post(
    "http://localhost:8000/api/resolve",
    headers=headers,
    json={"url": "https://weixin.qq.com/sph/xxxxx"},
)
data = resp.json()
if data["success"]:
    video = requests.get(data["data"]["video_url"], headers=headers)
    with open("wechat-channels.mp4", "wb") as f:
        f.write(video.content)
```

### JavaScript / Node.js

```javascript
const resp = await fetch("http://localhost:8000/api/resolve", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-API-Key": "your-api-key",
  },
  body: JSON.stringify({ url: "https://v.douyin.com/xxxxx/" }),
});
const data = await resp.json();
if (data.success) {
  console.log(data.data.video_url);
}
```

视频号两步（Node 18+ 内置 `fetch`，写入文件用标准库 `node:fs/promises`，无额外依赖）。不要把 `video_url` 直接丢给播放器。

```javascript
import { writeFile } from "node:fs/promises";

const resp = await fetch("http://localhost:8000/api/resolve", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-API-Key": "your-api-key",
  },
  body: JSON.stringify({ url: "https://weixin.qq.com/sph/xxxxx" }),
});
const data = await resp.json();
if (data.success) {
  const videoResp = await fetch(data.data.video_url, {
    headers: { "X-API-Key": "your-api-key" },
  });
  await writeFile(
    "wechat-channels.mp4",
    Buffer.from(await videoResp.arrayBuffer()),
  );
}
```

### cURL

```bash
curl -X POST http://localhost:8000/api/resolve \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{"url": "https://v.douyin.com/xxxxx/"}'
```

视频号两步：先解析拿到 `data.video_url`，再带 `X-API-Key` 请求该 URL 下载。不要把这个 URL 直接丢给播放器。

```bash
# 第一步：解析
curl -sS -X POST http://localhost:8000/api/resolve \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{"url": "https://weixin.qq.com/sph/xxxxx"}'

# 第二步：把上一步返回的 data.video_url 整段贴进引号（必须带 X-API-Key）
curl -L "<data.video_url>" \
  -H "X-API-Key: your-api-key" \
  -o wechat-channels.mp4
```
