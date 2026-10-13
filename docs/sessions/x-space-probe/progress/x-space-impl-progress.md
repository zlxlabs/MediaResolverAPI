# x-space-impl-20261014-01 进度存档（追加式）

## M1 url_parser Space URL 识别

- 当前阶段：implementing（M1 完成）
- 本段结论：url_parser 新增 `/i/spaces/<13位字母数字>`（x.com/twitter.com 含子域）识别，平台标识走类常量 `TWITTER_SPACE_PLATFORM`，与 `/status/` 提取器双向互斥；`tests/test_url_parser.py` 新增 TestTwitterSpaceIdentification 15 例全绿（51 passed），存量 43 例零破坏。Accept-Check `grep -c twitter_space url_parser.py == 1` 通过（方法名避开子串，唯一字面量在常量定义行）。
- 关键决策与已否决方案：识别放在 `identify_platform` 内、域名映射之前（对齐 wechat_channels 先例）；否决「把 twitter_space 加进 extractors 表」——会引入第二个字面量行，破坏 Accept-Check 计数。
- 下一步唯一动作：M2 config 配置项 + ytdlp provider（错误分类测试先行）。

## M2 依赖与配置

- 当前阶段：implementing（M2 完成）
- 本段结论：pyproject 钉 yt-dlp==2026.8.19（uv lock 解析为 2026.08.19，PEP440 归一）；Dockerfile 加 apt ffmpeg；config 新增 YTDLP_EXTRACT_TIMEOUT_SECONDS=30 与 PROVIDER_PRIORITY_TWITTER_SPACE。全量套件 454 passed 零破坏。
- 关键决策与已否决方案：错误分类锚定 yt-dlp 2026.08.19 源码 TwitterSpacesIE 实际文案（not found / not started yet / replay is disabled / not downloadable yet），不拍脑袋枚举。
- 下一步唯一动作：M3 ytdlp provider + twitter_space 服务映射 + resolve 接线。

## M3 provider/服务/接线

- 当前阶段：implementing（M3 完成）
- 本段结论：YtdlpProvider（to_thread + wait_for 超时，unavailable/login/generic 三分类，SpaceNotReplayError 终态）与 TwitterSpaceService（映射容错）落地；video_resolver 挂 twitter_space→[ytdlp] 链并在 _adapt_data 内联适配（providers/__init__.py 不在卡面允许清单，直接模块路径导入）。test_twitter_space.py 29 例全绿，含 resolve API 面与 pscp.tv 不泄漏断言。
- 关键决策与已否决方案：否决「为 ytdlp 建 YtdlpAdapter 类」——adapters/ 不在允许清单且平台映射仅一条，内联分支第二消费者不存在（反熵条款）。【待裁决偏差】yt-dlp SPACE_STATUS 四态中 timedout→post_live 仍可下载（源码注释明示），design 字面判据 live_status != "was_live" 会拒绝这部分可下载回放；按锁定决策实现（post_live 同样拒），报告中上报主脑裁决是否放行。另一偏差：fixture 实况顶层无 duration 键（D4 报告写「键存在值为 null」），测试按实况锁两种形态。
- 下一步唯一动作：M4 流式端点 stream.py（并发槽共享改造 + ffmpeg 子进程生命周期测试先行）。

## M4 流式端点

- 当前阶段：implementing（M4 完成）
- 本段结论：`GET /api/stream/twitter_space/{space_id}` 落地——共享并发槽（`_stream_slot_cm` 单源 429，微信/Space 双端点同 `MAX_CONCURRENT_STREAMS`）、每次请求现取 playlist（测试锁双请求两提取）、ffmpeg argv 锁定（-c copy/-bsf:a aac_adtstoasc/-movflags frag_keyframe+empty_moov/pipe:1 + 按声明传 -user_agent/-headers）、首块预读把 ffmpeg 瞬时失败升级为 502、断连同步 kill 不留尸（直接驱动生成器断言 kill_count==1）。全量 494 passed。
- 关键决策与已否决方案：否决「await process.wait() 收尸」——取消上下文中 await 不可靠，改为同步 kill+cancel+close（收尸交给 asyncio 子进程传输层 SIGCHLD 自动完成）；预读首块换取错误状态码可见性（否则 200 空体）。stderr 持续排干防 64KB 管道卡死，尾部仅入日志且经 redact（pscp.tv 不进日志）。
- 下一步唯一动作：M5 README + 真实环境三步验收（起服务→resolve→下载≥1MiB→ffprobe）。

## M5 真实环境验收与两处修复

- 当前阶段：implementing（M5 完成，待提交验收）
- 本段结论：真实环境三步全过。关键发现：**audio-only fragmented MP4 必须显式给切片参数**——`frag_keyframe` 只认视频关键帧，实测 ffmpeg 消费 HLS 正常（30x 速）但输出停在 32B moov 不吐字节（D3 探针证据的盲区：跑完整进程收尾才写盘，掩盖了增量不吐字）。修复：`-frag_duration 4000000`（仍为 mp4 容器，不换锁定决策）；第二修复：`_cleanup_ffmpeg` 不可调 StreamReader.close()（不存在该方法，首测时炸 AttributeError 且把响应挂在 more_body=True）。修复后实测：resolve 2.748s（200，twitter_space/audio，无 pscp.tv）；下载 1 MiB 墙钟 7.0s；ffprobe 显示 aac/48000Hz/2ch、容器 mov,mp4,m4a；断连后服务端无 ffmpeg 残留；服务日志 pscp.tv 零命中。audio 模式 resolve 亦通。
- 关键决策与已否决方案：`-frag_duration` 是锁定决策 4 同容器下的 muxer 参数补全（卡面假设节预留的「实测发现兼容性问题」分支），非容器更换；否决 matroska。否决在 finally 里 await 收尸——改同步 kill+cancel（见 M4）。
- 下一步唯一动作：提交 M5（README + 修复），跑全量套件与 Accept-Check，写报告。

## M6 gate primary findings 处置

- 当前阶段：implementing（M6 完成，待推送后等 CI）
- 本段结论：PR #43 gate primary（codex-sub）给 verdict=fail：3 major + 1 minor。处置：#4 raw_data 泄 playlist 进缓存（minor，实为锁定决策 4「绝不持久化」违规——cache.py:144 to_dict 落库含 raw_data）→ 修复：映射层递归脱敏所有字符串值（实测 requested_downloads[].url 也携带 playlist，字段枚举不可靠）；#1 中途异常退出静默收尾成 200 截断流（major）→ 修复：EOF 后 returncode 非 0 即抛 RuntimeError 中断响应（客户端看到传输中断而非假成功）；#2 读挂死永久占并发槽（major）→ 修复：stdout 读包 wait_for(HTTP_TIMEOUT_SECONDS)，超时 kill+抛错释放槽（复用既有上游超时旋钮，第二消费者成立）；#3 yt-dlp wait_for 超时后 to_thread 线程不可杀、持续卡顿会堆积（major）→ 与锁定决策 2「进程内库调用」直接冲突（子进程化才能真杀），上报主脑裁决，不自行改。修复后全量 497 passed；修复后真实环境复验：resolve 2.0s / 1MiB 4.4s / ffprobe aac-mp4 / 无 ffmpeg 残留 / 日志零泄漏。
- 关键决策与已否决方案：否决「按字段枚举脱敏」（requested_downloads 逃逸）；否决「读超时新增配置」（复用 HTTP_TIMEOUT_SECONDS）；否决「#3 改子进程」（锁定决策 2，只上报）。
- 下一步唯一动作：提交推送，等新 head 的 gate 结论，写报告。

## M7 gate primary 二、三轮处置与收敛边界

- 当前阶段：implementing（M7 完成，等 round-4 gate）
- 本段结论：round-2（2032c0f）三 findings 全修：专用有界 ThreadPoolExecutor(4)（进程内库调用锁定内，隔离默认 executor 防 getaddrinfo 全死）、收尸竞态闭合（EOF 后显式 wait 拿确定退出码，测试锁定）、fixture 换合成 pscp.tv 形态凭证（SapzA 令牌零残留，红验复跑 1 注入→5 红→还原→30 绿）。round-3（6fd3682）：security-twitter-space-auth-bypass 为误报（router 级 verify_api_key 同文件顶部，新增显式 401 测试钉死）；correctness-ffmpeg-partial-output-success 重标——None 竞态与异常退出均已上抛，唯一残余「exit 0 但内容截断」无本地信号可判（D4 无时长元数据），已在判据处注释声明；thread-exhaustion 降级 minor（承认隔离，剩余为锁定决策 2 内已知限界）；committed-credential 转指 probe-report.md——不在本卡可写范围，URL 早已过期，归主脑裁决（保留历史证据 or 后续清洗）。
- 关键决策与已否决方案：否决「给 exit-0 截断加检测」（无对账信号，只能假精确）；否决「修 probe-report.md」（不在 Scope-Globs，历史证据归主脑）。
- 下一步唯一动作：等 round-4 gate 结论；若同泛化 finding 再现且无新事实，收敛停止、报告上报主脑裁决。

## M8 续派（dlg-20261013-194528-7c1867）：段抓取收归服务端 + post_live 放行

- 当前阶段：implementing（代码完成，真实环境重试中）
- 本段结论：修复 B：live_status 判据改 `not in ("was_live", "post_live")`，post_live 测试反转为放行。修复 A：服务端现取并解析 m3u8（master 跟首个 variant、urljoin 相对解析、空表报错）→ ffmpeg 改 `-f aac -i pipe:0`（ffmpeg 的 ADTS 解复用器名为 aac，首测 `-f adts` 报 Unknown input format 即被预读 502 路径抓住）→ 服务端 httpx 逐段抓写 stdin（读超时 HTTP_TIMEOUT_SECONDS，任何段失败杀 ffmpeg 上抛）→ 全段写完关 stdin，exit≠0 上抛保留。完整性不变式「响应正常收尾 ⇔ 全部段成功且 ffmpeg exit 0」由 4 个生命周期测试锁定（段失败中断/异常退出/收尸竞态/双向 stall）。实现期抓到两处：_spawn_ffmpeg 漏传 stdin=PIPE（spawn_record 的 kwargs 断言当场抓住）；测试假 resolver 绕过空表检查后补了端点守卫。全量 510 passed；本地真实管道验证（lavfi 生成 ADTS → -f aac pipe:0 → fMP4 输出 ffprobe aac/mp4）通过。
- 关键决策与已否决方案：否决「段列表字段枚举脱敏」沿用递归脱敏；否决「m3u8 解析引第三方库」（标准库 urljoin + 手写行过滤，卡面禁止新依赖）；写侧 stall 由读侧超时兼管（ffmpeg 停止消费 → stdout 停止产出 → 读超时触发清理，不再单设写超时旋钮）。
- 下一步唯一动作：真实环境三步重跑（网络到 x.com 今晚抖动：SSL EOF/30s 超时/无 formats 混合出现，后台每 3 分钟重试）；成则取证提交，持续失败则按卡面预案报 blocked 上行。

## M9 stdin 互锁与 StreamWriter 真机双 bug，三步全过

- 当前阶段：implementing（收尾：提交推送 + 报告）
- 本段结论：真机首跑连环 502，逐层定位出两个单测假进程挡不住的真机 bug：①预读-泵启动顺序死锁——`-f aac -i pipe:0` 的 ffmpeg 收到 stdin 前零 stdout，旧顺序预读白等 30s 超时；修复为泵任务先于预读，并新增门控假进程测试（stdout 等 stdin 喂数据才出流）锁住顺序；②`await stdin.write()`——StreamWriter.write 是同步方法（await None 即 TypeError `'NoneType' object can't be awaited`），背压应用 `await drain()`；单元假 stdin 恰好是 async write 挡住了它，真机 pump 日志（segments=2379, err=NoneType...）一枪定位。另补两处观测缺口：段泵失败必须落日志（此前被任务取消吞掉）；端点对空段列表加 502 守卫。修复后真实环境三步全过（04:55）：resolve 1.96s（200，twitter_space/audio，无泄漏）→ 流式 1 MiB 11.8s（含现场提取+清单解析+首批段）→ ffprobe aac/48kHz/2ch、容器 mov,mp4,m4a；断连后无 ffmpeg 残留；服务日志 pscp.tv 零命中。全量 511 passed。插曲记录：排查中一度误判为网络抖动/线程池毒化，最终由泵日志 + faulthandler 线程栈（worker 空闲=提取已完，挂的是预读）实锤为顺序死锁；今早的 SSL EOF/超时/无 formats 是叠加的真实瞬时抖动（CLI 隔离验证网络通路正常）。
- 关键决策与已否决方案：否决「预读改为跳过」——502-on-instant-failure 语义保留，只是把喂数据的一方先跑起来；否决「假 stdin 维持 async write」——假接口必须镜像真实接口（StreamWriter.write 同步），否则测试挡不住这类 bug（本次就是反例教材，写入报告）。
- 下一步唯一动作：提交推送、盯 CI gate、写最终报告。
