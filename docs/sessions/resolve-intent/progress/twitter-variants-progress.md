## 2026-09-14 — VideoInfo contract

- 当前阶段：implementing — VideoInfo + to_dict
- 本段结论：为统一视频信息增加可选 `variants` 字段，并让缓存序列化带出该键；既有构造方不受影响。
- 关键决策与已否决方案：沿用任务卡的附加可选字段契约，不增加开关或兼容分支。
- 下一步唯一动作：在 Twitter 解析层从既有 candidates 构造全部 mp4 variants。

## 2026-09-14 — Twitter parsing

- 当前阶段：implementing — Twitter 解析层
- 本段结论：Twitter 解析现在把 candidates 中的全部 mp4 variant 映射为 url、bitrate、width、height、quality，并按码率升序输出；HLS 仍不进入 candidates，主选流逻辑未变。
- 关键决策与已否决方案：无
- 下一步唯一动作：把 variants 接入缓存命中重建路径。

## 2026-09-14 — Cache round-trip

- 当前阶段：implementing — cache 回环
- 本段结论：缓存重建从序列化数据透传 `variants`，旧缓存行没有该键时自然得到 `None`；新缓存数据可完整回环。
- 关键决策与已否决方案：沿用任务卡约定，不添加旧行回填或额外兼容分支。
- 下一步唯一动作：增加 API 响应模型和 `_build_response` 的 variants 映射。

## 2026-09-14 — API response contract

- 当前阶段：implementing — API 模型与映射
- 本段结论：新增 `VideoVariant` 和可选 `VideoInfoResponse.variants`，响应映射逐条绝对化 URL；`VideoInfo.variants is None` 时响应字段保持 `null`。
- 关键决策与已否决方案：variants 只作为常驻附加字段，不增加请求参数或平台分支。
- 下一步唯一动作：补解析层与 API/缓存回环测试，并先执行未改代码红验。

## 2026-09-14 — Tests

- 当前阶段：implementing — 测试
- 本段结论：新增解析层完整 mp4、排序/非法码率测试，以及 API force-refresh 与缓存命中逐一相等的 X variants 断言；同时锁定非 Twitter 响应为 `null`。
- 关键决策与已否决方案：无
- 下一步唯一动作：完成 README 字段表与示例，并执行红验和全量测试。
