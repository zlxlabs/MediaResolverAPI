# 跨 harness 顾问咨询载荷（自包含版）

> 本文件 = consult-card.md + design.md + ci-templates 源码逐字摘录，合并为单文件。
> 原因：顾问沙箱只能读本仓；/tmp 与跨仓路径不可达。摘录段均标注源文件与行号，逐字未改。

=== PART 1: design.md（拆卡设计）===
# DESIGN：MediaResolverAPI 发版规范化（接入 D3 自动部署 + 版本管理规范化）

- 会话：pi-lead-1791982596862-2673639（2026-10-14）
- 状态：待用户确认拆卡边界后随首张实现卡 PR 进 git
- 风险档位：本仓 `risk-tier: internal`（P1 红线见 agent-config 评审纪律）

## 1. 背景与目标

现状（2026-10-14 实测）：

| 事实 | 证据 |
|---|---|
| 版本号两处硬编码且已漂移：`pyproject.toml` 与 `app/core/config.py` 都是 `1.0.0`，而 CHANGELOG Unreleased 段积累了大量 feature（X Space、视频号、download_mode…） | `grep -n version pyproject.toml app/core/config.py`；`CHANGELOG.md` |
| `git tag -l` 为空，无任何版本锚点 | `git tag -l` |
| `/health` 返回静态 `1.0.0`，与线上实际部署的 commit 无关 | `app/main.py:118`、`app/core/config.py:19` |
| 发版两段式纯手工：本地 `docker/push_to_acr.sh`（build→ACR，tag=40位SHA）→ `scp`+`ssh fordeal` 跑 `docker/pull_and_deploy.sh`（pull→retag→compose up→探针→失败回滚） | `docker/*.sh` |
| 生产容器 GIT_SHA=92a9c0763fec…（2026-10-14 部署），部署目录 `<srv:media-resolver-api>/docker/`，compose 文件名是 `docker-compose.deploy.yml` | `ssh fordeal 'docker exec media-resolver-api printenv GIT_SHA'`；`docker inspect` label |
| 平台标准：自托管服务部署走 D3（`zlxlabs/ci-templates/build-deploy.yml`），未接入的仓才配 `deploy_targets.json`，二选一 | `gate-hub/docs/guides/platform-onboarding.md` |

目标：用户已拍板走 D3 路线。发版动作收敛为「merge 到 master 自动 build→部署→探针→失败自动回滚」，
版本语义由 git semver tag + CHANGELOG 切版流程承载，`/health` 透出真实 version + git_sha。

## 2. 关键设计事实（已逐一核实）

1. **D3 caller 参照**：`zlxlabs/one_translate_tts/.github/workflows/deploy.yml` 钉
   `zlxlabs/ci-templates/.github/workflows/build-deploy.yml@v2` + `ci_templates_ref: v2`。
   `v2` 是当前会动的 tag（2026-09-06 起全舰队 12 仓钉 @v2，@v1 冻结）。
2. **runner**：`build-deploy.yml` 的 `runner` 默认 `self`（VM201 self-hosted 池，org 级 77 台，
   labels `self-hosted,linux,x64,codex`，已核实 org runners API）。self 模式跳过 Tailscale step
   （VM201 本身在 tailnet 内），直连目标机。
3. **目标机与端口**：fordeal = 100.107.95.24（tailnet IP，sshd 已绑 100.107.95.24:22，
   见 TTS caller 注释）。本服务生产端口 8206→8000，健康口 `http://localhost:8206/health`。
4. **deploy_dir**：`<srv:media-resolver-api>/docker`（容器 compose label 核实）。
   **坑**：D3 的 `scripts/pull_and_deploy.sh` 在 deploy_dir 内跑裸 `docker compose`（依赖默认
   发现 `docker-compose.yml`），而该目录现有文件名是 `docker-compose.deploy.yml`——接入时需在
   服务器侧落一份内容一致的 `docker-compose.yml`（server-owned，一字不差复制）。
5. **首次部署安全**：D3 部署脚本的回滚基线是 `.deploy-state/last_good_tag`，首次部署时不存在。
   缓解两条：(a) T4 纪律——deploy.yml 必须作为零应用代码变更的独立 PR 合入，新 SHA 镜像与
   生产逐字节同源，探针必过；(b) 部署前用当前生产容器 `GIT_SHA`（92a9c0763fec…）预置
   `last_good_tag`，首次部署即有真实回滚基线。
6. **secrets**：caller 需 7 个 repo secrets。本仓已有 `FEISHU_CI_WEBHOOK`、`SILO_*`。
   缺 6 个：`ACR_USERNAME`/`ACR_PASSWORD`（本机 `~/.docker/config.json` 该 registry 的 auth
   可解出）、`SSH_DEPLOY_KEY`（新生成专用密钥 `d3_media_resolver_ed25519`，公钥追加到 fordeal
   authorized_keys）、`KNOWN_HOSTS`（`ssh-keyscan -p 22 100.107.95.24`）、`TS_AUTHKEY` 与
   `CI_TEMPLATES_PAT`（runner=self 与 ci-templates 已公开的背景下运行时都不会用到；示例
   caller 注释明确 PAT「any placeholder value works」——先置占位值，workflow_dispatch 启动
   校验通过即可；若 GitHub 启动校验拒绝，升真值并问用户要 Tailscale authkey）。
7. **触发**：`on: push: branches: [master]` + `workflow_dispatch`（补发口），`paths-ignore`
   排除 `**.md`、`docs/**`、`.github/**`（纯文档/CI 改动不触发生产部署，与 TTS 一致）。
8. **手工脚本处置**：`docker/push_to_acr.sh`、`docker/pull_and_deploy.sh` 保留为 break-glass
   应急通道（D3 不可用时人工部署），头部注释标明身份，不删除。
9. **版本单一来源**：`pyproject.toml` 是唯一字面量（发版时 bump）；`app/core/config.py` 改从
   `importlib.metadata.version("media-resolver-api")` 取值（镜像内 `uv pip install .` 装出的
   dist metadata、本地 `uv run` 同步安装，两处都成立；取不到 = 装坏了，fail fast 不兜底）。
   新增 `GIT_SHA` 配置（默认 `"unknown"`，镜像 build-arg 注入），`/health` 增加 `git_sha` 字段。
10. **首次规范化版本号**：Unreleased 无 breaking（CORS 修的是无效组合，属 Fixed），切 **1.1.0**。

## 3. 拆卡（两卡串行）

### 卡 A（infra，M，--class big）：D3 自动部署接入

零应用代码变更。范围：

1. 服务器预置（ssh fordeal）：`docker-compose.yml` 落位（内容 = `docker-compose.deploy.yml`
   逐字节）、生成 `d3_media_resolver_ed25519` 密钥对 + 公钥进 authorized_keys、
   `.deploy-state/last_good_tag` 预置 `92a9c0763fec37c591e399eacf18df4585c7dc4b`。
2. 仓内新增 `.github/workflows/deploy.yml`（参数见 §2），`docker/push_to_acr.sh` 头部注释标
   break-glass 身份；随卡落盘本设计文档 `docs/sessions/261014-d3-release/design.md`。
3. `gh secret set` 配 6 个缺失 secrets。
4. 分支上 `workflow_dispatch` 试跑全链（build→ACR→SSH→compose up→探针），绿 = 流水线证明；
   然后主脑验收合并，merge push 触发正式首次自动部署。
5. 完成标志（可命令验证）：deploy run success；`.deploy-state/last_good_tag` == master HEAD
   SHA；`ssh fordeal 'docker inspect media-resolver-api'` StartedAt 晚于合并时间、镜像
   GIT_SHA == HEAD；`curl localhost:8206/health` 200。

验收归主脑（合并、盯 CI、生产核验）。回滚点：删 deploy.yml + 服务器 compose 还原 + 手工脚本
照旧可用；last_good_tag 预置值保证任何一次失败都有回退点。

### 卡 B（app，S，--class quick）：版本规范化 v1.1.0

依赖卡 A 全绿后开工（合入即走 D3 首次真实应用变更部署）。范围：

1. `pyproject.toml` version → 1.1.0；`config.py` APP_VERSION 改 importlib.metadata 取值、
   新增 GIT_SHA 配置；`/health` 增加 git_sha；启动日志带 sha。
2. 测试：version 契约（settings.APP_VERSION == dist metadata）+ /health 含 git_sha（
   遵守 test-discipline，最小断言、红验可触发）。
3. `CHANGELOG.md` Unreleased → `[1.1.0] - <发版日>`。
4. `CONTRIBUTING.md` 增「发版流程」节：merge master 自动部署、版本唯一字面量在 pyproject、
   发版动作 = 切 CHANGELOG + bump pyproject + 合并 + `git tag vX.Y.Z`（主脑打）、
   手工脚本仅 break-glass。
5. 完成标志：pytest 绿；PR 合并后 D3 自动部署绿；生产 `/health` 返回 `1.1.0` + master SHA；
   主脑打 tag `v1.1.0` 并 push。

## 4. 非目标

- 不接 Infisical 迁移（生产 `.env` 已在服务器侧，D3 不碰 env，本仓 `.env` 不入 git，现状合规；
  platform-onboarding 的 Infisical 步骤属另一批工作，不在本次范围）。
- 不做多镜像 release lane（单镜像服务，用不上 `build-deploy-release.yml`）。
- 不做 busy-lock 门禁（无长任务持锁需求；X Space 流式下载是否需要，观察后再议）。
- 不迁移 zlx-ops-sdk（已接，`zlx-ops-sdk` 在依赖里）。

## 5. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 首次 D3 部署探针失败且无回滚基线 | 零应用代码变更 PR + 预置 last_good_tag（§2.5） |
| compose 文件名不被 D3 发现 | 服务器侧先落 `docker-compose.yml`（§2.4） |
| D3 与手工脚本双路径漂移 | 手工脚本降级 break-glass 并注释标明；`deploy_targets.json` 不引入（二选一） |
| secrets 占位值被启动校验拒绝 | 卡内含 workflow_dispatch 启动验证步骤；拒绝则升真值、问用户 |
| merge 后自动部署与「标记 ready」节奏冲突 | deploy.yml 有 paths-ignore；文档 PR 不触发部署 |

=== PART 2: consult-card.md 六格（六格中「机械事实」以 PART 1 与 PART 3 为准）===
## 三栏

### 机械事实（原始证据，不加解释）

全部设计输入已逐条核实并写入设计文档：`<session-tmp>/design.md`（请通读）。
可独立复核的命令级事实摘要：

1. 仓 `~/projects/work/MediaResolverAPI`（zlxlabs/MediaResolverAPI，default branch=master，risk-tier: internal）。`git tag -l` 为空；`pyproject.toml` 与 `app/core/config.py:19` 均为 `version = "1.0.0"`；`CHANGELOG.md` 有大段 Unreleased。
2. 现行发版：本地 `docker/push_to_acr.sh`（build→推 ACR，tag=40 位 SHA，拒绝 dirty worktree）→ `ssh fordeal` 跑 `docker/pull_and_deploy.sh`（pull→retag :latest→compose up→探针→失败自动回滚）。
3. 生产：fordeal=100.107.95.24（tailnet，sshd 绑 100.107.95.24:22，TTS caller 注释为证）；容器 `media-resolver-api`，端口 8206→8000，`printenv GIT_SHA` = 92a9c0763fec37c591e399eacf18df4585c7dc4b（2026-10-14T08:17 启动）；deploy_dir=<srv:media-resolver-api>/docker（compose label 核实），compose 文件名 `docker-compose.deploy.yml`。
4. D3 流水线：`zlxlabs/ci-templates/.github/workflows/build-deploy.yml@v2`（v2 为当前会动 tag，12 仓在钉）。`scripts/pull_and_deploy.sh` 在 deploy_dir 内跑裸 `docker compose`（依赖默认文件名 docker-compose.yml）；回滚基线 `.deploy-state/last_good_tag`，首次部署时不存在。runner 默认 `self`（VM201 self-hosted 池，org runners API total_count=77，labels self-hosted,linux,x64,codex）；self 模式跳过 Tailscale step。secrets 契约：7 个（ACR_USERNAME/ACR_PASSWORD/SSH_DEPLOY_KEY/KNOWN_HOSTS 必填 + TS_AUTHKEY/CI_TEMPLATES_PAT required:true + FEISHU_CI_WEBHOOK required:false）；本仓已有 FEISHU_CI_WEBHOOK，缺 6 个。ci-templates 仓 `.private=false`。
5. 参照仓 `zlxlabs/one_translate_tts` deploy.yml：钉 @v2、ci_templates_ref: v2、runner 默认 self、secrets 全量 7 个齐备；其部署注释要求「首次接入必须零应用代码变更独立 PR 合入，新 SHA 镜像与旁路手工验证版逐字节一致，首次自动部署探针必过，last_good_tag 基线安全建立」。
6. gate-hub `platform-onboarding.md`：自托管服务部署走 D3，未接入的仓才配 deploy_targets.json，**二选一**；ci-templates examples/caller-workflow.yml 注释「CI_TEMPLATES_PAT … repo is public now; kept but unused this version, any placeholder value works」。

### 主脑判断

方案 = 两卡串行（详见 design.md §3）：

- **卡 A（infra，M，--class big）**：零应用代码变更。服务器预置（compose 落位、专用部署密钥、last_good_tag 预置当前生产 SHA）→ secrets ×6（ACR 取自本机 docker config、密钥新生成、TS_AUTHKEY/CI_TEMPLATES_PAT 先占位）→ 新增 deploy.yml（@v2，push master + workflow_dispatch，paths-ignore md/docs/.github）→ 分支 workflow_dispatch 试跑全链 → 主脑验收合并 → merge push 触发正式首部署。完成标志：run success + last_good_tag==HEAD + 生产容器 StartedAt/镜像 GIT_SHA==HEAD + /health 200。
- **卡 B（app，S，--class quick）**：版本规范化 v1.1.0。pyproject 唯一字面量 → 1.1.0；config.py 改 importlib.metadata 取版本（PackageNotFoundError 即 fail fast）+ 新增 GIT_SHA 配置；/health 加 git_sha；契约测试两条；CHANGELOG 切 [1.1.0]；CONTRIBUTING.md 写发版 SOP；合并后 D3 自动部署绿、生产 /health=1.1.0+SHA，主脑打 tag v1.1.0。

判断依据：D3 是平台标准且前提现成（同机 TTS 已跑通）；手工脚本已具备的 SHA 不可变/探针/回滚模型与 D3 同构，迁移语义连续；首次部署风险用 T4 零应用变更纪律 + 预置回滚基线双保险。

### 主脑希望听到的答案（显式写出，供对抗）

1. 两卡拆分与串行依赖是否正确？卡 A 里「分支 workflow_dispatch 试跑 = 一次真实生产部署（零应用变更镜像）」这一步是否值得，还是应该合并后再首跑？
2. TS_AUTHKEY / CI_TEMPLATES_PAT 占位值策略是否会踩 GitHub required secret 的坑（required:true 下空串/占位串是否放行启动校验）？若不可行，最低成本的真值来源是什么？
3. compose 文件名落位、last_good_tag 预置这两个服务器侧动作有没有我漏掉的副作用（如 D3 后续 reconcile / oneshot 判定、与 server-owned compose 的所有权冲突）？
4. 版本单一来源用 importlib.metadata（pyproject 为唯一字面量）是否成立？有没有更贴合本仓现状的做法？
5. 是否存在应拆未拆的第三张卡（如 Infisical、手工脚本下线、README 部署章节更新）？

## 六格

### 1. 方案目标

把「merge 到 master」变成唯一日常发版动作（自动 build→部署→探针→失败自动回滚），版本语义由 semver tag + CHANGELOG 承载，/health 可观测真实版本。立项理由（用户原话）：「看下怎么规范化发版」；路线选择已由用户拍板：接 D3 自动部署。

### 2. finding 按形态归类的计数表

| finding 形态 | 次数 | 计数口径与证据来源 |
|---|---:|---|
| 无（本方案尚无 review 轮次，首次收口前的规划咨询） | 0 | 答不出→本格如实填 0：方案未进过任何 review |

### 3. 这个方案至今抓到过几次真问题

0 次——尚未实施。规划阶段已抓到并写进设计的坑：D3 compose 默认文件名不匹配（docker-compose.deploy.yml → 需落 docker-compose.yml）、首次部署无回滚基线（→预置 last_good_tag）、D3 与手工双路径漂移风险（→手工脚本降级 break-glass）。边际检出口径：这些是规划期靠读 ci-templates 源码抓到的，不是运行期闸的贡献。

### 4. 当前计划与已发生的前置动作

第 1 次收口。已发生：现状盘点（版本/tag/脚本/CI）、D3 文档通读（RUNBOOK + README + TTS caller + pull_and_deploy.sh 源码）、服务器只读核实（deploy_dir/容器 GIT_SHA/compose 文件名/org runners/secrets 清单）、用户拍板路线、本设计文档落盘。未发生：任何写动作（服务器未动、secrets 未配、workflow 未建）。

### 5. 当时在场的相关规则逐字原文

- gate-hub platform-onboarding.md：「deploy_targets.json | 仅未接 D3 的 docker 部署仓 | … | `docker/deploy_targets.json` 存在；接入 D3 后应删除（两条部署路径互斥）」
- ci-templates RUNBOOK-go-live.md：「**被动升级 ≠ 自动升级。**」「caller 的触发条件与 `paths-ignore` 取舍见 README」「同一 PR 连推会互相取消 CI」（此条来自 agent-config AGENTS.md CI/CD 节奏段原文：「`tests.yml` 的 `concurrency` 组按 ref 聚合并对非 main 设 `cancel-in-progress`，一轮只推一次、推完等终态再改」）
- agent-config AGENTS.md 部署约定：「上线前 `diff` 仓库 compose 与部署目录 compose」「手工部署仓收工核对两条机械判据：生产镜像 tag == `git rev-parse --short=7 origin/main`；…运行时行为类 issue 的完成条件要含『已部署』或显式『待部署』」
- agent-config AGENTS.md 增量交付授权边界：「走完 PR 漏斗（本地漏斗绿 → ready → CI/gate 全绿）再合并进默认分支是主脑自主动作，不请示。须用户明确授权的只有绕过 PR/CI」

### 6. 你能核实什么、不能核实什么

- 可核实：上述 1-6 全部命令级事实（advisor 有只读工具可复核 design.md 引用的路径）；GitHub 侧 secrets 只能列名单不能读值（gh secret list 可用）。
- 不能核实：GitHub required secret 对占位值的启动校验行为（只能 workflow_dispatch 实测，属卡 A 验证步骤）；TTS 的 SSH_DEPLOY_KEY 密钥本体（不可读，故卡 A 新生成专用密钥而非复用）；生产探针实测（需真实部署动作）。
- 需要顾问核实的项目：design.md §2 各条与 ci-templates 源码是否一致；卡 A 卡 B 完成标志是否可命令验证、有无缺口。

## 顾问报告输出结构（四段，不得骑墙）

① 结论（继续/降级/撤回）② 支撑证据（指向格子编号或自行核实的事实）③ 主脑漏掉了什么 ④ 结论在什么条件下会翻

=== PART 3: ci-templates/scripts/pull_and_deploy.sh 摘录 A（L280-360：compose 发现与部署）===
```bash
compose_list_services() {
  local config_rc=0 services=""
  services="$(
    cd "$DEPLOY_DIR" && "$DOCKER_BIN" compose config --services 2>&1
  )" || config_rc=$?
  if [ "$config_rc" -ne 0 ]; then
    log "compose config --services failed; compose up will not run" >&2
    return 1
  fi
  printf '%s\n' "$services"
  return 0
}

validate_oneshot_services() {
  local svc all_services="" invalid=""
  [ -z "$ONESHOT_SERVICES" ] && return 0
  all_services="$(compose_list_services)" || return 1
  for svc in $ONESHOT_SERVICES; do
    if ! printf '%s\n' "$all_services" | grep -qx "$svc"; then
      invalid="${invalid:+$invalid }$svc"
    fi
  done
  if [ -n "$invalid" ]; then
    log "oneshot_services references unknown compose service(s): $invalid" >&2
    return 1
  fi
  return 0
}

rollback_compose_services() {
  local svc all_services="" keep=""
  all_services="$(compose_list_services)" || return 1
  for svc in $all_services; do
    case " $ONESHOT_SERVICES " in
      *" $svc "*) ;;
      *) keep="${keep:+$keep }$svc" ;;
    esac
  done
  if [ -z "$keep" ]; then
    log "rollback refused: oneshot_services covers every compose service; nothing would remain to run" >&2
    return 1
  fi
  printf '%s\n' $keep
  return 0
}

oneshot_schema_hint() {
  [ -z "$ONESHOT_SERVICES" ] && return 0
  log "hint: this release may have run one-shot/migration services; the previous application version may be incompatible with the current database schema — manual verification required"
}

# --- deploy a specific tag: pull (if remote) + retag + compose up ------------
deploy_tag() {
  local ref="$1"
  if [[ "$ref" != *@* ]]; then
    ref="${ACR_IMAGE}:${ref}"
  fi
  log "deploying ${ref}"
  pull_image "$ref" || return $?
  "$DOCKER_BIN" tag "$ref" "${IMAGE_NAME}:latest" || return $?
  if [ "$ROLLBACK_MODE" -eq 0 ]; then
    validate_oneshot_services || return $?
  fi
  if [ "$ROLLBACK_MODE" -eq 1 ] && [ -n "$ONESHOT_SERVICES" ]; then
    local svc_list="" list_rc=0
    svc_list="$(rollback_compose_services)" || list_rc=$?
    if [ "$list_rc" -ne 0 ]; then
      return 1
    fi
    # shellcheck disable=SC2086
    ( cd "$DEPLOY_DIR" && "$DOCKER_BIN" compose up -d $svc_list )
  else
    ( cd "$DEPLOY_DIR" && "$DOCKER_BIN" compose up -d )
  fi
}

# --- health probe: returns 0 if the service answers as expected --------------
health_probe() {
  if [ -z "$HEALTHCHECK_URL" ]; then
    PROBE_STATUS="skipped"
    PROBE_FINAL_CODE=""
```

=== PART 4: ci-templates/scripts/pull_and_deploy.sh 摘录 B（L536-600：last_good_tag 基线与探针失败路径）===
```bash
do_deploy() {
  event enter
  mkdir -p "$STATE_DIR"

  local prev_good="" prev_good_digest="" rollback_ref="" deploy_rc=0
  local compose_ps container_logs compose_ps_rc=0 container_logs_rc=0 rollback_rc=0
  [ -f "$GOOD_TAG_FILE" ] && prev_good="$(cat "$GOOD_TAG_FILE")"
  [ -f "$GOOD_DIGEST_FILE" ] && prev_good_digest="$(cat "$GOOD_DIGEST_FILE")"

  if [ "$prev_good" = "$GIT_SHA" ]; then
    log "this SHA already in last_good_tag; skip forward deploy; reconcile only"
    record_last_good_identity "$ACR_IMAGE:$GIT_SHA"
    DEPLOY_OUTCOME="skipped_already_deployed"
    event exit
    return 0
  fi

  deploy_tag "$GIT_SHA" || deploy_rc=$?
  if [ "$deploy_rc" -ne 0 ]; then
    log "deploy of $GIT_SHA failed (rc=$deploy_rc)"
    DEPLOY_OUTCOME="deploy_failed"
    event exit
    return "$deploy_rc"
  fi

  if health_probe; then
    echo "$GIT_SHA" > "$GOOD_TAG_FILE"
    record_last_good_identity "$ACR_IMAGE:$GIT_SHA"
    DEPLOY_OUTCOME="deployed"
    log "deploy of ${GIT_SHA} healthy; recorded as last good"
    event exit
    return 0
  fi

  log "health probe FAILED for ${GIT_SHA}"
  echo "[deploy][evidence] compose-ps:"
  compose_ps=""
  compose_ps="$(
    cd "$DEPLOY_DIR" && timeout --kill-after=1s "${EVIDENCE_TIMEOUT}s" \
      "$DOCKER_BIN" compose ps 2>&1
  )" || compose_ps_rc=$?
  if [ "$compose_ps_rc" -eq 124 ]; then
    log "[deploy][evidence] compose-ps timed out after ${EVIDENCE_TIMEOUT}s"
  elif [ "$compose_ps_rc" -ne 0 ]; then
    log "[deploy][evidence] compose-ps failed (rc=${compose_ps_rc})"
  fi
  printf '%s\n' "$compose_ps" | sed 's/^/[deploy][evidence] /' || true
  echo "[deploy][evidence] container-logs:"
  container_logs=""
  container_logs="$(
    cd "$DEPLOY_DIR" && timeout --kill-after=1s "${EVIDENCE_TIMEOUT}s" \
      "$DOCKER_BIN" compose logs --tail 100 --no-color 2>&1
  )" || container_logs_rc=$?
  if [ "$container_logs_rc" -eq 124 ]; then
    log "[deploy][evidence] container-logs timed out after ${EVIDENCE_TIMEOUT}s"
  elif [ "$container_logs_rc" -ne 0 ]; then
    log "[deploy][evidence] container-logs failed (rc=${container_logs_rc})"
  fi
  printf '%s\n' "$container_logs" | sed 's/^/[deploy][evidence] /' || true
  if [ -n "$prev_good" ] && [ "$prev_good" != "$GIT_SHA" ]; then
    log "rolling back to previous good tag ${prev_good}"
    rollback_rc=0
    ROLLBACK_MODE=1
    rollback_ref="$ACR_IMAGE:$prev_good"
    if [ -n "$prev_good_digest" ]; then
```

=== PART 5: ci-templates/.github/workflows/build-deploy.yml 摘录（L103-137：runner 默认值与 secrets 契约）===
```yaml
      runner:
        # self | hosted — same two values and meaning as zlxlabs/gate's gate-v2.yml
        # `runner` input (this workflow's runs-on ternary below mirrors that file's
        # mapping pattern verbatim, just without the PR/fork-guard clause a deploy
        # job doesn't need). self = self-hosted (VM201 fleet, labels
        # self-hosted,linux,x64,codex); hosted = GitHub-hosted ubuntu-latest.
        # Default self since D3 migration P1 (2026-07-27): every @v1 caller deploys
        # on the self-hosted fleet; a caller sets `runner: hosted` explicitly to
        # opt out (emergency fallback lane, paid/free GitHub minutes).
        description: "Deploy job runner pool: self (self-hosted VM201 fleet, default since D3 migration P1) | hosted (GitHub-hosted ubuntu-latest, explicit opt-out / emergency fallback)."
        required: false
        default: "self"
        type: string
    # EXPLICIT secrets only — least privilege. Do NOT add `inherit`.
    secrets:
      ACR_USERNAME:
        required: true
      ACR_PASSWORD:
        required: true
      SSH_DEPLOY_KEY:
        required: true
      KNOWN_HOSTS:
        required: true
      TS_AUTHKEY:
        required: true
      CI_TEMPLATES_PAT:
        required: true
      # 飞书告警 webhook。2026-09-06(#46) 从 repo variable 迁到 secret：
      # vars 不被 Actions 打码,当时每个仓每次部署的日志里都躺着一份完整可用的 webhook URL。
      # required: false 是**长期**设置,不是过渡期——shoplazza-capabilities 合法地没有 webhook,
      # 改成 required: true 会让那个仓的部署卡在 workflow 校验上。取不到值时通知层
      # 打 warning 并跳过,不影响部署结论。
      FEISHU_CI_WEBHOOK:
        required: false

```

=== PART 6: zlxlabs/one_translate_tts .github/workflows/deploy.yml 全文（参照 caller）===
```yaml
# 调 Lane B 复用流水线(ci-templates @v1)。push main 自动 build→ACR→入 tailnet→SSH fordeal→探针→部署。
# 钉 @v1(不钉 @main)——一个坏 commit 不会扇出炸全舰队。
# 本文件是 T4 第 5 步:必须作为零应用代码变更的独立 PR 合入——新 SHA 构建的镜像
# 内容与旁路手工验证版逐字节一致,首次自动部署探针必过,last_good_tag 基线安全建立。
name: deploy
on:
  push:
    branches: [main]
    # 仅改 CI/文档不触发生产部署:改 workflow/文档不该扇出一次 D3 应用部署。
    # 真实 src 改动(不在下列路径)照常部署。
    paths-ignore:
      - '**.md'
      - 'docs/**'
      - '.github/**'
  # 手动触发口:paths-ignore 排除了 .github/**,workflow 自身修复后需手动补发部署
  workflow_dispatch:

jobs:
  ship:
    uses: zlxlabs/ci-templates/.github/workflows/build-deploy.yml@v2
    with:
      image_name: one-translate-tts                  # == ACR 镜像名 == 宿主 compose image 短名 == fleet 台账 id
      host: 100.107.95.24                            # fordeal(生产机)的 Tailscale IP;sshd 已加绑 100.107.95.24:22
      ssh_user: lixing
      deploy_dir: <srv:one-translate-tts>
      healthcheck_url: http://localhost:3011/api/healthz   # 校验必需 env + prompts.json 可加载,不调外部服务
      build_context: next_trans_tts                  # 应用在子目录;dockerfile 相对 build_context
      dockerfile: Dockerfile
      healthcheck_warmup: 20
      healthcheck_retries: 10
      healthcheck_interval: 5
      ci_templates_ref: v2
    secrets:                                         # 逐个显式传,最小权限(禁止 secrets: inherit)
      ACR_USERNAME: ${{ secrets.ACR_USERNAME }}
      ACR_PASSWORD: ${{ secrets.ACR_PASSWORD }}
      SSH_DEPLOY_KEY: ${{ secrets.SSH_DEPLOY_KEY }}
      KNOWN_HOSTS: ${{ secrets.KNOWN_HOSTS }}
      TS_AUTHKEY: ${{ secrets.TS_AUTHKEY }}
      CI_TEMPLATES_PAT: ${{ secrets.CI_TEMPLATES_PAT }}
      FEISHU_CI_WEBHOOK: ${{ secrets.FEISHU_CI_WEBHOOK }}   # #46：webhook 从 vars 迁到 secrets（vars 不打码，会进日志）
```

=== PART 7: ci-templates/examples/caller-workflow.yml 摘录（CI_TEMPLATES_PAT 占位值授权注释）===
```yaml
      SSH_DEPLOY_KEY: ${{ secrets.SSH_DEPLOY_KEY }}
      KNOWN_HOSTS: ${{ secrets.KNOWN_HOSTS }}
      TS_AUTHKEY: ${{ secrets.TS_AUTHKEY }}             # runner joins tailnet to reach the internal host
      CI_TEMPLATES_PAT: ${{ secrets.CI_TEMPLATES_PAT }} # repo is public now; kept but unused this version, any placeholder value works
```
