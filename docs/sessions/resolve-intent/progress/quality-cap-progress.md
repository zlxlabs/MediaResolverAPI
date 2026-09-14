## 里程碑 1：请求模型与组合校验

- 当前阶段：implementing
- 本段结论：ResolveRequest 已接受可选 `quality`，仅允许 `^\d+p$`；与 `download_mode=audio` 组合在 Pydantic 请求校验阶段返回 422。
- 关键决策与已否决方案：保持 quality 的请求值原样用于后续缓存归一化；不在端点函数内静默忽略 audio + quality。
- 下一步唯一动作：为 VideoCache 增加 quality 列并完成两步 SQLite 启动迁移。
