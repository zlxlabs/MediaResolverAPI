## 里程碑 1：设计正文

当前阶段：implementing

本段结论：已按任务卡原文落盘 X（Twitter）视频解析设计，锁定 TikHub 主源、Cobalt 兜底、平台标识与选流不变式。

关键决策与已否决方案：采用 `twitter` 平台名和 TikHub `fetch_tweet_detail?tweet_id=` 单端点；不采用只接 Cobalt、只接 TikHub、yt-dlp、平台名 `x`。

下一步唯一动作：补齐 URL 解析与 Twitter fixture/测试，并提交红测试阶段。
