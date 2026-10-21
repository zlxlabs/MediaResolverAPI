# 任务卡：X Space final 下载不再在响应结束后多发空 body + 清理指向已删除脚本的 break-glass 引用

写法指引见 docs/guides/task-cards.md

## 目标

1. **ASGI 响应结束后多发空 body**：`app/api/stream.py` `_FinalDownloadResponse.__call__` 循环里最后一块以 `more_body=remaining > 0`（即 False）发出后，循环外又无条件 `await send({"type": "http.response.body", "body": b"", "more_body": False})`。ASGI 规范禁止响应完成后再发消息；TestClient 已打出 `Received "http.response.body" after response completed.`。
   - 先在**真实 uvicorn** 下实证后果：起本地 uvicorn（临时端口、临时 SPACE_CACHE 目录预置 final 文件、不连外网），curl 整文件与 Range 各下一次，记录服务端日志是否出现异常/报错行，原文贴进报告。
   - 修好的样子：每个响应恰好发一次 `more_body=False`（含 length=0、文件提前读空等边界），整文件/Range/416 行为与字节不变；测试断言 ASGI send 序列中 `more_body=False` 恰出现一次且其后无任何消息。
2. **break-glass 旧引用**：`.github/workflows/deploy.yml:4` 头注释写「break-glass 恢复见 docs/sessions/261014-d3-release/design.md §2.8（git show a0cd8bf 取回）」，`docs/sessions/261014-d3-release/design.md` 约第 73-74、109-110 行仍写通过 `git show a0cd8bf` 取回已删除手工脚本。现行流程以 `CONTRIBUTING.md`「发版流程」段为准（PR #54 改写为 `gh workflow run deploy.yml --ref master` 补发 + 依据 ci-templates `pull_and_deploy.sh@v2` 的服务器侧恢复）。
   - `deploy.yml:4` 改为指向 `CONTRIBUTING.md` 发版流程段，去掉 `a0cd8bf`；只改这一行注释，不动任何 YAML 键值。
   - 历史设计文档不改写原文：在 §2.8 段首与涉及 `a0cd8bf` 的位置旁加一行「已废止：现行恢复流程见 CONTRIBUTING.md 发版流程」标注。

## 非目标

- 不改 `_FinalDownloadResponse` 的 Range 解析、块大小、线程化读取（PR #52 已落地）。
- 不改 `CONTRIBUTING.md`（属 PR #54）、不改 deploy.yml 除第 4 行注释外的任何内容。
- 不动 CHANGELOG / 版本号（主脑统一切版）。

## 基线与所有权

- **Task-Id**：
- **Verify-Mode**：local
- **Verify-Command**：make test
- **Narrow-Verify**：uv run --extra dev pytest tests/test_stream_offload_io.py tests/test_twitter_space_stream.py tests/test_final_download_asgi.py -q
- **Diff-Lines-Target**：150
- **Diff-Lines-Hard**：400
- **阶段**：repairing
- **锁定决策**：
  - deploy.yml 只改第 4 行注释，YAML 语义零变化（`git diff` 只许出现注释行）。
  - 历史设计文档只加废止标注，不删改原文。
- **任务类型**：backend-logic
- **复杂度**：S
- **Base commit**：e9afd4436e36df6a2e58729d3946d5b5aef29383
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

- **root_cause_group**：ASGI 收尾消息重复；历史文档引用未随流程更新
- **introduced_by_commit**：pre-existing
- **open_findings**：D1 stream.py _FinalDownloadResponse 末块后多发空 body（C 卡执行器报出）；D2 deploy.yml:4 与 261014 design.md §2.8 指向已删除脚本（B 卡执行器报出）

## 修改边界

- **允许**：`app/api/stream.py`（仅 `_FinalDownloadResponse`）、`tests/test_final_download_asgi.py`（新建）、`tests/test_stream_offload_io.py`、`.github/workflows/deploy.yml`（仅第 4 行注释）、`docs/sessions/261014-d3-release/design.md`（仅加废止标注）。
- **禁止**：`CONTRIBUTING.md`、`README.md`、`CHANGELOG.md`、`app/core/`、`app/services/`、`pyproject.toml`、`uv.lock`、`docker/`。
- **验证根默认禁止**：`.github/workflows/`（所有仓）。
- **验证根豁免**：.github/workflows/
  （豁免范围仅 `.github/workflows/deploy.yml` 第 4 行注释：头注释指向已废止的 break-glass 流程，需改为指向现行文档；不得改动任何键值或其它 workflow 文件。）
- **Scope-Globs**：app/api/stream.py tests/test_final_download_asgi.py tests/test_stream_offload_io.py .github/workflows/deploy.yml docs/sessions/261014-d3-release/design.md
- **高风险区域**：`deploy.yml` 是生产部署入口；改动只能是注释。

## 约束与假设

- **约束**（违反即拒收）：
  - 默认 TDD：先写能在 base 上变红的测试，再修。
  - 真实 uvicorn 实证只在本机临时端口进行，不连生产、不 SSH、不触发 workflow。
  - 禁引入 fallback/重试/防御式 try-catch。
- **假设**（执行器可自行调整，调整须在 report.md 写明理由）：
  - 测试可用自定义 ASGI `send` 记录消息序列直接驱动 `_FinalDownloadResponse`，也可经 TestClient。

## 完成条件

- **产物入库**：本卡产生的全部落盘产物均提交到 delegate 分配的 `card/<worktree 名>` 分支，验收以该分支上的提交为准；报告中贴出 `git log --oneline -1`、`git show --stat --format= HEAD` 与 `git status --short --untracked-files=all`（确认工作区与未跟踪文件干净）的实际输出。**每完成一个能独立通过测试的单元就 commit 一次，不要攒到最后一起提交**——执行器被 RuntimeMaxSec 墙、SIGTERM 或服务端限流杀掉时，只有已提交的部分保得住，未提交的改动即使还躺在 worktree 里也不算产物（2026-08-27 一张卡 33 分钟工作量、5 文件改完未提交、零报告全丢，issue #796；2026-08-29 两张卡同时撞 90 分钟墙，中途提交过的那张完整存活，零提交的那张全部工作滞留未提交态、只能靠续修捡回）。若 pre-commit 守卫拦下提交，拆成两类处置：守卫报错自带且只收紧约束的机械处置（收紧基线、同文件等量精简、折行），执行器直接按建议处置并记入报告「闸与绕过」；放宽、越界、登记例外（上调基线、新增例外文件、改动验证根、规则预算超限）处置权归主脑，执行器把守卫完整报错原样贴进报告并就此停下，保留现场（issue #3892）。越出 Scope-Globs 先在途申请，不直接停：`delegate scope-request "$DELEGATE_DISPATCH_ID" --add GLOB --reason 理由 --evidence 证据 --planned-change 计划`，再 `delegate scope-wait "$DELEGATE_DISPATCH_ID" --seq N`，等待期间可先做不依赖该文件的工作；退出码 0 即继续并在报告「与卡面的偏差」列出批准 seq，3（拒绝）/4（超时）或命令本身报错才照旧停下报告。大型原始证据（曲线、日志、dump，单文件 >1 MiB）放仓外，仓内只留摘要 + sha256 + 取回方式，file-size 闸会拒收。
- **行为验收**：
  - 整文件、Range、length=0 三种情形下 ASGI send 序列中 `more_body=False` 恰出现一次且为最后一条消息。
  - 修复前后真实 uvicorn 日志对照贴进报告（修复后无相关异常/报错行）。
  - `git diff <base> -- .github/workflows/deploy.yml` 只有第 4 行注释变化。
- **Accept-Check**：
  - `uv run --extra dev pytest tests/test_final_download_asgi.py -q` => `passed`
  - `make test` => `passed`
  - `grep -c 'a0cd8bf' .github/workflows/deploy.yml` => `0`
- **Scope 越界自检**：末次提交前跑 `card_scope_check.py --card <report 目录>/card.md --repo . --range <base>..HEAD`，红即走上方在途申请，批准后重跑至绿。
- **E2E-Assertion**：真实 uvicorn 进程服务 `/api/stream/twitter_space/{id}`（final 文件预置于临时 SPACE_CACHE），curl 下载字节与文件一致且服务端日志无 ASGI 违规报错。
- **失败可见性**：na: 本卡消除一条协议违规，不新增失败路径；既有 416 与读空分支语义不变
- **相关测试**：`tests/test_stream_offload_io.py`、`tests/test_twitter_space_stream.py`
- **跨发布边界不适用**：同进程 ASGI 调用，无序列化发布边界。
- **lint / typecheck / build**：`make test`
- **交付文档**：无
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

逐条作答：`### 踩到的坑`、`### 闸与绕过`、`### 与卡面的偏差`、`### 最贵的一步`、`### 新增测试判据`。新增测试判据每行格式**严格**为 `- tests/xxx.py：改坏 app/xxx.py:行 会红；对应卡面 <断言摘要>`，路径**不加反引号**（预检按字面量解析），红验原文另起代码块贴在行下。缺段与写「无」是两种事实，小标题必须出现。

## 当前状态

- **现场事实（主脑预取）**：PR #52 已合入（`0bb8b8c`），`_FinalDownloadResponse` 读取已线程化；循环外仍有无条件空 body 收尾；`deploy.yml:4` 仍含 `a0cd8bf`。
- **机理/根因陈述**：见「修复卡必填」。
- **已完成**：无
- **未完成**：全部
- **关键决策**：见锁定决策
- **已否决方案**：无
- **修改文件**：无
- **测试及结果**：无
- **已知问题**：无
- **下一步唯一动作**：写 `tests/test_final_download_asgi.py` 的 send 序列红测试
