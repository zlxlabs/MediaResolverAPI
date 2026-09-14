## 里程碑 1：设计基线

- 当前阶段：implementing
- 本段结论：已按任务卡逐字落盘 resolve 请求意图设计。后续实现只覆盖 download_mode/audio，不提前实现 quality。
- 关键决策与已否决方案：沿用设计中的意图式参数、静态路由、fail-fast 与缓存隔离；不采用 variants、format=mp3、静默回退视频。
- 下一步唯一动作：为 VideoInfo 与缓存键增加 download_mode/media_type，并实现旧表自动迁移。

## 里程碑 2：VideoInfo 与缓存隔离

- 当前阶段：implementing
- 本段结论：VideoInfo 默认携带 `media_type=video`，缓存键已扩展为 `(platform, video_id, download_mode)`。SQLite 启动连接和 CacheService 绑定数据库都会自动补列、重建唯一索引，旧行继续按 video 命中。
- 关键决策与已否决方案：不依赖 SQLAlchemy `create_all` 做存量迁移；不丢弃旧行、不引入手工 SQL 步骤。
- 下一步唯一动作：实现 YouTube audio 轨解析与 media_type 填充。

## 里程碑 3：YouTube 音频选轨

- 当前阶段：implementing
- 本段结论：YouTube audio 模式只读取 v2 `streamingData.adaptiveFormats`，过滤 `audio/*` 且必须有直链，按 bitrate 选择最高轨。默认 video 路径仍沿用原有 `videos.items` / `streamingData.formats` 选择逻辑。
- 关键决策与已否决方案：现有 web fixture 没有音频轨、v2 fixture 只有纯视频 adaptive 轨；因此不从 web schema 猜音频，也不接受只有 `signatureCipher` 的轨。
- 下一步唯一动作：把 download_mode 穿过 resolver、TikHub/Cobalt adapter 与端点错误映射。

## 里程碑 4：provider 路由与 API 契约

- 当前阶段：implementing
- 本段结论：resolver 已实现 audio 静态路由与四平台 fail-fast 400，Twitter 等五平台 audio 只调用 Cobalt；YouTube/TikHub 与 Cobalt adapter 会填充 media_type，Cobalt audio 请求体透传 `downloadMode`。
- 关键决策与已否决方案：默认 video 请求不增加 Cobalt 字段；audio 失败不退回 video；无音频平台在任何 provider 调用前抛出 `audio_not_available`。
- 下一步唯一动作：新增集中测试 fixture，覆盖端到端 audio、路由、缓存迁移与 422。
