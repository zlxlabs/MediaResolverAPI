# wx-direct R1 verdict

verdict: **pass**

风险等级：仓未声明 `risk-tier`，按 **internal** 处理（P1 红线含越权 / 损坏他人数据；轮次上限 5，收敛需连续 2 轮无新增 P1）。建议另卡补声明。

审查对象冻结：`83e258d..3feb0c2`（2a85cbe 测试 / ea10c67 实现 / 3feb0c2 文档）。本文件所在提交不属于被审范围。

本轮新证据（生成结论后才拿到，不是重读同一份 diff）：
- `pytest tests/test_stream_wechat_channels_direct.py -q` → **7 passed**。
- 临时 `tests/_r1_review_probes.py`（已删）走空文件 / 416 / 恰好 131072 / CDN 200 / oversized Content-Range / 第二对非法 `decode_key` / 401·404·410 换链 / `__cause__` / `*/` 502 正文 / `_stream_slot` / 截断重试。
- 四处注入红验（改 Range 终点、去掉 `media = state["media"]`、`total is None` 猜 0、跳过 sph_code 校验），注入行均 `grep RED-VERIFY` 确认后跑测、再还原。
- OCR `status=reviewed`（minimax / MiniMax-M3），4 条；两条复核超时，按未核实处理。

无 P1。不阻塞合并。

## findings

| 编号 | P 级 | 违反 spec | 文件:行 | 复现 / 证据 | 建议修法 |
|---|---|---|---|---|---|
| F1 | P2 | 第 5 条：TikHub 失败应 502，不得未捕获变 500 | `app/api/stream.py:617-620` 只校验第一对；`:645` 换对后 `:660` `xor_chunk` 无 `try` | 首 URL 403 + 第二对 `decode_key="not-a-key"` → HTTP **500** `{"success":false,"error":"Internal server error"}`，日志 `Unhandled exception: decode_key is not a decimal integer: 'not-a-key'`，`opens==2` 且 Range 均为 `bytes=0-131071`，正文无 URL | 换对 `_fetch_media` 成功后立刻 `generate_keystream(state["media"]["decode_key"])`，`TypeError/ValueError` → 502 `"Invalid decode_key"`，与第一对对称 |
| F2 | P3 | 第 4 条短文件语义的空文件边角 | `_read_window:301-302` 416；harness `file_size=0` 拼出 `bytes 0--1/0` | 空文件 206 畸形 Range → 502 `CDN 206 response has malformed Content-Range`（3 次 CDN，继承窗内重试）；CDN 416 `bytes */0` → 502 `CDN returned 416`（1 次）。均大声失败，无静默成功 | 真实视频号 mp4 不会是 0 字节。可接受不修；若要锁契约，空文件走 200 + `encrypted_head_bytes=0` 或维持 502 并写进 README |
| F3 | P3 | 第 1 条「至多两次」vs 既有窗内重试 | `_read_window_retry:385-389` `_WINDOW_MAX_ATTEMPTS=3` | CDN 200 大文件 / 206 超窗 / body 截断：均 **3** 次同 URL GET 后 502。过期换链路径仍是 2 次（401/404/410 探针与 403 测试一致） | spec 已写「经既有 `_read_window_retry`」。不必为 /direct 另做重试预算；文档可注明非过期失败最多 3 次同窗 |
| F4 | P3 | 测试有效性（锁 aclose） | `tests/test_stream_wechat_channels_direct.py:90-94` 403 分支未写入 `opens[-1]["stream"]` | 换链测试只断言第二次流 `aclose`（happy path 才断言）。403 那条 FakeCdn 根本没挂到 `opens[0]["stream"]`，回归漏 `aclose` 不会红 | 403 也 `opens[-1]["stream"]=cdn`，并断言两次 `aclose_called` |
| F5 | P3 | 第 8 条反熵（可减可不减） | `stream.py:624-632` 闭包 `read_head` | 仅本函数两处调用，无第二消费者 | 可内联两次 `_read_window_retry`；不修也可 |

P1 两问（F1）：
1. 真实使用方式下会被触发吗？换链本身会（token 过期是设计路径；401/404/410 探针均为 200 + `cdn_url=URL_B`）。第二对 `decode_key` 要「能过 `_fetch_media` 的 None/"" 检查、又过不了 `_parse_decode_key`」（非十进制串 / 负数 / bool）。现网 TikHub 给的是整数；缺字段已是 502 `media incomplete`。**未在真实 TikHub 上量到该形态。**
2. 触发了后果能否接受？单请求 500，进程不崩、不静默错字节、不泄露 `full_url`。对照 R1 流式卡把「换对 ProviderError → 500」判 P2 的同一量纲：契约 502 vs 实装 500，**不是**数据丢失 / 静默出错 / 崩溃 / 越权 / 损坏他人数据。故 **不是 P1**。

## 工具标注 / 本仓判定

OCR `status=reviewed`（primary minimax）。外部 severity 只是输入。

| OCR 条 | 工具标注 | 本仓判定 | 两问 |
|---|---|---|---|
| 换对后不校验第二对 `decode_key` → 500 | high / bug（复核超时） | **P2 = F1** | 见上：Q1 未在真实 TikHub 量到非法 key；Q2 500 可接受为契约偏差 |
| 换链测试未断言第一次 403 流 `aclose` | low / test（复核超时） | **P3 = F4** | 不触发 P1 红线 |
| docstring「只发一次」vs 换链两次 | low / docs（confirmed） | **不记 finding** | 紧接下文已写换链重读；README 失败表也写了「自行换链重试一次」 |
| `encrypted_head_bytes` 可由 `head_b64` 推导，建议删 | low / maintainability（confirmed） | **无效** | 与 README 字段表及客户端协议第 2 步（用该字段作 Range 起点）冲突；反着文档化契约的意见不成立 |

## spec 逐条

1. **有界 Range + 过期至多两次 CDN**：`read_head` 调 `_read_window_retry(..., 0, KEYSTREAM_SIZE-1)`。start=0 `< _URL_REFRESH_MIN_OFFSET`，既有 helper **不会**换链，而是 `raise UpstreamDisconnected(str(exc)) from exc`。端点看 `__cause__` 为 `CdnHttpError` 且 status∈{401,403,404,410} 再 `_fetch_media` 一次。探针：`_read_window_retry` 遇 403 → 类型 `UpstreamDisconnected`，`__cause__` 稳定为 `CdnHttpError(403)`，`str` 为 `CDN returned 403`（无 URL）。happy/expired 测试断言 Range `bytes=0-131071`；expired 断言 2 次打开。MUT1 改成 `KEYSTREAM_SIZE-2` 后两条都红。
2. **完整长度 / `*` → 502**：`total is None` 回 502 `"CDN response missing complete length"`。STAR 探针正文无 URL。MUT3 猜 `total=0` 后该测试红。
3. **密钥与 `cdn_url` 用成功那一对**：`:659 media = state["media"]` 后 xor / 回传。expired 测试断言 `cdn_url==URL_B` 且明文等于用 KEY_B 加密再解的 `PLAIN_HEAD`。MUT2 去掉 rebind：expired 红、happy 仍绿。
4. **短文件 / 恰好 131072**：`file_size=1000` → `encrypted_head_bytes==1000` 且 `len(head_b64 解码)==1000`。恰好 131072：200，三字段均为 131072，`head==PLAIN_HEAD`，1 次 `bytes=0-131071`。空文件见 F2，非静默错。
5. **错误码与槽**：非法 sph 400（MUT4 跳过校验则红）；TikHub `ProviderError` 502；无 key 401；探针 `_stream_slot` 进入次数 `[]`、`stream_limiter.active==0`。
6. **不泄露 `full_url`**：本端点无新增 logger。502 detail 来自 `CdnHttpError`/`UpstreamDisconnected` 固定文案或 `"CDN response missing complete length"`。STAR / 200 大文件 / oversized / 截断探针正文均无 `https://cdn.test/a`。注入「`ProviderError` 文案自带 URL」会原样进 502（与流式端点同一 `str(exc)` 透传）；**现网 `tikhub.py` 的 ProviderError 只含 sph_code / HTTP 状态，不含 full_url**。500 的 F1 正文也无 URL。
7. **非目标**：diff 只动 `stream.py` 新路由 + 测试 + README `/direct`；不改 resolve/缓存；响应无 `decode_key`；无 302；无下游续传实现。
8. **反熵**：`WechatChannelsDirectInfo` 是 README 字段表的 HTTP 契约（客户端是第二消费者），不是投机抽象。`read_head` 见 F5。无新 fallback / 兼容分支。

## 失败路径（探针）

| 路径 | 行为 | 500？ | URL 进 502？ |
|---|---|---|---|
| start=0 过期 → `__cause__` | 稳定 `CdnHttpError`，端点换链 | 否 | 否 |
| 401 / 404 / 410 换链 | 与 403 相同：200 + `cdn_url=URL_B`，2 次 CDN | 否 | 否 |
| 第二对非法 decode_key | **500**（F1） | 是 | 否 |
| CDN 200 且 CL > 窗 | 502 `CDN ignored Range request`，3 次 GET | 否 | 否 |
| CDN 200 且 CL≤窗（短文件） | 200，用 Content-Length 当总长（既有 `_read_window` 小文件例外；真源是 206） | 否 | — |
| 206 `declared_end > end` | 502 `CDN 206 response exceeds requested window`，3 次 | 否 | 否 |
| body 截断 | 502 `upstream closed before range complete`，3 次 | 否 | 否 |
| `Content-Range` `*` | 502，无 URL | 否 | 否 |
| CDN 416 | 502 `CDN returned 416` | 否 | 否 |

重试预算 20 在本端点单窗耗不尽（先撞 3 次窗上限）。

## 测试有效性

FakeCdn **记录** Range/次数，**不断言**；断言在测试里：

- `test_direct_happy_path`：1 次、`bytes=0-131071`、`aclose`、字段表、`head_b64`==明文。
- `test_direct_expired_url_refreshes_media`：2 次、两次 Range、URL_A→URL_B、用第二对解密。
- 短文件未再断言次数/Range（happy 已锁 Range）。无空文件 / 416 / 200 / 第二对非法 key / 槽位用例（F1/F2/F4）。

红验：MUT1 Range 终点 → happy+expired 红；MUT2 旧 pair → expired 红；MUT3 猜长度 → missing-length 红；MUT4 跳过 sph → 400 测试红。改坏对应实现会红。

## 正向契约

响应六字段与 README 表一致（`content_type` 默认 `video/mp4`）。`encrypted_head_bytes == len(b64decode(head_b64))` 由 happy（131072）与 short（1000）锁死。
