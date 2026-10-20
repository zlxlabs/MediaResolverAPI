# 任务卡：API Key 常数时间比较 + X Space 下载/物化文件 IO 移出事件循环

写法指引见 docs/guides/task-cards.md

## 目标

修两处问题（P2）：

1. `app/api/deps.py:19` API Key 用 `api_key != settings.API_KEY` 普通比较，存在计时侧信道。改为 `hmac.compare_digest`（两侧按 utf-8 编码为 bytes 再比，避免非 ASCII 输入抛 TypeError）。未配置 API_KEY 放行、缺失/错误返回 401 的既有行为不变。
2. `app/api/stream.py` 两处同步文件 IO 在事件循环线程执行：
   - `_FinalDownloadResponse.__call__`（约 `stream.py:1007-1013`）在 async 里循环 `self._fh.read(...)`（及 `self._fh.seek`）；
   - 物化写入（约 `stream.py:1140-1145`）每块同步调用 `space_cache._disk_free_bytes()`（statvfs）与 `sink.write(chunk)`。
   修好的样子：这些阻塞调用经 `anyio.to_thread.run_sync`（或 Starlette `run_in_threadpool`）在工作线程执行，事件循环线程不再直接调用它们；Range/206/416、Content-Length、水位检查、part→final 发布、异常时 kill ffmpeg 的既有语义全部不变。

## 非目标

- 不改 Space 物化流程结构、并发槽、缓存目录布局、水位阈值、块大小。
- 不改 `app/api/resolve.py`、`app/services/cache.py`、`app/utils/validators.py`、`app/core/config.py`、任何 `*.md`（属并行卡）。
- 不动 CHANGELOG / 版本号（主脑统一切版）。

## 基线与所有权

- **Task-Id**：
- **Fixes-Issue**：
- **Goal**：
- **Verify-Mode**：local
- **Verify-Command**：make test
- **Narrow-Verify**：uv run --extra dev pytest tests/test_auth.py tests/test_twitter_space_stream.py tests/test_space_cache.py tests/test_stream_offload_io.py -q
- **Diff-Lines-Target**：300
- **Diff-Lines-Hard**：700
- **阶段**：repairing
- **锁定决策**：
  - API Key 比较只用 `hmac.compare_digest`，不引入其它鉴权改动。
  - 文件 IO 放线程池，不引入 aiofiles 等新依赖（不改 `pyproject.toml`/`uv.lock`）。
- **任务类型**：backend-logic
- **复杂度**：M
- **Base commit**：c7b4d46988be7fa282b9cd86a08bee7dad0072ef
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

- **root_cause_group**：安全比较与异步 IO 卫生缺失
- **introduced_by_commit**：pre-existing
- **open_findings**：F13 app/api/deps.py:19 非常数时间比较；F15 app/api/stream.py:1010/1142 事件循环内同步文件 IO

## 修改边界

- **允许**：`app/api/deps.py`、`app/api/stream.py`、`app/services/space_cache.py`（仅当线程化需要调整调用形态时）、下列测试文件、本卡 progress 文件。
- **禁止**：`app/api/resolve.py`、`app/services/cache.py`、`app/utils/`、`app/core/config.py`、`*.md`（progress 文件除外）、`pyproject.toml`、`uv.lock`、`docker/`——属并行卡或主脑切版范围。
- **验证根默认禁止**：`.github/workflows/`（所有仓）。
- **Scope-Globs**：app/api/deps.py app/api/stream.py app/services/space_cache.py tests/test_auth.py tests/test_twitter_space_stream.py tests/test_twitter_space.py tests/test_space_cache.py tests/test_stream_offload_io.py docs/sessions/261021-review-fixes/progress/C-sec-io-progress.md
- **高风险区域**：物化路径 finally 中 `agen.aclose()` 的清理顺序；线程化写入后异常仍须触发 ffmpeg kill 且 part 不发布。

## 约束与假设

- **约束**（违反即拒收）：
  - 默认 TDD：先写能在 base 上变红的测试，再修。
  - 禁引入 fallback/重试/防御式 try-catch。
- **假设**（执行器可自行调整，调整须在 report.md 写明理由）：
  - 「不在事件循环线程执行」的测试可打桩文件对象 `read`/`write` 与 `space_cache._disk_free_bytes`，记录调用线程 `threading.get_ident()`，断言不等于事件循环所在线程。

## 完成条件

- **产物入库**：本卡产生的全部落盘产物均提交到 delegate 分配的 `card/<worktree 名>` 分支，验收以该分支上的提交为准；报告中贴出 `git log --oneline -1`、`git show --stat --format= HEAD` 与 `git status --short --untracked-files=all`（确认工作区与未跟踪文件干净）的实际输出。**每完成一个能独立通过测试的单元就 commit 一次，不要攒到最后一起提交**——执行器被 RuntimeMaxSec 墙、SIGTERM 或服务端限流杀掉时，只有已提交的部分保得住，未提交的改动即使还躺在 worktree 里也不算产物（2026-08-27 一张卡 33 分钟工作量、5 文件改完未提交、零报告全丢，issue #796；2026-08-29 两张卡同时撞 90 分钟墙，中途提交过的那张完整存活，零提交的那张全部工作滞留未提交态、只能靠续修捡回）。若 pre-commit 守卫拦下提交，拆成两类处置：守卫报错自带且只收紧约束的机械处置（收紧基线、同文件等量精简、折行），执行器直接按建议处置并记入报告「闸与绕过」；放宽、越界、登记例外（上调基线、新增例外文件、改动验证根、规则预算超限）处置权归主脑，执行器把守卫完整报错原样贴进报告并就此停下，保留现场（issue #3892）。越出 Scope-Globs 先在途申请，不直接停：`delegate scope-request "$DELEGATE_DISPATCH_ID" --add GLOB --reason 理由 --evidence 证据 --planned-change 计划`，再 `delegate scope-wait "$DELEGATE_DISPATCH_ID" --seq N`，等待期间可先做不依赖该文件的工作；退出码 0 即继续并在报告「与卡面的偏差」列出批准 seq，3（拒绝）/4（超时）或命令本身报错才照旧停下报告。大型原始证据（曲线、日志、dump，单文件 >1 MiB）放仓外，仓内只留摘要 + sha256 + 取回方式，file-size 闸会拒收。
- **行为验收**：
  - 配置 API_KEY 时：正确 key → 200；错误、缺失、非 ASCII key（如 `密钥`）→ 401 而非 500；未配置时放行。
  - `/api/stream/twitter_space/{id}` 命中 final 时整文件与 Range 下载字节与原文件一致；读取发生在非事件循环线程。
  - 物化写入期间 `sink.write` 与磁盘水位检查发生在非事件循环线程；水位不足仍抛 `SegmentFetchError` 且 part 不发布。
- **Accept-Check**：
  - `uv run --extra dev pytest tests/test_auth.py tests/test_stream_offload_io.py -q` => `passed`
  - `make test` => `passed`
  - `grep -c 'compare_digest' app/api/deps.py` => `1`
- **Scope 越界自检**：末次提交前跑 `card_scope_check.py --card <report 目录>/card.md --repo . --range <base>..HEAD`，红即走上方在途申请，批准后重跑至绿。
- **E2E-Assertion**：经 FastAPI TestClient 调 `/api/stream/twitter_space/{id}`（final 文件预置于临时 SPACE_CACHE 目录，只打桩 yt-dlp/ffmpeg/上游 HTTP 最外层），断言返回字节与文件一致且读取在工作线程。
- **失败可见性**：na: 本卡不新增失败路径，既有 401 与 SegmentFetchError 语义不变
- **相关测试**：`tests/test_auth.py`、`tests/test_twitter_space_stream.py`、`tests/test_space_cache.py`
- **跨发布边界不适用**：同进程内调用，无序列化发布边界。
- **lint / typecheck / build**：`make test`
- **交付文档**：无（CHANGELOG 由主脑切版时统一写）
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
  本卡 progress 路径：`docs/sessions/261021-review-fixes/progress/C-sec-io-progress.md`
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

- **现场事实（主脑预取）**：`deps.py:19` `api_key != settings.API_KEY`；`stream.py:1007-1013` async 内同步 seek/read；`stream.py:1139-1145` 每块同步 `_disk_free_bytes()` 与 `sink.write`。
- **机理/根因陈述**：见「修复卡必填」。
- **已完成**：无
- **未完成**：全部
- **关键决策**：见锁定决策
- **已否决方案**：引入 aiofiles（新依赖，线程池已足够）
- **修改文件**：无
- **测试及结果**：无
- **已知问题**：无
- **下一步唯一动作**：写 `tests/test_auth.py` 非 ASCII key 红测试与 `tests/test_stream_offload_io.py`
