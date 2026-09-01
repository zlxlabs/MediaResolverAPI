# 预响应取消资源释放修复报告

## 1. 取消点枚举与处置

枚举方法：读取 `stream_wechat_channels` 从依赖解析到创建 `StreamingResponse` 的调用路径，并读取该路径上的本地辅助函数；把每个显式 `await` 和 `async with` 的隐式进入/退出等待都列为候选点。端点表的主范围止于 `StreamingResponse` 返回、响应头可以发送之前；响应体迭代属于 headers 之后，单独由现有 `_iter_decrypted` 的 `finally` 负责。按任务要求，`_open_cdn_stream_httpx` 内部的每个显式 `await` 也逐项列出。

| await 点 | 位置 | 此处取消时配额是否释放 | 上游连接是否释放 | 处置 |
| --- | --- | --- | --- | --- |
| `async with self._lock_for_loop()` 的隐式进入/退出等待 | `StreamLimiter.slot`，`app/api/stream.py:121` | 是 | 否，尚未打开上游 | 获取、计数和释放位于同一个 `@asynccontextmanager` 的 `try...finally`；锁等待取消时尚未增加计数，增加后取消由外层 `finally` 释放 |
| `async with stream_limiter.slot()` 的隐式进入/退出等待 | `_stream_slot`，`app/api/stream.py:276` | 是 | 否，尚未打开上游 | FastAPI 请求作用域的生成器依赖持有该 context manager，结构上覆盖端点调用和响应体生命周期；新增取消测试锁死 |
| `await provider.fetch_wechat_channels_media(object_id)` | `_fetch_media`，`app/api/stream.py:272` | 是 | 否，尚未打开上游 | 外层请求作用域依赖的 `finally` 覆盖 TikHub 等待；`test_pre_response_cancel_during_media_releases_slot` |
| `media = await _fetch_media(object_id)` | `stream_wechat_channels`，`app/api/stream.py:352` | 是 | 否，尚未打开上游 | 同上；取消异常是 `BaseException` 路径，不依赖 `except Exception` |
| `response = await client.send(request, stream=True)` | `_open_cdn_stream_httpx`，`app/api/stream.py:90` | 是 | 是 | client 所有权在返回 `_HttpxCdnStream` 前由 `try...finally` 持有；发送取消时 `finally` 调用 `client.aclose()`；`test_pre_response_cancel_during_cdn_open_closes_client` |
| `first_stream = await open_cdn_stream(media["full_url"], range_h)` | `stream_wechat_channels`，`app/api/stream.py:368` | 是 | 是（真实 opener） | 配额由请求依赖释放；打开阶段的部分资源由 `_open_cdn_stream_httpx` 自己的 `finally` 释放，端点只有在 await 成功返回后才获得 `first_stream` 所有权 |
| `await client.aclose()` | `_open_cdn_stream_httpx` 的清理 `finally`，`app/api/stream.py:96` | 是 | 是 | 这是取消路径上的结构性清理点，不再由 `except Exception` 触发；未发生响应对象所有权转移时 client 必须关闭 |
| `await first_stream.aclose()` | `stream_wechat_channels` 的预响应 `finally`，`app/api/stream.py:415` | 是 | 是 | 只要 opener 已返回流，端点在任何异常/取消/校验失败下都进入该 `finally`；配额由同一请求作用域依赖负责 |
| `await self._response.aclose()` | `_HttpxCdnStream.aclose`，`app/api/stream.py:62` | 是 | 是 | 端点清理调用该方法；其内部另有 `finally` 保证 client 关闭 |
| `await self._client.aclose()` | `_HttpxCdnStream.aclose` 的内部 `finally`，`app/api/stream.py:64` | 是 | 是 | response 关闭异常或取消时仍进入 client 清理结构；客户端断开测试继续锁死该行为 |
| `await self.release()` | `StreamLimiter.slot` 的 `finally`，`app/api/stream.py:131` | 是 | 不适用 | 该点位于响应体结束或客户端断开后的请求作用域退出，不属于响应头前窗口；仍由获取同一个 slot 的 context manager 成对负责，已有并发/客户端断开测试覆盖 |

账本结论：预响应取消发生在 TikHub 等待时，只有配额尚未归还；发生在 CDN 建连时，配额由请求依赖归还、client 由 opener 的 `finally` 关闭；发生在已返回流的预响应校验或异常路径时，端点 `finally` 关闭该流。所有“是”均有上述结构性理由或对应测试。

## 2. 根因与实现

根因是原端点把配额释放拆成响应体内部的 `finally` 和最外层 `except Exception` 两条路径。响应头发送前在 `_fetch_media` 或 `open_cdn_stream` 等待期间取消时，`asyncio.CancelledError` 不进入 `except Exception`，因此配额静默累积。原 `_open_cdn_stream_httpx` 也只在 `except Exception` 中关闭发送阶段的 client，取消会绕过关闭。

实现：

- 在现有 `StreamLimiter` 上增加标准 `@asynccontextmanager slot()`，获取和释放在同一个 `try...finally` 中。
- 通过请求作用域的 FastAPI 生成器依赖 `_stream_slot` 持有 slot，覆盖端点预处理、响应头和响应体；429 仍在获取失败时返回原 JSON 语义。
- 端点改为预响应 `finally` 关闭仍由端点持有的 `first_stream`，成功转交给响应体后由 `_iter_decrypted` 继续管理。
- `_open_cdn_stream_httpx` 在返回流前用 `try...finally` 持有 client；成功构造并转交流后清空本地所有权，取消发送时关闭 client。
- 未改解密、对账、Range、416、429 或服务端续传行为；未改允许范围外文件。

## 3. 新增回归测试

- `test_pre_response_cancel_during_media_releases_slot`：TikHub media 等待期间取消，断言请求结束后 `active == 0`。
- `test_pre_response_cancel_during_cdn_open_closes_client`：CDN client 的 `send` 等待期间取消，断言 `active == 0` 且已发送请求的 fake client 已关闭。
- `test_repeated_pre_response_cancels_do_not_exhaust_slots`：连续 `MAX_CONCURRENT_STREAMS + 1` 次预响应取消，随后正常请求返回 200 且正文完整，直接验证没有配额累积泄漏。

## 4. 反向红验

两次注入均在验证后恢复，未进入提交。

### 只在 `except Exception` 中手工释放

临时把 `app/api/stream.py:129` 的结构改为：

```python
except Exception:
    if acquired:
        await self.release()
```

运行：

```text
FAILED tests/test_stream_wechat_channels.py::test_pre_response_cancel_during_media_releases_slot
FAILED tests/test_stream_wechat_channels.py::test_pre_response_cancel_during_cdn_open_closes_client
FAILED tests/test_stream_wechat_channels.py::test_repeated_pre_response_cancels_do_not_exhaust_slots
3 failed, 31 deselected, 1 warning in 0.11s
```

三个测试均在 `assert stream_mod.stream_limiter.active == 0` 处失败，证明 `CancelledError` 绕过手工 `except Exception` 释放。

### 完全移除释放

临时把 `app/api/stream.py:131` 的释放改为：

```python
finally:
    pass
```

运行：

```text
FAILED tests/test_stream_wechat_channels.py::test_pre_response_cancel_during_media_releases_slot
FAILED tests/test_stream_wechat_channels.py::test_pre_response_cancel_during_cdn_open_closes_client
FAILED tests/test_stream_wechat_channels.py::test_repeated_pre_response_cancels_do_not_exhaust_slots
3 failed, 31 deselected, 1 warning in 0.11s
```

同样三个测试均在 `active == 0` 断言处失败，排除了“释放恰好由别处兜住”的假阳性。注入已恢复为：

```python
finally:
    if acquired:
        await self.release()
```

## 5. 验证

```text
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m pytest tests/ -q
235 passed, 107 warnings in 2.44s
```

```text
/home/zlx/projects/work/MediaResolverAPI/.venv/bin/python -m py_compile app/api/stream.py tests/test_stream_wechat_channels.py
通过
```

取消/并发窄测连续 5 轮均为：

```text
5 passed, 29 deselected
```

五轮均无失败。新增取消测试单轮为 `3 passed, 31 deselected`；流式文件全量为 `34 passed, 28 warnings`。

## 6. Git

实现与测试提交的实际输出：

```text
$ git log --oneline -1
c9869a7 fix: release stream resources on cancellation

$ git show --stat --format= HEAD
 app/api/stream.py                    |  99 +++++++++++++-----------
 tests/test_stream_wechat_channels.py | 142 +++++++++++++++++++++++++++++++++++
 2 files changed, 196 insertions(+), 45 deletions(-)
```

实现提交已落在 delegate 分配的分支 `card/MediaResolverAPI-20260901-09`。报告文件随后独立入库，故上面的 Git 输出准确对应实现/测试提交。

## 7. 状态

DONE

- Dispatch-Id：`dlg-20260901-121653-40ddcf`
- Base commit：`6ddbe33a637421e98684582c64fbdc2bee989577`
- 允许修改文件：`app/api/stream.py`、`tests/test_stream_wechat_channels.py`、`report.md`
- 实际修改文件符合边界；未改其他平台代码。
