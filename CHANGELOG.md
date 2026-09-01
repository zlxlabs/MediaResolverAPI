# Changelog

本项目所有重要变更都会记录在此文件。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added
- README 补齐下游接入文档：环境变量表（含 `PUBLIC_BASE_URL`）、视频号 `video_url` 必须带 `X-API-Key` 的差异说明，以及 Python / JavaScript / cURL 的视频号两步下载示例。
- `LICENSE`（MIT），补齐开源所需许可证。
- `CONTRIBUTING.md` 开发与贡献指南、`CHANGELOG.md` 变更日志。
- README 新增「运维仪表盘 API」章节，文档化 `/api/dashboard/*` 接口与 `/dashboard/` Web 仪表盘。
- Dockerfile 增加 `HEALTHCHECK` 健康探针与非 root 运行用户（uid 10001）。
- 微信视频号（`wechat_channels`）：平台表、降级链说明、流式解密下载端点，以及仓级 `risk-tier: internal` 声明。

### Changed
- CORS `allow_credentials` 改为 `False`，修正与 `allow_origins=["*"]` 的无效组合；README 新增「跨域访问（CORS）」接入说明。
- Dockerfile 依赖改为从 `pyproject.toml` 安装（单一来源），移除与 pyproject 重复的内联依赖列表。
- `.env.example` 补齐缺失配置项：`TIKHUB_RATE_LIMIT`、`TIKTOK_FALLBACK_REGIONS`、`PROVIDER_PRIORITY_*`（8 平台）；`COBALT_API_BASE` 默认值与代码对齐（留空即禁用）。

### Fixed
- README 平台表与 `data.platform` 枚举补充 Facebook，与运行时 `/api/platforms` 返回保持一致。

## [1.0.0]

- 首个版本：抖音 / TikTok / 快手 / YouTube / 小红书 / Instagram / Pinterest / Facebook
  视频 URL 解析，TikHub + Cobalt 多数据源、多级端点降级，缓存、翻译、用量统计与运维仪表盘。
