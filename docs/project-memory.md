# 仓专属事实

本文件收录只对本仓库成立的运行事实，给以后在本仓工作的会话读。
内容于 2026-09-03 从 Claude Code 自动记忆迁出，技术细节按归档原文保留，不概括。
条目可能已经过期，阅读时先看每条标注的记录日期。

## 视频号 CDN 直链实测

这条事实记录于 2026-09-02（归档 `modified: 2026-09-02T07:40:00Z`），之后 CDN 策略可能已变。

2026-09-02 对 sph_code AHaM8SrlXX（415MB）的 wxapp.tc.qq.com 直链实测：

- **不绑定请求方 IP**：本机解析出的 URL 在 `<FORDEAL_HOST>`（出口 107.155.12.202）照常 206。
- **持续慢读是否被掐取决于出网路径，不是 CDN 统一策略**：`<FORDEAL_HOST>` 稳定 200KB/s 读 300s（61MB）不断；
  本机同速率两次分别在 85s/17.5MB 与 122s/24MiB 被掐（`transfer closed`），本机 500KB/s 与 1MB/s、3MB/s 均不断。
  两边命中同一 CDN 网段 183.61.179.x，差异在本机侧路径。停顿 ≥90s 两边都必断（上一批已测）。
- **URL 查询串没有明文过期字段**（svrnonce 是签发时间），有效期靠轮询 `-r 0-0` 实测 ≥139 分钟仍有效（2026-09-02，/tmp/expiry.log）。
- **结论**：下游直连必须自带 Range 续传 + 过期换链，这是必需项不是纵深防御。

**How to apply:** 直连端点 `GET /api/stream/wechat_channels/{sph}/direct` 返回 cdn_url + 解密文件头 + content_length，
客户端从 131072 起直连 CDN 拉身子；断连/401/403/404/410 重调端点换链续传。探针脚本：/tmp/direct_resolve.py、/tmp/expiry_poll.sh、/tmp/slow_read.sh。
相关：[[fordeal-slow-link-e2e]]
- 直连端点已随 PR #18（f66787d）合入并于 2026-09-02 部署到 `<FORDEAL_HOST>`；生产端到端 md5 与流式端点一致。
