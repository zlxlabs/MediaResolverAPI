# 任务卡：发版文档与 D3 流水线对齐 + 版本测试有约束力 + APP_VERSION 不可被环境覆盖

写法指引见 docs/guides/task-cards.md

## 目标

修 v1.1.0 发版提交（83dc63e）引入的 10 条问题，让文档描述的就是流水线实际行为、版本测试改坏实现会变红：

1. `CONTRIBUTING.md:50` 写「失败会自动回滚到上一个可用版本」——与 `.github/workflows/deploy.yml:1-4` 头注释矛盾：只有**探针失败**自动回滚（last_good_tag）；拉取/重标记/启动阶段失败不回滚（job 红 + 人工恢复点 `media-resolver-api:pre-d3-baseline`）；对账失败与无法确认生产健康也不回滚。按上游事实改写。
2. `CONTRIBUTING.md:65` 线上对账写「`git_sha` 必须等于 master HEAD 的 commit SHA」——D3 注入的是 `${GITHUB_SHA::12}`（12 位短 SHA，ci-templates `build-deploy.yml@v2`）；且 deploy.yml `paths-ignore` 跳过 `**.md`/`docs/**`/`.github/**`，纯文档合并不部署。改为：与「最近一次触发部署的 master 提交」的 `git rev-parse --short=12` 比，并写明纯文档合并不触发部署。
3. `README.md:144`、`README.md:468` 的 `/health` 示例写「40 位 commit SHA」——改为 12 位短 SHA（本地跑为 `unknown`）。
4. `CONTRIBUTING.md:69-70` break-glass 步骤不可执行（取回的脚本成未跟踪文件被自带 dirty 检查拒绝；部署脚本只认 40 位标签而 D3 标签是 12 位）。改写为**可执行**的 D3 不可用时恢复流程，只引用当前存在的文件/命令/镜像标签（如 `workflow_dispatch` 补发、服务器上以 `pre-d3-baseline` 或已有 12 位标签镜像重标记后 `docker compose up -d`）；不得再指导取回 `a0cd8bf` 的脚本。无法在本卡验证的服务器侧命令须标注「未在本卡验证」。
5. `CONTRIBUTING.md:60` 发版清单只要求改 `pyproject.toml` 的 version——补「同步更新 `uv.lock`（`uv lock`）并一起提交」。
6. `CONTRIBUTING.md:62` `git tag vX.Y.Z` 未指定提交——改为 `git fetch origin && git tag vX.Y.Z <合并后 origin/master 的 SHA>`。
7. `CHANGELOG.md` 的 `[1.1.0]` 段漏记对外可见变更：`/health` 响应从 `{status}` 扩为 `{status, version, git_sha}`；X Space 回放两阶段物化下载（`twitter_space` 平台、`/api/stream/twitter_space`、`SPACE_CACHE_*`、`YTDLP_PROXY`、镜像新增 ffmpeg、需挂载 `/data/space-cache` 卷）；D3 自动部署接入与手工部署脚本删除。以 `git log v1.0.0..v1.1.0`（若无 v1.0.0 标签则以 1.0.0 切版提交为界）核实后补入 `[1.1.0]` 段；不新增 Unreleased 条目。
8. `tests/test_version.py:9` 契约测试拿 `settings.APP_VERSION` 与 `importlib.metadata` 自比恒真——改为与 `pyproject.toml` 的 `[project].version`（`tomllib` 读取）对照，安装元数据过期时测试必须红。
9. `tests/test_version.py:18` `git_sha` 断言只要求非空，默认 `"unknown"` 恒过——改为能锁住注入链：设置 `GIT_SHA` 环境变量后新建的 settings / `/health` 透出该值；并断言 `docker/Dockerfile` 存在 `ARG GIT_SHA` 与 `ENV GIT_SHA=$GIT_SHA`（删掉任一行测试变红）。
10. `app/core/config.py:25` `APP_VERSION` 是 BaseSettings 字段，环境变量/`.env` 的 `APP_VERSION` 能静默覆盖——改为不进入环境变量解析（模块常量或 `ClassVar` 等，形态执行器定），并加测试：设置 `APP_VERSION=0.0.0-bogus` 环境变量后版本仍等于 pyproject 版本。全仓 `APP_VERSION` 引用点（`cx references --name APP_VERSION`）保持可用。

## 非目标

- 不改 `.github/workflows/`、`docker/Dockerfile`、`docker/docker-compose.yml`、ci-templates 仓。
- 不改版本号、不新增 CHANGELOG Unreleased 条目、不打标签（主脑切 v1.1.1）。
- 不改 `docs/sessions/261014-d3-release/design.md` 历史文档；`deploy.yml` 头注释指向的 §2.8 若与新 break-glass 流程矛盾，在报告里列出，由主脑另行处置。
- 不 SSH 生产服务器、不触发 workflow_dispatch。

## 基线与所有权

- **Task-Id**：
- **Fixes-Issue**：
- **Goal**：
- **Verify-Mode**：local
- **Verify-Command**：make test
- **Narrow-Verify**：uv run --extra dev pytest tests/test_version.py -q
- **Diff-Lines-Target**：300
- **Diff-Lines-Hard**：700
- **阶段**：repairing
- **锁定决策**：
  - 12 位短 SHA 是上游既定事实，文档向它对齐，不改上游。
  - 运行时版本仍取自安装元数据（镜像内未必有 pyproject）；测试负责对照 pyproject。
  - break-glass 不得再引导取回已删除的手工脚本。
- **任务类型**：tests-docs
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

- **root_cause_group**：发版文档按已删除的手工路径书写、未对照 D3 实际行为；版本测试断言无约束力
- **introduced_by_commit**：2ea9905
- **open_findings**：F3 CONTRIBUTING.md:50；F4 CONTRIBUTING.md:65；F5 README.md:144/468；F6 tests/test_version.py:9；F7 tests/test_version.py:18；F8 app/core/config.py:25；F9 CONTRIBUTING.md:70；F10 CHANGELOG.md:10；F11 CONTRIBUTING.md:60；F12 CONTRIBUTING.md:62

## 修改边界

- **允许**：`CONTRIBUTING.md`、`README.md`、`CHANGELOG.md`（仅 `[1.1.0]` 段）、`app/core/config.py`、`tests/test_version.py`、本卡 progress 文件；若 `APP_VERSION` 改形导致其它引用点需改，走 scope-request。
- **禁止**：`app/api/`、`app/services/`、`app/utils/`（属并行卡）；`docker/`；`pyproject.toml`、`uv.lock`。
- **验证根默认禁止**：`.github/workflows/`（所有仓）。
- **Scope-Globs**：CONTRIBUTING.md README.md CHANGELOG.md app/core/config.py tests/test_version.py docs/sessions/261021-review-fixes/progress/B-release-docs-progress.md
- **高风险区域**：`app/core/config.py` 被全仓 import；改 `APP_VERSION` 形态后跑全量测试确认无引用断裂。

## 约束与假设

- **约束**（违反即拒收）：
  - 文档每条关于流水线行为的陈述，须能在 `deploy.yml` 或 ci-templates `build-deploy.yml@v2`（只读查看，`gh api repos/zlxlabs/ci-templates/contents/.github/workflows/build-deploy.yml?ref=v2` 或本机克隆）找到出处，报告逐条列出处。
  - 禁引入 fallback/防御式 try-catch。
- **假设**（执行器可自行调整，调整须在 report.md 写明理由）：
  - 本地 `.venv` 安装元数据可能是旧的 1.0.0：跑测试前用 `uv sync --extra dev` 或 `uv run` 刷新，并在报告记录新测试在**旧元数据**下确实变红（红验）。

## 完成条件

- **产物入库**：本卡产生的全部落盘产物均提交到 delegate 分配的 `card/<worktree 名>` 分支，验收以该分支上的提交为准；报告中贴出 `git log --oneline -1`、`git show --stat --format= HEAD` 与 `git status --short --untracked-files=all`（确认工作区与未跟踪文件干净）的实际输出。**每完成一个能独立通过测试的单元就 commit 一次，不要攒到最后一起提交**——执行器被 RuntimeMaxSec 墙、SIGTERM 或服务端限流杀掉时，只有已提交的部分保得住，未提交的改动即使还躺在 worktree 里也不算产物（2026-08-27 一张卡 33 分钟工作量、5 文件改完未提交、零报告全丢，issue #796；2026-08-29 两张卡同时撞 90 分钟墙，中途提交过的那张完整存活，零提交的那张全部工作滞留未提交态、只能靠续修捡回）。若 pre-commit 守卫拦下提交，拆成两类处置：守卫报错自带且只收紧约束的机械处置（收紧基线、同文件等量精简、折行），执行器直接按建议处置并记入报告「闸与绕过」；放宽、越界、登记例外（上调基线、新增例外文件、改动验证根、规则预算超限）处置权归主脑，执行器把守卫完整报错原样贴进报告并就此停下，保留现场（issue #3892）。越出 Scope-Globs 先在途申请，不直接停：`delegate scope-request "$DELEGATE_DISPATCH_ID" --add GLOB --reason 理由 --evidence 证据 --planned-change 计划`，再 `delegate scope-wait "$DELEGATE_DISPATCH_ID" --seq N`，等待期间可先做不依赖该文件的工作；退出码 0 即继续并在报告「与卡面的偏差」列出批准 seq，3（拒绝）/4（超时）或命令本身报错才照旧停下报告。大型原始证据（曲线、日志、dump，单文件 >1 MiB）放仓外，仓内只留摘要 + sha256 + 取回方式，file-size 闸会拒收。
- **行为验收**：
  - `APP_VERSION=0.0.0-bogus` 环境下 `/health` 的 `version` 仍等于 pyproject 版本。
  - 删除 `docker/Dockerfile` 的 `ENV GIT_SHA=$GIT_SHA` 行（临时，红验后还原）时 `tests/test_version.py` 变红。
  - pyproject version 与安装元数据不一致时 `tests/test_version.py` 变红。
  - `grep -n '40 位' README.md CONTRIBUTING.md` 无输出；`grep -n 'a0cd8bf' CONTRIBUTING.md` 无输出。
- **Accept-Check**：
  - `uv run --extra dev pytest tests/test_version.py -q` => `passed`
  - `make test` => `passed`
  - `grep -c '40 位' README.md` => `0`
- **Scope 越界自检**：末次提交前跑 `card_scope_check.py --card <report 目录>/card.md --repo . --range <base>..HEAD`，红即走上方在途申请，批准后重跑至绿。
- **相关测试**：`tests/test_version.py`
- **跨发布边界验收**（仅在存在跨 job/workflow/仓库的 artifact/envelope/JSON 发布边界时填写）：GIT_SHA 经 CI build-arg → Dockerfile ARG/ENV → 运行时 env 跨边界；本卡锁 Dockerfile 侧（真实 producer 文件 `docker/Dockerfile`），CI 侧 build-arg 在 ci-templates 仓，不在本卡范围，报告注明。
- **lint / typecheck / build**：`make test`
- **交付文档**：本卡即文档修复
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
  本卡 progress 路径：`docs/sessions/261021-review-fixes/progress/B-release-docs-progress.md`
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

逐条作答：`### 踩到的坑`、`### 闸与绕过`、`### 与卡面的偏差`、`### 最贵的一步`、`### 新增测试判据`（每个新增测试文件一行：`- <测试文件仓内路径>：改坏 <生产文件路径>[:行] 会红；对应卡面 <字段名或断言摘要>`，并附红验原文；本卡若只改既有测试文件，写「无」并在正文贴红验原文）。缺段与写「无」是两种事实，小标题必须出现。

## 当前状态

- **现场事实（主脑预取）**：`deploy.yml:1-4` 头注释写明仅探针失败自动回滚；`deploy.yml:9-13` paths-ignore；`config.py:25` `APP_VERSION: str = _dist_version()`；`Dockerfile:35-36` ARG/ENV GIT_SHA；主 checkout `.venv` 元数据为 1.0.0；v1.1.0 标签已在远端（指向 83dc63e）。
- **机理/根因陈述**：见「修复卡必填」。
- **已完成**：无
- **未完成**：全部
- **关键决策**：见锁定决策
- **已否决方案**：运行时读 pyproject 取版本（镜像内未必有 pyproject）
- **修改文件**：无
- **测试及结果**：无
- **已知问题**：无
- **下一步唯一动作**：改写 `tests/test_version.py` 为对照 pyproject 的红测试
