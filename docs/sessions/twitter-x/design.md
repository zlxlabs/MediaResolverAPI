# DESIGN-note：接入 X（Twitter）视频解析

用户把 x.com 或 twitter.com 的 status URL 交给 POST /api/resolve，拿到一条可下载的 mp4 直链和作者/统计元数据。

不做：指定清晰度、只下音频（issue #23）；X 直播 / Spaces；锁帖 cookies；yt-dlp；引用帖视频；status 下的 video/N 分轨。

不能让下游自己调 TikHub（本服务的价值是统一 VideoInfo）；公开 X 视频是明确需求不能砍；URL 形态无法靠文档约定让现有解析器认 x.com。

选定方案：平台名 twitter。TikHub 单端 fetch_tweet_detail?tweet_id= 主源，Cobalt 兜底。输入文法冻结为严格 path-only 提取 status ID（查询串与 fragment 绝不作为 ID 来源，文法外网址一律解析失败返回 (None, None)，外部 400；不再开放式防御新脏输入）。遍历本帖全部 `media.video[*].variants`，封顶 1080p 选流（优先池短边 <= 1080，末池短边 > 1080 取短边最小者兜底）。分类器不出终态。

不采用：只接 Cobalt（丢掉播放量/点赞/时长）；只接 TikHub（单端点无兜底）；引入 yt-dlp；平台名用 x（与 TikHub/Cobalt 标识不一致）。

不变式（均须测试锁死）：
1. [实测] 冻结输入文法：帖子编号只从 URL 的 path 提取（匹配 `/(?:i/)?status/<digits>(?:/|$)`）；查询串与 fragment 中的 `/status/数字` 一律当不存在（如 `/home?next=/someone/status/xxx` 得到 (None, None)）。评审范围以测试用例为准，文法外网址 = 不支持。锁在 tests/test_url_parser.py。
2. [实测] TikHub 链只打 fetch_tweet_detail，入参 tweet_id。锁在 tests/test_tikhub_provider_twitter.py param 断言。
3. [实测] 有 Cobalt 兜底，分类器永不出终态。锁在 test_classify_never_terminal 与全失败 not TerminalError。
4. [实测] 遍历本帖全部 `media.video[*].variants` 封顶 1080p：按短边 min(width, height) 选流，优先池 `1 <= 短边 <= 1080` 按 (短边, bitrate) 取最大（1080 压过 720，也压过 1440/2160）；次池短边未知 (0)；末池短边 > 1080 取短边最小者兜底；忽略 HLS 与 quoted.media。锁在 parser 用例。
5. [实测] GET /api/platforms 返回 twitter: [tikhub, cobalt]。锁在 platforms 用例。

[推断] 生产 Cobalt 对任意公开 X 视频都稳定；本卡用桩覆盖接线。主脑验收可用本地密钥对 https://x.com/0xCodez/status/2098782845183410287 探活（非本卡完成条件）。

入口：POST /api/resolve（TestClient，桩掉 provider HTTP）。提交 https://x.com/0xCodez/status/2098782845183410287?s=20。预期 200，data.platform==twitter，data.video_id==2098782845183410287，data.video_url 为选中的 mp4。
