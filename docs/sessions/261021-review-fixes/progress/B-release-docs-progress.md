## 里程碑 1：版本来源与 GIT_SHA 契约测试

- 当前阶段：修复中
- 本段结论：版本测试现对照 `pyproject.toml`，并覆盖环境变量不能改写版本、`GIT_SHA` 到 `/health` 的注入链及 Dockerfile 声明。`APP_VERSION` 已改为 `ClassVar`，避免 Pydantic 环境变量解析。
- 关键决策与已否决方案：继续从已安装发行包元数据读取运行时版本，未改为镜像中未必存在的 `pyproject.toml`。
- 下一步唯一动作：完成文档与 CHANGELOG 对齐。
