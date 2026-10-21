## 2026-10-21 — API Key 常数时间比较

- 当前阶段：repairing
- 本段结论：鉴权仅在已配置 API Key 时以 UTF-8 bytes 调用 `hmac.compare_digest`，保留未配置放行与缺失/错误 401。新增测试锁定 bytes 入参、非 ASCII 请求 401，以及原有正确/错误/缺失行为。
- 关键决策与已否决方案：只替换比较方式，不改鉴权流程；不使用字符串参数调用 `compare_digest`，避免非 ASCII 比较抛 `TypeError`。
- 下一步唯一动作：完成并验证 X Space 下载读取与物化写入的线程池卸载。

## 2026-10-21 — X Space 文件 IO 卸载

- 当前阶段：验证中
- 本段结论：最终文件的 seek/read、物化的磁盘水位 statvfs 与 sink.write 均通过 AnyIO 工作线程执行；TestClient 验证了整文件、206 Range、416 与字节一致性。低水位仍抛 `SegmentFetchError`、清除 part 且 kill ffmpeg；窄测 98 项通过。
- 关键决策与已否决方案：水位阈值关闭时仍跳过磁盘检查，保持旧短路行为；未引入 aiofiles、新依赖或生产抽象。
- 下一步唯一动作：跑 `make test` 并完成 scope/提交前检查。

## 2026-10-21 — 全量验证

- 当前阶段：待提交
- 本段结论：`make test` 全量 584 项通过；`grep -c 'compare_digest' app/api/deps.py` 为 1。文件 IO 线程归属、下载范围字节、低水位异常与 ffmpeg 清理均有定向判据。
- 关键决策与已否决方案：未改范围外文件；未增加依赖或文件 IO 包装抽象。
- 下一步唯一动作：对候选提交跑卡面 scope 检查并提交 IO 实现与测试。

## 2026-10-21 — 提交与远端核验

- 当前阶段：succeeded
- 本段结论：鉴权与 IO 修复分两次提交，均已推送到 `origin/card/fix-c-sec-io`；远端 tip 与本地 HEAD 相同，工作树干净，最终 scope 检查通过。
- 关键决策与已否决方案：push 响应提示仓库迁移，但推送成功；用 `git ls-remote` 核实远端分支 tip，不以本地跟踪引用代替。
- 下一步唯一动作：将完整执行报告写入 `$DELEGATE_REPORT_PATH`。
