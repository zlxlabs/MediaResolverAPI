## 里程碑 1：设计正文

当前阶段：implementing

本段结论：已按任务卡原文落盘 X（Twitter）视频解析设计，锁定 TikHub 主源、Cobalt 兜底、平台标识与选流不变式。

关键决策与已否决方案：采用 `twitter` 平台名和 TikHub `fetch_tweet_detail?tweet_id=` 单端点；不采用只接 Cobalt、只接 TikHub、yt-dlp、平台名 `x`。

下一步唯一动作：补齐 URL 解析与 Twitter fixture/测试，并提交红测试阶段。

## 里程碑 2：URL 与 TikHub 主源

当前阶段：implementing

本段结论：URLParser 已识别 x.com、twitter.com 子域、t.co 与 status 数字 ID；TwitterService 已按本帖第一条视频选最高码率 MP4，并完成 TikHub 单端链、参数和非终态降级接线。

关键决策与已否决方案：只读取 `data.media.video[0]`，无主帖视频才读取 `entities.media` 中第一条 `type==video` 的 `video_info`；跳过 HLS 与 quoted.media，TikHub 失败统一抛 VideoNotFoundError 交给 Cobalt。

下一步唯一动作：接入 Cobalt、VideoResolver 默认链、配置覆盖与平台适配器，并验证端到端桩路由。

## 里程碑 3：双 provider 接线

当前阶段：implementing

本段结论：TikHubAdapter、CobaltAdapter、CobaltProvider 与 VideoResolver 已接入 `twitter`；默认顺序为 `tikhub,cobalt`，并支持 `PROVIDER_PRIORITY_TWITTER` 覆盖，TestClient 已验证 `/api/platforms` 与 `/api/resolve` 桩路径。

关键决策与已否决方案：Cobalt 仅提供通用直链和 Twitter 展示名，TikHub 继续负责完整 VideoInfo；未增加请求字段，也未把 twitter 放入 URL_FALLBACK_PLATFORMS。

下一步唯一动作：补齐 API/README/CHANGELOG/通用引擎文档，并做反向红验与全量验证。

## 里程碑 4：接口与交付文档

当前阶段：implementing

本段结论：已更新 resolve 支持列表、README 平台表/响应枚举/平台列表示例、CHANGELOG Unreleased Added 与通用降级引擎的 X 行、超时和不变式说明。

关键决策与已否决方案：文档明确平台标识为 `twitter`，支持 x.com/twitter.com status URL，TikHub 主源、Cobalt 兜底；`URL_FALLBACK_PLATFORMS` 保持不包含 twitter。

下一步唯一动作：执行至少两次有效红验、运行目标测试与整仓 pytest，检查 diff 预算和工作区清洁度。

## 里程碑 5：目标测试闭环

当前阶段：implementing

本段结论：目标测试已覆盖 TikHub 选流、时长/统计、引用帖/HLS/图文拒绝、entities 视频备用形态、单端参数、永不终态、Cobalt 责任链与 TestClient 入口，共 56 项通过。

关键决策与已否决方案：`has_playable` 与 adapter 均复用 `TwitterService._parse_response`；Cobalt fallback 使用桩响应验证，不请求真实网络。

下一步唯一动作：完成反向红验后运行整仓 `pytest` 并整理最终报告。

## 里程碑 6：最终验证

当前阶段：complete

本段结论：两次反向红验均由断言失败转红并已精确还原；目标测试 56 项通过，整仓 pytest 首轮 360 项通过；最终提交后将再次复核相同命令。

关键决策与已否决方案：最终实现只改任务卡允许文件，未引入指定清晰度/音频字段、真实上游请求、引用帖视频、HLS 或 status 分轨解析。

下一步唯一动作：在本段提交后重跑 Verify-Command 与整仓 `pytest`，然后写入 delegate 报告。

## 里程碑 7：本地 review 收紧

当前阶段：implementing

本段结论：按仓库 fail-fast 约定删除 TwitterService 中异常 bitrate 与日期的防御式 catch，保留任务要求的实体视频回退和缺失视频返回 None 语义。

关键决策与已否决方案：OCR 前置扫描主腿启动后约 60 秒无结果并以退出码 130 停止，未把空结果当作通过；未引入新的重试或 fallback 机制。

下一步唯一动作：提交本次 review 收紧并重新跑目标测试、整仓 pytest。

## 里程碑 8：交付完成

当前阶段：complete

本段结论：fail-fast 收紧提交后，Verify-Command 56 项通过，整仓 pytest 360 项通过，工作区保持清洁。

关键决策与已否决方案：未采纳 OCR 主腿约 60 秒无输出后的结果为通过；最终验收以本地断言、桩接线、整仓测试和 git 取证为准。

下一步唯一动作：生成并提交最终 delegate 报告摘要，保持当前分支不变。

## 里程碑 9：多视频回归测试

当前阶段：repairing

本段结论：新增多视频 fixture，并用第一条低码率 MP4、第一条仅 HLS 两种场景锁定解析器必须继续遍历后续 `media.video` 条目；两条测试在旧实现上按预期断言失败。

关键决策与已否决方案：选本帖全部 `data.media.video[*].variants` 的全局最高码率 MP4；不读取 `quoted.media`，不实现 `/video/N` 选择。

下一步唯一动作：修改 TwitterService 的主帖选流遍历并通过新增回归测试。

## 里程碑 10：多视频全局选流修复

当前阶段：repairing

本段结论：TwitterService 已遍历本帖全部 `data.media.video[*].variants`，从所有 `video/mp4` 中选择全局最高 bitrate；第一条仅 HLS 时仍能命中后续视频，目标测试 58 项通过。

关键决策与已否决方案：仅在主帖没有任何可播 MP4 时回退 `entities.media`；不读取 `quoted.media`，不恢复只看 `video[0]` 的实现。

下一步唯一动作：提交修复并完成反向断言红验与整仓 `pytest`。

## 里程碑 11：畸形元数据 fail-soft 修复

当前阶段：repairing

本段结论：TwitterService 在解析 `bitrate`、`duration`、`created_at` 时统一增加 fail-soft 异常保护；非规范格式不抛出 ValueError，畸形 bitrate 视为 0、duration 视为 None、created_at 视为 None，只要存在合法 MP4 依然正常返回 VideoInfo；无可用 MP4 则返回 None，让 TikHub 能够优雅落入兜底链路。

关键决策与已否决方案：int/date 解析失败按字段不可用处理，不让解析器整体崩溃抛出 ValueError；维持不读取 `quoted.media` 的约束；不修改全局异常结构。

下一步唯一动作：执行反向红验断言并完成整仓测试与提交。

## 里程碑 12：交付完成与反向红验

当前阶段：complete

本段结论：针对畸形 bitrate/duration/created_at 的反向变异红验均确认为断言失败（AssertionError），还原后 62 项目标测试与 366 项整仓测试全部通过，diff 行数控制在预算内。

关键决策与已否决方案：不新增冗余抽象；直接在对应数值/时间解析边界做局部 fail-soft。

下一步唯一动作：提交修改至分支并产出最终 delegate 报告。

## 里程碑 13：冻结 Twitter 输入文法并封顶 1080p 选流

当前阶段：complete

本段结论：
1. 输入文法冻结：status ID 提取严格限制在 `urlparse(url).path` 上匹配 `/(?:i/)?status/<digits>(?:/|$)`；查询串和 fragment 里的 `/status/数字` 一律当不存在；文法外网址（如 `/home?next=/someone/status/xxx`）直接返回 `(None, None)`，外部 400，文法外网址 = 不支持，不再开放式防御新脏输入。
2. 选流封顶 1080p：短边 `min(width, height)` 分池。优先池 `1 <= 短边 <= 1080` 按 (短边, bitrate) 取最大，1080 压过 720 与 1440/2160；次池短边未知 (0) 按码率取最大；末池短边 > 1080 取短边最小者兜底，保证仍有一条可播 MP4 返回。
3. 经反向变异红验（path-only 与 1080 封顶两项断言转红）确认断言有效；文档与测试全量同步通过。

关键决策与已否决方案：不再在整段 URL 上跑正则；不保留 4K 压过 1080p；维持禁止读取 quoted.media。

下一步唯一动作：生成交付报告并提交分支。
