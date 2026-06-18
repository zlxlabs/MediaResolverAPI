# MediaResolverAPI

社交媒体视频链接解析服务 —— 输入一个社交媒体视频 URL，返回无水印直链及视频元数据。

## 支持平台

| 平台 | 短链解析 | 数据源 |
|------|---------|--------|
| 抖音 (Douyin) | ✅ | TikHub（多级端点降级） |
| TikTok | ✅ | TikHub → Cobalt |
| 快手 (Kuaishou) | ✅ | TikHub |
| YouTube | ✅ | TikHub → Cobalt |
| 小红书 (Xiaohongshu) | ✅ | TikHub（多级端点降级） |
| Instagram | ✅ | TikHub → Cobalt |
| Pinterest | ✅ | Cobalt |

> 含多个数据源的平台会按优先级依次尝试，前者失败自动降级到后者。
>
> **抖音多级端点降级**：在 TikHub 内部按 `web/fetch_one_video → web/fetch_one_video_v2 → app/v3/fetch_one_video_v3` 串行降级（覆盖同源抖动、跨源失效、版权受限）；私密/部分可见等终态立即短路；短链展开或 ID 提取失败时回退到 `hybrid/video_data` 入口。详见 [docs/douyin-fallback-design.md](docs/douyin-fallback-design.md)。
>
> **小红书多级端点降级**：在 TikHub 内部按 `app_v2/get_video_note_detail（仅 note_id）→ web_v3/fetch_note_detail（note_id + xsec_token）` 串行降级；`app_v2` 不依赖 token 故首选，token 缺失时跳过 `web_v3`；图文笔记/删除等终态立即短路。旧端点 `web/get_note_info_v3` 已被 TikHub 下线、Cobalt 不支持小红书，故为 TikHub 单源。详见 [docs/xiaohongshu-fallback-design.md](docs/xiaohongshu-fallback-design.md)。

---

## 快速开始

### 1. 环境准备

```bash
cp .env.example .env
# 编辑 .env，填入 API_KEY、TIKHUB_API_KEY 等配置
```

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
| `data.platform` | string | 平台标识：`douyin` `tiktok` `kuaishou` `youtube` `xiaohongshu` `instagram` `pinterest` |
| `data.video_id` | string | 平台视频 ID |
| `data.title` | string | 视频标题 |
| `data.description` | string | 视频描述原文 |
| `data.translated_description` | string \| null | 翻译后的中文描述（仅当 `translate=true` 且原文非中文时） |
| `data.author_name` | string | 作者昵称 |
| `data.author_id` | string | 作者 ID |
| `data.video_url` | string | 视频无水印直链 |
| `data.width` | int | 视频宽度（像素） |
| `data.height` | int | 视频高度（像素） |
| `data.duration` | int \| null | 视频时长（秒） |
| `data.quality` | string \| null | 视频质量 |
| `data.view_count` | int \| null | 播放量 |
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
    "tiktok": ["tikhub", "cobalt"],
    "instagram": ["tikhub", "cobalt"],
    "xiaohongshu": ["tikhub"],
    "youtube": ["tikhub", "cobalt"],
    "douyin": ["tikhub"],
    "kuaishou": ["tikhub"]
  }
}
```

---

### GET /health

健康检查，无需认证。

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

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

### cURL

```bash
curl -X POST http://localhost:8000/api/resolve \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{"url": "https://v.douyin.com/xxxxx/"}'
```
