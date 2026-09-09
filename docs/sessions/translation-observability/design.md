# DESIGN-note：翻译失败不得冒充成功

## 目标

`POST /api/resolve` 在翻译挂掉时仍然能解析视频，但**不能再把原文当成译文、也不能再把整单说成「已译」**。网关上某个模型名字被删掉时，自动换名单里的下一个；名单全灭时熔断翻译子系统，对外给出独立状态，并留下一条能动手的告警。

## 非目标

- 不把整次 `/api/resolve` 改成失败（解析视频是主功能）。
- 不上「翻译失败就让接口报错」的开关。
- 不改 Web2Eagle、不跨仓。
- 不把翻译搬出本服务。
- 不为翻译复用视频源那套降级引擎。
- 不改 Docker `HEALTHCHECK`（继续只探 `GET /health`）。
- 不在本卡把 Uptime Kuma 接到新健康口（接口就绪即可，接线是合并后的运维）。
- 不把 `deepseek-v4-flash` 的思考链配置写进本卡（当前请求形状已能出译文）。

## 为什么不是分区 / 删除 / 约定

- **分区**：翻译可以只放在 Web2Eagle。不行——本服务接口默认 `translate=true`，Web2Eagle 已经消费 `translated_description`，至少还有一个解析调用方。搬出去是改产品边界，不是修「失败装成成功」。
- **删除**：可以关 `TRANSLATION_ENABLED`。那是截肢式止血，用户已经用换模型把翻译救回来了，本卡要的是翻译留下但不再撒谎。
- **约定**：「请看错误日志 / 请看 GlitchTip」已经失败一个月。Web2Eagle 用「有译文用译文，没有就用原文」，资料库里外文简介看起来像合法结果。约定不能代替运行时诚实状态和熔断。

## 方案要点与已否决方案

- **要点 1：译文只在成功时存在。** 翻译返回结构化结果。`translated_description` 仅在状态 `ok` 时有值；失败时为 `null`，禁止把原文塞进译文栏。缓存同样：视频元数据可存，假译文不能存。
- **要点 2：缓存命中后仍可补译。** 命中缓存且简介非中文、译文为空、熔断未开时，再试翻译并回写缓存。熔断打开则不再打上游，直接带 `failed` 返回视频。
- **要点 3：模型名单，而不是单昵称。** `OPENAI_MODEL` 是第一个；`OPENAI_MODEL_FALLBACKS` 逗号分隔备用。一次请求按名单依次试。某名字返回「模型不存在 / 无可用渠道」则本冷却期内跳过它，避免每个视频都先挨一次 503。
- **要点 4：熔断在翻译子系统，不在整单。** 名单全灭 → 打开熔断（默认 900 秒）→ 冷却内零上游调用。冷却结束从名单头再试。状态活在 `TranslationService` 单例上（当前单容器），不为它上 Redis。
- **要点 5：启动探测可选且可关。** 默认开；测试必须关掉，避免 `TestClient` 打到真网关。探测失败只打开熔断，不阻止进程启动。
- **要点 6：一条可行动告警 + 独立健康口。** 熔断打开时向 GlitchTip（`sentry_sdk.capture_message`）打一条固定指纹事件，文案写清「去网关看渠道 / 改模型名单后重启」。`GET /health/translation` 只返回 `ok | disabled | unavailable`，**不**作为 Docker 存活探针。上报失败 fail-open，不影响解析。

- **已否决：整单 fail-loud。** 包装事故不应停产。
- **已否决：只加观测不加诚实缓存。** 修完网关仍会缓存 12 小时假译文。
- **已否决：只加接口字段等 Web2Eagle 去喊。** 下游会用原文顶上，人眼仍像没事。
- **已否决：复用视频降级引擎。** 领域不同，为翻译造通用引擎是新抽象且没有第二个消费者。
- **已否决：给健康口当 Docker HEALTHCHECK。** 翻译挂了会把整个解析容器重启。

## 关键不变式

1. [实测] 上游全部返回 503 `model_not_found` 时：`success=true`，`translated_description is null`，且不等于原文。锁在 `tests/test_resolve_translation.py`。
2. [实测] 上述失败不得把原文写入 `video_cache.translated_desc`。锁在同一文件。
3. [实测] 第一个模型 503 `model_not_found`、第二个 200：译文成功，第二次同进程请求不再打第一个模型。锁在 `tests/test_translation_openai.py`。
4. [实测] 名单全灭后打开熔断：冷却内零上游调用；只上报一次 `translation_upstream_unavailable`。锁在同一文件。
5. [实测] `GET /health/translation` 在熔断打开时 `status=unavailable`；`GET /health` 仍 `{"status":"ok"}`。锁在 `tests/test_translation_health.py`。
6. [实测] 启动探测在 `TRANSLATION_STARTUP_PROBE=false` 时不发 HTTP。锁在 conftest + health/probe 测试。

## 待验证前提

1. [推断] GlitchTip 的 `media-resolver-api` 项目能收到 `capture_message`（SDK 已接入，但 loguru 错误从未进过该项目）。本卡用 mock 锁「调用了 capture_message」；生产是否进项目在合并部署后由主脑用白名单字段核对，不作为本卡完成条件。
2. [推断] Web2Eagle 暂时不读 `translation_status`。本卡不依赖它来发现故障。

## 验收路径

1. 入口：`POST /api/resolve`（`translate=true`，非中文简介）以及 `GET /health`、`GET /health/translation`。
2. 步骤：单测按轴表全绿；用 httpx mock 覆盖 503 / 200 / 熔断，不打真网关。
3. 预期：失败时整单成功、译文为空、缓存无假译文、健康口与 `/health` 分离。主脑验收后再部署生产并决定是否把 Kuma 挂到 `/health/translation`。
