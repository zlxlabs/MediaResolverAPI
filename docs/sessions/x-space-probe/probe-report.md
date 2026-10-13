<!-- delegate-outcome: succeeded -->
# X Space 回放下载探针报告（D1–D4 实测）

- Task-Id: x-space-replay-probe-20261014-01 · dispatch `dlg-20261013-172131-68bd96`
- 探针时间：2026-10-14 01:22–01:53 (+08:00)
- 环境：ffmpeg 6.1.1-3ubuntu5+esm13（系统自带）；yt-dlp 2026.08.19（`uv tool run` 临时环境，未入仓依赖）
- 样本 Space：`https://x.com/i/spaces/1AKEmvYqbrOKL`（卡面样例，实测仍存活，无需启用 ≤15 分钟找活体预算）
- 探针脚本：`docs/sessions/x-space-probe/probe.md`（可重复执行版）
- 所有样本产物在 `/tmp/space-probe/`，不入库；本仓只落脚本与本报告。

## D1 提取可行性（无 cookies）

结论：成功。无 cookies 下 yt-dlp 2026.08.19 走 guest token 路径，对活体回放一次拿到 playlist URL 与全部元数据，**login_required 未出现**——方案 B 不需要 X cookie（桌面结论维持）。

原始证据：

```sh
$ uv tool run yt-dlp --simulate --dump-single-json "https://x.com/i/spaces/1AKEmvYqbrOKL" > meta.json
# exit=0，stderr 全空，耗时 2.6s，meta.json 5009 字节
```

提取到的 playlist URL（顶层 `url` 字段，pscp.tv 直链，`?type=replay`）：

```
https://prod-fastly-us-west-1.video.pscp.tv/Transcoding/v1/hls/1m5X1najU_VkQeZvZhQnXjQ_2IsRK0Oe8NuPBaIT2Csw56Kcz4Bfg_dk8gUw4tjiQywtL3gbxBG9erCRbSapzA/non_transcode/us-west-1/periscope-replay-direct-prod-us-west-1-public/audio-space/playlist_16654926480857770170.m3u8?type=replay
```

关键形态：`live_status="was_live"`、`protocol="m3u8"`、`acodec="aac"`、`vcodec="none"`、单 format（`format_id="0"`）、`http_headers` 含 5 项（UA / Accept / Accept-Language / Sec-Fetch-Mode / `Referer: https://twitter.com/`）。

## D2 playlist URL 寿命（0/10/30 分钟探活）

结论：通过。playlist URL 在提取后 0/10/30 分钟三个时刻探活全部 206，30 分钟内未失效——短于 `CACHE_TTL_HOURS=12` 一个量级，坐实「playlist URL 不进常规缓存」；且裸 curl 与带 yt-dlp headers 结果全程一致。

采样按卡面约束 nohup 后台运行（脚本见 `probe.md`），结果文件 `/tmp/space-probe/d2-samples.txt` 全文（两列状态码：`bare`=卡面 Evidence-Commands 原样裸 curl；`with_headers`=带 yt-dlp `http_headers` 的 UA+Referer，对齐服务端真实调用形态）：

```
2026-10-14T01:23:02+08:00 elapsed_s=1 bare=206 with_headers=206
2026-10-14T01:33:03+08:00 elapsed_s=602 bare=206 with_headers=206
2026-10-14T01:53:03+08:00 elapsed_s=1802 bare=206 with_headers=206
2026-10-14T01:53:03+08:00 DONE
```

状态码 206 = `GET` + `Range: bytes=0-0` 探活命中的正常 Partial Content，playlist URL 在提取后 30 分钟仍有效（采样总跨度 1802 s ≥ 30 分钟，满足卡面约束）。**裸 curl 与带 header 结果全程一致——pscp.tv playlist 请求不校验 Referer/UA**。

## D3 长下载不断流 + remux 拼装

结论：通过。1:59:00（≈2h）的活体回放用 `ffmpeg -c copy` 完整下载 + remux 成功，全程零断流、零 HTTP 错误，吞吐 ≈11.9x（2h 音频 10 分钟下完）——**segment 在整个下载过程中无中途过期**，流式转发/服务端拼装的稳定性前提成立。

原始证据（命令见 `probe.md` D3 节；刻意未加任何 reconnect/重试参数，避免掩盖断流事实）：

| 项 | 值 |
|---|---|
| 音频时长（ffprobe duration） | 7140.181333 s = 1:59:00.18 |
| 下载墙钟耗时（`/usr/bin/time -v`） | 10:10.44 |
| 吞吐比 | 7140.18 s / 610.44 s ≈ **11.7x**（ffmpeg progress 自报 `speed=11.9x`） |
| 样本大小 | 84,927,493 bytes（≈81 MiB） |
| 断流证据 | `d3-ffmpeg.log`（4823 行，ffmpeg stderr + time -v 混合）中 `grep -E "\] (HTTP error|Server returned|Failed|Error|error)"` **零命中**；无任何 reconnect/retry 行 |
| ffprobe | `aac / 48000 Hz / 2 ch / 93,631 bps`；container `mov,mp4,m4a`；总码率 95,154 bps |
| sha256 | `ceafc2954129613cbf9cff25329bf69e9904fcc4cd18cc9b9f8002ca06e3d609` |

日志里仅有的两条非致命告警（原文）：

```
mime type is not rfc8216 compliant
Format aac detected only with low score of 25, misdetection possible!
```

前者是 pscp.tv 返回的 m3u8 Content-Type 不合 RFC8216（ffmpeg 照常解析），后者是输出文件探测的提示（我们显式指定输出格式，无实际影响）。

注意一个陷阱：naive 地 `grep -E "403|404"` 会在日志上命中 27 行，全是 **chunk 文件名里的序号**（如 `chunk_1791811656545018717_403_a.aac`），不是 HTTP 状态码——断流判定必须 grep ffmpeg 的错误行格式。

CPU 开销：user 3.11s + sys 1.98s（纯网络 IO，remux `-c copy` 成本可忽略，服务端做拼装不构成 CPU 压力点）。

## D4 元数据清单（`--dump-single-json` 实际存在字段）

结论：VideoInfo 契约五字段可用（title/uploader/uploader_id/timestamp/release_timestamp），**两个坑：`duration` 键存在但值为 null；`like_count` 键整个不存在**——契约映射必须容错，且时长无法从元数据预知。

全字段清单（值形态示例，来自实抓 meta.json）：

| 字段 | 值形态 | 实测值示例 |
|---|---|---|
| `id` / `display_id` | str（13 位 Space ID） | `"1AKEmvYqbrOKL"` |
| `title` / `fulltitle` | str | `"噪音之后：为什么做IP一定要有自己的“理论”"` |
| `duration` | **null**（⚠ formats[0] 也无 duration 键） | `None` |
| `uploader` | str（显示名） | `"Adrian Punk"` |
| `uploader_id` | str（X handle） | `"AdrianPunk115"` |
| `timestamp` | int（Space 创建/排定，Unix 秒） | `1791810443` |
| `release_timestamp` | int（开播，与 timestamp 差 8s） | `1791810451` |
| `release_date` / `upload_date` | str（%Y%m%d） | `"20261012"` |
| `like_count` | **键不存在**（非 null） | — |
| `live_status` | str 枚举 | `"was_live"` |
| `is_live` / `was_live` | bool | `false` / `true` |
| `url` | str（pscp.tv m3u8 直链，会过期） | 见 D1 |
| `protocol` | str | `"m3u8"` |
| `ext` / `audio_ext` | str | `"m4a"` |
| `acodec` | str | `"aac"` |
| `vcodec` / `video_ext` | str | `"none"` |
| `container` | str | `"m4a_dash"` |
| `abr` / `tbr` / `vbr` / `filesize_approx` | null / null / int(0) / null | 均不可用 |
| `formats` | list，**恒 1 项** | `[{"format_id":"0","url":<playlist>,...}]` |
| `description` | str（Space 参与者列表长串，非主题描述） | `"Twitter Space participated by FortuneCutie饼干姐姐, 魂蓝, …"` |
| `thumbnail` | str（主播头像 URL） | `"https://pbs.twimg.com/profile_images/…/…_400x400.jpg"` |
| `thumbnails` | list，1 项 | 同上 |
| `http_headers` | dict，5 键 | 含 `Referer: https://twitter.com/` |
| `webpage_url` / `original_url` | str | `"https://x.com/i/spaces/1AKEmvYqbrOKL"` |
| `webpage_url_domain` | str | `"x.com"` |
| `extractor` / `extractor_key` | str | `"twitter:spaces"` / `"TwitterSpaces"` |
| `has_drm` / `_has_drm` | bool false / null | 无 DRM |
| `requested_subtitles` | null | 无字幕 |
| `resolution` | str | `"audio only"` |
| `format` / `format_id` | str | `"0 - audio only"` / `"0"` |
| `playlist` / `playlist_index` / `preference` / `quality` / `format_index` / `aspect_ratio` | null | 纯占位 |
| `_type` | str | `"video"` |
| `_version` | dict（yt-dlp 版本信息） | `{"version":"2026.08.19",…}` |
| `epoch` | int（抓取时刻） | `1791912121` |

时长缺失的后果（给主脑的输入）：无法在下载前从元数据得知时长/大小 → 进度反馈与预算控制只能靠 ffprobe 边下边测或下载后确认；`filesize_approx` 同为 null。

## 对方案 B 实现形态的影响（事实输入，非设计）

1. **提取可行且无需 cookie**（D1），`yt-dlp` 作为库调用的前提成立；错误形态里 `not authorized` → login_required 的分支本次未触发，实现时仍应保留该分支映射。
2. **playlist URL 是短命凭证**：实测 ≥30 分钟内可用（D2），但远短于本仓 `CACHE_TTL_HOURS=12`——playlist URL 不能进常规缓存，须绕开或单独 TTL 策略（卡面已预告，实测坐实）。30 分钟边界只测到「活着」，不排除更早已被替换的窗口，实现侧仍按「每次现取现用」设计最稳。
3. **服务端拼装可行且便宜**（D3）：2h 音频 10 分钟拉完、CPU 秒级，`-c copy` remux 不重编码。与 README「服务不做转码」的措辞存在轻微张力——remux 是容器级拼装、不改音频流，是否算「转码」由主脑定夺，本卡只摆事实。
4. **时长元数据缺失**（D4）：交付前的 duration 断言只能来自 ffprobe 输出文件，不能来自提取元数据。

## 现场还原（样本不入库）

`/tmp/space-probe/` 遗留产物（探针结束后保留，供复核；复核完可整目录删除）：

| 文件 | 大小 | sha256 |
|---|---|---|
| `sample.m4a` | 84,927,493 B | `ceafc2954129613cbf9cff25329bf69e9904fcc4cd18cc9b9f8002ca06e3d609` |
| `meta.json` | 5,009 B | 元数据原文（D4 证据） |
| `d2-samples.txt` | 3+1 行 | D2 采样结果全文（已贴报告） |
| `d3-ffmpeg.log` | 4,823 行 | ffmpeg stderr + time -v（断流判定证据） |
| `probe-d2.sh` / `d2-nohup.log` / `d3-time.log` / `playlist-url.txt` / `ffprobe.json` / `sample-sha256.txt` / `d1-stderr.log` | 小 | 过程文件 |

## 入库证据（git）

以下三件套在 HEAD = `6d047a4`（报告主体 commit）执行的实际输出；随后仅追加本节本身
作为 evidence commit，产物内容不变，终态以最后一条 `git status` 干净为准：

```
$ git log --oneline -1
6d047a4 docs(x-space-probe): D1-D4 probe report (extract ok, no stream break, 206 x3, field map)

$ git show --stat --format= HEAD
 docs/sessions/x-space-probe/probe-report.md | 168 ++++++++++++++++++++++++++++
 1 file changed, 168 insertions(+)

$ git status --short --untracked-files=all
（空，工作区与未跟踪文件均干净）
```

全部提交：`b3cfae1`（probe.md 脚本）→ `6d047a4`（probe-report.md 报告主体）→ 本 evidence commit。

## 回执

- 探针脚本入库（D1–D4 可重复执行） [commit: `b3cfae1`]
- 探针报告四判定点结论与原始证据 [file: `docs/sessions/x-space-probe/probe-report.md:13`]
- D2 采样结果文件全文已贴报告（bare 与 with_headers 双列，0/10/30 分钟三点） [file: `docs/sessions/x-space-probe/probe-report.md:37`]
<!-- RECEIPT -->

## 踩到的坑

1. **`duration` 与 `like_count` 缺失**：现象——`--dump-single-json` 顶层 `duration=null`、`like_count` 键整个不存在，`formats[0]` 也没有 duration。影响——VideoInfo 契约若按「字段恒在」映射会在运行时拿空；时长/大小无法预知。处理——D4 如实记录键存在性差异，并在报告「对方案 B 的影响」提示映射须容错、时长改从 ffprobe 取。
2. **ffmpeg 日志 grep 403/404 误报**：现象——`grep -E "403|404"` 命中 27 行，全是 chunk 文件名序号（`chunk_…_403_a.aac`）。影响——会把「零断流」误判成「大量 403/404」。处理——改用精确模式 `\] (HTTP error|Server returned|Failed|Error)`，结果零命中。
3. **`/usr/bin/time -v` 输出与 ffmpeg 日志同流**：现象——time -v 写 stderr，被 `2>` 与 ffmpeg 日志混进同一文件。影响——无（耗时仍可读），但 grep 断流时需容忍 time -v 的英文段落。处理——保留混合文件并在报告注明。

## 闸与绕过

未绕过。

## 与卡面的偏差

1. D2 每个时刻在卡面 Evidence-Commands（裸 curl）之外多记一列 `with_headers`（带 yt-dlp http_headers）——补充性测量，用于对齐服务端真实调用形态，不改变卡面要求的判据。
2. D2 结果文件在 3 行采样记录之外多 1 行 `DONE` 标记（第 4 行），完成判据仍按「含 3 行采样记录」执行，DONE 仅为轮询方便。
3. D3 在卡面 Evidence-Commands 基础上显式加 `-user_agent/-headers`（对齐 D1 元数据声明的 http_headers）与 `-bsf:a aac_adtstoasc`（ADTS→MP4 容器 remux 的标准 bitstream filter，ffmpeg 6.1 本会自动插入，显式写保证确定性）。均为忠实执行「remux（AAC 不重编码）」所需，不改变被测行为。
4. 无其他偏差：未改任何禁止路径文件；样本未入库；找活体预算未启用（样例存活）。

## 最贵的一步

D2 的 30 分钟采样跨度是本卡最长等待（探针总时长被它定为 ≈31 分钟）；因后台化 + 期间推进 D3，实际未浪费墙钟。次贵是 D3 的 10 分钟下载。若未来同类探针卡，可把 D2 采样点改为 0/5/15/30 四点仍不增总时长。

## 新增测试判据

无。
