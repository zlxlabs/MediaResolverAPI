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
