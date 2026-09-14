## 里程碑 1：设计正文

当前阶段：implementing

本段结论：已按任务卡原文落盘 X（Twitter）视频解析设计，锁定 TikHub 主源、Cobalt 兜底、平台标识与选流不变式。

关键决策与已否决方案：采用 `twitter` 平台名和 TikHub `fetch_tweet_detail?tweet_id=` 单端点；不采用只接 Cobalt、只接 TikHub、yt-dlp、平台名 `x`。

下一步唯一动作：补齐 URL 解析与 Twitter fixture/测试，并提交红测试阶段。

## 里程碑 2：URL 与 TikHub 主源

当前阶段：implementing

本段结论：URLParser 已识别 x.com、twitter.com 子域、t.co 与 status 数字 ID；TwitterService 已按本帖第一条视频选最高码率 MP4，并完成 TikHub 单端链、参数和非终态降级接线。

关键决策与已否决方案：只读取 `data.media.video[0]`，无主帖视频才读取 `entities.media` 中第一条 `type==video` 的 `video_info`；跳过 HLS 与 quoted.media，TikHub 失败统一抛 VideoNotFoundError 交给 Cobalt。

下一步唯一动作：接入 Cobalt、VideoResolver 默认链、配置覆盖与平台适配器，并验证端到端桩路由。

## 里程碑 3：双 provider 接线

当前阶段：implementing

本段结论：TikHubAdapter、CobaltAdapter、CobaltProvider 与 VideoResolver 已接入 `twitter`；默认顺序为 `tikhub,cobalt`，并支持 `PROVIDER_PRIORITY_TWITTER` 覆盖，TestClient 已验证 `/api/platforms` 与 `/api/resolve` 桩路径。

关键决策与已否决方案：Cobalt 仅提供通用直链和 Twitter 展示名，TikHub 继续负责完整 VideoInfo；未增加请求字段，也未把 twitter 放入 URL_FALLBACK_PLATFORMS。

下一步唯一动作：补齐 API/README/CHANGELOG/通用引擎文档，并做反向红验与全量验证。
