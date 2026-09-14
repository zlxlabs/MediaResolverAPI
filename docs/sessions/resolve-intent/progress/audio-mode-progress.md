## 里程碑 1：设计基线

- 当前阶段：implementing
- 本段结论：已按任务卡逐字落盘 resolve 请求意图设计。后续实现只覆盖 download_mode/audio，不提前实现 quality。
- 关键决策与已否决方案：沿用设计中的意图式参数、静态路由、fail-fast 与缓存隔离；不采用 variants、format=mp3、静默回退视频。
- 下一步唯一动作：为 VideoInfo 与缓存键增加 download_mode/media_type，并实现旧表自动迁移。
