# DESIGN-note：261021 代码审查修复（缓存续期 / 中文判定 / 发版文档 / 安全与阻塞 IO）

- 风险档位：本仓 `risk-tier: internal`
- 基线：origin/master `c7b4d46988be7fa282b9cd86a08bee7dad0072ef`（v1.1.0 已打 tag 并合入）
- 来源：2026-10-21 `/code-review xhigh` 全仓审查，15 条 finding

## 目标

修掉审查确认的 15 条问题，修完后另起一次 v1.1.1 发版（不在本批卡内）。

## 非目标

- 不重构缓存层 / 翻译层结构，不改缓存 TTL 数值与翻译阈值 0.3。
- 不改 `.github/workflows/` 与 ci-templates 仓（12 位短 SHA 是上游既定事实，文档向它对齐）。
- 不恢复已删除的手工部署脚本。
- 本批卡不碰 CHANGELOG 的 Unreleased 段、不改版本号；v1.1.1 由主脑合并后统一切版。

## 为什么不是分区 / 删除 / 约定

- 缓存续期：靠「约定命中时不翻译失败」不成立——熔断、未配 key、空描述都是常态路径，必须在代码上让命中路径不改过期时间。
- 版本单一来源：靠「约定不设 APP_VERSION 环境变量」就是现状的缺陷本身，必须让版本不进 settings 的环境变量解析。

## 方案要点与已否决方案

拆三张 Scope 互不重叠的卡并行：

| 卡 | 范围 | 文件 |
|---|---|---|
| A cache-zh | 缓存命中不续期、中文判定按字符计数、兜底异常不回显 str(e) | `app/api/resolve.py` `app/services/cache.py` `app/utils/validators.py` + 对应测试 |
| B release-docs | 发版文档与流水线对齐、CHANGELOG [1.1.0] 补漏、版本测试有约束力、APP_VERSION 不可被环境覆盖 | `CONTRIBUTING.md` `README.md` `CHANGELOG.md` `app/core/config.py` `tests/test_version.py` |
| C sec-io | API Key 常数时间比较、Space 下载/物化路径不阻塞事件循环 | `app/api/deps.py` `app/api/stream.py` + 对应测试 |

- resolve.py:318 的异常回显归 A 卡（与缓存命中同文件），保证 A/C 无文件交集。
- 已否决：缓存命中重翻译后调用 `cache_video` 并加「不续期」参数——给只服务一个调用点的写入函数加模式开关；改为命中路径只更新译文字段（实现形态由执行器定，但不得改 `expires_at`/`cached_at`）。
- 已否决：版本测试改为读 pyproject 运行时取版本——容器镜像内未必有 pyproject；运行时仍读安装元数据，测试负责对照 pyproject 发现元数据过期。

## 关键不变式

1. 缓存命中路径（任意翻译结果）不改变该记录的 `expires_at`；翻译失败/跳过不覆盖已有译文。——A 卡测试锁
2. `is_chinese_text` 对正常中文句子返回 True、对英文返回 False（按单个汉字计数）。——A 卡测试锁
3. 兜底异常响应体 `error` 不含异常原文。——A 卡测试锁
4. `/health` 的 version 等于 pyproject.toml 的 version，且设置 `APP_VERSION` 环境变量不能改变它。——B 卡测试锁
5. API Key 校验用 `hmac.compare_digest`；Space 文件读写不在事件循环线程同步执行。——C 卡测试锁

## 待验证前提

- `translation.is_chinese` 最终调用的是 `is_chinese_text`（A 卡执行器核实）。
- deploy.yml 各失败阶段是否回滚以 deploy.yml 头注释与实际 step 为准（B 卡执行器核实，文档向事实对齐）。

## 验收路径

主脑读报告 + `git diff` + 本地 `make test`；三卡合并后在新主干再跑一次全量，然后切 v1.1.1。
