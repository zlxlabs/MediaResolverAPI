# MediaResolverAPI

社交媒体链接解析服务：输入视频或 X Space 链接，获取媒体信息与下载地址。

`risk-tier: internal`

## 支持平台

| 平台 | 支持 | 数据源（默认优先级；Just One API 标识为 `justoneapi`） |
|---|---:|---|
| 抖音（Douyin） | ✅ | TikHub → Just One API |
| TikTok | ✅ | TikHub → Cobalt → Just One API |
| 快手（Kuaishou） | ✅ | TikHub → Just One API |
| YouTube | ✅ | TikHub → Cobalt → Just One API |
| 小红书（Xiaohongshu） | ✅ | TikHub → Just One API |
| Instagram | ✅ | TikHub → Cobalt → Just One API |
| Pinterest | ✅ | Cobalt |
| Facebook | ✅ | Cobalt |
| 微信视频号（WeChat Channels） | ✅ | TikHub → Just One API |
| X（Twitter） | ✅ | TikHub → Cobalt → Just One API |
| X Space（Twitter Spaces） | 回放 | yt-dlp |

> Just One API 仅在设置 `JUSTONEAPI_TOKEN` 后加入供应商链；留空时链上不出现该数据源。具体运行时供应商可通过 `GET /api/platforms` 查询。

## 选择下载方式

- **普通平台：拿直链。** `POST /api/resolve` 后使用返回的 `data.video_url` 下载；它通常是第三方 CDN 地址。详见[普通平台下载](docs/client.md#普通平台下载)。
- **视频号：带钥匙请求本服务。** `video_url` 指向本服务的流式端点，下载请求须带 `X-API-Key`；支持 Range。详见[视频号下载](docs/client.md#视频号下载)。
- **X Space：先等文件就绪，再下载。** Resolve 后请求服务端下载 URL；`202` 表示继续等待，`200` 才是文件，支持 Range 续传。详见[X Space 回放下载](docs/client.md#x-space-回放下载)。

## 快速开始

```bash
cp .env.example .env
```

设置服务鉴权密钥 `API_KEY` 和 TikHub 密钥 `TIKHUB_API_KEY`。`JUSTONEAPI_TOKEN` 可选；留空时 Just One API 不会加入调用链。其余配置见 [`.env.example`](.env.example)。启动方式：

```bash
pip install -e .
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

调用一次解析接口（将示例地址与密钥替换为实际值）：

```bash
curl -X POST http://localhost:8000/api/resolve \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: your-api-key' \
  -d '{"url":"https://v.douyin.com/xxxxx/"}'
```

普通平台可直接下载响应中的 `data.video_url`；视频号和 X Space 按上方对应协议处理。

## 文档

- [客户端对接协议](docs/client.md)：三种下载方式、视频号流式与直连协议、X Space 轮询与续传。
- [维护者架构一页纸](docs/architecture.md)：代码边界和供应商调用链。
- [`/docs`](http://localhost:8000/docs)：服务启动后的交互式 API 文档（含请求与响应字段）。
- [`.env.example`](.env.example)：完整环境变量清单。
