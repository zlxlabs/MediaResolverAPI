# 客户端对接协议

本文是客户端行为协议。通用请求字段、完整响应模型和交互式试调请使用服务自带 [`/docs`](http://localhost:8000/docs)；环境配置见 [`.env.example`](../.env.example)。除普通平台直链外，视频号和 X Space 的下载 URL 都指向本服务，需按各自协议处理。

## 普通平台下载

对普通平台调用 `POST /api/resolve`，带 `X-API-Key`，从成功响应的 `data.video_url` 获取下载地址。通常该地址是第三方 CDN 直链，可直接由客户端请求；下载媒体类型可参考 `data.media_type`。不需要把平台特有的解析字段或完整响应结构复制到客户端实现中，通用字段以 `/docs` 为准。

视频号与 X Space 不属于普通直链流程：前者要带鉴权访问本服务流式端点，后者要等本服务后台获取文件完成。分别见[视频号下载](#视频号下载)和[X Space 回放下载](#x-space-回放下载)。

### 音频解析例外

`POST /api/resolve` 可传 `download_mode: "audio"`；该模式不表示服务会转码为 MP3，媒体格式以 `data.media_type` 为准。抖音、快手、小红书和视频号没有音频路径，会返回 HTTP 400（错误以 `audio_not_available` 开头）。本文只列出这项无音频路径的例外；通用请求与响应字段见 `/docs`。`audio` 与 `quality` 同时使用会返回 HTTP 422。

## 视频号下载

视频号解析成功后，`data.video_url` 指向本服务的 `GET /api/stream/wechat_channels/{sph_code}`，不是第三方直链。请求须带 `X-API-Key`；服务未配置 `API_KEY` 时才不校验。客户端不需要也不应自行解密，收到的内容是可播放的 MP4。

### 流式下载与 Range

```bash
curl -L -H 'X-API-Key: your-api-key' \
  -H 'Range: bytes=0-131071' \
  -o video-partial.mp4 \
  'https://your-service.example/api/stream/wechat_channels/SHARE_CODE'
```

不带 `Range` 时返回完整文件（HTTP 200）；支持单个标准字节范围，带 `Range` 返回 HTTP 206 和 `Content-Range`。可用 Range 续传或按需读取；不支持的 Range 返回 HTTP 416。服务端按有界窗口读取上游，客户端不应假设自己的 Range 原样透传给上游。服务端并发槽用尽时返回 HTTP 429；上游错误可能在响应开始前表现为 HTTP 502，开始后则可能是连接中断或正文不完整。

### XOR 与明文上游

视频号不是“源站文件一律加密”：TikHub 上游的文件前段使用 XOR 密文，服务端解密后再转发；Just One API 上游返回的是明文，服务端直接转发。无论命中哪家，客户端从流式端点拿到的都是明文 MP4，不要再次 XOR 解密。

### `/direct` 与上游差异

`GET /api/stream/wechat_channels/{sph_code}/direct` 返回前段明文 `head_b64`、`content_length`、`encrypted_head_bytes` 和 `cdn_url`，供客户端选择是否绕过本服务向上游继续取文件。端点本身需 `X-API-Key`。`encrypted_head_bytes` 是文件头长度/续传偏移，不表示返回的头部仍是密文。

1. Base64 解码 `head_b64` 得到明文文件头。若头部长度已达到 `content_length`，文件已完整，不再发 Range。
2. **TikHub 命中：**`cdn_url` 是 TikHub CDN 地址。将文件头写入目标文件，再从 `encrypted_head_bytes` 位置对该 CDN 地址请求 `Range: bytes=<encrypted_head_bytes>-`，把剩余字节追加到文件头后。该上游前段的 XOR 解密已由服务完成。
3. **Just One API 命中：**`cdn_url` 是本服务流式端点的相对路径，不是 Just One API 的第三方直链。先拼接服务基址，再带 `X-API-Key` 对本服务 URL 请求同样的 Range；文件本来就是明文。不要把该相对路径发给第三方 host。
4. 续传时已保存字节数应作为 Range 起点；若 TikHub 临时直链失效，重新请求 `/direct` 获取新地址后再继续。最终文件长度应等于 `content_length`。

`/direct` 适用于需要自行分段拼接的下载器。普通客户端直接请求 `data.video_url` 即可，不必调用此端点。

## X Space 回放下载

X Space 采用两阶段交付：先 `POST /api/resolve`，再通过 `data.video_url` 请求本服务。后台任务把回放物化为单个文件后，客户端取得普通文件响应；无需处理 HLS、分段 URL 或认证细节。仅支持可回放的 Space，不支持直播中或未开播的 Space。

### Resolve、轮询与下载

以下脚本轮询同一个下载 URL。HTTP 202 仅表示仍在获取，等待 `retry_after` 建议间隔（示例用 5 秒）后继续；HTTP 200 请求的响应体就是文件，直接写入目标路径。其它状态不应继续轮询。

```bash
KEY='your-api-key'
BASE='https://your-service.example'
URL=$(curl -fsS -X POST "$BASE/api/resolve" \
  -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"url":"https://x.com/i/spaces/1AKEmvYqbrOKL"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["video_url"])')

while :; do
  status=$(curl -sS -H "X-API-Key: $KEY" -o space.m4a \
    -w '%{http_code}' "$URL")
  case "$status" in
    202) sleep 5 ;;
    200) break ;;
    *) printf 'X Space 下载失败，HTTP %s\n' "$status" >&2; exit 1 ;;
  esac
done
```

关键状态语义：

- **202 Accepted：等待。** 服务已接受或正在执行后台获取；响应包含 `retry_after`，通常按该间隔轮询同一 URL。任务不依赖客户端连接存活。
- **200 OK：已就绪并下载。** 响应正文是 `audio/mp4` 文件（m4a 容器），带文件长度信息。上面示例在这次响应中保存文件，避免为了探测就绪而另行下载一遍。
- **400、404、502、503：停止轮询并报告失败。** 可能分别表示 Space ID/回放状态不可用、回放不可用、后台获取或缓存失败、队列/存储准入失败；不要把这些状态当成“尚未就绪”。`401` 表示鉴权失败，也应停止。任务失败状态只呈现一次；新的 GET 可能开始新任务，是否重试应由调用方按失败原因决定，不能无条件紧循环。

### Range 续传

文件就绪后是普通文件响应，支持 Range 断点续传。中断后使用 curl 的 `-C -` 续传，例如：

```bash
curl -C - -H 'X-API-Key: your-api-key' \
  -o space.m4a 'https://your-service.example/api/stream/twitter_space/1AKEmvYqbrOKL'
```

续传请求须在文件已经就绪后执行；未就绪时仍按 202 轮询协议处理。服务端缓存保留窗口由 `SPACE_CACHE_TTL_HOURS` 配置（默认 72 小时），过期文件不能保证继续下载。Space 仅支持回放，直播中、未开播或已不可用的回放不会通过反复轮询变为可下载文件。
