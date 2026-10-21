# F-url-fallback 进度

## 2026-10-21：无损解析与真实路由验收

- 当前阶段：repairing
- 本段结论：先在基线运行新增测试，得到平台名丢失、三平台路由 400、快手 ID 空时仍调用 web_v2 的断言失败；随后将 parse_url 改为保留已识别平台，并按 video_id 裁掉快手 photo_id 端点。真实 TestClient 路由和 TikHub HTTP 边界测试及指定窄测现为 106 passed。
- 关键决策与已否决方案：沿用 resolve.py 已有的 Douyin hybrid 与 URL_FALLBACK_PLATFORMS 分支，不给 parse_url 增加平台特判、不改 ID 提取规则、不展开 v.kuaishou.com 短链；路由测试使用真实 parse_url，provider 只桩 TikHub HTTP 方法。
- 下一步唯一动作：提交本里程碑后按卡面把 parse_url 无 ID 返回临时改回 `(None, None)`，执行必做三路由红验并保存断言失败原文。

## 2026-10-21：必做红验与全量验证

- 当前阶段：verifying
- 本段结论：已在已提交真修复上仅将 parse_url 的无 ID 返回临时改回 `(None, None)`，快手、Instagram、抖音三条真实路由测试均以 HTTP 状态断言失败；随后只还原该行。还原后窄测 106 passed、`make test` 622 passed，范围检查通过。
  - 快手原始红验：`assert (400 == 200)`，`tests/test_resolve_by_url_fallback.py::test_id_extraction_failure_falls_back_to_by_url[kuaishou-https://v.kuaishou.com/2xAbCdE]`。
  - Instagram 原始红验：`assert (400 == 200)`，`tests/test_resolve_by_url_fallback.py::test_id_extraction_failure_falls_back_to_by_url[instagram-https://www.instagram.com/share/BAcDeFgHi]`。
  - 抖音原始红验：`assert (400 == 200)`，`tests/test_resolve_hybrid.py::test_id_extraction_failure_falls_back_to_hybrid`。
- 关键决策与已否决方案：红验完成后只恢复临时改动的 return 行，未用整文件 checkout/stash；未新增 fallback、重试或解析特判。
- 下一步唯一动作：生成完整交付报告，并采集最终提交与干净工作区证据。

## 2026-10-21：交付完成

- 当前阶段：completed
- 本段结论：完整报告已写入 dispatch envelope 指定报告路径；代码与测试均已入库，线上验收留待主脑部署后执行。
- 关键决策与已否决方案：最终保持 scope 与锁定决策不变；线上快手短链探活留给主脑部署后执行。
- 下一步唯一动作：主脑按报告验收并在部署后核实真实快手分享短链。
