# DESIGN-note：Just One API 作为按次计费的下载解析备源

## 目标

TikHub 拿不到可播放直链时，改问 Just One API，客户端仍然从 `POST /api/resolve` 拿到能下的地址；视频号客户端地址不变，还是本服务的流式端点。

## 非目标

- 不替换 TikHub 做主源，不宣称比 TikHub 更便宜（TikHub 单价本轮未核到一手数字）。
- 不把 Just One API 塞进 TikHub 内部多级端点引擎。
- 不接 Facebook / Pinterest / X Space（目录无对应下载接口）。
- 不新开 B 站、微博、公众号。
- 不把 314 个目录接口搬进来；短链展开、标题、指标、评论一律不打。
- 本轮不接 `download_mode=audio` 的 Just One API 路径（YouTube 虽有 `audios.items`，不纳入验收）。
- 不把官方同步 SDK 放进异步热路径。
- 不把探测用的临时 token 写入仓库或生产 `.env`。
- 定价 xlsx 不入库。

## 为什么不是分区 / 删除 / 约定

- **分区**：不能靠「每个平台只认一家店」消掉单源故障。抖音 / 快手 / 小红书 / 视频号今天只有 TikHub；TikTok / Instagram / YouTube / Twitter 在 Cobalt 也失败后仍然没第三源。换供应商必须走现有 `VideoResolver` 责任链，不能靠再开一个服务进程把问题隔开。
- **删除**：探测已证明详情/下载接口能出可播放地址，砍掉备源等于接受这八个平台在上游挂掉时解析失败。视频号解密中转不能删——客户端契约是本服务 URL；Just One API 只替换上游取数。
- **约定**：不能靠「人肉换 key / 文档写备用供应商」在请求路径上自动切源。计费、超时、禁叠打必须写在路由里，约定挡不住一次成功 ¥0.30–0.60 的视频号误打。

## 方案要点与已否决方案

- **要点**：
  1. 新增 `JustOneApiProvider`（`provider_name=justoneapi`），实现现有 `BaseProvider`。HTTP 走仓内异步 `HTTPClient`，token 仅作 query `token`，日志禁止打印带 token 的完整 URL。
  2. 默认责任链（可被已有 `PROVIDER_PRIORITY_<PLATFORM>` 覆盖）：
     - 抖音 / 快手 / 小红书 / 视频号：`tikhub → justoneapi`
     - TikTok / Instagram / YouTube / Twitter：`tikhub → cobalt → justoneapi`
     - 其余平台不变。
  3. 未配置 `JUSTONEAPI_TOKEN`（空）时该 provider 直接跳过，行为与今天一致。
  4. 每个平台一次成功请求必须能出直链，禁止为同一业务结果叠打：
     | 平台 | 只打这一枪 | 单价（2026-11-03 定价表） |
     |---|---|---|
     | 抖音 | `GET /api/douyin/get-video-detail/v2?videoId=` | ¥0.10 |
     | 快手 | `GET /api/kuaishou/get-video-detail/v2?videoId=` | ¥0.10 |
     | 小红书 | `GET /api/xiaohongshu/get-note-detail/v6?noteId=`（已确认视频 + 有 noteId） | ¥0.15 |
     | 视频号 | 已有 `objectId`：`GET /api/weixin-channels/get-video-download-url/v1`；只有分享链接、没有 ID：先 `get-video-basic-info/v1` 再 download（¥0.30+¥0.30） | ¥0.30 或 ¥0.60 |
     | TikTok | `GET /api/tiktok/get-post-detail/v1?postId=` | ¥0.10 |
     | YouTube | `GET /api/youtube/get-video-detail/v1?videoId=` | ¥0.10 |
     | Instagram | `GET /api/instagram/get-post-detail/v1?code=` | ¥0.10 |
     | Twitter | `GET /api/twitter/get-post-detail/v1?tweetId=` | ¥0.10 |
  5. 计费语义跟官方文档：业务 `code==0` 才扣费；`301` 可重试且不扣费；`100/302/303/400/500/600/601/602` 不扣费、不重试成叠打。本 provider 单次超时独立预算（建议 20–30s），不把官方建议的 60–120s 叠在 TikHub 已耗的 25–55s 后面。
  6. 解析复用现有平台 service，适配器只做信封对齐：
     - 抖音：`data.aweme_detail.video.play_addr / play_addr_h264 / bit_rate / download_addr` 与现网 TikHub aweme 同形，走 `DouyinService`。
     - 小红书 v6：`video_info_v2.media.stream.h264[].master_url` 与现网 app_v2 同形，走 `XiaohongshuService`。
     - 快手：JOA 返回 snake_case `photos[].streamManifest` + `main_mv_urls`；现网解析器只认 camelCase `data.photo.manifest`。**只在 JustOneAPI 适配器里把 `photos[0].streamManifest` 改写成 `manifest`**，不顺手改 TikHub 快手 app 链。
     - YouTube：`videos.items[].url`（googlevideo）；Instagram：`video_url` / `video_versions`；Twitter：`media.video[].variants` mp4。各走对应 service 或最小适配。
  7. 视频号客户端契约不变：`data.video_url` 仍是本服务 `/api/stream/wechat_channels/{sph_code}`，必须带 `X-API-Key`。Just One API 的 `download_url` 实测是已解密 mp4（`ftypisom`，`Content-Type: video/mp4`，Range 206），宿主为他们的 `IP:port/media/weixin-channels`，**不回传给客户端**。流式层在 justoneapi 命中时改为拉这条明文 URL，跳过 XOR。TikHub 命中时仍走 `full_url` + `decode_key`。
  8. `data.provider` 增加 `justoneapi`。缓存命中继续跳过上游，避免同一条视频付两次。
  9. 终态：Just One API 对「有 Cobalt 的平台」不得抛 `TerminalError`（与现网「误判终态会跳过 Cobalt」同一条）。抖音 / 小红书若能稳定识别私密/图文，才允许终态短路；识别规则未探测前当 retryable，链走完再失败。

- **已否决**：
  - 全平台无差别塞进链：Cobalt 边际成本为 0，成功一次却要按次付费；用户已拍板 Cobalt 平台作为**第三源**，不是第二源。
  - 视频号改走 Just One API 为主：每次成功 ¥0.30–0.60，无证据更稳。
  - 把 Just One API 嵌进 TikHub `_run_chain`：那是同一供应商换端点，换店应走 `VideoResolver`。
  - 热路径引入官方同步 SDK（`httpx.Client`，默认 timeout 60）：与现有异步 provider 异构，且会把 token 打进 query 的同一套日志风险。
  - 探测未做就按文档实现：OpenAPI `data` 是空对象；已用真实调用打掉这个未知。
  - 客户端直接使用视频号 `download_url`：宿主是第三方 IP:port，时效与可达性未证明，且破坏「视频号 video_url 指向本服务」的现网契约。
  - 已有平台 ID 仍打 `share-url-transfer`：短链展开本仓已做，再打是重复付费。

## 关键不变式

1. [实测] Just One API 详情/下载接口在 `code==0` 时能给出可播放媒体。2026-11-03 对八个平台各至少成功一次：抖音 `play_addr.url_list`；快手 `streamManifest`/`main_mv_urls` mp4；小红书 v6 `master_url`（host `*.rednotecdn.com`，path 以 `.mp4` 结尾）；TikTok `PlayAddrStruct.UrlList` / `bitrateInfo[].PlayAddr`；YouTube `videos.items[].url`（googlevideo）；Instagram `video_url` mp4；Twitter `variants` mp4；视频号 `download_url` Range 206 + `ftypisom`。锁：provider 的 `has_playable` 与适配器解析必须读上述字段；测试用探测裁剪后的 fixture（禁止入库完整带签 URL）。
2. [实测] 视频号 Just One API 下载是明文 mp4，不是 TikHub 那套加密头。锁：stream 层按「这次 resolve 的上游」分支：justoneapi 跳过 XOR，tikhub 仍 XOR。测试：明文 fixture 不解密仍能拼出 `ftyp`；加密 fixture 不解密则不是 `ftyp`。
3. [约定→落地后由测试锁] 同一业务结果只打表内那一枪。锁：provider 单测断言每平台 `_call_endpoint` 路径集合；有 ID 时路径不含 `share-url-transfer`；视频号有 objectId 时不含 `get-video-basic-info`。
4. [约定→落地后由测试锁] 默认链顺序即方案要点第 2 条。锁：`VideoResolver._build_provider_chains` 单测；`PROVIDER_PRIORITY_*` 仍可覆盖。
5. [约定→落地后由测试锁] 空 token 等于该源不存在，不发请求。锁：未设 `JUSTONEAPI_TOKEN` 时 httpx 调用次数为 0。
6. [约定→落地后由测试锁] 有 Cobalt 的平台，Just One API 不得抛 `TerminalError`。锁：沿用 `tests/test_video_resolver_terminal.py` 的责任链语义，补 justoneapi 夹具。

## 待验证前提

1. [推断] 抖音 / 小红书在 Just One API 上的私密/删除/图文信号长什么样。验证：拿到真实终态样本再启用 `TerminalError`；本轮没有样本就当 retryable。
2. [推断] 视频号 `download_url` 的时效、是否绑 IP、大文件（探测样本约 416 MiB）从 fordeal 拉全文件的速率。验证：上线前在 fordeal 本机对明文 URL 做小 Range 抽样（16 MiB 级）；全文件不在本机慢链上跑。失败则 stream 层报 502，不把坏 URL 交给客户端。
3. [推断] 快手 JOA 的 `photos[0]` 改写成 `manifest` 后，现有 `KuaishouService._parse_response` 能选出 `representation[].url`。验证：用探测裁剪的 fixture 跑解析器；红了就只改适配器映射，不改 TikHub 链。
4. [推断] 生产长期 token 与探测用 token 不是同一把。验证：部署前检查生产 `.env` 的 `JUSTONEAPI_TOKEN` 长度非空且不是探测会话用过的那把。

## 验收路径

1. 入口：`POST /api/resolve`（带 `X-API-Key`），再按平台取 `data.video_url`。
2. 步骤：
   1. 本地：TikHub 桩失败、Just One API 桩返回探测裁剪 fixture → `success=true` 且 `data.provider=justoneapi`，直链字段非空。
   2. 抖音 / 快手 / 小红书 / TikTok / YouTube / Instagram / Twitter：`video_url` 为第三方 CDN，不指向本服务。
   3. 视频号：`video_url` 仍为本服务 `/api/stream/wechat_channels/{sph_code}`；随后 `GET` 该 URL（带 key、`Range: bytes=0-15`）得到 `ftyp` 且未走 XOR。
   4. 空 token：链上无 justoneapi 请求，失败形态与现网 TikHub 单源失败一致。
   5. 有 ID 的请求：抓 provider 实际发出的 query，不含 `shareUrl` / `share-url-transfer`。
3. 预期：上线后真实流量仅在 TikHub（及 Cobalt 平台的 Cobalt）失败时才产生 Just One API `code==0` 账单；仪表盘能按 `provider=justoneapi` 分开看。

探测账单（供对照，非验收项）：2026-11-03 用临时 token 打了详情/下载，八平台均 `code==0` 扣费；视频号打了两枪、小红书 v6 打了三枪（查 `video_info_v2` 字段）。该 token 用完即撤，不进生产。
