# x-space-materialize-20261014-01 进度存档

## 2026-10-14 开工：现场核查完毕

- 当前阶段：implementing（U1 状态机模块开始前）
- 本段结论：基线 e56ad8e 干净、分支 card/x-space-mat；基线测试 74 passed（space 两文件）。环境事实：starlette 1.3.1（FileResponse 原生 Range 成立）、python 3.14 / asyncio_mode=auto、无 pre-commit 钩子、本机 .env 的 YTDLP_PROXY/API_KEY 均为空（真实凭据在生产机）。卡面两处存疑已登记待报告：①「resolve/GET 触发 spawn」中 resolve.py 不在允许修改清单，只能做 GET 触发；②「启动时扫描 .part」的启动钩子在 app/main.py lifespan（同样不在清单），改为惰性首次使用时扫描（语义等价：任何 spawn 必经端点、端点先触发扫描）。
- 关键决策与已否决方案：输出落盘选「ffmpeg stdout → 读超时循环 → 写 .part」（复用 _forward_ffmpeg_output 全部生命周期校验），否决「ffmpeg 直接写 .part 文件路径」（会失去读侧 stall 检测，且要改 argv 断言）；后台任务并发槽选「排队等待（blocking_slot）」，否决「占不到槽即失败」（会把暂时性饱和变成 202→502 翻转，违背锁定决策 3「任务持有槽直到完成或失败」）。
- 下一步唯一动作：写 tests/test_space_cache.py 状态机测试（先红）。

## 2026-10-14 U1+U2 落地：状态机与两阶段端点全绿

- 当前阶段：implementing（剩真实环境验证 + 文档收尾）
- 本段结论：U1（space_cache 状态机，d3d7d89）+ U2（两阶段端点，bc14081 + faba9d3）落地，全量 544 passed。TDD 顺序执行：17 个新语义测试先红后绿；红验两项均成立——① os.replace 改为直写 → test_publish_is_atomic_rename_part_gone_final_intact 红在 `assert not part.exists()`（AssertionError）；② ENDLIST 门禁旁路 → test_playlist_without_endlist_fails_no_final_no_part 红在 `assert 200 == 502`（物化假成功），均只还原注入行。
- 关键决策与已否决方案：物化管线复用 _forward_ffmpeg_output 生成器（含 stall 超时、stderr 排干、段泵收尾、exit code 校验、finally 清理），消费端 `async for` 写文件 + `finally: await agen.aclose()`；段数对账用 pump 的 progress 计数（pump 逐段 fail-fast 下是常态化 tripwire，锁「跳段静默成功」类回归）。错误契约保留：SpaceNotReplayError→400 / VideoNotFoundError→404 / 其余 502 脱敏，在轮询时呈现（提取在后台任务内，首次 GET 一律 202）。
- 下一步唯一动作：README/ops.env/compose 文档提交 + 真实环境全链验证。

## 2026-10-14 收工：真实环境全链验证通过，卡面完成

- 当前阶段：deliverable（报告落盘，等主脑验收）
- 本段结论：真实环境（本机 uvicorn + 活体 Space 1AKEmvYqbrOKL，直连无代理）走通全链：resolve→202→12 次轮询（约 3 分钟）→200（85,112,923 B）；Range 截断 50 MB 后 `curl -C -` 续传 206，`cmp` 逐字节一致；ffprobe=aac/48000Hz/2ch/mp4/7140s；缓存命中二次 GET 0.07s 零重复提取；限速 8k/s 挂起客户端期间其他 Range 请求毫秒级返回；重启带陈旧 .part → 启动扫描回收（日志 startup scan removed 1）、final 保留。服务日志零 ERROR/Traceback。全量 544 passed，diff 923+/230-（预算 1200 内）。
- 关键决策与已否决方案：无新增（沿袭前两段）。
- 下一步唯一动作：写 report.md（回执 + 四问 + 偏差清单），收工。

## 2026-10-14 review-fix 轮：F1–F4 修复完毕，全量 551 passed

- 当前阶段：deliverable（等 gate primary 复审）
- 本段结论：gate primary round-1 四条 finding 全部修复——F2（major）：sweep 由「首次使用跑一次」改为常驻周期清理（`ensure_started` spawn `_sweep_loop`，3600s 常量、无新配置项；单轮失败 logger.error 不静默、循环不死）；F1（主脑降 P2 的廉价加固）：`startup_scan` 只回收 mtime 老于 600s 宽限期的 `.part`，README Space 节新增第 10 条「缓存目录是单进程契约」；F3：`ensure_started`/`start` 的 OSError 映射 502 固定文案（不裸抛 500）；F4：502 detail 绝对路径清洗为 basename（`_strip_internal_paths`）。新增 7 测试（StartupScanGrace 2 + PeriodicSweeper 2 + CacheFailureSurface 3），全量 551 passed。红验三处均断言失败红型：F1 注入去 grace → `assert 2 == 1`；F3 注入裸抛 → `assert 500 == 502`；F4 注入不清洗 → detail 含 tmp 路径 `not in` 断言红；均只还原注入行、还原后绿（commit 70c7c29 / 23164e0 之后注入，工作区干净）。
- 关键决策与已否决方案：周期清理用 asyncio.Task 而非 loop.call_later（feedback 字面为「常驻后台 asyncio 清理任务」；Task 版测试内 finally cancel 可干净收敛）；F3 用 `raise_server_exceptions=False` 的本地 TestClient 锁死——注入消失时 PermissionError 裸抛会表现为 500 响应而非测试 error，保住「红验红型必须是断言失败」的可核对性。无新增配置项/抽象（周期与宽限均为常量，反熵条款）。
- 下一步唯一动作：写 report.md（回执 + 四问），push，收工。

## 2026-10-14 收口轮：R2 生命周期×资源矩阵全边角修复完毕，全量 562 passed

- 当前阶段：deliverable（等 gate primary 复审 + 主脑验收）
- 本段结论：R2 三条 finding + 主脑两条增补检查 + 矩阵重推两项增补全部落地——R2 major：失败态迁有界注册表（cap 1024 FIFO 逐出，逐出=忘掉失败态下次重拉；done Task 连 traceback 不再滞留 _tasks，成功/取消查询时归位弹出）；R2 minor 交付竞态：实证 Starlette FileResponse 先发状态码后开文件（响应阶段无法转 202），改为 fd 前置打开（space_cache.open_final）+ 自写 _FinalDownloadResponse（单区间 Range，fd 一开 unlink 永不影响本读者；打开失败=被清理的合法状态迁移→未命中 spawn+202）；R2 minor 反投毒：_started 移到全部 init 成功后，sweeper spawn 防重。矩阵重推增补两项：A）孤儿 .part 纳入 sweep TTL（宽限期跳过的新鲜孤儿在无重启长运行进程中原本永不回收）；B）done 条目归位改查询驱动+周期推式双保险（客户端失败后不再轮询时，sweeper 每周期全量 reap，否则 traceback 滞留、cap 失效——R2 major 的马甲场景）。红验四处全断言失败红型：R2-1 `assert 1025 == 1024`、R2-2 `assert 500 == 202`、R2-3 `assert False is True`、ADD-B（done 条目滞留断言）；均只还原注入行。全量 562 passed，累计 diff 1689/2600 hard。
- 关键决策与已否决方案：交付竞态否决「端点内探测 open + 仍用 FileResponse」（探测→FileResponse 开文件的窗口防不住锁死测试的注入时序，且响应阶段异常已不可转 202）与「手写多区间 Range」（单区间覆盖断点续传与拖动，多区间回 200 全量 RFC 合法，diff 减半）；init 串行化否决「asyncio.Lock」（ensure_started 是同步函数不可 await；单线程事件循环内同步代码天然串行，跨 loop 双跑靠幂等+spawn 防重）。_tasks 表不设 cap：条目规模由活跃请求 id 数界定、done 必归位（推拉双保险），引入排队 cap 即任务队列（非目标）。
- 下一步唯一动作：push + report.md（含矩阵增补节），收工。

## 2026-10-14 R3 收敛：4 条 finding + 物化任务生命周期矩阵闭环

- 当前阶段：deliverable（窄/全量验证通过，待最终提交后复验与 push）。
- R3-1：`_materialize_to_file` 先打开 `.part` 再 spawn ffmpeg；打开失败时无子进程、drain/pump 任务或生成器需要清理。测试 `test_part_open_failure_does_not_spawn_process_or_tasks` 锁死打开失败时子进程数与残留任务均为 0。
- R3-2：`space_cache.start` 以模块常量 `_MAX_MATERIALIZATION_TASKS = 8` 限制 pending+running；先归位所有 done 项，再在同一锁内判容量。队列满抛 `SpaceCacheAdmissionError`，HTTP 层返回 `503 / X Space materialization queue is full`；已接受任务不被拒绝路径取消。
- R3-3：`start` 在创建任务前比较当前磁盘 free 与 `SPACE_CACHE_MIN_FREE_MB`；低于水位抛 `SpaceCacheAdmissionError`，返回 `503 / storage below watermark`。周期 sweep 的水位清理保留。
- R3-4：合法但不可满足区间（起点超出文件，或结束点小于起点）返回 `416 + Content-Range: bytes */<size>`；无法解析的 Range 忽略并返回全量 `200`。
- 矩阵自查：①排队/占槽：未 spawn 的 task 仅占有界队列；slot 仍按原 blocking 语义释放，不改运行中任务；②sink open 失败：先 open，故无 process/task/agen；③spawn 失败：文件上下文关闭，外层异常路径删除 `.part`，无 drain/pump/agen；④首块/分段/读取失败：已启动 agen 的 finally 清 process、drain、writer，外层失败清 `.part`；⑤sink 中途写 ENOSPC：新增 `test_sink_write_failure_closes_generator_and_reaps_tasks` 锁住 aclose 清理；⑥正常完成：stdout EOF 后 drain、writer、exit code 与段数对账通过才原子发布，现有成功与真实子进程用例覆盖；⑦运行中取消：agen finally 清进程与子任务、blocking slot 释放，新增 `CancelledError` 分支删除 `.part` 后原样重抛，测试锁住四项；⑧显式 `agen.aclose()`：现有 `test_disconnect_kills_process_and_cancels_tasks` 验证 kill 一次且 drain/writer 被取消。矩阵未留下需要扩展的并发机制。
- 矩阵额外发现：`CancelledError` 不继承 `Exception`，原 `_materialize_space` 异常清理路径不会删除取消时的 `.part`；新增专用清理并重抛，不吞取消。
- 红验：R3-1 把 spawn 提前后断言显示 `spawned=[Process ...]`；R3-2 把容量比较 `>=` 注入为 `>` 后 `202 != 503`；R3-3 把水位比较 `<` 注入为 `>` 后 `202 != 503`；R3-4 去掉 `end < start` 判断后 `206 != 416`；矩阵取消把取消分支注入为 `except Exception` 后 `.part` 仍存在。以上均为 `AssertionError`，只还原注入行。
- 真实路径命令证据：队列 probe 用实际 ASGI GET 得到首请求 `202`、第二个 ID `503 X Space materialization queue is full`、pending=1；水位 probe 用临时目录的真实 `shutil.disk_usage`，阈值临时设为 free+1MiB，返回 `503 storage below watermark`、pending=0；R3-1 测试在失败版本会启动并回收真实 Python 子进程，修复版本 spawn 数为 0。
- 验证：`uv run --extra dev pytest tests/test_twitter_space_stream.py tests/test_space_cache.py -q` → 80 passed；`uv run --extra dev pytest -q` → 568 passed。派发时主干基线查询失败，继承红仍未能判定。
- 新增/修订测试只落在 `tests/test_twitter_space_stream.py`；`tests/pytest-test-durations.json` 在当前 checkout 中不存在，未创建。
- 下一步唯一动作：最终提交后重跑全量测试，采集 git 回执，push 本卡分支并写完整报告。
