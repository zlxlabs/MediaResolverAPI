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
