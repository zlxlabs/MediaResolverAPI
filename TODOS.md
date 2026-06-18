# TODOS

## P3: 实时日志流（WebSocket）

**What:** 仪表盘通过 WebSocket 实时推送新解析请求，替代当前的前端轮询机制。
**Why:** 比 30s 轮询更实时，运维体验更好，类似 tail -f。
**Pros:** 即时反馈，减少不必要的 HTTP 请求。
**Cons:** 增加服务端复杂度，需要 WebSocket 连接管理和断线重连逻辑。
**Context:** 仪表盘初版使用前端 30s 轮询 usage_log 表。WebSocket 可在需求明确后升级。FastAPI 原生支持 WebSocket。
**Effort:** M (human ~2 days / CC ~20 min)
**Depends on:** Web Dashboard 仪表盘功能完成后。

## P3: 抖音 play_count 播放量补数

**What:** 通过 TikHub `/api/v1/douyin/app/v3/fetch_multi_video_statistics` 单独补齐 `view_count`（播放量）。
**Why:** `douyin.py:89` 读 `statistics.play_count`，但 TikHub 文档明确「抖音大多数接口已不再返回作品的播放数」，该字段大概率恒为 `null`。
**Pros:** 拿回播放量字段，数据更完整。
**Cons:** 该接口付费 $0.025/次（远高于解析本身），且需额外一次网络往返；与无水印解析主线无关。
**Context:** 现有代码对 `null` 已优雅降级（`_parse_count(None)` 返 `None`），README 也声明该字段可空，故非缺陷而是数据缺口。仅在确有播放量需求时再做，建议做成可选参数按需触发。
**Effort:** S (human ~1h / CC ~10 min)
**Depends on:** 无。

## P3: 端点降级链模式泛化到 TikTok

**What:** 把本次为抖音落地的「provider 内多端点降级链 + 终态分类器」模式泛化到 TikTok（web/app 双源）。
**Why:** TikTok 同样存在单端点失效风险，web 与 app 接口独立失败，可复用同一降级机制提升成功率。
**Pros:** 复用已验证的链式重试 + 分类器，TikTok 解析鲁棒性对齐抖音。
**Cons:** 现在做属过早抽象（YAGNI）；TikTok 已有 cobalt 降级兜底，优先级低于抖音。
**Context:** 待抖音三级链在生产跑稳、provider 内「取→分类→解析→重试」的接口形状固化后再泛化，避免为单平台需求过早做通用抽象。
**Effort:** M (human ~0.5 day / CC ~20 min)
**Depends on:** 抖音多级降级机制上线并稳定后。
**Status:** 已被 `docs/handoff-generic-fallback-engine.md`（通用降级引擎）取代，TikTok 一并纳入全平台多级降级。

## P3: 通用降级引擎命中后的双重解析

**What:** 引擎命中端点后 `has_playable` 已解析出 `VideoInfo` 但丢弃，adapter 随后又 `_parse_response` 一遍；6 平台各命中一次解析两遍。
**Why:** 纯浪费 + DRY 味道（解析逻辑=校验逻辑跑两遍）；非正确性 bug（解析为纯函数无副作用）。
**Pros:** 消除每次命中的一次冗余 dict-walk + Service 实例化。
**Cons:** 要让 provider 跨 provider/adapter 边界返回已解析的 `VideoInfo`，改动较大、回归面广，与 cobalt provider 的 raw-dict 契约也要对齐。
**Context:** 本次抽引擎决定保留 raw-dict 契约（最小 diff、零回归），把双重解析记此 TODO。若未来要优化，方向是 `_run_chain` 直接返回 `has_playable` 解析出的 `VideoInfo`，adapter 退化为透传。
**Effort:** M (human ~0.5 day / CC ~20 min)
**Depends on:** 通用降级引擎上线后。

## P3: 清理平台 service 的死 get_video_info

**What:** 9 个平台 service 各有一个自带 HTTP 的 `get_video_info`（如 `kuaishou.py:21`），但 tikhub provider/adapter 路径只调 `_parse_response`，疑为 provider 重构前的旧直调路径。
**Why:** 死代码会随 4 个新平台接入继续繁殖；与本次「薄封装 + 解析器」重构同区，留着误导后人。
**Pros:** 减少每个 service 一段无用 HTTP 逻辑，解析职责单一。
**Cons:** 需先确认确无调用方（含测试/脚本/旧入口），误删会断隐藏路径。
**Context:** `grep -rn "async def get_video_info"` 命中 9 处；tikhub 路径只走 `_parse_response`（见 `tikhub_adapter.py:75`）。先查引用再决定整体删除还是保留 base 抽象。
**Effort:** S (human ~1h / CC ~10 min)
**Depends on:** 无（建议随通用引擎收尾一并清）。

## P3: 通用引擎各平台候选端点补充（需 schema 适配）

**What:** 实测时确认存在、但响应 schema 与现有解析器不同、暂未入链的候选端点：
- 快手 `app/fetch_one_video` / `app/fetch_one_video_by_url`（snake_case：`streamManifest`/`main_mv_urls`）
- TikTok `app/v3/fetch_one_video_by_share_url_v2`（`aweme_details` 复数）/ `web/fetch_post_detail`（`itemId`）
- Instagram `v3/get_post_info`（实测 400 flaky）/ `v1/fetch_post_by_id`（需数字 post_id 非 shortcode）
- YouTube `web/get_video_info_v3`（`playerResponse`/`initialData`）/ `web_v2/get_video_info`（snake_case）
**Why:** 当前每平台 2-3 级链已覆盖主路径，但这些端点能进一步加深降级层数，单端点连环下线时更鲁棒。
**Pros:** 降级链更深，单点故障容忍度更高。
**Cons:** 各需对应 service `_parse_response` 自适应新 schema + fixtures + 测试；收益递减（现有链已够用）。
**Context:** 端点路径/参数已在 `docs/generic-fallback-engine.md` §7 记录；实测样本在 `/tmp/hapi-blobs/probe/`（临时，需重新探测）。入链前必须用真实链接实测 schema，别凭名字猜。
**Effort:** M (human ~1 day / CC ~40 min，全部) — 可按平台拆分按需做。
**Depends on:** 无。
