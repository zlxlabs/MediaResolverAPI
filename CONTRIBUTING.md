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

日常发版是全自动的：PR 合入 `master` 后，D3 流水线自动 build 镜像 → 部署 → 探针检查，
失败会自动回滚到上一个可用版本。你只需要盯两处：`gh run list --workflow deploy`
看流水线结论，以及飞书部署回执。

版本号规则：**唯一字面量在 `pyproject.toml`**，其余地方（`/health`、启动日志）都是
运行时从安装元数据读的，不要手改。

发版动作清单：

1. 把 `CHANGELOG.md` 的 `[Unreleased]` 段切版为 `## [X.Y.Z] - 日期`，按 Keep a
   Changelog 惯例重建空的 `[Unreleased]` 段。
2. `pyproject.toml` 的 `version` bump 到同一个 `X.Y.Z`，两个号必须一致。
3. 合入 `master`，等 D3 部署完成。
4. 主脑打标签：`git tag vX.Y.Z && git push origin vX.Y.Z`。

线上对账：部署完成后，`curl https://<生产地址>/health` 返回的 `version` 必须等于
`pyproject.toml` 的版本，`git_sha` 必须等于 `master` HEAD 的 commit SHA。对不上就
说明发版链路有漏步，先查部署日志再排查。

`docker/push_to_acr.sh` 等手工发布脚本已从仓库删除（互斥由仓库形态保证）。
break-glass（仅 D3 不可用时）从 git 历史取回：
`git show a0cd8bf:docker/push_to_acr.sh`，它与 `last_good_tag` 回滚状态互斥，
不要同时走两条发布路径；下次 D3 恢复后会自动重新对齐 `last_good_tag`。
