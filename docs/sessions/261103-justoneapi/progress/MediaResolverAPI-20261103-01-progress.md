# 进度存档：MediaResolverAPI-20261103-01

## 2026-11-03 11:52 · 单元 1：JustOneApiProvider + 链接线 + 空 token 守卫

- **当前阶段**：implementing
- **本段结论**：JustOneApiProvider（8 平台端点单枪表 + 视频号两枪媒体方法 + 业务码不叠打 + token 不进日志/异常）落盘；VideoResolver 默认链按锁定决策接入 justoneapi，空 token 时链上不出现该源；config/.env.example/conftest（autouse 空 token）就位；provider 单测 33 条全绿，全量 655 绿。
- **关键决策与已否决方案**：JOA HTTP 用 Cobalt 式裸 httpx.AsyncClient 单发，不用 HTTPClient——其默认 max_retries=3 会把不计费业务码打成叠打，且其错误日志会内嵌带 token 的完整 URL，两个都违反锁定决策；resolve 级视频号只打 basic-info 一枪（video_url 恒为本服务流端点，download-url 留给 stream 层），纯数字 video_id 视为 objectId 不打 basic-info（约束 3）。
- **下一步唯一动作**：JustOneApiAdapter 信封对齐 + resolver 链/E2E 测试（单元 2）。
