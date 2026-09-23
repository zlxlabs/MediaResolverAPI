# 视频号瞬态重试 + attempts 脱敏原因（#34 + #33）进度存档

## 段落 1：实现完成（implementing）

- 当前阶段：implementing，行为实现 + 测试收尾中。
- 本段结论：`_run_chain` 通用引擎已支持每端点有限重试（视频号 3 次 + 0.3s 退避，其余链上限 1）；`classify` 支持 `(decision, reason)` 扩展并归一化记入 `attempts`；`_call_endpoint` 在视频号 POST 路径非 2xx + dict body 时抛 `EndpointHttpError(status, body)`；下载侧手搓 3 次循环已删除，改为单次调用。
- 关键决策与已否决方案：
  - 决策：`EndpointHttpError` 只在视频号 POST 路径抛出，GET 路径保持直接返回 body——否则抖音/小红书两处旧测试（断言直接返回 body）转红，而约束要求那 8 个文件零改动全绿。GET 通道的 handler 代码保留但对其无行为变化。
  - 决策：`WECHAT_CHANNELS_TOTAL_BUDGET` 保持 30s 不动；重试前检查剩余预算，不足则停试并走 `VideoNotFoundError`，不换成 timed out。
  - 已否决（卡锁定）：只在下载路径加第二次循环；在 API 层加重试；按响应文本正则猜原因。
- 下一步唯一动作：跑全量回归 + 红验（上限 3→1 注入），然后按三段提交。
