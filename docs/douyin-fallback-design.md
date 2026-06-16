# 抖音无水印解析 · 多级降级机制设计

> 状态：已过 `/plan-eng-review`（架构/代码质量/测试/性能四节）+ codex 外部交叉评审。决策已锁定，待实现。
> 分支：master ｜ 数据源：TikHub API V5.3.2

---

## 1. 背景与目标

当前抖音解析只走单一 TikHub 端点 `/api/v1/douyin/web/fetch_one_video`（`tikhub.py:338`），是单点故障；且抖音在 `VideoResolver` 里没有 Cobalt 降级（`video_resolver.py:81` `"douyin": [tikhub_provider]`）。该端点失效 / 返回受版权限制内容 / 自建短链解析失败时，整条请求直接失败。

目标：在不改变 `VideoResolver` 的 tikhub→cobalt 抽象前提下，为抖音构建**端点级多级降级**，覆盖三类独立失败：同源抖动、跨源失效、版权受限（reason=8），并对私密/删除等终态快速短路，避免无谓重试与超时放大。

---

## 2. TikHub 抖音「无水印地址」渠道全景（调研结论）

| 渠道 | 端点 | 入参 | 成本 | 画质/水印 | 关键特点 |
|------|------|------|------|-----------|----------|
| Web V1（现状） | `/douyin/web/fetch_one_video` | `aweme_id` | 基础 | `play_addr` 无水印 | 官方建议失效转 v2/app |
| Web V2 | `/douyin/web/fetch_one_video_v2` | `aweme_id` | 基础 | 同上 | V1 的**同源**备份 |
| App V3 | `/douyin/app/v3/fetch_one_video` | `aweme_id` | 基础 | 字段更全 | 空返回看 `data.filter_list[0].reason` |
| **App V3-V3** | `/douyin/app/v3/fetch_one_video_v3` | `aweme_id` | 基础 | **无版权限制** | 取 reason=8 受限内容（短剧/影视/文章） |
| 分享链接入口 | `web\|app/.../fetch_one_video_by_share_url` | `share_url` | 基础 | web 版画质略高 | 免自建短链展开+ID 提取 |
| 混合解析 | `/hybrid/video_data` | `url`/分享文本 | 基础 | 抖音+TikTok 通用 | **内部处理短链**；有 `minimal`/`base64_url` |
| 最高画质（未采用） | `web\|app/.../fetch_video_high_quality_play_url` | `aweme_id`/`share_url` | **$0.005/次** | 原始上传画质无压缩 | 支持 `region=CN` 国内 CDN |
| 播放量补数（→TODO） | `/app/v3/fetch_multi_video_statistics` | `aweme_ids` | $0.025/次 | — | 抖音多数接口已不返 play_count |

核心判断：
1. 抖音可降级的本质是「同一 ID 打多个端点」，失败是**端点级独立**的（web 挂 app 可能正常；app 返 reason=8 时 web 反而能出）。
2. `fetch_one_video_v3` 是「解版权限制」专版，是短剧/影视类受限内容的关键兜底。
3. `hybrid/video_data` 内部自带短链展开 + 分享文本解析，是「自建解析入口失败」的兜底。

---

## 3. 已锁定的设计决策

| # | 决策 | 选择 | 来源 |
|---|------|------|------|
| 范围 | 降级链深度 | **三级：web v1 → web v2 → app v3-v3** | 用户 |
| 范围 | 付费高清端点 | **不纳入**，维持 `play_addr` 无水印 | 用户 |
| 范围 | 解析入口兜底 | **加 hybrid 兜底** | 用户 |
| A1 | 多响应结构解析 | 每端点声明自己的解析器 | 评审 |
| A2 | 失败语义 | 解析 `reason`，终态错误立即短路 | 评审 |
| 结构 | 解析器落点 | 扩展 DouyinService + provider 配链 | 评审 |
| 性能 | 超时控制 | 缩短单端超时(~20-25s) + 总预算上限(~50s) | 评审 |
| **E1** | 解析+分类逻辑所在层 | **搬进 provider**：取→分类→解析→失败重试（adapter 对 douyin 走透传） | codex 交叉 |
| **E2** | hybrid 兜底所在层 | **上移到 resolve.py 路由层**（短链展开/ID 提取失败时走 hybrid，缓存改为拿到 aweme_id 后再写/命中） | codex 交叉 |
| 硬化 | codex #3/#4/#5/#9/#10/#12/#13 | **全部纳入**（见 §6） | codex 交叉 |

---

## 4. 目标架构（数据流）

```
POST /api/resolve  (resolve.py)
   │  url
   ▼
┌──────────────── 入口解析（路由层，E2）────────────────┐
│ is_short_url? ─Y─▶ resolve_short_url                  │
│ parse_url ─▶ (platform, aweme_id)                     │
│                                                       │
│ 展开失败 / id 提取失败 (douyin)                        │
│        └────────────▶ hybrid 路径: 直接把原始 url/分享  │
│                       文本交给 TikHubProvider(hybrid)  │
│ 缓存: 仅在已知 (platform, aweme_id) 时查/写；          │
│        hybrid 命中后用返回的 aweme_id 归一化回填缓存    │
└───────────────────────────────────────────────────────┘
   │ (platform=douyin, aweme_id | hybrid-url)
   ▼
VideoResolver.resolve  (provider 链 tikhub→… 不变)
   │
   ▼
TikHubProvider.fetch_video_info(douyin)  ← 取→分类→解析→重试 全在 provider 内 (E1)
   │
   │  端点链 = [ (T1 web v1, parse_web),
   │            (T2 web v2, parse_web),
   │            (T3 app v3-v3, parse_app) ]
   │  （入口为 hybrid-url 时：链首替换为 (hybrid/video_data, parse_hybrid)）
   │
   ├─ for endpoint in chain  (总预算 asyncio.timeout ≈50s 包住整链)
   │   ├─ fetch(endpoint, 单端超时~20-25s, HTTPClient retries 调小)
   │   ├─ classify(whole response):           ← #9 整响应分类器，非单索引
   │   │     ├ TERMINAL (reason∈{5,10}/已删除/404) ─▶ raise DouyinTerminalError  ← #5 独立异常
   │   │     │                                        └▶ 立即短路，不试后续端点
   │   │     ├ RETRYABLE (reason=8 / envelope 异常 / 空) ─▶ 试下一端点
   │   │     └ OK ─▶ parser(response)
   │   └─ parser 失败也算 RETRYABLE ─▶ 试下一端点   ← #10 解析失败也重试
   │
   ├─ 命中 ─▶ 返回 VideoInfo
   └─ 全链失败 ─▶ VideoNotFoundError(含每端点尝试摘要)

   每端点尝试落结构化日志: endpoint/status/tikhub_code/reason/decision/parser/duration  ← #12
```

**异常契约（#5 关键）：**
- 新增 `DouyinTerminalError(VideoNotFoundError)` 表示终态。
- `VideoResolver.resolve` 当前对 `VideoNotFoundError` 会 `continue` 到下个 provider（`video_resolver.py:213`）。抖音默认链只有 tikhub 不受影响，但 env 可覆盖链 → 需让 resolver 对终态异常**不再 fallback**（识别 `DouyinTerminalError` 直接抛出）。

---

## 5. 落点与改动清单（每文件）

| 文件 | 改动 | 关联决策 |
|------|------|----------|
| `app/services/providers/tikhub.py` | douyin 的 `PLATFORM_ENDPOINTS` 单端点 → 端点链（dataclass 列表：endpoint/param/parser/kind）；`fetch_video_info` 对 douyin 走「取→分类→解析→重试」闭环；新增 `_classify_douyin(response)` 整响应分类器；catch `httpx.HTTPStatusError` 取终态体（#4）；单端超时 + 总预算 `asyncio.timeout`（#3） | E1,A1,A2,#3,#4,#9,#10 |
| `app/services/platforms/douyin.py` | 保留 `_parse_response`，**web v1/v2/app v3 三者复用同一个**（实测均为 `data.aweme_detail` 同构，见 §12）；仅新增 `_parse_hybrid_response`（hybrid 的 `data` 即 detail，换根路径）。建议把 `_parse_response` 重构出一个吃 `detail` dict 的内部方法，两个 root 共享 | A1,结构 |
| `app/services/providers/base.py` | 新增 `DouyinTerminalError(VideoNotFoundError)` | #5,A2 |
| `app/services/video_resolver.py` | `resolve` 识别终态异常不再 fallback 到下一 provider；attempts 日志补端点级字段 | #5,#12 |
| `app/services/adapters/tikhub_adapter.py` | douyin 走透传（provider 已产出 VideoInfo），不再强制 `_parse_response` | E1 |
| `app/api/resolve.py` | 短链展开/ID 提取失败（douyin）不直接 400 → 走 hybrid 解析路径；缓存改为「拿到 aweme_id 后再写/命中」 | E2,#6,#7,#8 |
| `app/utils/http_client.py` | 暴露可调 `max_retries`（供链式调用调小，避免重试×端点放大）| #3 |
| 测试 fixtures | 新增脱敏真实样本：T1/T2/T3/版权/私密/删除/hybrid | #13 |

约 7 个文件，0~1 个新数据类 + 1 个新异常类，未触发复杂度硬上限。

---

## 6. 测试计划（complete-by-default，全部 mock `HTTPClient` + 真实样本 fixture）

`tests/fixtures/douyin/`（新建，**脱敏真实响应**，#13）：`web_v1.json`、`web_v2.json`、`app_v3.json`、`copyright_reason8.json`、`private_reason5.json`、`deleted.json`、`hybrid.json`。

`tests/test_tikhub_provider_douyin.py`（新建）：
- T1 命中即返回，断言 T2/T3 **未被调用**
- T1 空 → T2 命中
- T1/T2 空(reason=8) → T3(v3) 命中
- **reason=5/10 / 已删除 → `DouyinTerminalError`，断言 T2/T3 未被调用**（回归铁律：终态短路核心保证）
- 解析失败（envelope 合法但 `play_addr` 缺失）→ 视为可重试，试下一端点（#10）
- 全链空 → `VideoNotFoundError`，错误含各端点尝试摘要
- 总预算超时 → 在 ~50s 处中止，不到 180s（#3）
- `httpx.HTTPStatusError` 携带终态体 → 正确分类（#4）

`tests/test_douyin_parser.py`（新建）：
- `_parse_response` / `_parse_app_response` / `_parse_hybrid_response` 各吃对应 fixture → 同一 `VideoInfo` 形状，断言 `video_url` 非空、宽高/统计映射正确

`tests/test_url_parser.py` / `test_resolve_api.py`（扩展/新建）：
- douyin 短链展开失败 / id 提取失败 → 触发 hybrid 路径（[→整合]：路由层到 provider 的一条整合测试）
- 终态视频 → API 返回清晰错误而非空白失败

唯一整合测试：「短剧降级到 T3」与「入口失败走 hybrid」跨层链路。无 LLM/E2E 需求。

---

## 7. 失败模式（生产视角）

| 新代码路径 | 现实失败 | 有测试? | 有错误处理? | 用户可见? |
|------------|----------|---------|-------------|-----------|
| 端点链串行 | 多端点连环超时 | ✅(总预算测试) | ✅ asyncio.timeout 总预算 | ✅ 明确错误 |
| 终态短路 | 私密/删除被当可重试 | ✅(回归铁律) | ✅ DouyinTerminalError | ✅ 明确错误 |
| hybrid 入口兜底 | hybrid envelope 变更 | ✅(parser 测试) | ✅ 解析失败转可重试 | ✅ |
| reason 分类器 | reason 不在 index0/多 filter | ✅(#9 整响应分类) | ✅ 整响应扫描 | ✅ |
| HTTPStatusError | 终态体藏在 4xx 抛错里 | ✅(#4 测试) | ✅ catch 后分类 | ✅ |

无「无测试 + 无错误处理 + 静默失败」的关键缺口。

---

## 8. 并行实施策略

| 步骤 | 触及模块 | 依赖 |
|------|----------|------|
| S1 异常类 + 分类器 | providers/, platforms/ | — |
| S2 端点链 + provider 闭环 | providers/, platforms/ | S1 |
| S3 resolver 终态不 fallback | services/(video_resolver) | S1 |
| S4 路由层 hybrid + 缓存 | api/, services/(url_parser) | S2 |
| S5 fixtures + 测试 | tests/ | S2,S3,S4 |

- Lane A: S1 → S2 → S4（顺序，共享 providers/）
- Lane B: S3（依赖 S1，可在 S2 进行时并行起步）
- 执行：S1 先行；随后 A 主干推进，B 并行；最后 S5 收口。S2 与 S3 都引用新异常类（S1 产物），合并前对齐。

---

## 9. NOT in scope（明确不做）

- 付费「最高画质」端点 `fetch_video_high_quality_play_url`（$0.005/次）—— 维持免费 `play_addr` 无水印（用户决策）。
- play_count 播放量补数 —— 付费且与无水印主线无关，转 TODOS.md（P3）。
- 端点降级链泛化到 TikTok —— 过早抽象，待抖音链跑稳后再做，转 TODOS.md（P3）。
- 抖音 Cobalt 降级 —— Cobalt 不支持抖音，无意义。
- 批量端点（`fetch_multi_video*`）—— 单视频解析 API 用不上。

## 10. What already exists（复用而非重建）

- `VideoResolver` 责任链（`video_resolver.py`）—— 复用，provider→cobalt 抽象不动，降级仅在 TikHubProvider 内部加端点链。
- `DouyinService._parse_response`（`douyin.py:50`）—— 复用为 web 解析器，新增 app/hybrid 解析方法与之并列。
- `_validate_response`（`tikhub.py:180`）—— 演进为整响应分类器（#9），不另起炉灶。
- `url_parser.resolve_short_url` / `parse_url`（`url_parser.py`）—— 复用为主路径，仅在其失败时降级到 hybrid。
- `_save_failed_response`（`tikhub.py:228`）—— 复用为 fixture 采集来源（脱敏后入 #13）。
- `UsageLog` 落库（`resolve.py:206`）—— 复用，端点级字段补进 attempts 日志。

---

## 11. Implementation Tasks

> 由本次评审 + codex 外部意见综合，每项对应具体发现。

- [ ] **T1 (P1, human ~1h / CC ~10min)** — providers/base — 新增 `DouyinTerminalError(VideoNotFoundError)`
  - Surfaced by: codex #5 — 终态在 resolver 层会被 continue 到下个 provider
  - Files: `app/services/providers/base.py`
  - Verify: import + isinstance 测试
- [ ] **T2 (P1, human ~2h / CC ~20min)** — providers/tikhub — douyin 整响应分类器 `_classify_douyin`（terminal/retryable/ok）
  - Surfaced by: A2 + codex #9/#10 — 区分终态/可重试，整响应而非单索引，解析失败也可重试
  - Files: `app/services/providers/tikhub.py`
  - Verify: `test_tikhub_provider_douyin.py` 分类用例
- [ ] **T3 (P1, human ~3h / CC ~25min)** — providers/tikhub + platforms/douyin — douyin 端点链 + provider 内「取→分类→解析→重试」闭环；web v1/v2/app v3 复用 `_parse_response`，仅新增 `_parse_hybrid_response`（实测 app v3 同构、hybrid 换根，见 §12）
  - Surfaced by: 范围(三级链) + A1 + E1 — 每端点独立解析器、解析搬进 provider
  - Files: `app/services/providers/tikhub.py`, `app/services/platforms/douyin.py`, `app/services/adapters/tikhub_adapter.py`
  - Verify: 链顺序/命中/全失败用例
- [ ] **T4 (P1, human ~1h / CC ~10min)** — utils/http_client + tikhub — 单端超时 ~20-25s + `asyncio.timeout` 总预算 ~50s + 调小 retries
  - Surfaced by: 性能问题4 + codex #3 — max_retries=3 放大超时到 ~180s+
  - Files: `app/utils/http_client.py`, `app/services/providers/tikhub.py`
  - Verify: 总预算超时用例
- [ ] **T5 (P1, human ~1h / CC ~10min)** — providers/tikhub — catch `httpx.HTTPStatusError` 取终态响应体
  - Surfaced by: codex #4 — raise_for_status 让 404/429 分支成死代码
  - Files: `app/services/providers/tikhub.py`, `app/utils/http_client.py`
  - Verify: HTTPStatusError 携终态体分类用例
- [ ] **T6 (P1, human ~1h / CC ~10min)** — services/video_resolver — 终态异常不再 fallback 到下个 provider + attempts 端点级日志
  - Surfaced by: codex #5/#12
  - Files: `app/services/video_resolver.py`
  - Verify: env 覆盖链时终态不 fallback 用例
- [ ] **T7 (P1, human ~2.5h / CC ~20min)** — api/resolve + url_parser — 短链展开/ID 提取失败走 hybrid 路径 + 缓存语义（拿到 aweme_id 后写/命中）
  - Surfaced by: 范围(hybrid 兜底) + E2 + codex #6/#7/#8
  - Files: `app/api/resolve.py`, `app/services/url_parser.py`
  - Verify: 入口失败→hybrid 整合测试 + 缓存归一化用例
- [ ] **T8 (P1, human ~2h / CC ~20min)** — tests — fixtures(脱敏真实样本) + 全部单测/整合测试
  - Surfaced by: 测试评审 + codex #13
  - Files: `tests/fixtures/douyin/*.json`, `tests/test_tikhub_provider_douyin.py`, `tests/test_douyin_parser.py`, `tests/test_url_parser.py`, `tests/test_resolve_api.py`
  - Verify: `pytest tests/ -q`

---

## 12. 实测发现（fixtures 采集，2026-06-16）

采集脚本 `scripts/collect_douyin_fixtures.py`，样本链接 `https://v.douyin.com/-Q8et5ToUhs/`
（aweme_id `7592102779420115355`，正常公开视频）。四个端点均 HTTP 200 拿到无水印直链。

| 端点 | `data` 结构 | detail 根路径 | bit_rate 档数 | 现有 `_parse_response` 可用? |
|------|------------|--------------|--------------|------------------------------|
| web_v1 | `{aweme_detail, log_pb, status_code}` | `data.aweme_detail` | 6 | ✅ |
| web_v2 | `{aweme_detail, log_pb, status_code}` | `data.aweme_detail` | 6 | ✅ |
| app_v3 | `{status_code, aweme_detail, extra, log_pb}` | `data.aweme_detail` | 2 | ✅ |
| hybrid | detail 字段直接铺在 `data` 上 | **`data` 本身** | 2 | ❌ 需换根 |

**对设计的影响：**
1. **app v3 与 web 同构** → 砍掉原计划的 `_parse_app_response`，三端复用 `_parse_response`（设计 §5/T3 已修正）。证实 codex #1 的契约问题只剩 hybrid 一种异形，复杂度更低。
2. **hybrid 确需独立解析**（`data` 即 detail）→ 证实 codex #2。落地方式：把 `_parse_response` 重构出吃 `detail` dict 的内部方法，web/app 传 `data["aweme_detail"]`、hybrid 传 `data`，复用同一套字段映射（DRY）。
3. **play_count 实测为 `0`**（非真实播放量）→ 证实 TODO「play_count 数据缺口」，确认不可靠。
4. **bit_rate 档数 web(6) > app/hybrid(2)** → `bit_rate[0]` 取首档在不同端点画质档位不同；如需稳定画质策略可在 parser 内按 `gear_name`/分辨率挑选（非本次范围，可留意）。
5. fixtures 已确认**不回显 API key**（`params` 仅含 aweme_id/url），可安全提交。

**终态样本仍缺**：`copyright_reason8` / `private_reason5` / `deleted` 需用对应受限/失效链接重跑脚本
（`--prefix` 区分）。正常样本（web_v1/web_v2/app_v3/hybrid）已就位于 `tests/fixtures/douyin/`。
