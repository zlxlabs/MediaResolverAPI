# 修复报告：video_url 不再静默拼 localhost

- **Dispatch-Id**：dlg-20260901-095348-f33ca3
- **分支**：`card/MediaResolverAPI-20260901-03`
- **root_cause_group**：可用性依赖一个新增的、既有部署必然缺失的配置项，且缺失时走默认值而非报错，失败形态为静默返回不可用地址。
- **introduced_by_commit**：`2984924`
- **结论**：解析层只存相对路径 `/api/stream/wechat_channels/{id}`；API 层用 `PUBLIC_BASE_URL`（非空）或 `request.base_url` 补成绝对地址。空配置不再吐 localhost。全量 194 passed。本卡 diff +164/-17。

## 现场

工作树沿用本会话 `card/MediaResolverAPI-20260901-03`，开工 HEAD 即卡面 Base `fdd81f8`。无交接单。同 dispatch id 的 unit 是本派发现场。

## 改了什么

1. `WechatChannelsService._parse_response`：`video_url = f"/api/stream/wechat_channels/{video_id}"`，不再读 `PUBLIC_BASE_URL`。
2. `app/api/resolve.py`：注入 FastAPI `Request`（参数名 `http_request`，避开 body 的 `request`）。`_build_response` 对以 `/` 开头的 `video_url` 补全；`http(s)://` 开头的第三方地址原样返回，无平台名判断。
3. `PUBLIC_BASE_URL` 默认值改为 `""`。`.env.example` 注释：留空即按请求 Host 推导，仅反代未透传正确 Host 时才需要设置。
4. P2：`_REDACT_KEYS` 只补 `cover_img_url`，现有脱敏测试加断言。

## `request.base_url` 在本仓部署形态下成不成立

**成立，没有改回配置项方案。** 证据：

- `docker/docker-compose.yml` / `docker-compose.deploy.yml` 只有 `8100:8000` / `8206:8000` 端口直映，仓库内无 nginx/caddy/traefik。
- `app/main.py` 无 `ProxyHeadersMiddleware`，全仓无 `X-Forwarded-*` 处理。
- uvicorn 直接对外时，调用方的 `Host` 就是 `request.base_url`（例如 `http://host:8206`），这正是调用方能访问的地址。

若将来前面加反代且不透传 Host，推导会变成容器内网地址——那时才需要设 `PUBLIC_BASE_URL`。这是 `.env.example` 里那句注释要覆盖的场景，不是现在的形态。

## 四格验收

| # | 结果 |
|---|---|
| 1 空配置 + Host `example.com:9000` | `http://example.com:9000/api/stream/wechat_channels/{id}` |
| 2 `PUBLIC_BASE_URL=https://media.example.org` | 显式配置优先，不用请求 Host |
| 3 缓存命中换 Host | 第二次是 `b.example.com`，不含 `a.example.com` |
| 4 抖音绝对 CDN URL | 与写入值完全一致，补全逻辑未碰到 |

第 3 格要走到现有「已知 video_id 才查缓存」分支：sph 短链 `parse_url` 生产路径拿不到 object_id，本来就不会查缓存。测试里把 `parse_url` 桩成返回 object_id，这样走的是真实 `get_cached_video` → `_build_response` 代码，没有另造缓存机制。未改 `resolve.py` 的缓存查找条件。

## 红验（第 3 格）

注入行（随后改回）：

```python
video_url = f"http://injected-host.invalid/api/stream/wechat_channels/{video_id}"
```

命令（无管道，退出码来自 pytest 本身）：

```
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m pytest \
  tests/test_resolve_wechat_channels_url.py::test_cache_hit_uses_current_request_host_not_cached_host -q --tb=short
```

实际输出（节选）：

```
F
E   AssertionError: assert False
E    +  where False = ...startswith('http://a.example.com/api/stream/wechat_channels/')
E    +    where ... = 'http://injected-host.invalid/api/stream/wechat_channels/14998022876670594427'.startswith
FAILED ...test_cache_hit_uses_current_request_host_not_cached_host
1 failed ...
PYTEST_EXIT:1
```

注入生效判据：失败信息里的 URL 正是注入的 `injected-host.invalid`，不是请求 Host。绝对 URL 不以 `/` 开头，API 层不改写，所以第一次响应就已经钉死假 host——这就是「缓存里带 host」的同一缺陷。确认变红后已改回相对路径。改回后再跑该测试通过。

## 验证

```
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m py_compile \
  app/core/config.py app/services/platforms/wechat_channels.py app/api/resolve.py \
  tests/test_tikhub_provider_wechat_channels.py tests/test_resolve_wechat_channels_url.py
COMPILE_EXIT:0

/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m pytest tests/ -q
194 passed, 80 warnings in 1.42s
PYTEST_EXIT:0
```

## git

```
$ git log --oneline -1
358ee97 fix: assemble wechat_channels video_url at request time
```

```
$ git show --stat --format= HEAD
commit 358ee97016f56e862f38346c8ee982c0b7a55f38
Author:     zj1123581321 <zj1123581321@users.noreply.github.com>
AuthorDate: Tue Sep 1 17:59:56 2026 +0800

    fix: assemble wechat_channels video_url at request time

    Store a host-free relative path in VideoInfo/cache and absolutize in the
    API layer from PUBLIC_BASE_URL or the request Host, so missing config no
    longer silently returns localhost.

 .env.example                                  |   5 +-
 app/api/resolve.py                            |  32 +++++--
 app/core/config.py                            |   4 +-
 app/services/platforms/wechat_channels.py     |   7 +-
 tests/test_resolve_wechat_channels_url.py     | 124 ++++++++++++++++++++++++++
 tests/test_tikhub_provider_wechat_channels.py |   9 +-
 6 files changed, 164 insertions(+), 17 deletions(-)
```

未 push。未改 url_parser / tikhub provider / resolver / adapters / scripts / docs / README / `.env`。
