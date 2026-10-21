**① 结论：继续，但卡内容需纳入 5 项修订。** 主脑的根因判断成立，方向正确。parse_url 无损化加单点策略是对的。修订点全是落地细节，不改方向。

**② 关键证据（均为亲读 HEAD 代码）**

- **根因成立。** `app/services/url_parser.py:108-112` 只对 wechat_channels 返回平台名，其余压成 (None,None)。更深一层：wechat_channels 根本不在 `_extract_video_id` 的 extractors 表里（url_parser.py:212-222），所以那个特判的真实语义是「平台无提取器时放行」。无损化后这条语义自然消失，不需要替代物。
- **by_url 链确实吃原始 url。** 快手 `app/services/providers/tikhub.py:681-684`，web_share 端点喂 share_text=original_url。Instagram tikhub.py:748-751 两端都喂 original_url。视频号 tikhub.py:840 喂 share_url。(c) 问题答案：是。
- **抖音 L181 死代码判断成立。** `_extract_douyin_id` 对无 /video/ 的 URL 返回 None，parse_url 随即返回 (None,None)，resolve.py:181 永远不命中。hybrid 本身仍可经 resolve.py:167-170 的短链展开失败路径到达，所以「死」的只是 L181 这一条入口。
- **假绿不止一个文件。** `tests/test_resolve_hybrid.py:79-87` 用 monkeypatch 喂 `("douyin", None)` 测 L181，和 #8 完全同构。主脑表格「测试桩点在缺陷上游」应记 2 文件，不是 1。
- **(a) test_url_parser 受影响的断言。** 无损化后以下 5 处会从 (None,None) 变为 ("twitter", None)：`tests/test_url_parser.py:108`、`:111`、`:112`、`:231`、`:234`。原因是 x.com 域名回落到 twitter 平台，但路径无 /status/。其余 (None,None) 断言不变，因为 identify_platform 本身返回 None。resolve 层仍 400：twitter 不在兜底集合，落到 resolve.py:190。`tests/test_twitter_space.py:365-369` 走的是真实 parse_url 且断言 400，改后仍绿，这是现成的 resolve 层回归守卫。

**③ 主脑漏掉了什么**

1. **「其余平台零变化」要按层表述。** issue #8 原话要求其余平台识别结果零变化。在 parse_url 层这不成立，twitter 有 5 处断言翻转。在 resolve 层成立，非兜底平台仍 400。卡的完成判据必须写清是 resolve 层零变化，否则评审会按字面判不通过。

2. **(b) 抖音复活的实际后果不是「正收益」那么干净。** 非视频抖音 URL，例如用户主页或 /note/ 图文，当前 400。复活后走 hybrid，tikhub.py:607 只跑 hybrid 一端，`_classify_aweme` 对无 aweme_detail 的响应返回 retryable，链耗尽抛 VideoNotFoundError，最终 resolve.py:311-314 返回 HTTP 200 且 success=false。代价是一次 TikHub 调用，上限 25 秒。TikHub 对主页 URL 的真实响应我核不了。建议接受，但卡里要显式写出这条行为变化并加一条测试锁定它，而不是靠复活隐式发生。

3. **快手 by_url 路径会先打一次空参请求。** video_id 为空时，tikhub.py:684 让 web_v2 端点带 photo_id="" 先跑一次，`_run_chain` 不跳过空参端点。结果是多一次无意义 HTTP 调用，上限 25 秒，然后才到 web_share。小红书已有「按条件裁剪链」的先例，见 tikhub.py:641-645，照抄不算新抽象。建议同卡修：video_id 为空时裁掉只吃 id 的端点。

4. **策略其实有三份副本，不是两份。** 第三份在 resolve.py:167-170：短链展开失败时只对抖音放行。无损化之后这一段可以折叠：展开失败直接让原短链走 parse_url，identify_platform 命中抖音、提 ID 失败，得到 ("douyin", None)，由 L181 单点处理。tiktok 短链失败则得到 ("tiktok", None) 落 400，`tests/test_resolve_hybrid.py:90-98` 仍绿，只是错误文案从 "Failed to resolve short URL" 变为 "Unsupported URL"。这一折叠是可选项，但若将来 v.kuaishou.com 真要进 SHORT_URL_DOMAINS，它就变成必选，否则快手短链展开失败会卡在 L173 的 400 而到不了 by_url 兜底。

5. **test_resolve_hybrid.py 必须一并迁到真实 parse_url。** 否则抖音分支的假绿原样保留，和 #8 原文「不改测试的话假绿会再来一次」矛盾。

**短链判断：同意不加 v.kuaishou.com。** 代价已在上面第 3、4 点算清。真实链接验证不过再加，届时第 4 点升级为必做。

**(d) 其他同类双副本：** URL_FALLBACK_PLATFORMS 是对 tikhub 链参数名的手工镜像，真相在 tikhub.py 的链表里。按用户规则不该为单一消费者派生它。可加一条结构性测试锁住镜像关系，而不是加抽象。优先级低，可不进本卡。

**未核项：** #41、#48 的 gh 证据与 CI 日志本环境无法核实，按主脑陈述处理，不下判断。TikHub web_share 对 v.kuaishou.com 短链、hybrid 对抖音主页 URL 的线上行为均不可核。

**④ 结论翻转条件**

- 若 owner 把「其余 6 个平台识别结果零变化」严格读在 parse_url 层，本方案降级：唯一替代是让 resolve 在 parse_url 返回 None 后再调一次 identify_platform，这是把有损接口保留并多一次调用，比主脑方案差，但满足字面约束。
- 若真实快手短链经 web_share 走不通，需加 SHORT_URL_DOMAINS 且第 4 点折叠变为必做，卡的范围扩大但方向不变。
- 若团队不接受抖音非视频 URL 从 400 变为 200/success=false 并多一次 TikHub 调用，则需在 L181 加一道保护，这等于重新引入一条平台特判，与「单点策略」目标冲突，此时应撤回「抖音复活是正收益」这一条，其余保留。fable rc=0
