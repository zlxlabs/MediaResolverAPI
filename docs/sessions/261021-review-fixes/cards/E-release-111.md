# 任务卡：切 v1.1.1 发版（CHANGELOG + 版本号 + uv.lock）

写法指引见 docs/guides/task-cards.md

## 目标

按 `CONTRIBUTING.md`「发版流程」第 1、2 步切 v1.1.1（第 3 步合并、第 4 步打标签由主脑做）：

1. `CHANGELOG.md`：新增 `## [1.1.1] - 2026-10-21` 段（放在空的 `## [Unreleased]` 之下、`[1.1.0]` 之上），`[Unreleased]` 保持为空段。内容按 Keep a Changelog 分 `### Fixed` / `### Changed` / `### Security`，逐条写**调用方可感知**的变化，素材以 `git log v1.1.0..origin/master --merges` 中 PR #51 #52 #54 #55 的 PR 正文为准（`gh pr view <N> --json body`）：
   - #51：缓存命中不再续期过期时间（此前翻译不成功时每次命中续期 12 小时、会返回已失效直链）；翻译失败不再覆盖已有译文；中文判定按汉字个数计数（此前正常中文被判为非中文、被送去中译中）；兜底异常响应 `error` 固定为 `Internal error`，不再回显异常原文。
   - #52：API Key 改为常数时间比较，非 ASCII key 返回 401 而非 500；X Space 下载/物化文件 IO 移出事件循环。
   - #54：`APP_VERSION` 不再可被环境变量 / `.env` 覆盖；发版与恢复文档对齐 D3 实际行为（12 位短 SHA 对账等）。
   - #55：X Space final 下载不再在响应结束后多发空 body（此前每次下载服务端报 `RuntimeError` 并记 ERROR）。
   - 不写内部过程（任务卡、审查、记分卡等）。
2. `pyproject.toml` 的 `version` 改为 `1.1.1`，运行 `uv lock` 同步 `uv.lock`（应只有项目自身版本一行变化；若有其它依赖变动，报告列出并停下说明，不要提交依赖升级）。

## 非目标

- 不改任何 `app/`、`tests/`、`docker/`、`.github/` 文件；不打标签、不推标签。
- 不改 `[1.1.0]` 及更早段落。

## 基线与所有权

- **Task-Id**：
- **Verify-Mode**：local
- **Verify-Command**：make test
- **Narrow-Verify**：uv run --extra dev pytest tests/test_version.py -q
- **Diff-Lines-Target**：60
- **Diff-Lines-Hard**：150
- **阶段**：implementing
- **锁定决策**：
  - 版本号 1.1.1（只含修复，semver patch）。
  - `uv.lock` 只允许项目自身版本变化。
- **任务类型**：tests-docs
- **复杂度**：S
- **Base commit**：2f0013f1a24bf5ecefc8d3508ff7f5e35e703e89
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

## 修改边界

- **允许**：`CHANGELOG.md`、`pyproject.toml`（仅 version 行）、`uv.lock`。
- **禁止**：其余全部文件。
- **验证根默认禁止**：`.github/workflows/`（所有仓）。
- **Scope-Globs**：CHANGELOG.md pyproject.toml uv.lock
- **高风险区域**：`uv lock` 可能顺带升级依赖——只接受项目自身版本变化。

## 约束与假设

- **约束**（违反即拒收）：
  - 不提交任何依赖版本变化。
- **假设**（执行器可自行调整，调整须在 report.md 写明理由）：无

## 完成条件

- **产物入库**：本卡产生的全部落盘产物均提交到 delegate 分配的 `card/<worktree 名>` 分支，验收以该分支上的提交为准；报告中贴出 `git log --oneline -1`、`git show --stat --format= HEAD` 与 `git status --short --untracked-files=all`（确认工作区与未跟踪文件干净）的实际输出。**每完成一个能独立通过测试的单元就 commit 一次，不要攒到最后一起提交**——执行器被 RuntimeMaxSec 墙、SIGTERM 或服务端限流杀掉时，只有已提交的部分保得住，未提交的改动即使还躺在 worktree 里也不算产物（2026-08-27 一张卡 33 分钟工作量、5 文件改完未提交、零报告全丢，issue #796；2026-08-29 两张卡同时撞 90 分钟墙，中途提交过的那张完整存活，零提交的那张全部工作滞留未提交态、只能靠续修捡回）。若 pre-commit 守卫拦下提交，拆成两类处置：守卫报错自带且只收紧约束的机械处置（收紧基线、同文件等量精简、折行），执行器直接按建议处置并记入报告「闸与绕过」；放宽、越界、登记例外（上调基线、新增例外文件、改动验证根、规则预算超限）处置权归主脑，执行器把守卫完整报错原样贴进报告并就此停下，保留现场（issue #3892）。越出 Scope-Globs 先在途申请，不直接停：`delegate scope-request "$DELEGATE_DISPATCH_ID" --add GLOB --reason 理由 --evidence 证据 --planned-change 计划`，再 `delegate scope-wait "$DELEGATE_DISPATCH_ID" --seq N`，等待期间可先做不依赖该文件的工作；退出码 0 即继续并在报告「与卡面的偏差」列出批准 seq，3（拒绝）/4（超时）或命令本身报错才照旧停下报告。大型原始证据（曲线、日志、dump，单文件 >1 MiB）放仓外，仓内只留摘要 + sha256 + 取回方式，file-size 闸会拒收。
- **行为验收**：
  - `uv run --extra dev pytest tests/test_version.py -q` 通过（pyproject 1.1.1 与安装元数据一致）。
  - `git diff <base> -- uv.lock` 只有项目自身 version 行变化。
- **Accept-Check**：
  - `grep -c '^## \[1.1.1\] - 2026-10-21' CHANGELOG.md` => `1`
  - `grep -c '^version = "1.1.1"' pyproject.toml` => `1`
  - `make test` => `passed`
- **Scope 越界自检**：末次提交前跑 `card_scope_check.py --card <report 目录>/card.md --repo . --range <base>..HEAD`，红即走上方在途申请，批准后重跑至绿。
- **相关测试**：`tests/test_version.py`
- **跨发布边界不适用**：纯文档与版本元数据变更。
- **lint / typecheck / build**：`make test`
- **交付文档**：`CHANGELOG.md`
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

逐条作答：`### 踩到的坑`、`### 闸与绕过`、`### 与卡面的偏差`、`### 最贵的一步`、`### 新增测试判据`（本卡不新增测试，写「无」）。缺段与写「无」是两种事实，小标题必须出现。

## 当前状态

- **现场事实（主脑预取）**：`[Unreleased]` 为空段；`pyproject.toml:3` 为 `version = "1.1.0"`；v1.1.0 标签指向 83dc63e；PR #51 #52 #54 #55 已合入主干。
- **机理/根因陈述**：无
- **已完成**：无
- **未完成**：全部
- **关键决策**：见锁定决策
- **已否决方案**：无
- **修改文件**：无
- **测试及结果**：无
- **已知问题**：无
- **下一步唯一动作**：读四个 PR 正文写 `[1.1.1]` 段
