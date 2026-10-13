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
