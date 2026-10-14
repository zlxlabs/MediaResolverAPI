# DESIGN：MediaResolverAPI 发版规范化（接入 D3 自动部署 + 版本管理规范化）

- 会话：pi-lead-1791982596862-2673639（2026-10-14）
- 状态：已吸收 codex 顾问评审（结论「降级」，四条翻正条件全部落实，见 §6），随卡 A PR 进 git
- 风险档位：本仓 `risk-tier: internal`（P1 红线见 agent-config 评审纪律）

## 1. 背景与目标

现状（2026-10-14 实测）：

| 事实 | 证据 |
|---|---|
| 版本号两处硬编码且已漂移：`pyproject.toml` 与 `app/core/config.py` 都是 `1.0.0`，而 CHANGELOG Unreleased 段积累了大量 feature（X Space、视频号、download_mode…） | `grep -n version pyproject.toml app/core/config.py`；`CHANGELOG.md` |
| `git tag -l` 为空，无任何版本锚点 | `git tag -l` |
| `/health` 只返回 `{"status":"ok"}`，version 挂在根路径 `/`，与线上实际部署的 commit 无关 | `app/main.py:118-124` |
| 发版两段式纯手工：本地 `docker/push_to_acr.sh`（build→ACR，tag=40位SHA）→ `scp`+`ssh fordeal` 跑 `docker/pull_and_deploy.sh`（pull→retag→compose up→探针→失败回滚） | `docker/*.sh` |
| 生产容器 GIT_SHA=92a9c0763fec…（2026-10-14T08:17 部署），部署目录 `/srv/media-resolver-api/docker`（服务器 `/srv` 别名 → home 下真实目录，D3 字面量传入），compose 文件名 `docker-compose.deploy.yml` | `ssh fordeal 'docker exec media-resolver-api printenv GIT_SHA'`；compose label |
| 平台标准：自托管服务部署走 D3（`zlxlabs/ci-templates/build-deploy.yml`），未接入的仓才配 `deploy_targets.json`，二选一 | `gate-hub/docs/guides/platform-onboarding.md` |

目标：用户已拍板走 D3 路线。发版动作收敛为「merge 到 master 自动 build→部署→探针→失败自动回滚」，
版本语义由 git semver tag + CHANGELOG 切版流程承载，`/health` 透出真实 version + git_sha。

## 2. 关键设计事实（已逐一核实）

1. **D3 caller 参照**：`zlxlabs/one_translate_tts/.github/workflows/deploy.yml` 钉
   `zlxlabs/ci-templates/.github/workflows/build-deploy.yml@v2` + `ci_templates_ref: v2`。
   `v2` 是当前会动的 tag（2026-09-06 起全舰队 12 仓钉 @v2，@v1 冻结）。
2. **runner**：`build-deploy.yml` 的 `runner` 默认 `self`（VM201 self-hosted 池，org 级 77 台，
   labels `self-hosted,linux,x64,codex`，已核实 org runners API）。self 模式跳过 Tailscale step
   （该 step 条件 `if: inputs.runner == 'hosted'`，build-deploy.yml L218-229），直连目标机。
3. **目标机与端口**：fordeal = 100.107.95.24（tailnet IP，sshd 已绑 100.107.95.24:22）。
   本服务生产端口 8206→8000，健康口 `http://localhost:8206/health`。fordeal 的 sshd 另听
   一个内网地址:35220（本机 ssh config，不入 git），但 runner 连的是 100.107.95.24:22，known_hosts
   必须按后者采集。
4. **deploy_dir**：`/srv/media-resolver-api/docker`（服务器 `/srv` 符号别名 → home 下真实
   目录，容器 compose label 核实；D3 caller 以字面量传入——`jobs.<job_id>.with` 不支持
   secrets 上下文，见 §7-1）。
   **该目录不是 git checkout**（部署流是 scp 脚本 + 服务器侧 owned compose，见
   `docs/handoff-generic-fallback-engine.md` §7），不存在「仓内 compose 覆盖服务器」的所有权冲突。
   **坑**：D3 的 `scripts/pull_and_deploy.sh` 在 deploy_dir 内跑裸 `docker compose`（依赖默认
   发现 `docker-compose.yml`），而该目录现有文件名是 `docker-compose.deploy.yml`——接入时在
   服务器侧建**符号链接** `docker-compose.yml → docker-compose.deploy.yml`（不是复制副本：
   单一真身，零漂移；server-owned 语义不变）。
5. **首次部署安全（按 codex 评审修订）**：
   - 「零应用代码变更 ⇒ 镜像逐字节一致」**不成立**（GIT_SHA build-arg 必不同、依赖层不保证
     可复现）——正确表述是「应用代码零变更 ⇒ 行为等价，由探针实证」，不承诺探针必过。
   - D3 部署脚本 `deploy_tag` 失败（pull/retag/compose up 非零退出）**直接返回、不走回滚分支**
     （pull_and_deploy.sh L553-559）；只有探针失败才回滚。因此 deploy 前必须在服务器侧做
     **镜像快照**（当前运行容器 Image ID 打 `media-resolver-api:pre-d3-baseline` 本地安全 tag），
     作为 deploy_tag 失败时的人工恢复点（`docker tag pre-d3-baseline :latest && compose up -d`）。
   - 回滚基线预置：`.deploy-state/last_good_tag` 写入当前生产 SHA
     `92a9c0763fec37c591e399eacf18df4585c7dc4b`；`last_good_digest` 写入**核实过的** ACR digest
     （部署前 `docker manifest inspect :92a9c07…` 确认远端该 tag 存在，不预置
     「只是名字对得上」的基线）。
6. **secrets（按 codex 评审修订，占位值以源码消费路径为据，不以 required 校验为据）**：
   caller 需 7 个 repo secrets。本仓已有 `FEISHU_CI_WEBHOOK`。缺 6 个：
   - `ACR_USERNAME`/`ACR_PASSWORD`：本机 `~/.docker/config.json` 该 registry 的 auth 解出。
   - `SSH_DEPLOY_KEY`：新生成专用密钥 `d3_media_resolver_ed25519`，公钥追加 fordeal authorized_keys。
   - `KNOWN_HOSTS`：`ssh-keyscan -p 22 100.107.95.24`（runner 实连地址，见 §2.3）。
   - `TS_AUTHKEY`：**占位值**。唯一消费点是 Tailscale step，条件 `if: runner=='hosted'`
     （build-deploy.yml L218-229），self 模式整步跳过——占位值运行时不触达，不是「校验放行
     所以安全」。若未来切 `runner: hosted`，必须先换真值。
   - `CI_TEMPLATES_PAT`：**占位值**。checkout 步注释（L162-167）明确：ci-templates 迁 org 后
     public、默认 token 即可，PAT 已失效且带无效 token 反而 401 炸 checkout（canary run
     30094192249 实录），「这一步不再消费，留到 v2 一起摘」。
7. **触发时序（按 codex 评审修订）**：`workflow_dispatch` 在 workflow 文件进入默认分支前
   不可用（dispatch API 按默认分支解析 workflow），**删除「分支试跑」步骤**——deploy.yml 的
   PR 合并即首跑（push master 触发），合并后的运行就是首次全链验证。因此：
   pre-flight（服务器预置 + secrets）必须全部前置到合并之前；合并这个动作由主脑在验收后执行。
   `paths-ignore` 排除 `**.md`、`docs/**`、`.github/**`（纯文档/CI 改动不触发生产部署）。
8. **手工脚本处置**：`docker/push_to_acr.sh`、`docker/pull_and_deploy.sh` 保留为 break-glass
   应急通道（D3 不可用时人工部署），头部注释标明身份，不删除。
9. **版本单一来源（按 codex 评审修订）**：`pyproject.toml` 是唯一字面量（发版时 bump）；
   `app/core/config.py` 的 `APP_VERSION` 改从 `importlib.metadata.version("media-resolver-api")`
   取默认值（Dockerfile `uv pip install .` 装出的 dist metadata、本地 `uv run` 同步安装，两处
   都成立；PackageNotFoundError 即安装损坏，fail fast 不兜底）。它是 BaseSettings 字段，环境
   变量仍可覆盖——契约测试锁默认值路径，且卡 B 验收含**真实容器环境**实测（Unknowns 纪律：
   结论随运行上下文变化的验证在真实消费环境跑）。新增 `GIT_SHA` 配置（默认 `"unknown"`，
   镜像 build-arg 注入）。
10. **`/health` 形态**：现返回 `{"status":"ok"}`，version 在根路径 `/`。卡 B 将 `/health` 扩为
    `{status, version, git_sha}`，并同步更新精确响应断言的既有测试
    （`tests/test_translation_health.py`）。
11. **首次规范化版本号**：Unreleased 无 breaking（CORS 修的是无效组合，属 Fixed），切 **1.1.0**。

## 3. 拆卡（两卡串行）

### 卡 A（infra，M，--class big）：D3 自动部署接入

零应用代码变更。时序（全部 pre-flight 前置，合并即首跑）：

1. 服务器预置（ssh fordeal，只动本服务目录）：compose 符号链接落位（§2.4）、镜像快照
   `pre-d3-baseline`（§2.5）、ACR 回滚 tag 的 manifest 核实 + digest 记录（§2.5）、
   `.deploy-state/last_good_tag`+`last_good_digest` 预置（§2.5）、部署密钥生成与授权（§2.6）。
2. `gh secret set` 配 6 个缺失 secrets（§2.6），`gh secret list` 断言 7 个齐（含已有 webhook）。
3. 仓内改动：新增 `.github/workflows/deploy.yml`（参数见 §2）；两个手工脚本头部注释标
   break-glass 身份；本设计文档落盘 `docs/sessions/261014-d3-release/design.md`。
4. draft PR → 本地漏斗（`make test`）绿 → ready；**主脑验收合并**；merge push 自动首跑。
5. 完成标志（可命令验证，主脑合并后核）：deploy run conclusion=success（job 级，非
   aggregator reason）；`ssh fordeal 'cat …/.deploy-state/last_good_tag'` == master HEAD SHA；
   容器 `StartedAt` 晚于合并时间且镜像 `GIT_SHA` == HEAD；宿主 `curl localhost:8206/health`
   200；飞书收到成功回执（notify_on_success: true）。

回滚点：删 deploy.yml + 服务器 compose 符号链接摘除 + `pre-d3-baseline` 快照恢复 + 手工脚本
照旧可用；last_good_tag 预置保证探针失败路径有真实回退镜像。

### 卡 B（app，S，--class quick）：版本规范化 v1.1.0

依赖卡 A 全绿后开工（合入即走 D3 首次真实应用变更部署）。范围：

1. `pyproject.toml` version → 1.1.0；`config.py` APP_VERSION 改 importlib.metadata 默认值
   （§2.9）、新增 GIT_SHA 配置；`/health` 扩为 `{status, version, git_sha}`（§2.10）；启动
   日志追加 sha。
2. 测试：既有精确响应断言更新；新增契约两条——settings.APP_VERSION == dist metadata、
   /health 含 git_sha（遵守 test-discipline：最小断言、能红）。
3. `CHANGELOG.md` Unreleased → `[1.1.0] - <实际发版日>`。
4. `CONTRIBUTING.md` 增「发版流程」节：merge master 自动部署；版本唯一字面量在 pyproject；
   发版动作 = 切 CHANGELOG + bump pyproject + 合并 + 主脑 `git tag vX.Y.Z`；手工脚本仅
   break-glass。
5. README 如有 /health 响应描述则同步。
6. 完成标志：pytest 绿；本地容器（假业务 env）起服务，`curl /health` 返回 1.1.0 + 构建注入
   的 GIT_SHA（真实容器环境，§2.9）；PR 合并后 D3 自动部署绿、生产 `/health` 返回 1.1.0 +
   master SHA；主脑打 tag `v1.1.0` 并 push。

## 4. 非目标

- 不接 Infisical 迁移（生产 `.env` 已在服务器侧，D3 不碰 env，本仓 `.env` 不入 git，现状合规）。
- 不做多镜像 release lane（单镜像服务）。
- 不做 busy-lock 门禁（无长任务持锁需求；X Space 流式下载是否需要，观察后再议）。
- 不迁移 zlx-ops-sdk（已在依赖里）。
- 卡 A 不改任何应用代码（含 config.py）。

## 5. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 首次 D3 部署探针失败且无回滚基线 | 应用代码零变更 + `last_good_tag`/`last_good_digest` 预置（digest 经 manifest 核实） |
| `deploy_tag` 失败（pull/retag/up 非零退出）无自动回滚 | 部署前镜像快照 `pre-d3-baseline`，人工恢复步骤写入卡面 |
| compose 文件名不被 D3 发现 | 服务器侧符号链接 `docker-compose.yml → docker-compose.deploy.yml`（单真身，无副本漂移） |
| 服务器 compose 被仓覆盖 | 已核实部署目录非 checkout（scp 部署流 + server-owned），不成立 |
| D3 与手工双路径漂移 | 手工脚本降级 break-glass 并注释标明；不引入 `deploy_targets.json`（二选一） |
| 占位 secrets 未来踩坑 | `TS_AUTHKEY` 切 `runner: hosted` 前必须换真值；`CI_TEMPLATES_PAT` 待上游 v2 摘除契约——两条写进 deploy.yml 注释 |
| merge 后自动部署与文档 PR 节奏冲突 | paths-ignore 已排除 md/docs/.github |
| 环境变量覆盖 APP_VERSION | 契约测试锁默认值 + 卡 B 容器环境实测 |

## 6. 咨询记录

- claude-fable（拆卡评审首选顾问）：周限 429，不可用（2026-10-14）。
- codex：结论**降级**（证据等级判断，非方案否定）。四条 finding 与落实：
  1. workflow_dispatch 在文件进默认分支前不可用 → 删除分支试跑，合并即首跑，pre-flight 全前置（§2.7）。
  2. 首次部署回滚保证写得过满 + `deploy_tag` 失败无回滚分支 → 改口「行为等价由探针实证」+ 镜像快照人工恢复点 + ACR manifest 核实基线（§2.5）。
  3. 服务器 compose 所有权/漂移存疑 → 核实非 checkout，符号链接方案消除双文件漂移（§2.4）。
  4. 卡 B 漏 `/health` version 字段与既有精确断言测试 → 补进卡 B 范围（§2.10、§3 卡 B）。
  另：占位值不得以 required 校验为据 → 补源码消费路径证据（§2.6）。
  翻正条件全部满足，按纪律无需二次咨询（无不可逆动作提前，卡 A 合并由主脑在验收后执行）。
- 咨询载荷归档：`retro/consult/261014-d3-release-plan.md`（自包含版，随卡 A 或卡 B PR 进 git）。

## 7. 接入后的修订记录（首跑前）

1. **D3_DEPLOY_DIR secrets 注入方案废弃**。首版为绕 public-scan 把 `deploy_dir` 经
   `secrets.D3_DEPLOY_DIR` 注入；gate primary 实锤该写法不成立：官方 contexts 表中
   `jobs.<job_id>.with.<with_id>` 可用上下文仅 github/needs/strategy/matrix/inputs/vars，
   无 secrets——触发时报 Unrecognized named-value，部署根本不会启动。换方案：服务器侧建
   `/srv/media-resolver-api` 符号别名 → home 下真实目录（主脑预置并验证：compose 符号链接
   与 `.deploy-state` 均可达），deploy.yml 用不含 home 前缀的字面量，public-scan 可过，
   `D3_DEPLOY_DIR` secret 已删。
2. **dockerfile 默认值陷阱**。上游 push_to_acr.sh build 用 `-f "${BUILD_CONTEXT}/${DOCKERFILE}"`，
   DOCKERFILE 默认 `Dockerfile`、BUILD_CONTEXT 默认 `.`；本仓 Dockerfile 在 `docker/` 子目录，
   不传则首跑 build 必失败。deploy.yml 显式 `dockerfile: docker/Dockerfile`（build_context
   保持默认 "."）。
3. **gate primary 三条 findings 处置**：
   | # | finding | 本仓判定 | 依据/处置 |
   |---|---|---|---|
   | 1 | Dockerfile 不在仓库根，默认值 build 必失败 | 实锤，已修 | `dockerfile: docker/Dockerfile`（§7-2） |
   | 2 | workflow_dispatch 可从非 master ref 部署 | 接受不修 | 设计的补发口（design §2.7）；全舰队 caller 同款，单仓收窄破坏一致性 |
   | 3 | 首跑与手工部署/并发部署竞态 | 接受不修 | 上游 build-deploy.yml L145-147 `concurrency: deploy-${{ inputs.host }}` cancel-in-progress=false 按 host 排队 + 主机 SSH flock 双保险；手工脚本已标 break-glass 互斥（§5） |
