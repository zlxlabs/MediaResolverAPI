## 2026-09-14 — VideoInfo contract

- 当前阶段：implementing — VideoInfo + to_dict
- 本段结论：为统一视频信息增加可选 `variants` 字段，并让缓存序列化带出该键；既有构造方不受影响。
- 关键决策与已否决方案：沿用任务卡的附加可选字段契约，不增加开关或兼容分支。
- 下一步唯一动作：在 Twitter 解析层从既有 candidates 构造全部 mp4 variants。
