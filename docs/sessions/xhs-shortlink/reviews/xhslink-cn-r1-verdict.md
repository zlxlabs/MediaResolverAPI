verdict: pass

# PR #19 第 1 轮独立审查

审查范围固定为 `e7b5c03fe46e6d66abc97e00e5fdf61fc2a0413d..5b7d9674b92ef2b14113560c13b9a1bf69f4ebd0`，不包含审查期间的新提交。风险等级为 `internal`；本轮没有命中 P1 红线。OCR 前置状态为 `reviewed`（profile `minimax`，findings 为空），人工审查仍覆盖全部冻结 diff。

## Findings

| ID | P 级 | 违反的不变式/红线 | 文件与行号 | 触发路径 | 证据 | 一句话修法 |
|---|---|---|---|---|---|---|
| — | — | — | — | — | 本轮无有效 finding。 | — |

## 1. 正向覆盖

- `app/services/url_parser.py:24,41` 将 `vt.tiktok.com` 同时加入 `SHORT_URL_DOMAINS` 与 `PLATFORM_DOMAINS`，平台值为 `tiktok`。
- `app/services/url_parser.py:26,54` 将 `xhslink.cn` 同时加入两个集合，平台值为 `xiaohongshu`。
- 新测试 `tests/test_url_parser.py:120-129` 对两个新域名同时断言 `is_short_url(...) is True` 和 `identify_platform(...)` 的平台值，锁定不变式 1、2 以及“双集合同步”的不变式 4。
- 原有测试 `tests/test_url_parser.py:102-108` 继续覆盖 `vm.tiktok.com` 与 `xhslink.com` 的短链识别；代码对这两个旧条目仅保留原值，未改变不变式 3。旧平台映射也未被删除或改写。
- 改动没有新增抽取器，没有触及 `app/api/resolve.py` 的短链入口、TikHub 链或 `URL_FALLBACK_PLATFORMS`，符合不变式 5。

## 2. 反向红验

红验在基线 `e7b5c03fe46e6d66abc97e00e5fdf61fc2a0413d` 的临时工作树中执行，仅带入本次测试文件；没有运行实现测试套件。为绕开与目标测试无关且缺少 `sqlalchemy` 的全局 `conftest.py`，命令使用 `uv run --frozen --extra dev pytest -q --noconftest`，测试文件自身的 `parser` fixture 未被绕过。

基线红验原始断言失败输出：

```text
FF                                                                       [100%]
=================================== FAILURES ===================================
_ TestShortUrlDetection.test_new_short_domains_identify_platform[https://xhslink.cn/o/8Gx4uVk2CR1-xiaohongshu] _

>       assert parser.is_short_url(url) is True
E       AssertionError: assert False is True
E        +  where False = is_short_url('https://xhslink.cn/o/8Gx4uVk2CR1')
E        +    where is_short_url = <app.services.url_parser.URLParser object at 0x7949030039d0>.is_short_url

_ TestShortUrlDetection.test_new_short_domains_identify_platform[https://vt.tiktok.com/ZSVc3fkd2/-tiktok] _

>       assert parser.is_short_url(url) is True
E       AssertionError: assert False is True
E        +  where False = is_short_url('https://vt.tiktok.com/ZSVc3fkd2/')
E        +    where is_short_url = <app.services.url_parser.URLParser object at 0x794902e4ccd0>.is_short_url

=========================== short test summary info ============================
FAILED tests/test_url_parser.py::TestShortUrlDetection::test_new_short_domains_identify_platform[https://xhslink.cn/o/8Gx4uVk2CR1-xiaohongshu]
FAILED tests/test_url_parser.py::TestShortUrlDetection::test_new_short_domains_identify_platform[https://vt.tiktok.com/ZSVc3fkd2/-tiktok]
2 failed in 0.06s
RED_VERIFY_BASE_EXIT=1
```

反向变异只删除 `SHORT_URL_DOMAINS` 中的 `xhslink.cn`，保留 `PLATFORM_DOMAINS` 中的同名键。注入确认：

```diff
@@ -23,7 +23,6 @@ class URLParser:
         'vm.tiktok.com',     # TikTok短链
         'vt.tiktok.com',     # TikTok新短链
         'xhslink.com',       # 小红书短链
-        'xhslink.cn',        # 小红书新短链
         'youtu.be',          # YouTube短链
```

变异后的原始断言失败输出：

```text
F.                                                                       [100%]
=================================== FAILURES ===================================
_ TestShortUrlDetection.test_new_short_domains_identify_platform[https://xhslink.cn/o/8Gx4uVk2CR1-xiaohongshu] _

>       assert parser.is_short_url(url) is True
E       AssertionError: assert False is True
E        +  where False = is_short_url('https://xhslink.cn/o/8Gx4uVk2CR1')
E        +    where is_short_url = <app.services.url_parser.URLParser object at 0x7a9edde179d0>.is_short_url

=========================== short test summary info ============================
FAILED tests/test_url_parser.py::TestShortUrlDetection::test_new_short_domains_identify_platform[https://xhslink.cn/o/8Gx4uVk2CR1-xiaohongshu]
1 failed, 1 passed in 0.12s
RED_VERIFY_MUTATION_EXIT=1
```

第一次错误行号的补丁和第二次错误顺序的补丁均未采信：它们分别报告补丁损坏/不适用，且没有确认注入生效；之后已按实际 diff 重做并完成上述有效红验。所有临时工作树和变异均已删除/还原。

## 3. 反熵

- 新增内容只有两个既有集合中的四个字面量和对应参数化测试；没有新增抽取器、包装层、状态、配置项或抽象，因此不存在无第二消费者的熵增意见。

## 4. 误匹配与 `www` 前缀

- `app/utils/validators.py:46-58` 的 `extract_domain` 返回小写精确 `parsed.netloc`；`app/services/url_parser.py:145-146` 对 `SHORT_URL_DOMAINS` 做精确集合查找。因此 `evilxhslink.cn`、`xhslink.cn.evil.example` 等无关域名不会命中短链白名单。
- `identify_platform` 的既有 `endswith("." + platform_domain)` 语义会识别合法域名的子域，但短链展开仍由精确的 `is_short_url` 控制，不会因此把任意子域交给 `resolve_short_url`。这不是本 diff 新增的包装或 fallback 路径。
- `www.xhslink.cn` / `www.vt.tiktok.com` 不在短链精确白名单内。按本卡给出的真实使用契约，触发样例是无 `www` 的两个精确域名，未声明 `www` 别名，因此第一问“真实使用方式下会被触发吗”：本轮没有证据表明会触发。即使收到该未声明别名，路由不会调用短链展开，随后缺少可抽取的视频 ID 时显式返回 400；第二问“触发后果能否接受”：这是可见的请求失败，不是静默错误，也不命中本仓 P1 红线。故不判 P1。

## Backlog

- 若产品契约今后明确支持 `www` 短链别名，应为别名补充测试，并将其同步加入两个集合；当前不属于本轮 spec，不阻塞合并。
- 未改动的其他平台短链（例如 `v.kuaishou.com`）属于存量范围，本轮不审；如需行为变更另开卡。

## 固定条款

红验安全（原文）：凡按「改坏生产代码 → 确认测试红 → 还原」验证断言恒真性的红验，改坏前必须先 commit（或至少 stash）同文件里已验证的真修复；还原只许还原刚改坏的那一处，禁止整文件 `git checkout -- <file>`。

红验有效性（原文）：反向验证的转红输出必须原文贴进报告，且红的类型必须是断言失败（AssertionError / 明确的期望-实际对比）。

反熵条款（原文）：禁止顺手新增抽象。
