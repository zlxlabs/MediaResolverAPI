# 跨 harness 顾问咨询载荷：MediaResolverAPI issue 分诊与 #8 根治方案

## 候选结论菜单
- `继续`：主脑方案（下文）可按原样派卡。
- `降级`：方案方向对但应缩小/改做法。
- `撤回`：前提不成立。
每格填不出就写「答不出」。

## 机械事实
仓：本仓，HEAD f3755bd（master）。risk-tier 未声明 → 按 internal。open issue 4 个：
- #41 gate-disposition.yml 只 5 输入。现状：PR #42（2026-10-12 合并）已改为 8 输入 + 8 转发 + @v2；当前文件已核对。完成判据②（真实误报 finding 合法调用一次）无真实样本。
- #48 D3 deploy_tag 失败不回滚。上游 zlxlabs/ci-templates#68 已 CLOSED(COMPLETED, 2026-10-15)，v2 标签 → ad2185fa1105e35b852e784bd59fdbb4e90d2e05（ls-remote 核实）；本仓 deploy.yml `uses: .../build-deploy.yml@v2`；最近一次 deploy run 39533611795 日志显示 `@refs/tags/v2 (ad2185fa...)`，success。上游注明回滚分支未真实触发，仅单测锁定。
- #39 视频号域外形态归因 backlog，按样本门槛挂起（无真实样本不臆造分支）。
- #8 快手/Instagram by_url 兜底生产走不到。主干仍存在：
  - app/services/url_parser.py:78 `parse_url`：识别出平台但 `_extract_video_id` 失败时，仅 `platform == "wechat_channels"` 返回 `(platform, None)`，其余全部 `return None, None`（L104-112）。
  - app/api/resolve.py:35 `URL_FALLBACK_PLATFORMS = frozenset({"kuaishou","instagram","wechat_channels"})`；L181 `if platform == "douyin" and not video_id: use_hybrid=True`；L185 `elif platform in URL_FALLBACK_PLATFORMS and not video_id:` 放行；L190 `elif not platform or not video_id: 400`。
  - parse_url 唯一生产调用方是 resolve.py:180（cx references）。
  - SHORT_URL_DOMAINS（url_parser.py:25）无 v.kuaishou.com。`_extract_kuaishou_id` 只认 /short-video/。
  - tests/test_resolve_by_url_fallback.py 的 `_patch_urlparser` monkeypatch 了 `parse_url`/`identify_platform`/`is_short_url`，直接喂 `(platform, None)`。
  - issue 评论实测（构造 URL）：`v.kuaishou.com/2xAbCdE`、`kuaishou.com/f/X9aBcDeF`、`instagram.com/share/BAcDeFgHi` 均 `identify_platform` 命中但 `parse_url → (None,None)`。
  - owner 评论：修复卡完成判据需一条真实快手分享短链跑通 POST /api/resolve，派卡前需先拿到。

## 主脑判断
1. #41、#48 已被修复：附证据关单。#41 判据②标「待验证（无真实误报样本，不造 finding）」后关。#48 关单，注明前向失败回滚分支未被真实触发。
2. #39 维持 backlog 不动。
3. #8 根因不是「少了两个平台的特判」，而是**同一条策略（哪些平台在 ID 缺失时可兜底）存在两份副本**：resolve.py 的 URL_FALLBACK_PLATFORMS 与 parse_url 内硬编码的 wechat_channels 特判；加上 parse_url 返回值有损（把「平台已识别、ID 未知」与「平台未知」压成同一个 (None,None)）。新增平台时只改一份就必漂移——快手/IG 正是这么坏的，且同一机制让 resolve.py:181 的抖音 hybrid 兜底分支也成了死代码（parse_url 对抖音 ID 失败同样返回 (None,None)）。
   根治 = 让 parse_url 无损：识别出平台就返回 (platform, video_id_or_None)，删掉 wechat_channels 特判；兜底策略只在 resolve.py 一处决定（已有的 L190 继续对非兜底平台 400）。测试改为走真实 parse_url（只桩 resolver/上游网络），并加一条结构性测试：对每个 PLATFORM_DOMAINS 平台的「可识别但无 ID」URL，parse_url 返回平台名非 None。
   短链：v.kuaishou.com 是否加进 SHORT_URL_DOMAINS——倾向**不加**为第一步：by_url 端点（TikHub 快手 web_share 吃 share_text=原始 url）本就为该场景设计，少一次网络展开；若真实链接验证不过再加。
4. 拆卡：只有 #8 一张实现卡（单文件对 + 测试，无可横切的并行面），派 --class big。前置：向用户要一条真实快手分享短链（及可选 IG /share/ 链）作为完成判据。
5. 一个 PR 里同时修抖音死分支（同根因，修 parse_url 后自动复活）——需确认抖音 hybrid 兜底复活后不会让原本 400 的抖音非视频 URL（如用户主页）走进 hybrid 产生新错误形态。

## 主脑希望听到的答案（供对抗）
「继续」：parse_url 无损化 + 单点策略即可根治，不加 v.kuaishou.com 短链展开，抖音分支复活是正收益。

## 1. 方案目标
issue #8 原文：「兜底分支要求 parse_url() 返回 (platform, None)。而现有 parse_url 在 ID 提取失败时，对这两个平台返回的是 (None, None)」「把相关测试从 monkeypatch parse_url 改成走真实 parse_url……不改测试的话，同样的假绿会再来一次」「parse_url 是所有平台共用入口，改动需确认其余 6 个平台的识别结果零变化」。用户本次要求：「优先让问题不发生，而不是打补丁」。

## 2. finding 形态计数
| 形态 | 次数 | 口径 |
|---|---:|---|
| 策略双副本漂移（parse_url 特判 vs URL_FALLBACK_PLATFORMS） | 3（kuaishou、instagram、douyin 死分支） | 主脑读 HEAD 代码 |
| 测试桩点在缺陷上游（假绿） | 1 文件 | tests/test_resolve_by_url_fallback.py |
| 上游已修本仓未关单 | 2（#41、#48） | gh 核查 |

## 3. 抓到过几次真问题
不适用（非闸类方案）。边际检出：答不出。

## 4. 当前计划与前置动作
第 1 次收口。已发生：读 4 个 issue、核对 HEAD 代码、核对 ci-templates#68 与 deploy run。未发生：关单、派卡、拿真实链接。

## 5. 相关规则原文
- 「不新增没有第二消费者的抽象」（用户全局规则）
- 「禁引入 fallback/重试/防御式 try-catch，除非卡的完成条件显式要求；错误一律 fail fast 上抛。」
- 「跨进程……边界必须断言 producer 实际发出的 payload」「断言本身也要核对约束力——改坏对应实现时它会不会变红」
- #39：「无真实样本不臆造分支」

## 6. 可核实/不可核实
可核实：仓内代码、测试、git 历史（请亲读 app/services/url_parser.py、app/api/resolve.py、tests/test_resolve_by_url_fallback.py、tests/test_url_parser.py、app 下 kuaishou/instagram/douyin provider 链 by_url 端点参数构造）。
不能核实：真实快手分享链接形态、TikHub 线上行为、PR 评论/CI 日志。
请顾问核实：(a) parse_url 无损化后，tests/test_url_parser.py 中哪些现有断言 (None,None) 的用例会变（如 twitter 主页、space 无 id），resolve 层是否仍 400；(b) 抖音分支复活后非视频抖音 URL 走 hybrid 的后果；(c) 快手/IG by_url 链 build_params 在 video_id=None 时是否真用 original_url；(d) 是否还有同类「策略双副本」值得同卡收口。

## 顾问报告输出结构
① 结论（继续/降级/撤回）② 关键证据（指格子或自查事实）③ 主脑漏掉了什么 ④ 结论翻转条件
