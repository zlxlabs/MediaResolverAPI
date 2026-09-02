# wx-stream-window 进度

## 2026-09-02 提交① 配置与 `_read_window`

- 当前阶段：implementing
- 本段结论：新增 `STREAM_WINDOW_BYTES`（默认 4194304）与唯一打 CDN 的 `_read_window`：有界 Range、整块读完再 `aclose`、206 元数据校验。端点仍走旧透传，本段不改对外语义。
- 关键决策与已否决方案：轴 13 的「起点 0 + 200 + Content-Length ≤ 窗口」例外写在 `_read_window` 里，因为它封的是规范允许的行为。无。
- 下一步唯一动作：用 `_read_window` 替换 `_iter_decrypted` 透传，Range 矩阵按窗口大小参数化。
