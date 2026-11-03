# 进度存档：MediaResolverAPI-20261103-05

## 2026-11-03 14:41 · 单元 1：文档收成两份（README 对接合同 + docs/architecture.md）+ 车间纸出仓

- **当前阶段**：verifying
- **本段结论**：活文档收成两份——`README.md`（对接合同：平台表/环境变量/`data.provider`/`/api/platforms`/视频号两种上游/`/direct`）与新建 `docs/architecture.md`（104 行，给改代码的人：两层路由、默认供应商表、空 token 跳过、TikHub 端点链表、`TerminalError` 停整链、parse_url 无损与两条兜底入口、视频号 XOR/明文与 objectId 复用、Just One API 约束）。卡面清单的 24 份车间纸全部 `git rm`（四份设计稿/交接 prompt + 任务卡/进度/咨询/探测报告），保留四份 `docs/sessions/*/design.md` 与 `docs/project-memory.md`。全量 `make test` 696 绿；Accept-Check 与行为验收逐条绿。
- **关键决策与已否决方案**：
  - **README 只打补丁**（净增 60 行，未超 150 行上限）：平台表数据源列改为「TikHub→Just One API」「TikHub→Cobalt→Just One API」，并在表下注明「Just One API 一列只在 `JUSTONEAPI_TOKEN` 非空时存在」；删掉「TikHub 单源」表述（那是高风险区：会让对接人以为抖音/小红书/快手/视频号没有备源）。
  - **`/direct` 按上游分支写**（高风险区）：TikHub 命中 → `cdn_url` 是 TikHub CDN 直链；Just One API 命中 → `cdn_url` 是本服务相对路径 `/api/stream/wechat_channels/{sph_code}`、头已是明文、客户端对本服务发 Range，禁止对第三方 host 发 Range。README 的 `/direct` 字段表与「客户端拼接协议」同步改，不再宣称「永远 CDN 直链」。
  - **architecture.md 只搬事实不搬纸**：TikHub 端点链表从 `generic-fallback-engine.md` §4 搬来并把「兜底」列改成「端点层之外还有谁兜底」；schema 事实留在 `app/services/platforms/*.py` 注释里不复制；不复制任务列表、GSTACK 评审、探测逐条、未实现 TODO（候选端点指向 tikhub.py 链常量注释）。
  - **代码注释只删路径**：`douyin.py` / `xiaohongshu.py` / `collect_douyin_fixtures.py` 指向被删文档的路径就地改掉（xiaohongshu 的 RST 字面块 `::` 保留，否则缩进块失去标记）。
  - **已否决**：为省事把 schema 事实再抄一份进 architecture.md（卡面明令禁止，且会与代码漂移）；删 `docs/sessions/261014-d3-release/design.md`（deploy.yml 引用它）；把 `/direct` 写成「TikHub 终态后再试备源」（错：终态停整条供应商链，含 Just One API）。
- **未做（越权，需主脑裁）**：`CONTRIBUTING.md:36`、`docs/sessions/261014-d3-release/design.md:39`、`retro/consult/20261014-115831-codex-09e2bb.md` 三处仍指向已删文档（分别为 `docs/generic-fallback-engine.md`、`docs/handoff-generic-fallback-engine.md`、`x-space-probe/probe-report.md`）；三份文件均不在本卡 Scope-Globs 内，未改，已在报告里显式列出待主脑处理。
- **下一步唯一动作**：commit + push + 开 draft PR（assignee @me），写 report.md 收尾。
