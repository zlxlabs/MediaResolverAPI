# 架构一页纸（维护者向）

> 这页给改代码的人看。对接合同（现在能调什么、返回什么、环境变量）在 [README](../README.md)。
> 本页只讲「一次解析请求在仓内怎么走」，看完即可定位该改哪个文件。
> `docs/sessions/*/design.md` 是已落地决策的存档（历史 ADR），不是活文档，别往里堆新内容。

## 1. 两层路由，不可混

| 层 | 代码位置 | 换的是什么 | 排链者 |
|----|---------|-----------|--------|
| 供应商层 | `app/services/video_resolver.py` → `VideoResolver.provider_chains` | 换**供应商**（TikHub / Cobalt / Just One API / yt-dlp） | 每个平台一条有序链 |
| 端点层 | `app/services/providers/tikhub.py` → `TikHubProvider._run_chain` | 换**同一供应商的端点** | 每个平台一条有序端点链 |

两条链都是「按序尝试、命中即停」，但换的东西不同：

- 端点层只存在于 TikHub provider 内部（Cobalt / Just One API 没有端点链），它解决「TikHub 把某个端点下线了」。
- 供应商层解决「整家店挂了 / 这家店没这个平台」。
- **Just One API 是供应商层的备源，不进 TikHub 的 `_run_chain`**：那是换店，不是换端点。
- 两层的失败语义不同，端点层的终态见 §4，供应商层的终态停整链也见 §4。

## 2. 默认供应商链

链在 `VideoResolver._build_provider_chains()` 一次性构建；`PROVIDER_PRIORITY_<PLATFORM>`（逗号分隔）可整条覆盖，覆盖后不再追加默认源。

| 平台 | 默认链（`JUSTONEAPI_TOKEN` 非空时） | token 留空时 |
|------|-------------------------------|-----------|
| 抖音 douyin | tikhub → justoneapi | tikhub |
| 快手 kuaishou | tikhub → justoneapi | tikhub |
| 小红书 xiaohongshu | tikhub → justoneapi | tikhub |
| 微信视频号 wechat_channels | tikhub → justoneapi | tikhub |
| TikTok tiktok | tikhub → cobalt → justoneapi | tikhub → cobalt |
| Instagram instagram | tikhub → cobalt → justoneapi | tikhub → cobalt |
| YouTube youtube | tikhub → cobalt → justoneapi | tikhub → cobalt |
| X (Twitter) twitter | tikhub → cobalt → justoneapi | tikhub → cobalt |
| Pinterest pinterest | cobalt | cobalt |
| Facebook facebook | cobalt | cobalt |
| X Space twitter_space | ytdlp | ytdlp |

**空 token = 整源不存在**：`JUSTONEAPI_TOKEN` 为空时 `justoneapi` 根本不进 `provider_map`，链上不出现该源、也发不出任何请求（provider 自身同样拒绝无 token 调用）。这是「不花钱、不打上游」的保证，不是可临时拨动的降级开关。

**Just One API 每平台一次成功只打一枪**（按次计费）：`JustOneApiProvider.PLATFORM_ENDPOINTS` 一平台一端点，禁止为同一业务结果叠打；视频号的例外见 §6。

`download_mode=audio` 不走上面的链：Twitter / TikTok / Instagram / Pinterest / Facebook 只走 Cobalt；抖音 / 快手 / 小红书 / 视频号没有音频路径，直接 400（detail 以 `audio_not_available` 开头）。

## 3. TikHub 端点链（端点层）

`_run_chain` 是唯一实现（DRY）：构造参数 → 串行打端点 → 三态分类 → 解析校验 → 命中即停。整链由 `asyncio.timeout(total_budget)` 兜底，单端 `max_retries=0`（重试交给链本身，避免超时被 HTTPClient 的默认重试放大）。

分类器三态：`terminal`（内容终态，立即短路）/ `retryable`（换下一个端点）/ `ok`（进解析校验；`ok` 但解析不出可播放直链也算可重试）。

| 平台 | 端点链（命中即停） | 入参 | 端点层内判终态 | 端点层之外还有谁兜底 |
|------|-------------------|------|---------------|-------------------|
| 抖音 | `douyin/web/fetch_one_video → web/fetch_one_video_v2 → app/v3/fetch_one_video_v3`（hybrid 兜底见 §5） | `aweme_id` | ✅ 私密 / 部分可见（reason 5/10） | Just One API |
| 小红书 | `xiaohongshu/app_v2/get_video_note_detail → web_v3/fetch_note_detail` | `note_id`；`web_v3` 另需 `xsec_token`，缺则该端点被裁掉 | ✅ 图文 / 删除 / 私密 | Just One API |
| 快手 | `kuaishou/web/fetch_one_video_v2 → web/fetch_one_video` | `photo_id` / `share_text=原始 url` | ❌ 无真实终态样本，不臆造 | Just One API |
| TikTok | `tiktok/app/v3/fetch_one_video → _v2 → _v3` | `aweme_id` | ❌ 有兜底，不判终态 | Cobalt → Just One API |
| Instagram | `instagram/v2/fetch_post_info → v1/fetch_post_by_url` | 两端都喂原始 url | ❌ 轮播 / 非视频 ≠ 不可用 | Cobalt → Just One API |
| YouTube | `youtube/web/get_video_info → web/get_video_info_v2` | `video_id` | ❌ 有兜底，不判终态 | Cobalt → Just One API |
| 微信视频号 | `wechat_channels/v2/fetch_video_detail`（单端点 POST） | `object_id`，空则 `share_url` | ❌ 链内不判终态 | Just One API |
| X (Twitter) | `twitter/web/fetch_tweet_detail`（单端点 GET） | `tweet_id` | ❌ 有兜底，不判终态 | Cobalt → Just One API |

超时预算（单端 / 整链，秒）：抖音 25/50、小红书 25/50、快手 25/55、TikTok 18/60、Instagram 25/55、YouTube 25/55、视频号 25/30、X 25/30。视频号单端点可重试 3 次、退避 0.3s；其余平台单端只试一次。

各平台 service 的 `_parse_response` 是 schema 自适应解析器，端点层的 `has_playable` 与 adapter 共用它（保证「校验通过 ⟹ 适配成功」）。各家响应的具体字段落点写在 `app/services/platforms/<plat>.py` 的注释里，本页不复制。

## 4. 终态：`TerminalError` 停掉整条供应商链

`VideoResolver.resolve` 捕获 `TerminalError` 后**立刻停掉整条供应商链**并抛错，不再试后面的供应商——包括 Just One API。TikHub 侧的终态来源：抖音私密 / 部分可见（reason 5/10）、小红书图文 / 删除 / 私密。

两条推论（改代码时别踩）：

1. **有兜底的平台在 TikHub 链内不判终态**：TikTok / Instagram / YouTube / X 的分类器把终态信号降级为可重试，否则终态一旦误判，Cobalt 与 Just One API 永远轮不到。快手没有已确认的终态样本，同样不判。
2. **Just One API 不得抛 `TerminalError`**：有 Cobalt 的平台尤其不能，否则会跳过 Cobalt。

## 5. 路由层：parse_url 无损与两条兜底入口

`app/api/resolve.py` 先判短链并展开，再 `URLParser.parse_url` 取 `(platform, video_id)`。

- **无损**：`parse_url` 已识别平台但提不出 ID 时，仍返回平台名（`identify_platform` 与「提 ID」分离），路由层据此决定走哪条兜底，而不是一律 400。
- **`URL_FALLBACK_PLATFORMS = {kuaishou, instagram, wechat_channels}`**：这三家的端点链里有吃 url 的端点，ID 提不出时放行原始 url，让链里的 by_url 端点接手；链只吃 ID 的平台仍 400。
- **抖音 hybrid**：短链展开失败或 ID 提取失败时，改走 TikHub 的 `hybrid/video_data`（它自己吃分享文本、内部再展开一次），不走 400。
- 兜底路径只在「ID 未知」时生效：此时不查缓存，解析成功后用拿到的 ID 回填缓存键。

## 6. 视频号：门口始终是本服务

视频号的 `data.video_url` 恒为本服务 `/api/stream/wechat_channels/{sph_code}`（必须带 `X-API-Key`），上游直链一律不回传客户端。差别在「上游给的是密文还是明文」：

| 上游 | 媒体形态 | 流式层 | `/direct` 的 `cdn_url` |
|------|---------|--------|----------------------|
| TikHub | 加密 mp4（前 131072 字节加密） | 按窗口拉密文、XOR 解密后转发 | TikHub CDN 直链（带时效 token） |
| Just One API | 明文 mp4 | 零 XOR，直接转发 | 本服务 `/api/stream/wechat_channels/{sph_code}` |

- **明文 / 密文由显式标记决定**：`app/services/wechat_channels_media.py` 取回的 media 带 `decrypt` 标记（TikHub 命中为 `True`，Just One API 命中为 `False`），流式层与 `/direct` 都据此决定要不要 XOR。误走 XOR 会损坏片头。
- **上游选择**：TikHub 先取媒体，失败且 `JUSTONEAPI_TOKEN` 非空时才降级 Just One API。
- **objectId 复用**：视频号 resolve 层只打 Just One API 的 basic-info 一枪拿 objectId 并落进 `VideoCache`；流层按 sph 短码反查该缓存行取回 objectId，只补 download 一枪，不为同一次成功重复付 basic-info 的钱。
- **`/direct` 的 `cdn_url` 是分支值**：TikHub 命中回 CDN 直链（客户端自己拼身子，绕开「下游到本服务」这一跳）；Just One API 命中回本服务流地址（对方直链是第三方 host，时效与可达性未证）。两种情况下 `head_b64` 都是**明文**文件头。

## 7. Just One API 的位置与约束

- 走供应商层（§2），不进 TikHub 端点层（§3）。
- token 只作 query 参数；日志与异常文本禁止出现带 token 的完整 URL（httpx 异常原文会内嵌 URL，故只记异常类型名与 path）。
- 业务 `code==0` 才扣费；`100/301/302/303/400/500/600/601/602` 不扣费。provider 单次调用不叠打（一次即止），不替上游做重试。
- HTTP 走裸异步 httpx 而不是仓内 `HTTPClient`：`HTTPClient` 默认对 HTTP 5xx 叠打（会把不计费业务码打成多次请求），且其错误日志会内嵌带 token 的完整 URL。
- 响应信封由 `JustOneApiAdapter` 对齐到现有平台 service 的解析器（例：快手只在适配器里把 `photos[0].streamManifest` 改写成 `manifest`，TikHub 侧的快手链不动）。
