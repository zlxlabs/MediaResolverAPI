# Just One API H0 独立审查

<!-- delegate-outcome: succeeded -->

failure-visibility: p1-found

## 结论

**阻断 H0 交付：1 条 P1。** TikTok 只有 `bitrateInfo`、没有 `PlayAddrStruct` 时，真实 HTTP 入口返回 `200 / success=true / provider=justoneapi / video_url=""`。另登记 3 条非阻断 P2（Instagram 分支、YouTube 音频无效请求、快手作者），1 条未实测候选（SQLite 异常）；未修改实现。

- 固定 base：`b80e35152b74289c835d308e0c64946f1d03f135`。
- 固定 H0：`57db231a795a05c39f889ebe082ee9fda0eb5055`；`git ls-remote origin refs/pull/60/head` 实读相同 SHA。
- 规格：H0 的 `docs/sessions/261103-justoneapi/design.md`；`README.md:5` 为 `risk-tier: internal`。
- 已读 `$AGENT_CONFIG/claude/skills/review-discipline/SKILL.md` 与 `$AGENT_CONFIG/claude/skills/test-discipline/SKILL.md`，并读审查方法增补；未读实现报告、实现进度正文、咨询报告、主脑会话或主脑停机规则，未向下派发。
- OCR 前置证据采用卡面已完成的 `reviewed_fallback`：primary 超时、backup 成功；未重启付费扫描。卡面未给各条原始 severity，以下不虚构工具级别。
- 审查分支 `feat/MediaResolverAPI-261103-review-H0` 初始 HEAD 为 base，初始 `git status --porcelain=v1` 空。

## P1：不要把空的 H264 节点交给 TikTok 解析器

锚点：`app/services/adapters/justoneapi_adapter.py:178` 接受仅有码率列表的有效输入，却在 `:191` 无条件创建真值字典 `play_addr_h264`；既有 `app/services/platforms/tiktok.py:301` 因此选中空 `url_list`，没有选有效 `bit_rate`；`app/services/video_resolver.py:260` 仅判断对象真假，随后成功返回，HTTP 层缓存并回传这个空地址。

违反设计「目标」、关键不变式 1 和验收路径 2.1（成功必须有可播放地址，TikTok 两种字段均在规格内）。**P1 两问**：①会触发吗？已用真实 `/api/resolve` 消费路径、H0 入库 fixture 仅删除 `PlayAddrStruct` 复现，默认 quality 未指定；未调用收费上游，真实流量频率未知。②后果可接受吗？不可接受：有可播媒体却静默报成功并缓存空地址，客户端无法下载；覆盖配置若把 Cobalt 放后面，还会以这个假成功提前结束链。

触发：下列确定性探针输出 `''`（完整报告还保存 HTTP 入口命令及 `200/true/空地址` 输出）。后果：假成功、坏缓存，违反核心解析契约。代价：适配器内约 2–5 行局部调整，仅在 URL 非空时保留 H264 节点；无须新抽象、重试或兜底机制。

```bash
# 在 scratch-worktree.sh 绑定 H0 后，以该树为 cwd 执行；PY 为只读复用的既有解释器。
env -u PYTHONPATH PYTHONDONTWRITEBYTECODE=1 $PROJECT/.venv/bin/python - <<'PROBE'
import json
from pathlib import Path
from app.services.adapters.justoneapi_adapter import JustOneApiAdapter
p = json.loads(Path('tests/fixtures/justoneapi/tiktok_post.json').read_text())
p['data']['video'].pop('PlayAddrStruct')
v = JustOneApiAdapter().adapt(p, 'tiktok', '7267439653153003782')
print(repr(v.video_url))  # 实测 ''；列表内仍有两条可播放 URL
PROBE
```

## 八条不变式核对（锚点均指 H0）

| 卡面不变式 | 实现锚点 | 测试锚点与裁决 |
|---|---|---|
| 1 空 token 整源不存在 | `video_resolver.py:95`；`providers/justoneapi.py:109`；`wechat_channels_media.py:57` | `tests/test_justoneapi_provider.py:110,129,145`；`test_stream_wechat_justoneapi.py:194`，通过；默认与覆盖链均过滤。 |
| 2 默认链与覆盖 | `video_resolver.py:103,140` | `test_justoneapi_resolver_chain.py:60,68,77,83`，通过；两条默认链断言在 base 有效转红。 |
| 3 锁定单枪、已有 ID 不转分享 | `providers/justoneapi.py:46,112,185,200` | `test_justoneapi_provider.py:169,189,224,237,276,295`；传输边界实际 URL/query 已断言，fixture 入库。七平台详情一次；视频号已有 objectId 只 download，只有 sph 时 basic→download；无标题/指标/评论请求。 |
| 4 JOA 不得终态短路 | `providers/justoneapi.py:135,150`；`video_resolver.py:299` | `test_video_resolver_terminal.py:59,93`，通过；真实 JOA provider 业务失败后继续 Cobalt，未抛 TerminalError。 |
| 5 视频号 XOR/明文与客户端门口 | `wechat_channels_media.py:63,67`；`api/stream.py:474,492,541,1242`；adapter `:215` | `test_stream_wechat_justoneapi.py:266,283,294,307,332`，以及原加密流/direct 套件均通过；响应无 `media.example.test`。`/direct` 相对本服务流路径符合设计第 7 点。 |
| 6 resolve→stream 两枪 | `wechat_channels_media.py:25,39,63`；`api/resolve.py:284` | `test_stream_wechat_justoneapi.py:159,332`，通过；真实缓存写读边界复用 objectId，实际记录恰为 basic-info×1、download-url×1。 |
| 7 异步、无探测 token、行数/零改动 | `providers/justoneapi.py:129` 为 `httpx.AsyncClient`；配置 token 缺省空 | provider 传输边界测试通过；diff 机械核对 stream 为 `+23/-25`（净 -2）；指定旧测试与 kuaishou service 的 diff 均空。未发现探测凭据，未读取真实凭据；SDK/凭据/行数属静态核对，无独立运行测试锁。 |
| 8 平台边界及 audio 不假成功 | provider 支持集合 `:38`；adapter `:68`；resolver `:188` | `test_other_platforms_unchanged`、`test_adapter_unsupported_platform_returns_none`、`test_adapter_audio_mode_not_supported` 通过；YouTube audio 额外探针最终 VideoResolverError、无空地址成功，但有一次无效 JOA 详情请求（P2）。 |

## OCR 八条独立裁决

各条均独立判断严重度与交付阻断；工具原 severity 未随卡提供。「两问」依次为真实触发证据、后果是否可接受。

| OCR 项 | 代码与实测 | P1 两问；规格关联 | 独立判定 / 阻断 |
|---|---|---|---|
| 1 token/resolver 单例泄漏（输入已 refuted） | `tests/conftest.py:56` 还原 token；接口夹具 `test_justoneapi_resolver_chain.py:207,279` 及 stream E2E `:367` 清单例；混合顺序窄测 187 passed | 指控路径未复现，清理点存在；无已证后果；不变式 1 | 同意 refuted / 否，不能称「本轮已修」。 |
| 2 TikTok bitrate-only 空地址 | adapter `:191` → TikTok `:301`；真实 HTTP 实测 200/true/空串 | 会（有效字段分支的 HTTP 探针）；不可接受，假成功；设计关键不变式 1 | **P1 / 是**，见上文。 |
| 3 IG versions-only | adapter `:206` 放行但未映射，Instagram `:150` 只读 video_url；fixture 删 video_url 后 adapt 为 None | 在本地生产适配器复现，未量真实上游频率；显式失败且链可继续，internal 可作为已知缺口接受；设计方案点 6 | P2 / 否，登记缺口，不能以全平台夹具绿宣称此分支支持。 |
| 4 刷新媒体后 XOR 翻转（输入已 refuted） | stream `:397` 仅 offset≥131072 可刷新；crypto `:206` 此后原样返回；既有流测试通过 | 所述 XOR 损坏路径不成立；无该后果；不变式 5 | 同意 refuted / 否，未声称供应商文件内容必一致。 |
| 5 SQLite 查询异常无 except | `wechat_channels_media.py:34` 查询会抛错、`:48` finally 关会话；未制造或观测真实锁冲突 | 未实测，不能把猜测当 P1；若发生是可见 500、没有多打一枪，内部诊断可接受；不变式 6 仅正常缓存路径 | P2 候选 / 否；不建议无依据 catch/fallback，修复机制代价不成立。 |
| 6 YouTube audio 无效 JOA 请求 | resolver `:193` 让 YouTube audio 走普通链；provider 忽略 mode，adapter `:68` 丢弃；桩探针记录详情×1 后 VideoResolverError | 本地真实 resolver 路径复现，实际收费未发生；一枪无效请求可内部接受，最终失败可见，不构成音频假成功；设计 audio 非目标 | P2 / 否；如后续治理，调用前排除该源约 1 分支/2–4 行，无须新机制。 |
| 7 direct 相对路径 | stream `:1244` 回本服务流路径；`test_direct_joa_plaintext_returns_service_url` 通过 | 会返回相对路径；这是设计方案点 7 明文契约，按同源 URL 解析即可；不变式 5 | 不成立 / 否；README 概括不足以反转本轮明确设计。 |
| 8 快手作者/计数 | adapter `:139` 保留 user_name；Kuaishou `:106` 读 userName；原入库 fixture 直接 adapt 得 author_name='' | 作者缺失已复现，计数夹具无该字段、未实测；播放正确，internal 可接受缺元数据；设计目标元数据，非关键下载不变式 | 作者 P2、计数仅候选 / 否；局部映射约 1–3 行，不改被锁定 service。 |

Instagram：触发为裁剪 fixture 后走真实 adapt；后果是丢掉已有可播列表并明确报失败；可选局部映射约 3–6 行，不引入网络兜底。上述 P2 接受登记，不派修复机制；本轮唯一阻断为 TikTok P1。未发现额外需要新增状态或通用抽象的独立 finding。

## 验证、红验与环境边界

路径变量：`PROJECT` 为主仓根，`AGENT_CONFIG` 为规则源仓根，`RUNTIME` 为本派发规则 runtime 根；完整绝对路径保存在派发报告。入库版本不公布本机账户路径。

- H0 scratch：`$HOME/scratchpad/vt-rwrgGa/worktree`，`git rev-parse HEAD` = H0 全 SHA。
- base scratch：`$HOME/scratchpad/vt-V9Yxlz/worktree`，`git rev-parse HEAD` = base 全 SHA。
- 两树均由 `$RUNTIME/scripts/git/scratch-worktree.sh` 管理，脚本已自动回收；路径不存在且不再注册，均已断言。
- 使用既有主仓 venv 的解释器只读运行，不安装依赖、不软链 venv；`env -u PYTHONPATH PYTHONDONTWRITEBYTECODE=1`，每树显式 cwd；`app.__file__` 均在对应 scratch，禁止串回审查树。
- 窄测：`python -m pytest tests/test_justoneapi_provider.py tests/test_justoneapi_resolver_chain.py tests/test_stream_wechat_justoneapi.py tests/test_video_resolver_terminal.py tests/test_stream_wechat_channels.py tests/test_stream_wechat_channels_direct.py -q` → **187 passed, 113 warnings in 3.32s**；`git diff --check base H0` 通过。
- 红验从 H0 测试文件原样抽取 `test_default_chain_four_platforms_tikhub_then_justoneapi`、`test_default_chain_cobalt_platforms_three_sources` 及 resolver fixture。只复制测试；token fixture 用 `monkeypatch.setitem(settings.__dict__, "JUSTONEAPI_TOKEN", "redcheck-test-token")` 注入新设置，避免 base 无新字段造成无效异常；没有复制任何生产实现。
- 命令：在 H0 `python -m pytest tests/test_review_red_sample.py -q` → **2 passed**；随后 `scratch-worktree.sh <本卡仓> <base全SHA> -- bash -c 'cp <H0-scratch>/tests/test_review_red_sample.py tests/; env -u PYTHONPATH PYTHONDONTWRITEBYTECODE=1 <既有python> -m pytest tests/test_review_red_sample.py -q'` → **2 failed**，均为原始列表断言 AssertionError（单源缺 justoneapi、双源缺 justoneapi），无 ImportError/SyntaxError。完整可重建命令见派发报告。
- 夹具检查：传输测试断言 provider 发出的 query，流测试断言 HTTP 序列化响应和实际字节，E2E 穿过真实缓存写读；但 TikTok/IG 的并存字段夹具掩盖了替代字段分支，这正是已登记缺陷。未以测试全绿替代输入覆盖审查。
- 环境依赖：本地裸 shell + HTTP TestClient 已跑；未调用生产/收费上游、未跑远端 CI/systemd，因此不声明生产 token、上游实际分布或远端 gate 已验证。继承红：主干基线不可用，未能判定；新红：两条故意 base 红验；H0 窄测无新红。
- 熵增核对：新增 provider/adapter 接入现有多源协议；媒体模块由 stream/direct 两入口共用并承担缓存与源选择；with_joa 八处调用，三个设置均有生产消费者；无新增表或镜像缓存。未发现无第二消费者的通用化机制。

### 新增测试判据
无

## 踩到的坑

整份新测试文件依赖新模块，直接复制到 base 会导入失败；已抽取原行为断言并只做测试 fixture 兼容。首次 HTTP 探针遇应用 lifespan 重配日志，已关闭 app 日志后重跑并保存白名单结果。

## 闸与绕过

公开内容扫描首次因本机绝对路径拒绝推送；确认远端分支不存在后，入库路径改用环境变量，完整路径留本地报告。无绕过；未改 PR、未标 ready、未部署。

## 与卡面的偏差

无实现修改；只抽样测试做 base 红验，未重跑全量/远端套件。排除进度正文等实现自述；对非执行规格文档不做内容审查。未建立新测试文件入库。

## 最贵的一步

独立追踪适配器→平台解析器→责任链→HTTP 的假成功，并把替代字段输入送入真实消费入口；费用为零，全部上游均打桩。
