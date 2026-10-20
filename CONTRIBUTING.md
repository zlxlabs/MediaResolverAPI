# 贡献指南

感谢你对 MediaResolverAPI 的关注！本文档说明如何参与开发。

## 开发环境

```bash
# 1. 克隆并进入项目
git clone <repo-url> && cd MediaResolverAPI

# 2. 准备配置
cp .env.example .env
# 编辑 .env，至少填入 TIKHUB_API_KEY；本地开发可留空 API_KEY 跳过鉴权

# 3. 安装依赖（含开发依赖）
pip install -e ".[dev]"

# 4. 启动服务
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 运行测试

```bash
pytest
```

- 测试不依赖网络：各平台的真实 API 响应已固化为 `tests/fixtures/<platform>/*.json`，解析逻辑针对 fixtures 做断言。
- 新增/修改平台解析时，请同步补充对应 fixture 与用例。
- 采集新 fixture 可参考 `scripts/collect_*_fixtures.py`。

## 代码约定

- Python ≥ 3.11，遵循 PEP 8。
- 平台解析放在 `app/services/platforms/`，数据源适配放在 `app/services/providers/` 与 `app/services/adapters/`。
- 多级端点降级走 `app/services/providers/tikhub.py` 的通用引擎，设计见 `docs/generic-fallback-engine.md`。
- 新增配置项时，请**同时**更新 `app/core/config.py` 与 `.env.example`，保持两者一致。
- 新增面向客户端的接口时，请**同步**更新 `README.md`。

## 提交 PR

1. 从 `master` 切出特性分支。
2. 确保 `pytest` 全绿。
3. 在 `CHANGELOG.md` 的 `[Unreleased]` 段落记录变更。
4. 提交 PR 并描述动机与影响范围。

## 发版流程

日常发版由 D3 流水线自动 build 镜像、部署并检查探针。**只有新版本探针失败，且回滚后的
旧版本通过同预算探针验证时，才会自动回滚到 `last_good_tag`**。服务器拉取、重标记或启动
阶段失败只会令 job 失败，不自动回滚；镜像对账失败以及无法确认生产处于健康版本时也不自动
回滚，需人工核对/恢复。拉取、重标记或启动失败的人工恢复点是服务器本地镜像
`media-resolver-api:pre-d3-baseline`。你只需要盯两处：`gh run list --workflow deploy` 看流水线
结论，以及飞书部署回执。

版本号规则：**唯一字面量在 `pyproject.toml`**，其余地方（`/health`、启动日志）都是
运行时从安装元数据读的，不要手改。

发版动作清单：

1. 把 `CHANGELOG.md` 的 `[Unreleased]` 段切版为 `## [X.Y.Z] - 日期`，按 Keep a
   Changelog 惯例重建空的 `[Unreleased]` 段。
2. `pyproject.toml` 的 `version` bump 到同一个 `X.Y.Z`，并运行 `uv lock` 同步更新
   `uv.lock`，两者一起提交。
3. 合入 `master`，等 D3 部署完成。
4. 主脑打标签：`git fetch origin && git tag vX.Y.Z "$(git rev-parse origin/master)" && git push origin vX.Y.Z`，确保标签指向合并后的 `origin/master` 提交。

线上对账：部署完成后，`curl https://<生产地址>/health` 返回的 `version` 必须等于
`pyproject.toml` 的版本，`git_sha` 必须等于**最近一次触发部署的 master 提交**的
`git rev-parse --short=12` 值（D3 注入 12 位短 SHA）。纯文档合并被 deploy 工作流的
`paths-ignore` 排除，不触发部署；这种情况下生产仍对应之前那次触发部署的提交，而非当前
`master` HEAD。

`docker/push_to_acr.sh` 等手工发布脚本已从仓库删除。D3 不可用时，先从默认分支补发部署：
`gh workflow run deploy.yml --ref master`。若 D3 无法执行而需服务器侧恢复，在部署目录中可把
已存在的 `pre-d3-baseline` 或本机已有的 12 位 SHA 镜像重标记为 compose 使用的 `latest`，再
运行 `docker compose up -d`，例如：

```bash
cd /srv/media-resolver-api/docker
# 选择一个本机确实存在的来源镜像标签（二选一）
docker tag media-resolver-api:pre-d3-baseline media-resolver-api:latest
# 或：docker tag media-resolver-api:<12位SHA> media-resolver-api:latest
docker compose up -d
```

**服务器侧恢复命令未在本卡验证**；执行前须确认所选来源镜像在服务器本地存在。
