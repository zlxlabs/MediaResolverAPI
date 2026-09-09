verdict: pass

# 翻译诚实失败路径第 2 轮独立审查

审查范围固定为 `f8721314950a4ae140a4b3b721eb7a4a40eb1a56..8b629809af096dd790acdedc9e3454fdf9457672`，两点 diff，不纳入审查期间新提交。依据 `docs/sessions/translation-observability/design.md`、任务卡六条不变式、以及本轮新增的两份外部证据（Web2Eagle 调用方代码、Docker HEALTHCHECK 时间线）。第 1 轮 verdict（pass，F-1 至 F-5）仅作输入；本轮不复述其内容，只做复核处置。结论：**0 P1，pass**。

## Findings（本轮新增）

| ID | P 级 | 违反的契约 | 文件与行号 | 触发路径与证据 | 一句话修法 |
|---|---|---|---|---|---|
| R2-1 | P3 | 反熵条款：冗余代码 | `app/api/resolve.py:209-211`（else 分支） | 缓存命中路径 `translate=false` 时，else 分支把 `translation_result` 重赋为与第 176-178 行初始值完全相同的 `TranslationResult(skipped_not_requested, None)`，是死赋值。 | 删除该 else 分支。 |
| R2-2 | P3 | 无法溯源到被违反的不变式（按纪律降一级）；关联 design.md 待验证前提 2 | 调用方 `web2eagle/backend/app/api/video.py:243,254-258` | 翻译失败后 Web2Eagle 把 `translated_desc=None` 写进**它自己的**缓存，TTL 内不再回问本服务；熔断恢复后的补译要等其缓存过期。属调用方行为，本服务契约（null + `translation_status` + 冷却后可补译）已提供所需信息，本仓无可修对象。 | 不改本仓；记 backlog，供 Web2Eagle 侧后续消费 `translation_status` 时参考。 |

## 第 1 轮 findings 复核（视角 3：误拒）

- **F-1（P2，lifespan 探测在 yield 前）**：本轮新证据把后果从「启动延迟」精确为「部署门禁可回滚」（见视角 2），但两问不过：①真实触发需网关 TCP 黑洞恰好发生在启动/部署窗口，且生产当前单模型配置下 30 秒探测落在两个窗口内；②后果是响亮回滚、旧镜像继续服务，非数据丢失/静默出错/崩溃。维持 P2，**不升 P1**，也不算写重。
- **F-2（P2，网络异常不进熔断/告警）**：调用方视角的新证据反而收窄其后果——超时模式下每个请求串行 30s×N，Web2Eagle 侧 `MEDIA_RESOLVER_API_TIMEOUT` 先炸，`fetch_video_info` 返回 None，任务以 `resolve_failed` 响亮失败（`video.py:250-253,664-672`），不会形成「整单成功、简介外文、无人被喊」。维持 P2。
- **F-3 / F-4（P2，测试锁定不足）**：本轮未重跑实现测试套件（卡面不要求）；差距定性不变，维持 P2。
- **F-5（P2，`translation/__init__.py` 转发层）**：本轮复查 `app/main.py:24`、`app/api/resolve.py:21-25` 仍从 `app.services.translation.openai` 直接导入，包级 re-export 依旧零消费者。维持 P2，未写重。

## 视角 1：调用方——failed + 译文 null 进入 Web2Eagle 后像什么

是**诚实降级，不是新的静默失败**。证据链：

1. 本服务失败响应：`success=true`、`translated_description=null`、`translation_status="failed"`（README 已把该契约写进公开字段表）。
2. 调用方 `video.py:254-255`：`if not translated_desc: translated_desc = video_info.translated_description` → 保持 None，不编造。
3. `eagle/client.py:270`：文件名回退到原文 `description`；`client.py:303-306`：有译文写「描述：」，无译文写「**原始描述：**」——资料库里外文简介带显式标注，与译文可区分。
4. 「无人被喊」在服务端已被拆：熔断开 → Sentry 固定指纹告警（不变式 4）+ `/health/translation` 独立状态（不变式 5）。调用方暂不读 `translation_status` 是 design.md 待验证前提 2 明列的既有事实，告警是唯一喊人通道这一点在 F-2 的复核中已按上表处置。

契约充分性结论：本服务对调用方暴露的信息足够（诚实 null、状态枚举、可补译的缓存语义）；调用方怎么用是 Web2Eagle 的事，且 design 非目标明确「不改 Web2Eagle、不跨仓」。

## 视角 2：运行时误伤——HEALTHCHECK 时间线会不会判容器不健康

实测事实（非再读 main.py）：uvicorn 0.51.0 `Server.startup()`（`.venv/.../uvicorn/server.py:103-105`）**先 await lifespan 再 `create_server`（server.py:142）**——探测期间 8000 端口根本未监听，`/health` 是 connection refused。

时间线（`docker/Dockerfile:26-27`：start-period=10s、interval=30s、timeout=5s、retries=3；探测上界 = 30s×N 模型串行，httpx 单请求超时 30s）：

- N=1（生产现状，`OPENAI_MODEL_FALLBACKS` 默认为空）：端口 ≤~30s 打开；start-period 内的失败不计入 retries，t=40s 首次计次检查即成功 → 永远不会 unhealthy。
- N=2/3：t=40/70s 计次失败 1-2 次后端口打开 → 到不了连续 3 次。
- N≥4 且网关黑洞：t≈100s 被判 unhealthy，但 `docker-compose.yml:15` 与 `docker-compose.deploy.yml:16` 均为 `restart: unless-stopped`——Docker 的重启策略不消费健康状态，无 autoheal/swarm，容器继续跑，探测结束后下一次检查自愈。

部署侧门禁（`docker/pull_and_deploy.sh:58-62`）：`/health` 12×5s=60s 窗口，超窗 `fail_with_rollback` 回滚旧镜像并 exit 2。探测 >~55s（N≥2 + 网关黑洞）时部署会**响亮失败并回滚**，旧镜像继续服务——这是部署门禁按设计工作，不是运行时不健康。

P1 两问：①真实使用下触发？需「网关 TCP 黑洞」且恰在启动/部署窗口；生产单模型配置下即使黑洞也在两个窗口之内。②触发后后果可接受？最坏为短暂 unhealthy 标记（无消费者、自愈）或一次响亮回滚；探测有 httpx 超时上界不会卡死 lifespan，无崩溃循环、无数据损失。两问都不过 → 不是 P1。F-1 维持 P2。

## 视角 4：熵增（只看第 1 轮未点名的）

- `probe_translation_upstream`：含真实逻辑（失败且熔断未开才开熔断），消费者为 lifespan 与 `test_translation_health.py:107-118` 的 monkeypatch 断言，非转发-only 层，不算熵。
- `TranslationResult`/`TranslationStatus`：resolve、main、测试、README 公开契约多端消费，成立。
- `OPENAI_MODEL_FALLBACKS`/`TRANSLATION_CIRCUIT_COOLDOWN_SECONDS`/`TRANSLATION_STARTUP_PROBE`：均对应 design.md 要点 3/4/5 且进 .env.example 与 README，无凭空配置。
- 唯一新增熵是 R2-1 的死赋值（P3）。

## Backlog

- F-1：探测移出 yield 前关键路径并限总时长（本轮补充：同时消除部署门禁误回滚面）。
- F-2：网络异常与模型不可用统一进熔断/告警语义。
- F-3 / F-4：补启动探测正、负路径断言。
- F-5：删 `translation/__init__.py` 无消费者 re-export。
- R2-1：删 resolve.py 缓存路径 else 死赋值。
- R2-2：跨仓观察项——Web2Eagle 缓存 None 译文且不读 `translation_status`，供其侧后续参考；本仓无动作。

## 约束复核

本轮未改任何生产/测试代码，未做红验（卡面明确不跑实现测试套件，且无新增断言需验证恒真性）；新增文件仅本 verdict。

红验安全（固定条款，原样保留）：凡按「改坏生产代码 → 确认测试红 → 还原」验证断言恒真性的红验，改坏前必须先 commit（或至少 stash）同文件里已验证的真修复；还原只许还原刚改坏的那一处，禁止整文件 `git checkout -- <file>`。

红验有效性（固定条款，原样保留）：反向验证的转红输出必须原文贴进报告，且红的类型必须是断言失败（AssertionError / 明确的期望-实际对比）。

反熵条款（固定条款，原样保留）：禁止顺手新增抽象。
