verdict: pass

# PR #19 第 2 轮独立审查（调用方视角）

审查范围固定为 `e7b5c03fe46e6d66abc97e00e5fdf61fc2a0413d..5b7d9674b92ef2b14113560c13b9a1bf69f4ebd0`（2 文件，+18/-1），不含审查期间的新提交。风险等级 `internal`。本轮视角与第 1 轮正交：不重复键存在性/测试锁/红验，专查调用方 `app/api/resolve.py` 会不会把修完的白名单绕开或吞掉。本轮无有效 finding。

## Findings

| ID | P 级 | 违反的不变式/红线 | 文件与行号 | 触发路径 | 证据 | 一句话修法 |
|---|---|---|---|---|---|---|
| — | — | — | — | — | 本轮无有效 finding。 | — |

## 视角 1：调用方路径——`resolve.py` 是否只靠 `is_short_url` 决定展开

是。全仓 grep 确认 `is_short_url` / `resolve_short_url` 的唯一调用方是 `app/api/resolve.py:130,132`；展开与否只由 `is_short_url` 这一个精确集合查找门控，`identify_platform` 只在展开失败分支里用于抖音特判（`resolve.py:135`）。白名单修完后不存在第二条能绕开它的入口。

调用方原文（本 diff 未改此文件，`sed -n '129,164p' app/api/resolve.py` 实测输出）：

```python
        # Step 1: Resolve short URL if needed
        if url_parser.is_short_url(original_url):
            logger.info(f"Resolving short URL: {original_url}")
            resolved = await url_parser.resolve_short_url(original_url)
            if resolved:
                original_url = resolved
            elif url_parser.identify_platform(original_url) == "douyin":
                # 抖音短链展开失败：不 400，改走 hybrid（其内部自带短链展开）
                logger.info("Short URL expand failed, falling back to douyin hybrid")
                platform, use_hybrid = "douyin", True
            else:
                log_data["error_msg"] = "Failed to resolve short URL"
                raise HTTPException(
                    status_code=400,
                    detail="Failed to resolve short URL",
                )

        # Step 2: Parse URL to identify platform and video ID
        if not use_hybrid:
            platform, video_id = url_parser.parse_url(original_url)
            if platform == "douyin" and not video_id:
                # 抖音但 ID 提取失败（如新链接格式）：改走 hybrid 兜底
                logger.info("Douyin id extraction failed, falling back to hybrid")
                use_hybrid = True
            elif platform in URL_FALLBACK_PLATFORMS and not video_id:
                # 平台已识别但 ID 提取失败（如新链接格式）：不 400，放行让该平台降级链的
                # by_url 端点用原始 url 兜底（kuaishou web_share / instagram v1+v2）。
                # video_id 保持空，chain 的 build_params 改喂 original_url（评审 Issue 5）。
                logger.info(f"{platform} id extraction failed, falling back to by_url chain")
            elif not platform or not video_id:
                log_data["platform"] = platform
                log_data["error_msg"] = "Unsupported URL"
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported URL or could not extract video ID: {original_url}",
                )
```

修前/修后逐条走查（在冻结 base `e7b5c03` 的临时 worktree 与审查树分别实测，`parse_url` 顺带验证）：

```text
# base（修前）实测：
'https://xhslink.cn/o/8Gx4uVk2CR1'  is_short_url=False identify_platform=None   parse_url=(None, None)
'https://vt.tiktok.com/ZSVc3fkd2/'  is_short_url=False identify_platform=tiktok parse_url=(None, None)

# head（修后，5b7d967）实测：
'https://xhslink.cn/o/8Gx4uVk2CR1'  is_short_url=True  identify_platform=xiaohongshu parse_url=(None, None)
'https://vt.tiktok.com/ZSVc3fkd2/'  is_short_url=True  identify_platform=tiktok      parse_url=(None, None)
```

- 修前 `xhslink.cn`：`is_short_url` 为假 → 跳过展开 → `parse_url` 域名不识别（`xhslink.cn` 不是 `xhslink.com` 的子域）→ `(None, None)` → 命中 `resolve.py:158-164` 的 400 `Unsupported URL or could not extract video ID`。与卡面证据「生产 400 发生在 parse_url 失败、不是 TikHub 失败」一致；同笔记长链当天已缓存成功（note_id `6a87c5060000000033025e11`）也印证 TikHub/解析器健康，缺陷只在展开前门。
- 修前 `vt.tiktok.com`：`identify_platform` 靠 `tiktok.com` 的 `endswith` 子域匹配已能认出 `tiktok`，但 `is_short_url` 为假 → `parse_url` 提取不到 video_id → 同一条 400。即「平台已认出但仍 400」的形态真实存在，修复正是补上了这个缺口。
- 修后：两个域名进 `is_short_url` 真分支 → `resolve_short_url` 展开为长链 → 对长链 `parse_url` 走既有路径。`parse_url` 对短链本身返回 `(None, None)` 属预期（短链无 video_id，修前修后一致），不进入决策是因为展开已先行替换 `original_url`。
- `URL_FALLBACK_PLATFORMS = {kuaishou, instagram, wechat_channels}`（`resolve.py:30`）不含 tiktok/xiaohongshu，本 diff 未改它，符合不变式 5。

## 视角 2：误展开——新键会不会把不该展开的 URL 拿去跟随跳转

不会。`is_short_url`（`url_parser.py:145-146`）是对 `extract_domain` 的小写精确 `netloc` 做集合查找，无子域/后缀匹配。head 实测对照（命令：`uv run --frozen python` 导入 `url_parser` 逐条打印）：

```text
'https://xhslink.cn.evil.example/abc'  is_short_url=False identify_platform=None
'https://notxhslink.cn/abc'            is_short_url=False identify_platform=None
'https://www.xhslink.cn/o/8Gx4uVk2CR1' is_short_url=False identify_platform=xiaohongshu
'https://xhslink.cn.attacker.tld/o/x'  is_short_url=False identify_platform=None
```

对照 URL `https://xhslink.cn.evil.example/abc` 仍为 False，不会被拿去 HTTP 跟随跳转。注意 `www.xhslink.cn` 修后 `identify_platform` 变为 `xiaohongshu`（新增 `xhslink.cn` 键带来的 `endswith` 子域匹配），但 `is_short_url` 仍为 False → 不展开 → `parse_url` 无 video_id → 显式 400，与修前同为可见失败，行为等价。另：`resolve_short_url` 展开后 `resolve.py` 会对最终长链重新 `parse_url`，开放重定向到非白名单平台域只会落到 400 `Unsupported URL`，不会被静默接受。`resolve_short_url` 跟随跳转不校验最终宿主是存量行为（v.douyin.com 等 8 个旧域同此），非本 diff 引入，记 backlog。

## 视角 3：静默失败——展开失败是 fail-loud 还是 success=false 空 data

是 fail-loud。修完后短链展开失败（`resolve_short_url` 返回 None）且平台非抖音时，`resolve.py:139-143` 直接 `raise HTTPException(400, "Failed to resolve short URL")`，不存在返回 success=false 空 data 的路径。下游消费方 web2eagle `backend/app/api/video.py:552,564-566`：`raise_for_status()` 失败 → `logger.error("MediaResolverAPI returned HTTP ...")` → `fetch_video_info` 返回 None → 调用处 `:209-213` 抛 `VideoWorkflowError("resolve_failed", "MediaResolverAPI returned no media ...")`，是任务级可见失败，不是吞错。

P1 两问（针对「展开失败 → 400 → 下游 no media」这条链，拟标项：无，以下为排除记录）：

1. 真实使用方式下会被触发吗：会——短链展开依赖对 xhslink.cn / vt.tiktok.com 的实时 HTTP，超时/反爬/对端故障是常态，本机实测该路径存在且可达。
2. 触发后果能否接受：能——后果是显式 HTTP 400 + 下游任务报 `resolve_failed`，用户看到失败而非拿到错误/空洞数据；不丢数据、不越权、不损坏他人数据。不命中 internal 档 P1 红线「静默出错」，不判 P1。

## 视角 4：熵增——第 1 轮是否漏记包装层

重读冻结 diff 全量：新增仅为两个既有集合中的 4 个字面量 + 13 行参数化测试，外加 `url_parser.py` 文件末尾补换行符（`\ No newline at end of file` 消除，纯格式）。无新增抽象、文件、状态、包装层、配置项。与第 1 轮反熵结论一致，无漏记。

## Backlog

- `resolve_short_url` 跟随跳转不校验最终宿主（存量，8 个旧短链域同此形态）；若今后担心短链服务被劫持指向内网，可在展开后加最终域校验，不属本 diff，不阻塞合并。
- `www.xhslink.cn` 等 `www` 别名仍按 R1 backlog 处理（产品契约未声明别名，当前显式 400）。

## 固定条款

红验安全（原文）：凡按「改坏生产代码 → 确认测试红 → 还原」验证断言恒真性的红验，改坏前必须先 commit（或至少 stash）同文件里已验证的真修复；还原只许还原刚改坏的那一处，禁止整文件 `git checkout -- <file>`。

红验有效性（原文）：反向验证的转红输出必须原文贴进报告，且红的类型必须是断言失败（AssertionError / 明确的期望-实际对比）。（本轮未做变异红验——视角不含测试锁重验；base/head 行为对照为只读探针，临时 worktree `/tmp/r2-base-probe` 已删除。）

反熵条款（原文）：禁止顺手新增抽象。
