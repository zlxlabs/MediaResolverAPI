# DESIGN-note：resolve 请求意图（只下音频 / 清晰度封顶）

下游（如 ASR）只需要音频时，不该被迫下载完整 MP4；调用方也应能封顶清晰度以控制体积与耗时。本 note 覆盖 issue #23 的完整落地计划，分两卡串行：卡 1 落地 `download_mode`，卡 2 落地 `quality`。

不做：`variants[]` 菜单式响应（当前无第二个真实消费者；意图式落地后它只是中间产物的序列化，届时再加）；转码与容器格式协商（不承诺 MP3，ASR 消费的是可解码音频流，m4a/opus 均可）；yt-dlp；改变任何默认行为；把「最高清」立为全站新不变式。

选定方案：

1. 请求体加 `download_mode: "video" | "audio"`（缺省 `video`）与 `quality: "<int>p"`（可选）。参数表达意图，不表达容器格式。
2. 响应加 `media_type: "video" | "audio"`（缺省 `video`）自描述字段；直链仍放 `video_url`，向后兼容。
3. `audio` 意图的三层语义（按平台静态路由，不逐个试）：
   - YouTube：TikHub 响应里选纯音频轨（v2 schema 的 `streamingData.adaptiveFormats` 中 `mimeType` 以 `audio/` 开头者；现状代码在 `app/services/platforms/youtube.py:136-139` 把 adaptiveFormats 整体丢弃，本卡捡起音频部分）。
   - twitter / tiktok / instagram / pinterest / facebook：直接走 Cobalt，请求体带 `downloadMode: "audio"`（上游原生支持，本仓此前未透传）；audio 模式不打 TikHub。
   - douyin / kuaishou / xiaohongshu / wechat_channels：无音频路径，显式报错——HTTP 400，body 含稳定机读前缀 `audio_not_available`，且不发起任何 provider 调用。禁止静默退回视频。
4. `quality` 语义是「封顶」不是「精确匹配」：≤ cap 取最高档；全部高于 cap 时取最小超档。显式 `quality` 覆盖平台默认规则；不传时平台默认原样（Twitter 仍封 1080p，YouTube 仍优先 1080p 压 4K）。与 Twitter 已合并的 1080p 封顶同一语义。
5. 缓存键从 `(platform, video_id)` 扩为含 `download_mode`（卡 1）与归一化 `quality`（卡 2）的元组；存量缓存行视为 `video`/默认档继续服务。同一 `video_id` 的不同意图结果互不覆盖——这是本功能唯一的 P1 级静默出错风险。

不采用：菜单式 `variants[]`（无第二消费者）；`format=mp3`（引入转码责任）；audio 不支持时静默退回视频（违反 fail-fast）；顺手统一各平台默认选流规则（非目标，默认行为逐字节不变）。

不变式（均须测试锁死；卡 1 锁 1/2/3/5，卡 2 锁 4）：

1. [实测] 不传新参数的请求行为逐字节不变：既有测试文件零改动且全量绿。
2. [实测] audio 路由矩阵：youtube → TikHub 纯音频轨；twitter/tiktok/instagram/pinterest/facebook → 只走 Cobalt 且请求体含 `downloadMode: "audio"`；douyin/kuaishou/xiaohongshu/wechat_channels → HTTP 400 含 `audio_not_available` 且零 provider 调用。
3. [实测] 同一 `(platform, video_id)` 的 audio 与 video 缓存互不覆盖；旧表结构迁移后存量行继续命中 video 模式。
4. [实测] quality 封顶：≤cap 最高档；全 >cap 取最小超档；显式 quality 覆盖平台默认。
5. [实测] `media_type` 与直链一致：audio 时 `video_url` 是音频流地址；缺省与缓存旧数据读出均为 `video`。

入口：`POST /api/resolve`（TestClient，桩掉 provider HTTP）。提交 `{"url": <youtube>, "download_mode": "audio"}`，预期 200，`data.media_type == "audio"`，`data.video_url` 为音频流地址。
