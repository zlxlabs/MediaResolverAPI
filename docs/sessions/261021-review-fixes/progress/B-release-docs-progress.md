## 里程碑 1：版本来源与 GIT_SHA 契约测试

- 当前阶段：修复中
- 本段结论：版本测试现对照 `pyproject.toml`，并覆盖环境变量不能改写版本、`GIT_SHA` 到 `/health` 的注入链及 Dockerfile 声明。`APP_VERSION` 已改为 `ClassVar`，避免 Pydantic 环境变量解析。
- 关键决策与已否决方案：继续从已安装发行包元数据读取运行时版本，未改为镜像中未必存在的 `pyproject.toml`。
- 下一步唯一动作：完成文档与 CHANGELOG 对齐。

## 里程碑 2：发布文档与 D3 / X Space 行为对齐

- 当前阶段：修复中
- 本段结论：更新了发布回滚边界、版本锁文件与显式 SHA 打标签指令、12 位 SHA 对账及 D3 break-glass 流程；README 健康响应示例和 `[1.1.0]` CHANGELOG 已补齐。CHANGELOG 核对采用 `1ae307f..83dc63e`，因为仓库没有 `v1.0.0` 标签，`1ae307f` 是 1.0.0 切版提交。
- 关键决策与已否决方案：不再从 git 历史取回已删除的部署脚本；优先 workflow_dispatch 补发，服务器恢复只使用已有镜像标签与 `docker compose up -d`。
- 下一步唯一动作：完成红验、全量测试及最终范围/文档判据检查。

## 里程碑 3：红验、全量验证与范围检查

- 当前阶段：验收完成，待推送与提交报告
- 本段结论：旧安装元数据与 APP_VERSION 环境覆盖的测试均按预期断言失败后修复；临时移除 Dockerfile 的 GIT_SHA ENV 行时契约测试以断言失败。恢复原行后定向测试 5 项通过，`make test` 全量 582 项通过，范围检查通过。
- 关键决策与已否决方案：未触发 `workflow_dispatch`，未连接生产服务器；主干没有 v1.0.0 标签，CHANGELOG 基线按 `1ae307f..83dc63e` 核实。
- 下一步唯一动作：推送 `card/fix-b-release-docs` 并写入最终报告。
