# Changelog

本项目所有重要变更都会记录在此文件。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added
- `POST /api/resolve` 新增 `download_mode` 参数：`audio` 返回原生音频轨（YouTube 经 TikHub 取最高码率纯音频轨）或 Cobalt 音频直链（Twitter / TikTok / Instagram / Pinterest / Facebook），无音频路径的平台返回 HTTP 400。
- `POST /api/resolve` 新增 `quality` 参数：清晰度封顶（如 `720p`），不超过 cap 取最高档、全部高于 cap 取最低超档，显式覆盖平台默认选流；与 `download_mode=audio` 组合返回 HTTP 422。
- X（Twitter）video 模式的 resolve 响应新增 `data.variants`：全部 mp4 档位（`url` / `bitrate` / `width` / `height` / `quality`）按码率升序透出，供下游（如 ASR）自选低码率流。
- X（Twitter）公开视频解析：支持 x.com / twitter.com status URL，TikHub 获取元数据并以 Cobalt 兜底。
- 翻译结果状态、模型备用名单、翻译熔断与 `/health/translation` 健康口；翻译失败不再写入响应译文或缓存。
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
- `PUBLIC_BASE_URL` 留空时启动打一条 WARNING（字面量 `PUBLIC_BASE_URL 未设置`）：该配置缺失会让 `data.video_url` 按请求 `Host` 推导出内网地址，此前是静默失效，现在可直接 grep 启动日志发现（PR #35 遗留条目，在此补齐）。

### Fixed
- 微信视频号瞬态失败不再一次判死：`retryable` 在单端点链上最多重试 3 次（退避 0.3s），解析侧与下载侧共用同一引擎实现（下载侧手搓循环已删除）；`attempts` 携带脱敏失败原因（`data_missing` / `object_type_mismatch` / `error_body`），耗尽仍抛 `VideoNotFoundError`。
- README 平台表与 `data.platform` 枚举补充 Facebook，与运行时 `/api/platforms` 返回保持一致。

## [1.0.0]

- 首个版本：抖音 / TikTok / 快手 / YouTube / 小红书 / Instagram / Pinterest / Facebook
  视频 URL 解析，TikHub + Cobalt 多数据源、多级端点降级，缓存、翻译、用量统计与运维仪表盘。
