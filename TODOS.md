# TODOS

## P3: 实时日志流（WebSocket）

**What:** 仪表盘通过 WebSocket 实时推送新解析请求，替代当前的前端轮询机制。
**Why:** 比 30s 轮询更实时，运维体验更好，类似 tail -f。
**Pros:** 即时反馈，减少不必要的 HTTP 请求。
**Cons:** 增加服务端复杂度，需要 WebSocket 连接管理和断线重连逻辑。
**Context:** 仪表盘初版使用前端 30s 轮询 usage_log 表。WebSocket 可在需求明确后升级。FastAPI 原生支持 WebSocket。
**Effort:** M (human ~2 days / CC ~20 min)
**Depends on:** Web Dashboard 仪表盘功能完成后。
