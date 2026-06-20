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
