# F-url-fallback 进度

## 2026-10-21：无损解析与真实路由验收

- 当前阶段：repairing
- 本段结论：先在基线运行新增测试，得到平台名丢失、三平台路由 400、快手 ID 空时仍调用 web_v2 的断言失败；随后将 parse_url 改为保留已识别平台，并按 video_id 裁掉快手 photo_id 端点。真实 TestClient 路由和 TikHub HTTP 边界测试及指定窄测现为 106 passed。
- 关键决策与已否决方案：沿用 resolve.py 已有的 Douyin hybrid 与 URL_FALLBACK_PLATFORMS 分支，不给 parse_url 增加平台特判、不改 ID 提取规则、不展开 v.kuaishou.com 短链；路由测试使用真实 parse_url，provider 只桩 TikHub HTTP 方法。
- 下一步唯一动作：提交本里程碑后按卡面把 parse_url 无 ID 返回临时改回 `(None, None)`，执行必做三路由红验并保存断言失败原文。
