verdict: pass

# 翻译诚实失败路径第 1 轮独立审查

审查范围固定为 `f8721314950a4ae140a4b3b721eb7a4a40eb1a56..8b629809af096dd790acdedc9e3454fdf9457672`；只审该两点 diff，不纳入审查期间新提交。依据为 `docs/sessions/translation-observability/design.md` 与任务卡不变式。无 P1，故本轮 pass；以下 P2 不阻塞项进入 backlog。

## Findings

| ID | P 级 | 违反的契约 | 文件与行号 | 触发路径与证据 | 一句话修法 |
|---|---|---|---|---|---|
| F-1 | P2 | 不变式 6；设计要点 5：探测失败只打开熔断、不阻止启动 | `app/main.py:66-75`；`app/services/translation/openai.py:164-173` | 生产默认 `TRANSLATION_STARTUP_PROBE=True`（当前 `.env` 未设置该键）；`lifespan` 在 `yield` 前等待探测，单模型请求超时 30 秒，备用模型还会串行叠加。等待期间 ASGI 尚未提供 `/health`。 | 将探测移出 `yield` 前的启动关键路径，并限制探测总时长；失败仍只写熔断状态。 |
| F-2 | P2 | 不变式 4；设计要点 4、6：上游全不可用应进入独立 unavailable 状态并告警 | `app/services/translation/openai.py:130-162` | 真实网关超时/断连时异常被 catch，但模型不进入 `_dead_models`；两个模型均 `TimeoutError` 的不触网探针输出为 `result_status=failed`、`circuit_open=False`、`health_status=ok`、`alerts=[]`。响应虽诚实，健康口和告警却误报。 | 将每个模型的请求异常纳入“不可用”判定，使名单全失败时复用熔断与一次告警路径。 |
| F-3 | P2 | 不变式 6 的“探测失败打开熔断”未被测试锁死 | `tests/test_translation_health.py:104-122` | `test_startup_probe_exception_does_not_break_application` 只断言 `/health` 为 200；删掉 `app/main.py:71` 的 `translation_service.open_circuit(...)`，该测试仍可通过。 | 同一测试断言 `service.circuit_open is True` 或 `/health/translation` 为 `unavailable`。 |
| F-4 | P2 | 不变式 6 的“`TRANSLATION_STARTUP_PROBE=false` 不发 HTTP”未被测试锁死 | `tests/conftest.py:48-53`；`tests/test_translation_health.py` | autouse fixture 只改配置，没有 HTTP 计数器或禁止调用断言；实现若忽略 false 分支，测试没有确定性的负向证据。 | 增加 false 配置下的启动测试，断言探针客户端调用次数为 0。 |
| F-5 | P2 | 反熵条款：新增转发-only 层须有第二消费者 | `app/services/translation/__init__.py:1-15` | 仓内 `app` 无 `from app.services.translation import ...` 消费者；该文件仅转发 `openai.py` 的符号，未提供现有调用方需要的行为。 | 删除该层新增的 re-export，保持包入口为空，直接从实际模块导入。 |

## 四个审查视角

### 1. 正向：失败诚实性与状态正交

通过。`TranslationResult` 只有 `ok` 才携带文本，`_translation_text` 会把失败转换为 `None`；新鲜解析和缓存补译失败都会把 `translated_desc` 写成 `None`，不会把原文写入响应或 `video_cache.translated_desc`。`success=True` 与 `translation_status=failed` 分开返回，且缓存命中时会隐藏非中文或陈旧译文；未发现 P1 静默错误或数据损坏。

### 2. 反向：新增测试红验

在 base 临时 worktree 仅拷入 `tests/test_translation_openai.py`，运行两条新增测试；均以断言失败转红，未运行实现测试套件。关键输出：`AssertionError: assert 'str' == 'TranslationResult'`（基线仍返回原文字符串），以及 `AssertionError: assert False`（基线无告警 reporter）；结果为 `2 failed`。临时注入已还原，主工作树无改动。

### 3. 熔断、启动与健康口

名单全为 503 `model_not_found` 时，代码能跳过已死模型、打开进程内单例熔断、冷却内不再请求并只上报一次；`/health/translation` 与 `/health` 分离，且告警上报自身 fail-open。F-1、F-2 是启动时序和非 `model_not_found` 异常可观测性的 P2 缺口；F-3、F-4 是对应测试锁定不足。

### 4. 熵增与第二消费者

`OPENAI_MODEL_FALLBACKS`、`/health/translation`、熔断冷却和启动探测均有设计稿明确用途；`_translation_text` 同时服务新鲜解析与缓存路径，`_error_details` 同时服务错误码和模型不可用判定，单例状态由解析与健康口共享。F-5 是唯一确认的无消费者转发层。

## P1 判定

本轮无 P1。F-1/F-2 均不提 P1：真实生产路径可触发，但后果是启动延迟或健康/告警不准；翻译响应本身已返回 `failed`，不落入 internal 风险等级的“数据丢失、静默出错、崩溃、越权访问、损坏他人数据”红线。

## Backlog

- F-1：移出 lifespan 启动关键路径并补总时长边界。
- F-2：统一网络异常与模型不可用的熔断/告警语义。
- F-3/F-4：补齐启动探测正、负路径断言。
- F-5：删除无消费者的包级 re-export。

## 约束复核

红验安全（固定条款，原样保留）：凡按「改坏生产代码 → 确认测试红 → 还原」验证断言恒真性的红验，改坏前必须先 commit（或至少 stash）同文件里已验证的真修复；还原只许还原刚改坏的那一处，禁止整文件 `git checkout -- <file>`。

红验有效性（固定条款，原样保留）：反向验证的转红输出必须原文贴进报告，且红的类型必须是断言失败（AssertionError / 明确的期望-实际对比）。

反熵条款（固定条款，原样保留）：禁止顺手新增抽象。
