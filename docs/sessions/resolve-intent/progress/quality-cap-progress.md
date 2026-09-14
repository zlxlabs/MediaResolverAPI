## 里程碑 1：请求模型与组合校验

- 当前阶段：implementing
- 本段结论：ResolveRequest 已接受可选 `quality`，仅允许 `^\d+p$`；与 `download_mode=audio` 组合在 Pydantic 请求校验阶段返回 422。
- 关键决策与已否决方案：保持 quality 的请求值原样用于后续缓存归一化；不在端点函数内静默忽略 audio + quality。
- 下一步唯一动作：为 VideoCache 增加 quality 列并完成两步 SQLite 启动迁移。

## 里程碑 2：缓存质量键与两步迁移

- 当前阶段：implementing
- 本段结论：VideoCache 与 CacheService 已将归一化 quality（缺省为空串）纳入读写键；SQLite 启动迁移可从原始旧表经 download_mode 再迁 quality，旧视频行仍命中缺省档。
- 关键决策与已否决方案：卡 1 之前的旧索引保留历史名称作为过渡兼容索引，另建完整四列唯一索引；卡 1 结构则直接重建原唯一索引为四列，不丢存量行。
- 下一步唯一动作：为 YouTube 与 Twitter 加入显式 quality 封顶选流并锁住默认路径不变。

## 里程碑 3：YouTube 与 Twitter 封顶选流

- 当前阶段：implementing
- 本段结论：YouTube/Twitter 显式 quality 已按短边分辨率实现“≤cap 取最高档、全超 cap 取最小超档、同档比码率”；不传 quality 继续走原默认排序，Twitter 显式 `2160p` 可越过默认 `1080p`。
- 关键决策与已否决方案：全仓采用短边 `min(width, height)` 作为 `p` 档基准；没有可用分辨率元数据时显式 quality 走该平台原默认路径，不猜测 URL 或码率代表分辨率。
- 下一步唯一动作：把 quality 穿过 resolver/adapter/provider，并实现 Douyin 多档与其余平台 no-op 行为。
