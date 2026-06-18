# 通用多级降级引擎（Generic Fallback Engine）

> 状态：已实现上线。六大平台（抖音 / 小红书 / 快手 / TikTok / Instagram / YouTube）
> 的 TikHub 多级端点降级统一到一个配置驱动的引擎，每个平台只配置差异部分。

## 1. 背景

TikHub 会不定期下线端点（已踩：Instagram 旧端点 404、小红书 `web/get_note_info_v3`
下线）。单端点 = 单点故障。解决范式是 provider 内「多级端点降级链」：同一个 ID 串行
打多个端点，命中即停，终态短路。

抖音、小红书早期各实现一份近乎重复的链逻辑（取数 → 三态分类 → 解析校验 → 降级 →
超时预算），只有「端点链 + 分类器」不同。再给 4 个平台各抄一遍 = 6 份重复。故抽出
通用引擎：骨架唯一实现，平台只配置可变点。

## 2. 引擎骨架

代码：`app/services/providers/tikhub.py` 的 `_run_chain` + `_call_endpoint`。

```
取 token/参数 → build_chain（按条件裁剪，如缺 token 跳过需 token 的端点）
                       │
                       ▼
┌──────────── _run_chain（asyncio.timeout 总预算兜底）─────────────┐
│  for endpoint in chain:                                          │
│     params = build_params(endpoint)        # 平台差异：入参构造    │
│     data   = _call_endpoint(name, path, params, per_timeout)     │
│              · 单端 max_retries=0（重试交给链，防超时放大）         │
│              · 4xx 取 body 交分类器（终态信息常藏 body）            │
│     decision = classify(data)              # 平台差异：三态分类器  │
│        terminal  → raise terminal_exc      # 立即短路（仅单源平台） │
│        retryable → 记录 attempts, 下一端点                         │
│        ok        → has_playable(data)?     # 平台差异：解析校验    │
│                       True  → return data  # 命中即停             │
│                       False → 记 parse_failed, 下一端点           │
└──────────────────────────────────────────────────────────────────┘
   超时 → ProviderError("timed out")
   全链未命中 → VideoNotFoundError
```

可变点（回调 / 配置）：

| 可变点 | 类型 | 说明 |
|--------|------|------|
| `chain` | `list[(name, path, spec)]` | 已裁剪的端点表，串行尝试顺序 |
| `build_params` | `endpoint -> dict` | 端点 → TikHub query 参数（id / url / token 差异） |
| `classify` | `data -> "terminal"\|"retryable"\|"ok"` | 三态分类器 |
| `has_playable` | `data -> bool` | 用各平台 service 的 `_parse_response` 校验能否出直链 |
| `terminal_exc` | `type[TerminalError]` | 该平台终态异常类（有 cobalt 兜底的平台传基类占位，不会被 raise） |
| `total_budget` / `per_timeout` | `float` | 整链总预算 / 单端超时（秒） |

## 3. 两条关键设计约束

1. **终态短路只用于 TikHub 单源平台**（抖音 / 小红书 / 快手）。`VideoResolver` 遇任何
   `TerminalError` 会停掉**所有** provider 降级（不再试 Cobalt）。有 Cobalt 兜底的平台
   （TikTok / Instagram / YouTube）**链内一律不判终态**——误判终态会让 Cobalt 永远跑不到。
   分类器对这些平台把终态信号降级为 `retryable`，链走完后落 Cobalt。

2. **单端 `max_retries=0` + 整链 `asyncio.timeout(total_budget)`**：防止 HTTPClient
   默认重试把单次耗时放大。长链（端点多）相应调低单端超时、提高总预算，保证链能走完
   （`total_budget > 端点数 × per_timeout` 才能让每端都有机会跑）。

## 4. 各平台链配置

| 平台 | 端点链（命中即停） | 入参 | 终态 | 兜底 |
|------|-------------------|------|------|------|
| 抖音 | `web/fetch_one_video → web/fetch_one_video_v2 → app/v3/fetch_one_video_v3`（+ `hybrid/video_data` 兜底入口） | `aweme_id`（hybrid 吃 `url`） | ✅ 私密/部分可见(reason 5/10) | 无（TikHub 单源） |
| 小红书 | `app_v2/get_video_note_detail → web_v3/fetch_note_detail` | `note_id`（web_v3 +`xsec_token`，缺则跳过） | ✅ 图文/删除/私密 | 无（TikHub 单源） |
| 快手 | `web/fetch_one_video_v2 → web/fetch_one_video` | `photo_id` / `share_text=url` | ❌（无真实终态样本，不臆造） | 无（TikHub 单源，P1 重点） |
| TikTok | `app/v3/fetch_one_video → _v2 → _v3` | `aweme_id` | ❌（有 Cobalt 兜底） | Cobalt |
| Instagram | `v2/fetch_post_info → v1/fetch_post_by_url` | `code_or_url` / `post_url`（均喂原始 url） | ❌（非视频/轮播≠不可用） | Cobalt |
| YouTube | `web/get_video_info → web/get_video_info_v2` | `video_id` | ❌（有 Cobalt 兜底） | Cobalt |

### 超时参数（秒）

| 平台 | 单端 | 总预算 | 说明 |
|------|------|--------|------|
| 抖音 / 小红书 | 25 | 50 | 短链（2-3 端点） |
| 快手 / Instagram | 25 | 55 | 2 端点 |
| TikTok | 18 | 60 | 3 端点（3×18<60，保证走完） |
| YouTube | 25 | 55 | 2 端点 |

### 解析器自适应（schema differences）

同一平台不同端点常返回不同 schema，`has_playable` 与 adapter 用**同一个**平台 service 的
`_parse_response`（保证「has_playable 过 ⟹ adapt 成功」），故 service 需自适应：

- **快手** `KuaishouService._extract_photo`：兼容 `data.photo`（dict）与 `data[0]`（list）。
- **TikTok**：`has_playable` 用 `TikTokService`（非 DouyinService），保 `play_addr_h264` 优先；
  分类器与抖音共用 `_classify_aweme(allow_terminal=...)`（envelope 同源）。
- **Instagram** `InstagramService._parse_response`：自动识别 v1（`data.*`）/ v2（`data.data.*`）。
- **YouTube** `YouTubeService._adaptive_video_streams`：兼容 `data.videos.items`（预解析直链）与
  `data.streamingData.formats`（muxed 合流，跳过 signatureCipher 无直链的格式）+ `videoDetails` 基础信息。

## 5. 路由层 by_url 兜底（Issue 5）

`app/api/resolve.py`：平台已识别但 `video_id` 提取失败（如新链接格式）时，对降级链含
吃 url 端点的平台（`URL_FALLBACK_PLATFORMS = {kuaishou, instagram}`）不再 400，而是放行，
让链的 by_url 端点用原始 url 兜底（kuaishou `web/fetch_one_video?share_text=` /
instagram `v2 code_or_url` + `v1 post_url`）。空 `video_id` 跳过缓存查找，解析后用
`video_info.video_id` 回填（与抖音 hybrid 路径一致）。tiktok/youtube（链只吃 id）维持 400。

## 6. 测试范式

每平台一个 `tests/test_tikhub_provider_<plat>.py`，桩替换 `_call_endpoint`，覆盖：
分类三态 / 链命中即停 / 逐级降级 / 终态短路（或不短路，cobalt 平台）/ 全失败 /
单端 HTTPStatusError 取体 / 整链总预算超时 / **逐端点 param 构造硬断言**（防映错参数名
导致的静默 422）。fixtures 按端点/状态分文件，最小但忠实于实测 schema。

## 7. 未竟项（见 TODOS.md）

- 快手 `app/*` 端点（snake_case schema：`streamManifest`/`main_mv_urls`）接入。
- TikTok `app/v3/fetch_one_video_by_share_url_v2`（`aweme_details` 复数）/ `web/fetch_post_detail`（`itemId`）。
- Instagram `v3/get_post_info`（实测 400 flaky）/ `v1/fetch_post_by_id`（需数字 post_id）。
- YouTube `web/get_video_info_v3`（playerResponse）/ `web_v2/get_video_info`（snake_case）。
- 命中后的双重解析（has_playable 解析一遍、adapter 再解析一遍）优化。
- 清理平台 service 的死 `get_video_info`（provider 重构前的旧直调路径）。
