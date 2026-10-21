# 任务卡：parse_url 无损化——ID 兜底策略收归路由层单点（根治 #8）

写法指引见 docs/guides/task-cards.md

## 目标

本仓 risk-tier: internal。修 issue #8 的**根因**，不是给快手/Instagram 各补一条特判。

根因：「哪些平台在 ID 提取失败时可走兜底」这一条策略存在两份副本——
`app/api/resolve.py:35` 的 `URL_FALLBACK_PLATFORMS`（及 L181 抖音 hybrid 分支）与
`app/services/url_parser.py:104-112` `parse_url` 内硬编码的 `wechat_channels` 特判；
同时 `parse_url` 返回值有损：把「平台已识别、ID 未知」与「平台未知」压成同一个 `(None, None)`。
结果：路由层三条兜底分支里只有视频号活着，快手、Instagram（#8）与抖音 hybrid（resolve.py:181）
都是死代码；而路由测试 monkeypatch 了 `parse_url`，直接喂 `(platform, None)`，测试全绿。

修好的样子：

1. `parse_url` 契约改为无损：`identify_platform` 命中即返回 `(platform, video_id 或 None)`；
   只有无效 URL / 无域名 / 平台不支持时返回 `(None, None)`。删除 `wechat_channels` 特判
   （视频号不在 `_extract_video_id` 提取器表里，无损化后自然返回 `("wechat_channels", None)`）。
2. 兜底与否**只在** `resolve.py` 决定：抖音 → hybrid；`URL_FALLBACK_PLATFORMS` → by_url 链；
   其余平台 `video_id is None` → 400（现有 L190 分支）。
3. 快手 by_url 路径不再先打一次空 `photo_id` 请求：`_fetch_kuaishou` 在 `video_id` 为空时
   在链构造阶段裁掉吃 `photo_id` 的端点（照抄 `_fetch_xiaohongshu` tikhub.py:641-645 裁 token 端点的写法）。
4. 路由测试改走真实 `parse_url`（只桩最外层 provider / TikHub HTTP 调用），让同类假绿无法复发。

## 非目标

- 不把 `v.kuaishou.com` 加进 `SHORT_URL_DOMAINS`（先验证 web_share 直吃短链；真实链接验证不过再另开卡）。
- 不折叠 `resolve.py:167-170` 的「抖音短链展开失败」分支（可选重构，本卡不做）。
- 不改 `_extract_*_id` 的任何正则/识别规则；不新增平台。
- 不改 Instagram 链（`_fetch_instagram` 两端点已统一喂 `original_url`，无需裁剪）。
- 不新增「策略注册表」「平台能力描述」之类抽象；`URL_FALLBACK_PLATFORMS` 保持现有 frozenset。
- 不动 CHANGELOG / 版本号（主脑统一切版）；不动 issue #39 相关的视频号归因逻辑。

## 基线与所有权

- **Task-Id**：
- **Fixes-Issue**：8
- **Verify-Mode**：local
- **Verify-Command**：make test
- **Narrow-Verify**：uv run --extra dev pytest tests/test_url_parser.py tests/test_resolve_by_url_fallback.py tests/test_resolve_hybrid.py tests/test_resolve_wechat_channels_url.py tests/test_tikhub_provider_kuaishou.py -q
- **Diff-Lines-Target**：300
- **Diff-Lines-Hard**：700
- **阶段**：repairing
- **锁定决策**：
  - `parse_url` 无损化；策略只在 `resolve.py` 一处。禁止在 `parse_url` 内保留或新增任何按平台名放行的特判。
  - 抖音「已识别但无 ID」的 URL（含用户主页、`/note/` 图文）**接受**从 400 变为走 hybrid（上游失败时 HTTP 200 + `success=false`）——owner 已拍板（2026-10-21），须用测试显式锁定该行为，不得加抖音路径白名单把它挡回 400。
  - 「其余平台零变化」按 **resolve 层** 判：tiktok / youtube / xiaohongshu / pinterest / facebook / twitter / twitter_space 的「可识别但无 ID」URL 经 `/api/resolve` 仍返回 400；`parse_url` 层这些平台的返回值从 `(None, None)` 变为 `(platform, None)` 是预期变化，对应 `tests/test_url_parser.py` 断言随之更新。
  - 不加 `v.kuaishou.com` 短链展开。
- **任务类型**：backend-logic
- **复杂度**：M
- **Base commit**：f3755bdd3d79
- **Depends-On**：无
- **Branch**：由 delegate 分配
- **Worktree**：由 delegate 分配
- **当前唯一写入者**：本卡执行器
- **执行器与模型**：按 envelope 回填
- **子代理 fan-out**：不限
- **执行器角色声明**（codex / grok 卡必带，原样抄）：本会话就是执行器（implementer 角色），
  全局 AGENTS.md「模型编排」段的主代理委派纪律**不适用于本卡**；不限制亲自落盘还是
  委派子代理，唯一硬约束是最终产物落在指定路径——子代理不返回就直接自己写完。
- **提交授权**：授权 commit+push
- **计划者与审查者**：主脑（Claude Opus）拆卡与验收

## 修复卡必填

- **root_cause_group**：ID 兜底策略双副本（parse_url 特判 vs resolve.py 路由）+ parse_url 有损返回 + 路由测试桩点在缺陷上游
- **introduced_by_commit**：pre-existing
- **open_findings**：F1 url_parser.py:112 已识别平台 ID 失败返回 (None,None)（快手/IG/抖音兜底死代码，#8）；F2 tikhub.py:684 快手无 ID 时 web_v2 带空 photo_id 白打一次；F3 tests/test_resolve_by_url_fallback.py:43 与 tests/test_resolve_hybrid.py monkeypatch parse_url 造成假绿

## 修改边界

- **允许**：下列 Scope-Globs 内文件与本卡 progress 文件。
- **禁止**：`app/api/stream.py`、`app/core/config.py`、`app/services/platforms/**`、`*.md`（progress 文件除外）、`pyproject.toml`、`uv.lock`、`tests/fixtures/**`。
- **验证根默认禁止**：`.github/workflows/`（所有仓）。
- **Scope-Globs**：app/services/url_parser.py app/api/resolve.py app/services/providers/tikhub.py tests/test_url_parser.py tests/test_resolve_by_url_fallback.py tests/test_resolve_hybrid.py tests/test_resolve_wechat_channels_url.py tests/test_tikhub_provider_kuaishou.py tests/conftest.py docs/sessions/261021-issue8/progress/F-url-fallback-progress.md
- **高风险区域**：
  - `parse_url` 是全平台唯一入口（生产调用方仅 `resolve.py:180`）；`test_url_parser.py` 的 twitter 主页 / space 无 ID 等用例断言会翻转，须逐条在报告列出「旧期望 → 新期望」并说明 resolve 层仍 400。
  - 缓存键、日志字段在 `video_id is None` 时的行为：视频号已走过该路径，按其现状对齐，不新增分支。

## 约束与假设

- **约束**（违反即拒收）：
  - 默认 TDD：先写能在 base 上变红的测试，再修。
  - 禁引入 fallback/重试/防御式 try-catch。
  - 路由层测试（`test_resolve_by_url_fallback.py`、`test_resolve_hybrid.py`）**禁止** monkeypatch `parse_url` / `identify_platform` / `is_short_url`；只允许桩最外层 resolver/provider 或 TikHub HTTP。
- **假设**（执行器可自行调整，调整须在 report.md 写明理由）：
  - 快手链裁剪写法照 `_fetch_xiaohongshu`；若 `_run_chain` 已有等价机制，改用之并说明。
  - 测试输入 URL 用 issue #8 评论里的构造形态：`https://v.kuaishou.com/2xAbCdE`、`https://www.kuaishou.com/f/X9aBcDeF`、`https://www.instagram.com/share/BAcDeFgHi`；抖音无 ID 用 `https://www.douyin.com/user/MS4wLjABAAAAxxxx`。`v.kuaishou.com` 不在短链表，不会触发网络展开。

## 完成条件

- **产物入库**：本卡产生的全部落盘产物均提交到 delegate 分配的 `card/<worktree 名>` 分支，验收以该分支上的提交为准；报告中贴出 `git log --oneline -1`、`git show --stat --format= HEAD` 与 `git status --short --untracked-files=all`（确认工作区与未跟踪文件干净）的实际输出。**每完成一个能独立通过测试的单元就 commit 一次，不要攒到最后一起提交**——执行器被 RuntimeMaxSec 墙、SIGTERM 或服务端限流杀掉时，只有已提交的部分保得住，未提交的改动即使还躺在 worktree 里也不算产物（2026-08-27 一张卡 33 分钟工作量、5 文件改完未提交、零报告全丢，issue #796；2026-08-29 两张卡同时撞 90 分钟墙，中途提交过的那张完整存活，零提交的那张全部工作滞留未提交态、只能靠续修捡回）。若 pre-commit 守卫拦下提交，拆成两类处置：守卫报错自带且只收紧约束的机械处置（收紧基线、同文件等量精简、折行），执行器直接按建议处置并记入报告「闸与绕过」；放宽、越界、登记例外（上调基线、新增例外文件、改动验证根、规则预算超限）处置权归主脑，执行器把守卫完整报错原样贴进报告并就此停下，保留现场（issue #3892）。越出 Scope-Globs 先在途申请，不直接停：`delegate scope-request "$DELEGATE_DISPATCH_ID" --add GLOB --reason 理由 --evidence 证据 --planned-change 计划`，再 `delegate scope-wait "$DELEGATE_DISPATCH_ID" --seq N`，等待期间可先做不依赖该文件的工作；退出码 0 即继续并在报告「与卡面的偏差」列出批准 seq，3（拒绝）/4（超时）或命令本身报错才照旧停下报告。大型原始证据（曲线、日志、dump，单文件 >1 MiB）放仓外，仓内只留摘要 + sha256 + 取回方式，file-size 闸会拒收。
- **行为验收**（均经 FastAPI TestClient 调真实 `/api/resolve` + 真实 `parse_url`）：
  - `https://v.kuaishou.com/2xAbCdE`、`https://www.kuaishou.com/f/X9aBcDeF` → 不 400；TikHub 快手链**只**收到一次 web_share 调用，参数恰为 `{"share_text": <原始 url>}`，**没有** `photo_id` 请求。
  - `https://www.kuaishou.com/short-video/3xgktqzhbkse2fe` → web_v2 先收到 `{"photo_id": "3xgktqzhbkse2fe"}`（有 ID 路径零变化）。
  - `https://www.instagram.com/share/BAcDeFgHi` → 不 400；IG 链首端点收到 `{"code_or_url": <原始 url>}`。
  - 抖音无 ID URL（如用户主页）→ 进入 hybrid；上游无可播放结果时响应 HTTP 200、`success=false`（锁定 owner 已接受的行为变化）。
  - tiktok / youtube / xiaohongshu / pinterest / facebook / twitter（主页）/ twitter_space（无 id）各一条「可识别但无 ID」URL → `/api/resolve` 仍 400。
  - 视频号分享链行为不变（既有 `test_resolve_wechat_channels_url.py` 保持绿）。
  - 结构性锁：对 `PLATFORM_DOMAINS` 覆盖的每个平台各取一条「可识别但无 ID」URL，`parse_url` 返回的平台名非 None（防止将来新平台再丢平台名）。
- **Accept-Check**：
  - `grep -c 'if platform == "wechat_channels"' app/services/url_parser.py` => `0`
  - `grep -cE 'monkeypatch\.setattr\(up, "(parse_url|identify_platform|is_short_url)"' tests/test_resolve_by_url_fallback.py tests/test_resolve_hybrid.py` => `0`
  - `uv run --extra dev pytest tests/test_url_parser.py tests/test_resolve_by_url_fallback.py tests/test_resolve_hybrid.py tests/test_resolve_wechat_channels_url.py tests/test_tikhub_provider_kuaishou.py -q` => `passed`
  - `make test` => `passed`
- **Scope 越界自检**：末次提交前跑 `card_scope_check.py --card <report 目录>/card.md --repo . --range <base>..HEAD`，红即走上方在途申请，批准后重跑至绿。
- **E2E-Assertion**：从 `POST /api/resolve`（TestClient）到 TikHub HTTP 层：只打桩最外层 HTTP 调用（或 provider 的 `_request`），断言快手短链请求实际发出的端点与参数为 web_share + `share_text=<原始 url>`。
- **失败可见性**：Unsupported: file:app/api/resolve.py:195
  （resolve.py:190 非兜底平台无 ID 仍走既有 400 分支，`log_data["error_msg"]="Unsupported URL"`；由行为验收第 5 条测试锁定）
- **相关测试**：`tests/test_resolve_wechat_channels_url.py`、`tests/test_tikhub_provider_kuaishou.py`（既有，须保持绿；后者「web_v2 必须收到 {photo_id}」在有 ID 路径保持）
- **跨发布边界不适用**：同进程调用，TikHub HTTP 为最外层桩点。
- **lint / typecheck / build**：`make test`
- **截图或探活**：无（真实快手分享短链的线上 `POST /api/resolve` 由主脑部署后验收，不在执行器范围）
- **交付文档**：无（README 平台表与 CHANGELOG 由主脑切版时处理）
- **进度存档**（固定条款，原样保留）——复杂度 M / L 的卡适用；S 卡删除本条。执行器被
  SIGTERM / 撞墙打穿时，小步提交能保住代码，但决策脉络（走到哪一步、为什么这么改、
  否决过什么、下一步是什么）会丢；对策是把进度写成随 git 提交的文件，享受与代码同级
  的保命待遇。路径约定：`docs/sessions/<会话名>/progress/<卡名>-progress.md`（与既有
  `reviews/<卡名>-verdict.md` 约定同构，进 git）。写法为追加式：每完成一个里程碑追加
  一段，与该里程碑的 commit 同一次提交；不得回头改写历史段落。每段固定四项：`当前阶段`、
  `本段结论`（≤3 句）、`关键决策与已否决方案`（无则写「无」）、`下一步唯一动作`。
  拆卡方必须把该 progress 文件的精确文件路径（非目录 glob）写进卡面 Scope-Globs——
  目录级声明会让并行卡在 scope 闸交集判定上互相挡死。结论的证据若来自无法在验收时
  原样重取的外部时变状态，证据本身（原始输出片段）必须随结论写进该段正文，不得只留
  指向 report/envelope/state 目录的指针（#2618）。
  本卡 progress 路径：`docs/sessions/261021-issue8/progress/F-url-fallback-progress.md`
- **红验安全**（固定条款，原样保留）：凡按「改坏生产代码 → 确认测试红 → 还原」验证断言
  恒真性的红验，改坏前必须先 commit 同文件里已验证的真修复；还原只许还原
  刚改坏的那一处，禁止整文件 `git checkout -- <file>`——它还原到 HEAD，会连带丢掉同文件
  未提交的真修复（issue #196，gate-hub PR #297 实测：红验还原吃掉 F1 修复，红测试孤留分支，
  且中间状态会让验收方误判执行器谎报）。
- **红绿对照禁用 git stash**（#2636）：`refs/stash` 全 worktree 共享；干净工作树 stash 成功但不入栈，随后 pop 弹出他卡条目。对照用 `git show <sha>:<path>` 覆盖或 `cp` 备份，验完只还原刚改的那一处。
- **红验有效性**（固定条款，原样保留）：反向验证的转红输出必须原文贴进报告，且红的类型
  必须是断言失败（AssertionError / 明确的期望-实际对比）——撞出 ImportError /
  AttributeError / SyntaxError 说明注入方式坏了（多为全局替换殃及无关代码，红了但不是
  被验的那个不变式），必须换最小注入重验。注入方式危险度从高到低：全局正则替换标识符 >
  删整函数 > if False: > 只改判据本身那一行；默认用最后一种（issue #609）。红验不红时先证
  注入已生效（贴替换计数或 diff），才可下「断言恒真」结论（issue #505）。
  本卡必做红验：把 `parse_url` 中 ID 失败时的返回临时改回 `return None, None`，贴出快手/IG/抖音三条路由测试的断言失败原文。
- **反熵条款**（固定条款，原样保留）：禁止顺手新增抽象——新增接口/包装层/状态/配置项时，
  报告须写明它的第二个消费者是谁，或单消费者仍必要的理由；说不出即撤。禁止为通过测试
  顺手加 fallback/兼容分支（坏味道词表见 agent-config `templates/REFACTOR-guide.md`）。
  守卫报得对就改事实或走正规豁免通道，禁止为让检查通过加假引用/拆字面量；扫描式守卫变红
  只许登记新消费者或上报主脑停下，禁止转义/编码让扫描看不见（#3088）。
- **执行器自声明 outcome**（固定条款，原样保留）：报告文件（report.md）正文中、首个
  二级标题之前，必须恰好出现一行机读 outcome（HTML 注释承载，对人类读者不可见、不污染
  排版），行首顶格、大小写敏感，从下面两行中选一行：

```
<!-- delegate-outcome: succeeded -->
<!-- delegate-outcome: failed -->
```

  该值描述的是**执行器本次任务是否完成**，**与 review 的 pass/fail verdict 正交**。
  值域只有 succeeded / failed 两个。

- **执行器在途 blocked 上行**：遇到卡面未交代清楚、无法自行决定的阻塞问题时，在 report.md
  正文首个二级标题之前写恰好一行（无阻塞时写 0 行），行首顶格、大小写敏感：

```
<!-- delegate-blocked: 这里是阻塞问题原文 -->
```

## 收尾必答（执行器在 report.md 里作答）

逐条作答：`### 踩到的坑`、`### 闸与绕过`、`### 与卡面的偏差`、`### 最贵的一步`、`### 新增测试判据`（每个新增测试文件一行：`- <测试文件仓内路径>：改坏 <生产文件路径>[:行] 会红；对应卡面 <字段名或断言摘要>`，并附红验原文）。缺段与写「无」是两种事实，小标题必须出现。

## 当前状态

- **现场事实（主脑预取）**：`url_parser.py:104-112` 仅视频号返回 `(platform, None)`；`resolve.py:35/181/185/190` 三条兜底 + 400；`parse_url` 生产调用方仅 `resolve.py:180`；`tikhub.py:684` 快手 build_params 对 web_v2 喂 `video_id`（空则为 None/""）；`_fetch_instagram` 两端点均喂 `original_url`；`tests/test_resolve_by_url_fallback.py:43-47` monkeypatch 三个 url_parser 方法。
- **机理/根因陈述**：见「目标」与「修复卡必填」。顾问意见（fable5 继续 + 5 条修订、codex 降级要求写清边界）已并入锁定决策与行为验收，原文见 `docs/sessions/261021-issue8/consult/`。
- **已完成**：无
- **未完成**：全部
- **关键决策**：见锁定决策
- **已否决方案**：① 在 `parse_url` 里给快手/IG 各补一条 `return platform, None` 特判（延续双副本，下个平台再漂）；② resolve 层在 `parse_url` 返回 None 后再调一次 `identify_platform` 补平台名（保留有损接口，多一次调用）；③ 抖音按路径白名单挡回 400（重新引入平台特判，owner 已否）；④ 本卡加 `v.kuaishou.com` 短链展开（无真实样本前不加）。
- **修改文件**：无
- **测试及结果**：无
- **已知问题**：真实快手分享短链已取得：`https://v.kuaishou.com/KU3jplGP`（owner 2026-10-21 提供）。主干实测 `identify_platform → kuaishou`、`parse_url → (None, None)`、`is_short_url → False`，与 #8 机理一致。测试里可把它当作真实形态的输入（只桩 TikHub HTTP，不打线上）；线上 `POST /api/resolve` 由主脑在部署后验收。
- **下一步唯一动作**：在 `tests/test_resolve_by_url_fallback.py` 去掉 `_patch_urlparser`，用真实 `parse_url` 写快手短链红测试。
