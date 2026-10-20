## 2026-10-21 — API Key 常数时间比较

- 当前阶段：repairing
- 本段结论：鉴权仅在已配置 API Key 时以 UTF-8 bytes 调用 `hmac.compare_digest`，保留未配置放行与缺失/错误 401。新增测试锁定 bytes 入参、非 ASCII 请求 401，以及原有正确/错误/缺失行为。
- 关键决策与已否决方案：只替换比较方式，不改鉴权流程；不使用字符串参数调用 `compare_digest`，避免非 ASCII 比较抛 `TypeError`。
- 下一步唯一动作：完成并验证 X Space 下载读取与物化写入的线程池卸载。
