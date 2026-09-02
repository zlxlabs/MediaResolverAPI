# wx-stream-window 进度

## 2026-09-02 提交① 配置与 `_read_window`

- 当前阶段：implementing
- 本段结论：新增 `STREAM_WINDOW_BYTES`（默认 4194304）与唯一打 CDN 的 `_read_window`：有界 Range、整块读完再 `aclose`、206 元数据校验。端点仍走旧透传，本段不改对外语义。
- 关键决策与已否决方案：轴 13 的「起点 0 + 200 + Content-Length ≤ 窗口」例外写在 `_read_window` 里，因为它封的是规范允许的行为。无。
## 2026-09-02 提交② 主循环替换透传

- 当前阶段：implementing
- 本段结论：端点改为有界窗口探针 + `_iter_windows` 拼接，客户端无 Range 仍是 200、有 Range 仍是 206。旧的「CDN 首包 200/206 矩阵」已删，改由窗口大小 × Range 矩阵锁字节正确性。
- 关键决策与已否决方案：中间窗口 Content-Range 短于请求且未到 EOF 视为短包失败，不再沿用旧的「按 CDN 终点截断」；通配 `*` 完整长度无法给出准确 Content-Length，首窗按 502 处理。
- 下一步唯一动作：给窗口失败加上 3 次/总预算 20 的重试、401/403/404/410 换对，以及后续窗断开语义。
