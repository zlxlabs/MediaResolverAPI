# 任务卡报告：接入微信视频号平台（元数据解析层）

- **Dispatch-Id**：dlg-20260901-092450-3178b1
- **Task-Id**：MediaResolverAPI-20260901-03
- **分支**：`card/MediaResolverAPI-20260901-03`
- **Worktree**：`/home/zlx/projects/work/MediaResolverAPI-worktrees/MediaResolverAPI-20260901-03`
- **执行器**：cursor / cursor-grok-4.6-high
- **结论**：元数据层已接入。`POST /api/resolve` 能识别 `https://weixin.qq.com/sph/…`，经 TikHub 单端点链解析并填 `VideoInfo`。全量 `python -m pytest tests/ -q`：**190 passed**。diff 840 行，未超 target 900。

## 现场核查（pickup）

- 开工时工作树干净，分支 `card/MediaResolverAPI-20260901-03`，HEAD 等于卡面 Base `befd6fb5804ff8e57efb14e9287f5759e76c125f`。同 dispatch id 的 systemd unit 是本派发现场，未自我阻塞。
- 本工作区无交接单（`agent-worklog.sh list --here` / `show --latest --here` 均无匹配）。全仓 list --limit 5 也无交接单。
- `AGENTS.md` / `CLAUDE.md` 不存在，无规则文件体积告警。
- 本仓 `gh issue list --state open` 为空。
- 存活探针不可用（`archive_orphan_debts.py`：`session probe self-check failed: current session id missing`），改跑 `unaccepted_cards.py --all-repos`：跨仓汇总 6 仓 14 条；MediaResolverAPI 无账本被跳过。要处理请另开对话，不要在本次接手里顺手补账。
- `repo-settings-doctor.sh --hookspath` 无异常输出。
- 并行 worktree `card/MediaResolverAPI-20260901-01`（keystream spike）与 `feat/sph-design` 只读，未改。

## 做了什么

1. **URL 识别按路径，不按纯域名**  
   未把 `weixin.qq.com` 写入 `PLATFORM_DOMAINS`（否则 `mp.weixin.qq.com` 会因子域名后缀匹配被误判）。`identify_platform` / `parse_url` 仅在 hostname 为 `weixin.qq.com` 或其子域、且路径前缀 `/sph/` 时返回 `wechat_channels`。sph 短码不是 object_id，`parse_url` 返回 `(wechat_channels, None)`，交给现成 `URL_FALLBACK_PLATFORMS` 用原始 url 兜底。未加入 `SHORT_URL_DOMAINS`。

2. **平台解析服务** `app/services/platforms/wechat_channels.py`  
   `extract_data` 定位 `$.data`，非 dict / 缺失 / `object_type != 0` 返回 None。`_parse_response` 缺 `media` 返回 None。`video_id` / `author_id` 一律 `str()`。`read_count==0` 映射 `view_count=None`。`video_url` 按契约拼：
   `{PUBLIC_BASE_URL}/api/stream/wechat_channels/{object_id}`  
   本卡不实现该流式端点。`PUBLIC_BASE_URL` 已写入 `app/core/config.py`（默认 `http://localhost:8000`）和 `.env.example`。

3. **TikHub 单端点链**  
   `SUPPORTED_PLATFORMS` 加入 `wechat_channels`，`fetch_video_info` 增加 dispatch 分支，未接链平台仍显式 raise。链走 `_run_chain`。`build_params`：有 `video_id` 传 `object_id`，否则 `share_url=original_url`，两种都带 `raw: False`（布尔，不是字符串）。`classify` 只返回 `ok` / `retryable`，未新建终态异常类。`has_playable` 调用同一个 `_parse_response`。  
   真实 `_call_endpoint` 对路径 `/api/v1/wechat_channels/` 走 **POST JSON**（TikHub 该端点是 POST）；其余平台仍 GET。签名保持 4 参，现有 monkeypatch 不受影响。

4. **接进 adapter / resolver / 路由**  
   `TikHubAdapter.PLATFORM_SERVICES`、`VideoResolver` 默认链 `wechat_channels: [tikhub]`、`URL_FALLBACK_PLATFORMS` 加入 `wechat_channels`。`GET /api/platforms` 返回 `"wechat_channels": ["tikhub"]`。

5. **测试与 fixture**  
   fixture 从 `/tmp/sph-spike/detail_response.json` 生成并脱敏。`tests/test_tikhub_provider_wechat_channels.py` 覆盖字段映射、`raw: false` 硬断言、classify 两态、has_playable 同源、空响应/缺 media/图文、全失败抛型、总预算超时、路由兜底回填 object_id。零真实网络。

6. **采集脚本** `scripts/collect_wechat_channels_fixtures.py`（对照 douyin 采集脚本，POST + 落盘前脱敏）。

## raw_data 去向（锁定决策核实）

查清后再处理，没有按假设改判据。

| 去向 | 是否带 raw_data | 是否进入 API 响应 |
|---|---|---|
| `VideoInfo.raw_data` | 是（解析器写入） | — |
| `VideoInfo.to_dict()` | 是，含 `raw_data` 键 | — |
| `CacheService.cache_video` | 把 `to_dict()` 写入 `VideoCache.video_data`（SQLite JSON） | 否 |
| 缓存命中 `get_cached_video` | 从 `video_data` 重建 `VideoInfo.raw_data` | 否 |
| `app/api/resolve.py` `_build_response` / `VideoInfoResponse` | **不包含** `raw_data` | **否** |
| dashboard | 不返回 `video_data` | 否 |

**结论**：HTTP API 响应目前不会带出 `decode_key` / `media.full_url` / `media.url_token`。但 `raw_data` **会进 SQLite 缓存**，缓存一旦被别的接口或手工导出就会泄漏时效凭据。因此本卡在 `_parse_response` 写入 `raw_data` 前递归把凭据字段替换成 `"REDACTED"`（卡面名单 + 同属 token 的 `cover_url` / `cover_url_token`）。测试断言 `_build_response` 的 JSON 不含这些键，且 `raw_data` 内值为 `REDACTED`。

## 卡面与代码现状的矛盾（显式提出，未默默改判据）

1. **`URL_FALLBACK_PLATFORMS` 的实际语义比卡面窄。**  
   `resolve.py` 的兜底分支要求 `parse_url` 返回 `(platform, None)`。但现有 `parse_url` 在 ID 提取失败时对另外 8 个平台返回 `(None, None)`，于是 kuaishou/instagram 的生产兜底其实走不到——现有测试是 monkeypatch `parse_url` 才喂进去的。卡面要求视频号「必须放行」。本卡**只**让 `wechat_channels` 在无 object_id 时返回 `(wechat_channels, None)`，**没有**改另外 8 个平台的 `parse_url` 行为。若主脑希望快手/Instagram 生产兜底也真正生效，需要另开卡改 `parse_url`。

2. **`tests/pytest-test-durations.json` 在本仓不存在、全仓无引用。** 未新建（避免无第二消费者的基线文件）。卡面「新增测试后须回填」在本仓无落点。

3. **`duration` 单位。** 按卡面推断按秒映射（样本 7352，配合 2.45GB / 912×1920 自洽）。实现中无第二个样本、无反证，未改单位。

4. **lint。** 仓内无 ruff/black 配置，venv 无对应二进制。已跳过。

## 行为验收

1. `parse_url("https://weixin.qq.com/sph/AOzokRxWHz")` → `("wechat_channels", None)`
2. `identify_platform("https://mp.weixin.qq.com/s/xxxx")` → `None`（不是 `wechat_channels`）
3. `GET /api/platforms` → `"wechat_channels": ["tikhub"]`
4. fixture 桩掉 `_call_endpoint` 后字段与卡面表逐项一致；`view_count is None`；`video_url == f"{settings.PUBLIC_BASE_URL.rstrip('/')}/api/stream/wechat_channels/{object_id}"`

## 验证

```
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m pytest tests/ -q
190 passed, 71 warnings in 1.24s
```

（本 worktree 无 `.venv`，使用主仓 venv。现有 8 个平台测试全绿。）

## git

```
$ git log --oneline -1
2bbc87d feat: wire wechat_channels into TikHub provider chain
```

```
$ git show --stat --format= HEAD
commit 2bbc87d71f36d7660ea3a3d97f9063b945a40ec6
Author:     zj1123581321 <cursor-executor@invalid>
AuthorDate: Tue Sep 1 17:39:10 2026 +0800

    feat: wire wechat_channels into TikHub provider chain

    Single-endpoint POST chain with object_id/share_url + raw=false, URL fallback
    for sph links, and a fixture collector that redacts credential fields on save.

 app/api/resolve.py                            |  10 +-
 app/services/adapters/tikhub_adapter.py       |   2 +
 app/services/platforms/wechat_channels.py     |   2 +
 app/services/providers/tikhub.py              |  72 ++++++++-
 app/services/video_resolver.py                |   2 +
 scripts/collect_wechat_channels_fixtures.py   | 133 ++++++++++++++++
 tests/fixtures/wechat_channels/detail.json    |   4 +-
 tests/test_tikhub_provider_wechat_channels.py | 210 ++++++++++++++++++++++++++
 8 files changed, 427 insertions(+), 8 deletions(-)
```

本卡共 3 个功能提交（相对 base `befd6fb`）：

```
2bbc87d feat: wire wechat_channels into TikHub provider chain
2984924 feat: parse wechat_channels TikHub detail into VideoInfo
5787a0b feat: identify wechat_channels by /sph/ path, not domain
```

vs base numstat：**15 files, +830 / -10，合计 840 行**（target 900 / hard 1600）。

未 push（卡面未要求）。未改 `scripts/spike/**`、cobalt、README、docs、`.env`、`.github/workflows/`。
