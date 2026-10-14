# DESIGN-note：X Space 回放录音下载（服务端拼装单文件）

> 探针证据：同目录 [probe-report.md](probe-report.md)（D1–D4，2026-10-14 实测）。
> 用户授权：探针验收后自主落地、PR 走完整漏斗合并进 master、不部署（2026-10-14 夜）。

## 目标

用户把 `https://x.com/i/spaces/<13位ID>` 丢给 `POST /api/resolve`，拿到元数据和一个指向本服务的下载 URL；GET 该 URL 能把整场回放保存为单个可播放的音频文件（m4a），无需客户端处理 HLS 或认证。

## 非目标

- 不支持正在直播中的 Space（`live_status` 不在 `was_live`/`post_live` 两者时一律明确报错，含缺失；`post_live` 即已结束仍可回放，yt-dlp 源码注释与测试用例双证）。
- 不做转码（不改音频码流，只做容器拼装）；不做多码率选择（实测回放恒为单一音频 format）。
- 不做历史过期的 Space（X 只保留回放约 30 天，过期即明确报错，不做任何兜底取回）。
- 不做 X cookie / 登录态支持（D1 实测 guest token 足够；X 认证演进由 yt-dlp 版本升级吸收）。
- 不改抖音等其余平台；不把 yt-dlp 引入其他平台的解析链。

## 为什么不是分区 / 删除 / 约定

- **分区**：本特性与其他平台无共享可变状态；唯一共享资源是下载并发槽（与视频号流式代理同一进程内信号量），按既有 `MAX_CONCURRENT_STREAMS` 语义合并管理，不新增分区机制。
- **删除**：透传 playlist URL 的最小方案已被用户否决（下游要单文件）；「先落盘再服务」的形态被 D3 否决（11.7x 吞吐下边下边转发即可，无需磁盘管理）。
- **约定**：playlist URL 30 分钟寿命无法靠约定让下游避开（下游拿到的必须是稳定 URL），必须由服务端每次现取——这是运行时行为不是约定能解决的。

## 方案要点与已否决方案

- **要点**：
  1. `url_parser` 新增 `x.com/i/spaces/<13位字母数字>`（含 `/peek` 等尾段）识别，平台标识 `twitter_space`；与 `/status/<数字>` 互斥不冲突。
  2. 新平台服务 `twitter_space.py`：进程内以库方式调 yt-dlp（`extract_info(download=False)`，`asyncio.to_thread` 包裹，提取超时独立上限），从顶层 `url` 取 playlist URL，映射元数据到 `VideoInfo`（`media_type="audio"`、width/height=0、duration=None——D4 实测元数据无时长、like_count 键不存在，映射须容错）。
  3. `video_url` 指向本服务新流式端点 `GET /api/stream/twitter_space/{space_id}`（带 `X-API-Key`，与视频号同规则）：每次请求**现取** playlist URL（不进 12h 缓存，D2 坐实短命），**分段 HTTP 抓取由服务端 fail-fast 完成（2026-10-14 主审 round-4 major 裁定后修正：ffmpeg 自取分段在中间段 404 时以 exit 0 空 stderr 静默截断，实测见下方测量记录；服务端逐段抓取喂 ffmpeg stdin，`-c copy` 拼装语义不变），ffmpeg 子进程容器拼装后管道转发**（`Content-Disposition: attachment`）；并发槽与视频号共用 `MAX_CONCURRENT_STREAMS`，超限 429。
  4. 错误映射 fail fast：回放过期/不存在→404；直播中→400（明确文案）；提取/播放列表失败→502 带脱敏原因；无重试无兜底。
  5. Dockerfile 安装 ffmpeg（apt）；`yt-dlp` 进 pyproject 依赖（钉版本）。
  6. README：平台表加行 + Space 下载节（单文件流式、无 Range/不可拖动进度、需 API-Key、时长未知、30 天保留期、与「不做转码」措辞的精确化——remux 是容器拼装不重编码，不改该承诺）。
  7. 实现基于 `card/x-space-probe` 分支（探针文档随实现 PR 进主干，决策指针共用此分支）。
- **已否决**：
  - 透传 m3u8 playlist URL 给下游——用户拍板要单文件；且 URL 短命进缓存必坏（D2）。
  - 自持 cookie 直连 GraphQL——认证演进/query hash 轮换成自运维负担；yt-dlp 社区在背（codex 咨询一致，2026-10-14）。
  - 服务端先落盘完整文件再服务——D3 证明 11.7x 吞吐下边下边转发即可；落盘引入磁盘管理与清理问题。
  - Cobalt 作为数据源——未证实支持 Spaces；TikHub 现网无端点（OpenAPI 2026-10-14 核实）。

## 关键不变式

1. [实测] 无 cookie 提取可行：yt-dlp guest token 路径对活体回放 exit=0（probe-report D1）。
2. [实测] playlist URL 短命（≥30min 存活，远短于缓存 TTL）：流式端点每次现取，永不读缓存中的 playlist（probe-report D2；测试锁定：resolve 响应中不含 pscp.tv 域名 URL）。
3. [实测] 长流稳定：2h 回放 `-c copy` 全程零断流、CPU 秒级（probe-report D3）——服务端拼装不构成资源风险。
4. [实测] 元数据缺 duration/like_count：映射容错，`VideoInfo.duration=None` 不报错（probe-report D4；测试锁定：fixture meta.json 驱动的解析测试）。
5. [设计] 音频无宽高：`width=height=0`、`quality=None`（对齐 Instagram 单流先例，quality 对该平台 no-op 不报 422）。
6. [设计] Space URL 与推文 URL 互斥解析：`/i/spaces/<13位>` 不落入 `status/\d+` 提取器，反之亦然（测试锁定双向）。

## 测量记录（2026-10-14 主脑，gate primary round-4 major 裁定依据）

本地 HLS 夹具 3 段、中间段 404，用实现同参数 ffmpeg 实测：**exit 0、stderr 空、输出静默截断**（2/3 段，19212 字节）——ffmpeg 自取分段的透传形态存在不可检的假成功路径，故裁定分段抓取收归服务端（见方案要点 3 修正）。完整性不变式随之可证：**响应正常收尾 ⇔ 所有段抓取成功且 ffmpeg exit 0**；任何段失败即传输中断（客户端看到错误，非静默成功）。

## 交付形态 v2：两阶段物化（2026-10-14 codex 咨询后定稿，采纳其全部四点补全）

**根因**：下游要「文件」三要素（确定性长度、可续传、可验证完整性），实时流结构性不具备——R4 静默截断、挂起占槽、断连全白拉、重复流量是同一根因的不同切面。**方案**：获取与交付解耦——

- **阶段一（后台获取，稀缺代理出口池）**：resolve 未命中缓存时触发后台拉取任务（**任务脱离客户端连接**，codex 补全：不挂长连接避免被反代/下载器先超时）；playlist 有 `#EXT-X-ENDLIST` 且全部段抓取成功且 ffmpeg exit 0 → 写入 `{cache_dir}/{space_id}.part` 后**原子改名** `{space_id}.m4a`（改名即「完整」态）。
- **阶段二（交付，廉价磁盘池）**：对 final 文件普通文件下载（Content-Length + Range + 断点续传）。
- **等待协议**（codex 补全）：未命中时 GET 返回 `202 + {status, retry_after}`（不挂长连接）；进行中重复请求复用同一任务；失败返回可重试语义；缓存命中秒回。
- **完整性承诺措辞**（codex 补全，精确化）：交付承诺 = **「playlist 声明内容的完整交付」**（ENDLIST + 段数对账 + exit 0），**非「源内容完整」**——playlist 与原始广播的残差不可证，继续文档化（不改「不做转码」承诺）。
- **文件状态边界故障矩阵**（codex 补全；其确认单进程无需 DB）：崩溃遗留 `.part` → 启动扫描清理（重启即弃，客户端重试重新拉取）；磁盘满 → fail-loud 拒绝新任务不停旧服务；改名与清理并发 → 清理跳过正在交付的文件；重启后单飞失效 → 启动清 `.part` 即重置。
- **TTL**（codex 补全：24h 无依据，由续传承诺推导）：**72h 续传窗口** + 磁盘水位熔断（超阈值按 mtime 淘汰最旧）。
- **已否决**：边下边存渐进交付（Range 读增长文件，复杂度最高语义最糊）；任务队列/DB 状态机（单进程容器，文件系统即状态足够，codex 确认）；继续流式+读超时/心跳补丁（用户明令拒绝，不解决断点续传与静默截断）。
- **视频号跟随**：开放问题，本卡不裁（codex：视频号已提供长度与 Range，无证据支持跟随改形态）。

## 待验证前提

1. [推断] ffmpeg fragmented MP4 输出对常见播放器/下载器可播放——实现卡验收时用真实入口跑通一次 resolve+下载并 ffprobe 校验后提升为实测。
2. [推断] 流式端点并发槽与视频号共用 `MAX_CONCURRENT_STREAMS` 不互相饿死——实现卡在单进程内用并发测试验证后提升为实测。

## 验收路径

1. 入口：本机起服务（uvicorn），带 API_KEY。
2. 步骤：`POST /api/resolve` 提交 `https://x.com/i/spaces/1AKEmvYqbrOKL`（探针同款活体）→ 取 `data.video_url` → curl 带 `X-API-Key` 下载 ≥1MiB → `ffprobe` 校验容器与编解码。
3. 预期：resolve 返回 `media_type="audio"`、`platform="twitter_space"`；下载流为完整 m4a（可边下边收，首字节秒级）；ffprobe 显示 aac/mp4；`grep` 服务日志无异常栈。
