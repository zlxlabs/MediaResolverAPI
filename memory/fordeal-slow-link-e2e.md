---
name: fordeal-slow-link-e2e
description: "本机到 fordeal:8206 的链路只有约 20 KB/s，视频号大文件端到端要在 fordeal 本机跑，慢链路只做小 Range 抽样"
kind: fact
valid_as_of: 2026-09-02
origin: 22d9af91-4b8d-4cc5-8e78-24cdf3a226b9
---

这条事实是 2026-09-02 在 MediaResolverAPI 上得到的（origin 会话 `22d9af91-4b8d-4cc5-8e78-24cdf3a226b9`）。

2026-09-02 实测：本机直接访问 `http://10.100.1.228:8206` 的下载速率约 20 KB/s（415 MB 要 4 小时以上），
而 fordeal 本机访问 `localhost:8206` 全速（415 MB 约 74 s）。下游 VideoTranscriptAPI 当初约 200 KB/s
的慢速也在这一侧。

**Why:** 视频号流式端点的验收判据是「慢客户端也能拿到完整 md5 一致的文件」，跨慢链路拉全文件会先撞
我自己的超时；本机跑一次 25 分钟只到 40 MiB 被杀，浪费一轮。
**How to apply:** 全文件 A/B 场景把脚本 scp 到 fordeal 用 `localhost:8206` 跑（API_KEY 从
`fordeal:/home/lixing/docker/media-resolver-api/.env` 读，不打印）；慢链路只跑 16 MiB 级 Range 抽样验证停顿场景。
生产部署目录是 `fordeal:/home/lixing/docker/media-resolver-api/docker/`（compose 与 pull_and_deploy.sh 在该子目录）。
相关：[[delegate-card-gotchas]]
