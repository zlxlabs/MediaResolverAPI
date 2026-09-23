# 视频号瞬态重试 + attempts 脱敏原因（#34 + #33）进度存档

## 段落 1：实现完成（implementing）

- 当前阶段：implementing，行为实现 + 测试收尾中。
- 本段结论：`_run_chain` 通用引擎已支持每端点有限重试（视频号 3 次 + 0.3s 退避，其余链上限 1）；`classify` 支持 `(decision, reason)` 扩展并归一化记入 `attempts`；`_call_endpoint` 在视频号 POST 路径非 2xx + dict body 时抛 `EndpointHttpError(status, body)`；下载侧手搓 3 次循环已删除，改为单次调用。
- 关键决策与已否决方案：
  - 决策：`EndpointHttpError` 只在视频号 POST 路径抛出，GET 路径保持直接返回 body——否则抖音/小红书两处旧测试（断言直接返回 body）转红，而约束要求那 8 个文件零改动全绿。GET 通道的 handler 代码保留但对其无行为变化。
  - 决策：`WECHAT_CHANNELS_TOTAL_BUDGET` 保持 30s 不动；重试前检查剩余预算，不足则停试并走 `VideoNotFoundError`，不换成 timed out。
  - 已否决（卡锁定）：只在下载路径加第二次循环；在 API 层加重试；按响应文本正则猜原因。
- 下一步唯一动作：跑全量回归 + 红验（上限 3→1 注入），然后按三段提交。

## 段落 2：R1 + R2 收口（review-fixes）

- 当前阶段：review-fixes，gate primary 两 major 已收口，待全量回归确认。
- 本段结论：R1 起 `EndpointHttpError` 统一到所有 verb（POST-only 分叉被否决，抖音/小红书两条契约用例已改写）；R2 补两处——`_should_retry` 要求剩余预算 > `per_timeout + retry_backoff`，且总预算超时在「重试链 + 有 retryable + 未达上限」时改抛 `VideoNotFoundError`；`describe_failure` 的 `object_type` 仅 int 标量进 attempts，非 int 只记类型名。最终口径：耗尽恒为 `VideoNotFoundError`，attempts 原因字段只含标量。
- 关键决策与已否决方案：
  - 超时兜底三条件严格限定，首跳挂起与单端点链行为不变（两处既有 timeout 用例原样绿）。
  - 已否决：为让旧测试绿而保留 verb 分叉（R1 已否决）；把整包 `object_type` 打码后保留（容器一律不进日志）。
- 下一步唯一动作：全量回归 + F1/F2 最小注入红验后提交。

## 段落 3：R3 gate finding 收口（repairing）

- 当前阶段：repairing，撤销 R2 超时改判并补齐 object_type 有界归因。
- 根因：R2 把单次尝试自身超时误并入「预算耗尽」，在 `_run_chain` 的 `TimeoutError` 处理里改抛 `VideoNotFoundError`；同时把「容器不得进日志」错误收窄成「只允许 int」，丢失安全字符串归因。
- 本段结论：保留 `_should_retry` 的剩余预算 > `per_timeout + retry_backoff` 判定；预算没花完就耗尽仍为 `VideoNotFoundError`，单次尝试本身超时恢复为 `ProviderError(... timed out)`。`object_type` 仅允许精确 int 或 32 字符内、`[A-Za-z0-9_.:-]+` 安全字符串带值，容器、超长/不安全字符串、bool/float/None 只带类型名。
- 验收锁定：重试耗尽三跳仍为 `VideoNotFoundError`；预算装不下下一次尝试时调用数为 1 且非 timed out；单次尝试超时为 `ProviderError`；跨边界容器归因不泄露凭据。
