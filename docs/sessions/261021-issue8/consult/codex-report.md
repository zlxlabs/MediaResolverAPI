## ① 结论：降级

**#8 的根因判断成立**：让 `parse_url` 在已识别平台但提不出 ID 时返回 `(platform, None)`，并让路由决定能否兜底，是正确方向。但方案不宜按原样派卡：抖音非视频 URL 的行为会改变，快手无 ID 路径还会先请求一次需要 `photo_id` 的端点。这两处应写进同一张卡的验收边界。

## ② 关键证据

- [url_parser.py](app/services/url_parser.py:97) 目前只为视频号保留平台名；快手、Instagram 和抖音提取 ID 失败都会返回 `(None, None)`。[resolve.py](app/api/resolve.py:178) 因而到不了对应兜底分支。[路由测试](tests/test_resolve_by_url_fallback.py:43)直接桩掉了这个缺陷入口。
- 快手 `web_share` 确实以原始 URL 构造 `share_text`，Instagram 两个端点也都使用原始 URL，见 [tikhub.py](app/services/providers/tikhub.py:671) 和 [tikhub.py](app/services/providers/tikhub.py:738)。但快手链先调用 `web_v2`；无 ID 时它收到空 `photo_id`。现有参数测试只覆盖了有 ID 的情况。
- [test_url_parser.py](tests/test_url_parser.py:107) 中 X 用户主页、从 query 或 fragment 伪装的 status、无效或缺 ID 的 Space 路径，改后会从 `(None, None)` 变为 `("twitter", None)`；域外和无效 URL 仍是 `(None, None)`。这些 X 路径在现有 [resolve.py](app/api/resolve.py:190) 仍返回 400。
- 抖音则不同：任意已识别但无 ID 的抖音 URL，包括用户主页，改后都会进入 hybrid。上游失败时，[resolve.py](app/api/resolve.py:311) 返回 HTTP 200、`success: false`，取代当前的 400。[现有 hybrid 测试](tests/test_resolve_hybrid.py:79)同样桩掉了真实解析器，不能证明这个边界安全。
- 本地工作流可确认 #41 已有 8 项输入及转发、#48 本仓调用 `build-deploy.yml@v2`，见 [gate-disposition.yml](.github/workflows/gate-disposition.yml:9) 和 [deploy.yml](.github/workflows/deploy.yml:26)。远端标签、运行日志及 issue 状态不在此次本地核实范围内。

## ③ 主脑漏掉了什么

#8 的实现卡应要求：路由测试使用真实 `parse_url`；分别锁定快手、Instagram 原始 URL 到 provider 的实际参数；明确抖音非视频 URL 是否仍须 400。若须保持 400，就要先限定哪些抖音无 ID 链接可进 hybrid，不能仅靠“识别出抖音域名”。

“每个 `PLATFORM_DOMAINS` 平台返回平台名”的结构性测试可以加，但须另测视频号和 X Space 特殊规则，并保留非兜底平台的路由 400 断言。路由允许兜底的平台集合与 provider 的 URL 端点能力仍是一项跨层契约；用真实调用参数测试锁住即可，未见同卡必须新增的抽象。

#41 的真实误报处置判据尚未完成。若它是关单硬条件，不能把“待验证”写入后宣称全部验收完成。#48 的前向成功也不能证明失败回滚分支已在真实部署中触发。

## ④ 结论翻转条件

若确认产品允许抖音主页等无 ID 链接进入 hybrid、快手空 `photo_id` 首次请求可接受，并取得真实快手分享链完成端到端验证，则 #8 可转为**继续**。若真实链接无法被 `web_share` 解析，或需要先展开才能解析，则“不加 `v.kuaishou.com` 短链展开”的前提须撤回。
