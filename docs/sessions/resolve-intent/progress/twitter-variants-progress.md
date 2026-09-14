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
