# 会话交接 Prompt：抽取通用降级引擎 + 全平台多级端点降级

> 把本文件整段作为新会话的初始 prompt。它是自包含的：包含背景、目标、架构、
> 各平台端点清单、TDD 计划、部署与验证、坑位。新会话无需依赖任何历史上下文。

---

## 0. 你的任务（一句话）

把目前在抖音、小红书各实现一遍、结构几乎相同的「TikHub 多级端点降级」逻辑
**抽成一个通用引擎**，先把抖音/小红书无行为变更地重构到引擎上（有测试护着），
再用引擎给 **快手、TikTok、Instagram、YouTube** 接入多级端点降级。

工作顺序遵循「make the change easy, then make the easy change」：先重构，再扩展。

优先级：快手 P1（当前 tikhub 单源 + 单端点 + 无 cobalt 兜底，纯单点故障）→
TikTok / Instagram / YouTube P2（已有 cobalt 兜底，收益是加固 tikhub 层）。

---

## 1. 背景：为什么做这个

TikHub 会不定期下线端点（已踩 3 次：Instagram 旧端点 404、小红书 `web/get_note_info_v3`
下线、抖音单点风险）。单端点 = 单点故障。已有的解决范式是 provider 内「多级端点
降级链」：同一个 ID 串行打多个端点，命中即停，终态短路。

现状（`git log` 可见）：
- `fix(instagram): 兼容 /reels/ 复数链接`
- 抖音多级降级（`docs/douyin-fallback-design.md`，状态：已实现）
- 小红书多级降级（`docs/xiaohongshu-fallback-design.md`，状态：已实现上线）

问题：抖音的 `_fetch_douyin` 和小红书的 `_fetch_xiaohongshu` 是两份近乎重复的代码
（取数→三态分类→解析校验→降级→超时预算），只有「端点链 + 分类器」不同。再给 4 个
平台各抄一遍 = 6 份重复。需要抽通用引擎，每个平台只配置差异部分。

---

## 2. 必读的现有代码（先读再动）

| 文件 | 关键内容 |
|------|----------|
| `app/services/providers/tikhub.py` | **核心**。`DOUYIN_CHAIN`/`_fetch_douyin`/`_classify_douyin`/`_call_douyin_endpoint`/`_douyin_has_playable`；`XHS_CHAIN`/`_fetch_xiaohongshu`/`_classify_xhs`/`_call_xhs_endpoint`/`_xhs_has_playable`/`_extract_xsec_token`；`PLATFORM_ENDPOINTS`/`PLATFORM_PARAMS`；`fetch_video_info` 里 douyin/xiaohongshu 的分支派发 |
| `app/services/providers/base.py` | `TerminalError`(基类) / `DouyinTerminalError` / `XhsTerminalError`。新平台若有终态语义需加对应子类 |
| `app/services/platforms/<plat>.py` | 各平台 `_parse_response`（schema 自适应）。抖音 `DouyinService._extract_detail`、小红书 `XiaohongshuService.extract_note`+`_pick` 是 schema 自适应解析范例 |
| `app/services/adapters/tikhub_adapter.py` | `adapt()` 调 `service._parse_response(raw_data)`，**无需改动**（解析器自适应即可） |
| `app/services/video_resolver.py` | `default_chains`（平台→provider 列表，约 78-89 行）；`except TerminalError`（约 224 行，终态停链不 fallback） |
| `tests/test_tikhub_provider_douyin.py` | **降级链测试范式**：`_provider_with(monkeypatch, mapping)` 桩替换 `_call_*_endpoint`，断言 `calls` 顺序 + 命中/短路/全失败 |
| `tests/test_tikhub_provider_xiaohongshu.py` | 同上 + token 缺失跳过、双 schema |
| `tests/test_xiaohongshu_parser.py` / `tests/test_douyin_parser.py` | 解析器单测范式 |
| `tests/fixtures/{douyin,xiaohongshu}/*.json` | fixture 范式（每端点/每状态一个 json） |

降级链统一形态（抽引擎时以此为蓝本）：
```
取 token/参数 → 构造链(按条件裁剪，如缺 token 跳过需 token 的端点)
for 端点 in 链:
    data = _call_endpoint(端点, 参数)        # 单端 max_retries=0, per_timeout
    decision = classify(data)                # terminal | retryable | ok
      terminal → raise <Plat>TerminalError   # 立即短路
      retryable → 记录, 下一端点
      ok → has_playable(data)? return data : 记 parse_failed, 下一端点
全程 asyncio.timeout(total_budget) 兜底 → 超时 raise ProviderError("timed out")
全链未命中 → raise VideoNotFoundError
```

---

## 3. 目标架构：通用引擎

在 `TikHubProvider` 内新增一个**配置驱动**的通用引擎，把可变点抽成回调/配置：

```python
# 伪代码，按实际调整
async def _run_chain(
    self, *,
    chain: list[tuple[str, str]],          # [(name, path), ...]
    build_params: Callable[[str], dict],    # name -> query params（含 token/photo_id/aweme_id 等）
    classify: Callable[[dict], str],        # data -> "terminal"|"retryable"|"ok"
    has_playable: Callable[[dict], bool],   # data -> 能否解析出直链
    terminal_exc: type[TerminalError],      # 该平台终态异常类
    total_budget: float,
    per_timeout: float,
    target: str,                            # 日志用标识
) -> dict:
    ...  # 复刻 §2 蓝本，把 douyin/xhs 的公共骨架搬进来

async def _call_endpoint(self, name, path, params, per_timeout) -> dict:
    ...  # 合并现有 _call_douyin_endpoint / _call_xhs_endpoint（两者仅参数构造不同）
         # 保留 httpx.HTTPStatusError 取错误体交分类器、401→ProviderError 的逻辑
```

每个平台只保留一个薄封装 + 一个分类器：
```python
async def _fetch_douyin(self, ...):     # 重构为调用 _run_chain
async def _fetch_xiaohongshu(self, ...):# 重构为调用 _run_chain
async def _fetch_kuaishou(self, ...):   # 新增
async def _fetch_tiktok(self, ...):     # 新增
# ...
@staticmethod
def _classify_<plat>(data) -> str: ...  # 各平台差异（见下）
```

`has_playable` 可统一为 `_has_playable(platform, data)`：实例化对应 `PLATFORM_SERVICES`
的 service，调 `_parse_response`，判 `info and info.video_url`（复用 adapter 的服务映射）。

**关键约束（必须守住，否则破坏现有行为）：**
1. **抖音 hybrid 兜底**（`use_hybrid`、`DOUYIN_HYBRID`）和 **terminal reason 扫整个 filter_list**（codex #9）的语义不能丢——重构后 `test_tikhub_provider_douyin.py` 必须仍全绿。
2. **小红书 token 缺失跳过 web_v3** 的语义不能丢——`test_tikhub_provider_xiaohongshu.py` 必须仍全绿。
3. **单端 `max_retries=0` + 总预算 `asyncio.timeout`** 不能丢（codex #3，防超时放大）。
4. 重构是**纯结构变更**：先让现有抖音/小红书测试在新引擎上全绿，再加新平台（Beck：不要同时做结构与行为变更）。

---

## 4. 各平台端点清单（已确认存在，行为/schema 需新会话实测）

> 已通过 openapi.json 确认端点**存在**；但每个端点是否真能出无水印直链、响应 schema、
> 是否需要额外参数，**必须在新会话用真实链接逐个实测**（见 §6 探测方法）。不要凭名字猜。

### 快手 kuaishou（P1，当前单端点 `web/fetch_one_video_v2`，param `photo_id`，无 cobalt）
候选链（实测后定序）：
```
kuaishou/web/fetch_one_video_v2   (现状)
kuaishou/web/fetch_one_video
kuaishou/web/fetch_one_video_by_url   (吃 url，免 photo_id 提取)
kuaishou/app/fetch_one_video
kuaishou/app/fetch_one_video_by_url
```
- 需确认：各端点 param 名（`photo_id` vs `url`）、响应结构（现解析器 `KuaishouService._parse_response` 期望 `data.photo`）、是否有终态语义（私密/删除）。
- **同时给快手 default_chains 评估是否加 cobalt 兜底**（快手当前完全无兜底）。Cobalt 是否支持快手需实测；不支持就维持 tikhub 单源 + 多级链。

### TikTok（P2，当前 `app/v3/fetch_one_video`，param `aweme_id`，有 cobalt）
候选链：
```
tiktok/app/v3/fetch_one_video      (现状)
tiktok/app/v3/fetch_one_video_v2
tiktok/app/v3/fetch_one_video_v3
tiktok/app/v3/fetch_one_video_by_share_url_v2   (吃 share_url)
tiktok/web/fetch_post_detail
```
- TikTok 与抖音同源（`aweme_detail`/`aweme_id`/`filter_list`）。**优先尝试复用 `_classify_douyin` 和 `DouyinService` 的 schema 自适应解析**（实测确认结构一致再复用，否则写 TikTokService 自适应解析）。

### Instagram（P2，当前 `v2/fetch_post_info`，param `code_or_url`，有 cobalt）
候选链：
```
instagram/v2/fetch_post_info       (现状)
instagram/v3/get_post_info
instagram/v3/get_post_info_by_code
instagram/v1/fetch_post_by_url
instagram/v1/fetch_post_by_id
```
- 需确认各端点 param（`code_or_url` / `url` / `code` / `id`）与响应结构差异；`InstagramService._parse_response` 已兼容多格式（见现 `_validate_response` instagram 分支），扩展为自适应。

### YouTube（P3，当前 `web/get_video_info`，param `video_id`，有 cobalt）
候选链：
```
youtube/web/get_video_info         (现状)
youtube/web/get_video_info_v2
youtube/web/get_video_info_v3
youtube/web_v2/get_video_info
```
- YouTube 一般稳定，收益最低，放最后。

### 不适用：Pinterest / Facebook
cobalt 单源、无 tikhub 端点，provider 内 tikhub 链机制不适用。它们的单点是 cobalt
本身，唯一缓解是加第二 provider —— **本任务范围外**，仅在文档/TODO 标注。

---

## 5. TDD 执行计划

按阶段提交，每阶段测试全绿即 commit（用户偏好：测试通过及时 commit；过程中非必要不停下提问）。

1. **重构引擎（无行为变更）**
   - 抽 `_run_chain` + `_call_endpoint`，把 `_fetch_douyin`/`_fetch_xiaohongshu` 改为调用引擎。
   - 跑 `tests/test_tikhub_provider_douyin.py`、`tests/test_tikhub_provider_xiaohongshu.py`、解析器测试、全量 → **必须保持全绿（当前基线 97 passed）**。
   - commit：`refactor(tikhub): 抽取通用多级降级引擎，抖音/小红书迁移`
2. **快手（P1）**：先实测端点（§6）→ 写 fixtures + `tests/test_tikhub_provider_kuaishou.py`（红）→ 加 `KUAISHOU_CHAIN`/`_fetch_kuaishou`/`_classify_kuaishou`、必要时 `KuaishouTerminalError`、`KuaishouService._parse_response` 自适应 → 绿 → commit。
3. **TikTok（P2）**：同法；优先复用抖音分类器/解析器。
4. **Instagram（P2）**：同法。
5. **YouTube（P3）**：同法。
6. 每个平台：`video_resolver.py` 的 `fetch_video_info` 加分支派发（或引擎统一派发）、必要时调 `default_chains` 与 `except TerminalError`。

测试范式直接抄 `test_tikhub_provider_xiaohongshu.py`：`_provider_with` 桩替换 `_call_endpoint`，
覆盖：分类三态 / 链命中即停 / 逐级降级 / 终态短路 / 全失败 / 单端 HTTPStatusError 取体 / 总预算超时 / （IG/快手）by_url 或 token/缺参跳过。

---

## 6. TikHub 端点实测方法（关键，别凭名字猜）

API key 在服务器 `.env`（`TIKHUB_API_KEY`）。Bearer token 见服务器 `.env`，
**不要把真实 token 写进本文档或任何提交物**（占位符：`<TIKHUB_BEARER>`）。

探测某端点（以快手为例，换真实链接）：
```bash
AUTH='Authorization: Bearer <KEY>'
curl -s -G 'https://api.tikhub.io/api/v1/kuaishou/web/fetch_one_video' \
  --data-urlencode 'photo_id=<ID>' -H "$AUTH" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print("code",d.get("code")); print(json.dumps(d.get("data"),ensure_ascii=False)[:800])'
```
- HTTP 200 + 有视频流 = 可用；404 = 端点已下线；422 = 参数名不对（看 detail.loc 改参数）。
- 拿响应结构定位 `video_url` 精确路径 + 字段命名（camel/snake），喂给解析器。
- 端点参数 schema 看 `openapi.json` 的 `paths[...].parameters`。

需要每个平台准备 1-2 个真实测试链接（视频帖；IG/快手最好再来一个图文/私密帖测终态）。

---

## 7. 部署与生产验证

部署走 `docker-deploy` skill（先 build）。目标见 `docker/deploy_targets.json`：
`fordeal:/home/lixing/docker/media-resolver-api`。

```bash
# build + push ACR
cd docker && bash push_to_acr.sh
# 服务器拉取重启
scp docker/pull_and_deploy.sh fordeal:/home/lixing/docker/media-resolver-api/docker/
ssh fordeal "cd /home/lixing/docker/media-resolver-api && bash docker/pull_and_deploy.sh"
```

**坑位：**
- ACR 拉取偶发 `connection reset by peer`（瞬时网络）→ **直接重试一次**即成功。
- **生产实测不能打 `localhost:8000`**（命中宿主机别的服务，返回 `{"message":"Unauthorized"}`）。
  必须打容器 IP：
  ```bash
  ssh fordeal 'IP=$(docker inspect -f "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}" media-resolver-api); \
    curl -s -X POST http://$IP:8000/api/resolve \
    -H "Content-Type: application/json" -H "X-API-Key: <PROD_API_KEY>" \
    -d "{\"url\":\"<真实链接>\",\"translate\":false,\"force_refresh\":true}"'
  ```
- compose 文件用 `docker/docker-compose.deploy.yml`（服务器侧 owned，部署脚本自动识别）。

---

## 8. 环境与命令

- Python 解释器：**`.venv/bin/python`**（项目根 `.venv`）。跑测试：`.venv/bin/python -m pytest -q`。
- 项目根：`/home/zlx/projects/work/MediaResolverAPI`。
- 当前测试基线：**97 passed**（重构后不得减少）。
- 大量命令/日志输出优先用 context-mode 的 `ctx_batch_execute`（避免污染上下文）。

---

## 9. 收尾（任务完成时）

- [ ] 通用引擎抽取完成，抖音/小红书迁移后**原有测试全绿**（无行为变更）。
- [ ] 快手多级降级上线，生产实测真实链接成功；评估并落定快手是否加 cobalt。
- [ ] TikTok / Instagram / YouTube 多级降级上线，各自生产实测通过。
- [ ] 每个平台单测覆盖分类三态/链降级/终态短路/全失败/超时。
- [ ] 全量 pytest 绿；分阶段已 commit & push。
- [ ] 文档同步：
      - 新建 `docs/generic-fallback-engine.md`（引擎设计 + 各平台链配置表）。
      - 更新 `README.md` 平台表与「多级端点降级」小节（加快手/TikTok/IG/YT）。
      - 更新 `docs/{douyin,xiaohongshu}-fallback-design.md` 注明已迁移到通用引擎。
- [ ] 走 `docker-deploy` 部署到 fordeal 并生产验证。

---

## 10. 决策记录（已与用户确认）

- 范围：**全平台都上**（快手 + TikTok + IG + YouTube）。
- 方式：**先抽通用引擎再扩展**（重构 douyin/xhs 到引擎，再加新平台）。
- 风格：TDD，测试通过及时 commit，过程中非必要不停下提问，完成后同步文档。

---

## 11. 评审产出（/plan-eng-review 2026-06-18，已与用户逐项确认）

### What already exists（复用，勿重建）
- `_call_douyin_endpoint` / `_call_xhs_endpoint` ~95% 重复（httpx body 提取、401、`max_retries=0`），只差 param 构造 → 合并为 `_call_endpoint`（引擎核心 DRY 目标）。
- `TerminalError` 基类 + `video_resolver.py:224` 的 `except TerminalError` 短路已可直接泛化到新平台。
- `tikhub_adapter.PLATFORM_SERVICES` 已映射全 6 平台 → `_parse_response`；`has_playable` 直接复用此映射（无需新接线）。
- TODOS.md「P3: 端点降级链泛化到 TikTok」已被本计划取代。

### NOT in scope（已考虑并显式延后）
- **凭证轮换**：文档已脱敏（占位符）；服务器侧轮换 TikHub bearer + 生产 X-API-Key 由用户执行（本次选择暂不轮换）。
- **双重解析优化**：保留 raw-dict 契约（最小 diff、零回归），双重解析记 TODO（见 TODOS.md）。
- **平台 service 死 `get_video_info` 清理**：记 TODO，建议随引擎收尾一并清。
- **Pinterest / Facebook**：cobalt 单源、无 tikhub 端点，机制不适用；唯一缓解是加第二 provider，范围外。

### Failure modes（新代码路径 → 是否有测试 / 错误处理 / 用户可见）
| 失败场景 | 测试 | 错误处理 | 用户可见 |
|---|---|---|---|
| `_run_chain` 总预算超时 | ✅ | ✅ ProviderError("timed out") | ✅ 非静默 |
| `_call_endpoint` 401 | ✅(待补1处) | ✅ ProviderError | ✅ |
| 逐端点 param 映错 → 静默 422 | ✅ 已列为硬要求 | ✅ 降级 | ✅ |
| by_url 路由放行后传给不支持 provider | ⚠️ 需路由层测试 | ✅ 降级 VideoNotFound | ✅ |
| cobalt 平台误判 terminal → 跳过 cobalt | ✅ 规则:cobalt 平台不出 terminal | ✅ | ✅ |
| **`_has_playable` 失败端点写盘(tiktok.py:226)** | ❌ | ❌ | ❌ **静默** → **CRITICAL GAP** |

**CRITICAL GAP（1）**：`_has_playable` 校验时调 `TikTokService._parse_response`，失败会触发 `_save_failed_response` 写文件；5 端点链每次失败放大写盘且无人察觉。修复：校验路径抑制写盘（见任务 T12）。

### Worktree parallelization
| 步骤 | 模块 | 依赖 |
|---|---|---|
| 引擎重构 | `providers/tikhub.py` | — |
| 路由 by_url 放行 | `api/resolve.py` | — |
| 快手/TikTok/IG/YouTube | `providers/tikhub.py` + `platforms/<plat>.py` | 引擎重构 |

- **Lane A（先，串行）**：引擎重构（`tikhub.py`）。一切依赖它，必须先 merge。
- **Lane B（与 A 独立）**：`resolve.py` by_url 放行（小改 + 路由测试），可并行开发。
- **Lane C/D/E/F（A 之后）**：4 平台。各平台的 parser + 测试 + fixtures 互相独立、可并行 worktree；**但 `tikhub.py` 的链常量 + dispatch + `_fetch_<plat>` 集成编辑必须串行 merge**。
- **冲突标记**：4 平台都改 `tikhub.py` → 串行 merge 集成部分，避免冲突；parser/test/fixture 并行无碍。

执行顺序：Lane A 先 → 然后 B 与 (C..F 的 parser/test/fixture 开发) 并行 → tikhub.py 集成按 P1(快手)→P2(TikTok/IG)→P3(YouTube) 串行收口。

## Implementation Tasks
Synthesized from this review's findings. Each task derives from a specific finding above.

- [ ] **T1 (P1, human: ~5min / CC: ~2min)** — security — 脱敏 handoff 凭证(已做) + 服务器侧轮换 TikHub bearer + 生产 X-API-Key
  - Surfaced by: Outside voice — `docs/handoff:190,227` 明文生产凭证
  - Verify: `grep -nE '8p4MD|IsGxW1Bx' docs/` 返回空
- [ ] **T2 (P1, human: ~1day / CC: ~1h)** — tikhub — 抽 `_run_chain`+`_call_endpoint` 引擎，抖音/小红书迁移，97 测试全绿
  - Surfaced by: DRY — `_call_douyin_endpoint`/`_call_xhs_endpoint` ~95% 重复
  - Verify: `.venv/bin/python -m pytest -q`（≥97 passed）
- [ ] **T3 (P1, human: ~2h / CC: ~20min)** — tikhub — 全 6 平台迁移后删通用 `fetch_video_info` 路径 + `_validate_response` 死分支
  - Surfaced by: Code Quality Issue 3 — 两套有效性来源
- [ ] **T4 (P1, human: ~0.5day / CC: ~30min)** — kuaishou — 多级链 + `_classify_kuaishou`(仅 ok/retryable) + parser 自适应
  - Surfaced by: Step 0 — 快手唯一真单点故障(无 cobalt)
  - Verify: 生产实测真实链接成功
- [ ] **T5 (P1, human: ~2h / CC: ~20min)** — routing — 改 `resolve.py:138` 透传 original_url 走 by_url + 路由层测试
  - Surfaced by: Outside voice — by_url 端点被路由层拦截
- [ ] **T6 (P2, human: ~0.5day / CC: ~25min)** — tiktok — TikTok 链；has_playable 用 TikTokService，classify 复用 `_classify_douyin`
  - Surfaced by: Outside voice — DouyinService 取流逻辑与 TikTok 不一致(play_addr_h264)
- [ ] **T7 (P2, human: ~0.5day / CC: ~25min)** — instagram — IG 链 + 多 param 构造；非视频不判终态(轮播子节点)
  - Surfaced by: Outside voice — `is_video=false ≠ unavailable`
- [ ] **T8 (P2, human: ~0.5day / CC: ~20min)** — youtube — YouTube 链；先对齐 `_parse_response` 与 `_validate_response` schema
  - Surfaced by: Outside voice — `youtube.py:62` vs `tikhub.py:563` 不一致
- [ ] **T9 (P1, human: ~30min / CC: ~10min)** — tikhub — 终态规则：有 cobalt 平台(TikTok/IG/YT)classify 一律不出 terminal
  - Surfaced by: Outside voice — VideoResolver 遇 terminal 停所有 fallback
- [ ] **T10 (P2, human: ~1h / CC: ~15min)** — tikhub — per-chain 超时调参：长链单端 ~10s、总预算 ~60s（实测 p95 定值）
  - Surfaced by: Performance Issue 4 — 50s 预算 < 5×25s，深度降级不可达
- [ ] **T11 (P1, human: ~0.5day / CC: ~30min)** — tests — 每平台矩阵 + 逐端点 param 构造硬断言 + 引擎超时/全失败/终态短路
  - Surfaced by: Test review — param 构造静默 422 gap
- [ ] **T12 (P2, human: ~30min / CC: ~10min)** — tikhub — `_has_playable` 校验路径抑制 `_save_failed_response`
  - Surfaced by: Outside voice — TikTokService 解析失败写盘(`tiktok.py:226`)

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — (stale >7d) | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 1 | issues_found | ~13 raised, 4 → decisions, rest folded |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | issues_open | 9 issues, 1 critical gap |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | n/a (backend) |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **CODEX:** 秘钥泄露(已脱敏)、by_url 被路由层拦截、TikTok 解析器复用错误、cobalt 平台终态风险、YouTube parser/validation 不一致 — 均已转为 T1/T5/T6/T9/T8。
- **CROSS-MODEL:** 引擎是否过度抽象 — codex 倾向「显式 endpoint spec」，本评审倾向计划的 callback 设计；两者实为同一形态(callback 即数据驱动 spec)，无实质冲突，保留计划方案。
- **VERDICT:** ENG 评审完成（9 findings 全部转为带主人的任务 T1–T12，1 critical gap 已捕获为 T12）。全平台多级降级照计划全做，scope 未缩。实现前请先做 T1(凭证) + T2(引擎重构基线)。

NO UNRESOLVED DECISIONS
