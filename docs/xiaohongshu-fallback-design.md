# 小红书无水印解析 · 多级降级机制设计

> 状态：**已过 `/plan-eng-review`（架构/代码质量/测试/性能四节）**，待实现
> 数据源：TikHub API（openapi.json 实测，2026-06）
> 参照：[douyin-fallback-design.md](douyin-fallback-design.md) 的 provider 内闭环 + schema 自适应解析范式
>
> **评审锁定决策：** ① 链序 **app_v2(note_id) → web_v3(note_id+token)**（app_v2 不依赖 token，更鲁棒）；② xhs 链**移除 cobalt**，改 tikhub 单 provider；③ `XiaohongshuService` **删 `get_video_info()` 与死端点 `self.endpoint`**，只留 `_parse_response`，provider 接管取数。

---

## 1. 背景与故障现象

线上报错 `获取xiaohongshu视频信息失败`。实测复现（生产容器）：

```
url: https://www.xiaohongshu.com/explore/68a54752000000001d002090?xsec_token=...&xsec_source=pc_user
error: All providers failed for platform 'xiaohongshu'. Errors:
  [tikhub: ... '404 Not Found' for url '.../xiaohongshu/web/get_note_info_v3?share_text=...';
   cobalt: Cobalt API error 400: {"error":{"code":"error.api.link.invalid"}}]
```

根因：

1. **TikHub 端点 `web/get_note_info_v3` 已被下线（404）** —— 与之前 Instagram 旧端点被下线同一套路（`tikhub.py:40`、`xiaohongshu.py:20` 仍硬编码该死端点）。
2. **Cobalt 不支持小红书**（`link.invalid`）—— `video_resolver.py:84` 把 cobalt 列入 xhs 链是无效降级，xhs 实质是 **tikhub 单点**（与 douyin 同性质）。

即：当前小红书是「单端点 + 无效兜底」，端点一挂全链失败。

---

## 2. TikHub 小红书「笔记详情」渠道全景（openapi 实测）

实测同一笔记（视频笔记 `68a54752...`，URL 自带 `xsec_token`）：

| 渠道 | 端点 | 入参 | 实测 | 数据结构（视频流落点） | 关键特点 |
|------|------|------|------|----------------------|----------|
| **旧（现状）** | `web/get_note_info_v3` | `share_text` | **404 已下线** | `data.{video.media.stream.h264[].master_url}`（snake） | 死端点 |
| **App V2 视频** | `app_v2/get_video_note_detail` | `note_id` **或** `share_text` | ✅ 200 | `data.data[0].video_info_v2.media.stream.h264[].master_url`（snake） | **仅 note_id 即可，不依赖 token**；`type` 区分图文/视频 |
| **Web V3** | `web_v3/fetch_note_detail` | `note_id` **+** `xsec_token`（均必填） | ✅ 200 | `data.data.items[0].noteCard.video.media.stream.h264[].masterUrl`（**camel**） | 需 token；结构最干净 |
| App V2 图文 | `app_v2/get_image_note_detail` | `note_id`/`share_text` | （图文专用） | `images_list` | 视频降级不用，仅用于 type 判定参考 |
| 蒲公英 V2 | `app/get_note_info_v2` | `share_text` | ❌ 400 | — | 商家后台专用，不通用 |
| 旧 app V1 | `app/get_note_info` | `note_id`/`share_text` | 未采用 | — | 旧版 |

核心判断：

1. **两条独立可用渠道**，且 **schema 互不相同**（app_v2 snake + `video_info_v2`；web_v3 camel + `items[].noteCard`），都与已死的旧端点不同 → 必须 schema 自适应解析。
2. **app_v2 仅凭 note_id 即可**，不依赖 `xsec_token`（token 会过期/分享链可能缺失）→ 是**更鲁棒的首选**。
3. **web_v3 需 token**，但 token 在标准分享链里几乎总是带的，作为第二渠道补强（防 app 接口风控抖动）。
4. 小红书有「图文笔记」，视频端点对图文笔记拿不到视频流 → 应作为**终态短路**（再降级也无视频）。

---

## 3. 设计决策（待评审确认）

| # | 决策点 | 提案 | 理由 |
|---|--------|------|------|
| D1 | 降级链深度 | **二级：app_v2(note_id) → web_v3(note_id+token)** | 两条实测可用且独立；app_v2 不依赖 token 故首选 |
| D2 | 解析层落点 | **复刻 douyin 范式**：`XiaohongshuService._parse_response` 改 schema 自适应（一个解析器通吃 app_v2/web_v3/旧）；provider 内 `_fetch_xiaohongshu` 配链 | 与 douyin 一致，adapter 无需改 |
| D3 | xsec_token 来源 | provider 已有 `original_url`，**在 provider 内正则取 token**（不改 url_parser 签名）；token 缺失则 web_v3 跳过、只走 app_v2 | 最小改动；token 非必须 |
| D4 | 失败语义分类 | `terminal`（图文笔记/笔记删除私密）立即短路；`retryable`（端点错误/空/解析失败）试下一端点；`ok`（有视频流）返回 | 复刻 `_classify_douyin` |
| D5 | 超时控制 | 单端超时 ~25s（`max_retries=0`）+ 总预算 ~50s（`asyncio.timeout`） | 复刻 douyin，避免重试放大 |
| D6 | Cobalt 处置 | xhs 链**移除 cobalt**（实测 `link.invalid` 无效兜底），改为 tikhub 单 provider（与 douyin 一致） | 去掉无效降级，错误信息更清晰 |
| D7 | 缓存语义 | note_id 在 URL 解析阶段已拿到（`/explore/<id>`），**缓存键不变**，无需 douyin 那种 hybrid 回填 | xhs 无短链展开问题 |

---

## 4. 目标架构（数据流）

```
resolve.py
  └─ url_parser: 解析出 platform=xiaohongshu, video_id=note_id（已有）
       │  （原始 url 含 xsec_token，原样透传给 provider）
       ▼
VideoResolver: xiaohongshu → [tikhub_provider]   (D6: 去掉 cobalt)
       ▼
TikHubProvider.fetch_video_info
  └─ platform==xiaohongshu → _fetch_xiaohongshu(video_id=note_id, original_url)   (D2)
       ├─ 取 token = regex(original_url, xsec_token)                              (D3)
       ├─ 链 = [ (app_v2, note_id),  (web_v3, note_id+token if token else skip) ] (D1)
       ├─ for 端点 in 链:
       │     data = call(端点)            # 单端 max_retries=0, 25s              (D5)
       │     decision = _classify_xhs(data)                                       (D4)
       │       terminal → 抛 XhsTerminalError（图文/私密，短路）
       │       retryable → 记录，试下一端点
       │       ok → _xhs_has_playable(data)? 命中返回 : 记 parse_failed 试下一端点
       └─ 全失败 → VideoNotFoundError；超总预算 → ProviderError                  (D5)
       ▼
TikHubAdapter → XiaohongshuService._parse_response(data)   # schema 自适应         (D2)
  └─ 依次尝试：
       app_v2:  data.data[0].video_info_v2.media.stream.h264[].master_url
       web_v3:  data.data.items[0].noteCard.video.media.stream.h264[].masterUrl
       旧:      data.video.media.stream.h264[].master_url（保留兜底）
```

---

## 5. 改动清单

| 文件 | 改动 |
|------|------|
| `app/services/providers/tikhub.py` | 新增 `XHS_CHAIN`、`_fetch_xiaohongshu`、`_call_xhs_endpoint`、`_classify_xhs`、`_xhs_has_playable`、`_extract_xsec_token`；`fetch_video_info` 在 `platform==xiaohongshu` 时走链；`PLATFORM_ENDPOINTS`/`PLATFORM_PARAMS` 注释/清理旧死端点 |
| `app/services/platforms/xiaohongshu.py` | `_parse_response` 改 schema 自适应（提取 `_extract_note`：依次匹配 app_v2 / web_v3 / 旧三种结构，归一到 VideoInfo）；camelCase/snake 字段双名兼容；`get_video_info` 端点更新（或弃用，统一由 provider 取数） |
| `app/services/base.py`(providers) | 新增 `XhsTerminalError(ProviderError)`（复刻 `DouyinTerminalError`） |
| `app/services/video_resolver.py` | `xiaohongshu` 链去掉 cobalt（D6）；`XhsTerminalError` 不再降级（复刻 douyin 终态处理 `:224`） |
| `tests/` | 复刻 douyin 测试范式：分类器三态、链命中/降级/全失败、终态短路、两 schema 解析、token 缺失只走 app_v2 |

---

## 6. 风险与边界

1. **图文笔记**：视频端点对图文笔记返回无视频流 → `_classify_xhs` 判 terminal 短路，错误信息明确（"该笔记为图文，无视频"）而非泛化失败。
2. **token 缺失/过期**：app_v2 不依赖 token 首选可覆盖；web_v3 在无 token 时跳过，不报错。
3. **schema 漂移**：解析器多结构兜底 + 解析失败归 `retryable`，单结构变更不致全挂。
4. **计费**：每命中一次端点计费一次；链是"命中即停"，正常只调 1 次（app_v2 命中）；仅 app_v2 失败才调 web_v3。
5. **画质**：app_v2 实测 h264 258 档、web_v3 259 档，均为 rednotecdn 无水印 mp4，差异可忽略；解析器统一取 `h264[0].master_url`。

---

## 7. 验收

- [ ] 原始报错链接（视频笔记）解析成功，返回 rednotecdn mp4 无水印直链
- [ ] 图文笔记返回明确终态错误，不空转重试
- [ ] app_v2 注入失败时自动降级 web_v3 命中
- [ ] 单测覆盖：分类三态 / 链降级 / 终态短路 / 双 schema 解析 / 无 token 路径
- [ ] 生产容器实测通过后再 commit

---

## 8. 解析器字段双名兼容（评审 CQ1，必修）

三结构字段命名不一致，解析器须抽 `_pick(d, *names)` 归一，否则 web_v3 路径宽高错乱：

| 字段 | 旧(死) | app_v2 | web_v3 |
|------|--------|--------|--------|
| 直链 | `master_url` | `master_url` | `masterUrl` |
| 宽 | `weight` | `weight`/`width` | `width` |
| 高 | `height` | `height` | `height` |
| 流路径 | `data.video.media.stream.h264` | `data.data[0].video_info_v2.media.stream.h264` | `data.data.items[0].noteCard.video.media.stream.h264` |

`_parse_response` 先按结构定位 note 节点（依次试三结构），再用 `_pick` 取字段。

---

## Implementation Tasks

- [ ] **T1 (P1)** — tikhub.py — 新增 `XHS_CHAIN`/`_fetch_xiaohongshu`/`_call_xhs_endpoint`/`_classify_xhs`/`_xhs_has_playable`/`_extract_xsec_token`，`fetch_video_info` 在 `xiaohongshu` 分支走链
  - Files: `app/services/providers/tikhub.py`, `app/services/providers/base.py`(新增 `XhsTerminalError`)
  - Verify: 原始报错链接解析返回 rednotecdn mp4
- [ ] **T2 (P1)** — xiaohongshu.py — `_parse_response` 改 schema 自适应 + `_pick` 双名兼容；删 `get_video_info()` 与 `self.endpoint`
  - Files: `app/services/platforms/xiaohongshu.py`
  - Verify: app_v2 与 web_v3 两样本均解析出正确宽高/直链
- [ ] **T3 (P1)** — video_resolver.py — `xiaohongshu` 链去 cobalt；`XhsTerminalError` 不降级
  - Files: `app/services/video_resolver.py`
  - Verify: 图文笔记返回明确终态，不空转
- [ ] **T4 (P1)** — 测试 — 复刻 douyin 范式覆盖 §3 全部 12 路径
  - Files: `tests/`
  - Verify: 新增单测全绿

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | clean | 3 issues, 0 critical gaps（全部已决并入方案） |

- **VERDICT:** ENG CLEARED — 可进入实现。链序/Cobalt/死代码三项决策已锁定，CQ1 字段双名兼容已并入 §8，测试 12 路径已列入 T4。
- **失败模式覆盖:** 图文笔记 terminal 短路（有测试）、token 过期由 app_v2 兜底；无"无测试+无错误处理+静默失败"的关键缺口。

NO UNRESOLVED DECISIONS
