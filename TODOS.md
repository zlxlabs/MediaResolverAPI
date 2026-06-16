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
