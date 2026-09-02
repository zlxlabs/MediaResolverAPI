# wx-direct R2 verdict

verdict: **pass**

风险等级：**internal**（`README.md:5` 已有 `risk-tier: internal`；仓根无 AGENTS.md/CLAUDE.md。P1 红线含越权 / 损坏他人数据；轮次上限 5，收敛需连续 2 轮无新增 P1）。卡面写「仓未声明」沿用 R1 对 AGENTS.md 空缺的判断；本轮按 README 声明 + 卡面 internal 审。

审查对象冻结：全量 `83e258d..39c2fd5`；H0 = `3feb0c2`；H0..H1 = `06bae5e..39c2fd5`（R1 verdict + 2580189 测试 + 39c2fd5 修复）。审查中出现的新提交不属于本轮。

本轮新证据（结论生成后才拿到；不是重读 R1 同一份 diff / 同一组探针）：
- `pytest tests/test_stream_wechat_channels_direct.py -q` → **8 passed**。
- 临时 `tests/_r2_review_probes.py`（已删，未入库）：README 客户端 Range（短文件 `bytes=1000-`、恰好 131072 的 `bytes=131072-`）；sph 正则样本；流式槽打满时 `/direct` 仍 200 且 `_stream_slot` 进入次数 `[]`；两对 key 密文不同、xor 后明文相同，混用则明文崩；CDN 200 + `Content-Length=2MiB` → `/direct` 502 `CDN ignored Range request`；200 且 CL 头=1000、body=1MiB → `_consume_cdn_body` 实读 3×1MiB 后 502（窗内重试 3）；`star_length` 502 前 `media_calls==[sph]`；`_open_cdn_stream_httpx` 源码无 host 校验、`follow_redirects=True`。
- 只读走 README 四步；对照 `tikhub.py:752-816` share_url 拼法、流式 `_iter_windows` 捕获的 `decode_key`。

无新增 P1。不阻塞合并。

## H0..H1 四问

范围：`git diff 06bae5e..39c2fd5`（`app/api/stream.py` +4 行，`tests/test_stream_wechat_channels_direct.py` +30/-2）。

1. **本轮是否只修了登记在案的 F1、F4？** 通过。
   - F1：换对 `_fetch_media` 成功后立刻 `generate_keystream(state["media"]["decode_key"])`，`TypeError/ValueError` → 502 `"Invalid decode_key"`（`stream.py:648-651`）。新测试 `test_direct_refresh_invalid_second_decode_key_is_502`：首 URL 403 + 第二对 `"not-a-key"` → HTTP 502，正文无 URL，`opens==1`（第二对非法 key 时不再打 CDN）。
   - F4：403 分支把 FakeCdn 写入 `opens[-1]["stream"]`；`test_direct_expired_url_refreshes_media` 断言两次 `aclose_called`。
   - 其余：harness 暴露 `medias` 供 F1 测试改第二对。无产品代码旁路。

2. **是否新增未经批准的抽象？** 通过。无新类型 / 配置项 / 包装层。

3. **状态 / 事实源 / fallback 是否无依据增加？** 通过。未改 `state` 形状，未加 fallback，长度权威仍是 CDN `Content-Range` 完整长度（拿不到 502）。

4. **是否留下双路径？** 通过。第二对校验复用第一对同一 `generate_keystream` + 固定文案 502，补齐对称，不是第二套解密或长度逻辑。

四问均通过，不计入新增 P1。

## findings

| 编号 | P 级 | 状态 | 违反 spec | 文件:行 | 复现 / 证据 | 建议修法 |
|---|---|---|---|---|---|---|
| F1 | P2 | **已修** | 第 5 条：TikHub 数据非法应 502 | `stream.py:648-651`；测试 `test_stream_wechat_channels_direct.py:178-192` | 本轮 8 passed；非法第二对不再 500 | — |
| F4 | P3 | **已修** | 测试有效性（锁 aclose） | `test_stream_wechat_channels_direct.py:91-94,174-175` | 403 流已挂到 `opens[0]["stream"]`，expired 断言两次 aclose | — |
| F2 | P3 | 接受不修（R1） | 第 4 条空文件边角 | `_read_window` 416 / 畸形 Range | 本轮无新证据推翻；真源 mp4 非 0 字节 | — |
| F3 | P3 | 接受不修（R1） | 第 1 条 vs 窗内重试 3 | `_WINDOW_MAX_ATTEMPTS=3` | 本轮 200 大 body 探针仍是 3 次同窗，过期换链仍是 2 次 CDN | — |
| F5 | P3 | 接受不修（R1） | 第 8 条反熵 | `read_head` 闭包 | H1 未再加抽象 | — |
| F6 | P3 | **新**；接受不修 | 第 9 条：只读 README 四步应能拼出全文 | `README.md:356` 第 2 步；对照 `:349` 字段表 | 短文件 200 且 `encrypted_head_bytes=content_length=1000` 时，第 2 步字面发出 `Range: bytes=1000-`（恰好 131072 则 `bytes=131072-`）。RFC 7233：first-byte-pos ≥ 文件长度 → CDN **416**。第 3 步重试列表无 416；第 4 步若客户端把 416 当失败会误判，其实文件头已是全文。字段表已写「此时 head_b64 就是整个文件」，四步没接上。真源视频号 mp4 ≫ 128KB（卡面 415MB） | 第 2 步加一句：若 `encrypted_head_bytes >= content_length` 则不要发 Range。不修也可 |

无新增 P2。F6 不阻塞。

## 本轮新角度

### 下游消费者（只读 README）

- `encrypted_head_bytes` 与 Range 起点：第 2 步写 `bytes=131072-`（或从该字段起）。大文件两者都是 131072，能拼出与流式端点一致的文件（卡面已实测 415MB md5）。短文件 / 恰好 131072 见 F6。
- `content_length` vs 随后 CDN 206 的 `Content-Range` 完整长度：字段表 + spec 2 指定端点字段取自**这次文件头响应**的完整长度；第 4 步收尾也钉死信这个字段。后继身子 206 的 YYYY 只该拿来交叉核对，冲突时仍信端点字段。不是空档。
- 换链后新 `content_length`：第 3 步只说换 `cdn_url` 并从已下载偏移续传，没钉「长度是文件属性、跨对不应变」。背景事实：明文跨 URL 对相同 → 长度不应变；若变了，用新值会截已写字节或永远凑不齐。真源同一 mp4 不会变。不单开 finding。
- 短文件第 2 步越界 Range：会，见 F6。不会静默拼出错误字节——要么 416 当失败，要么忽略 416 后第 4 步 `1000==1000` 通过。

### 对抗

- **SSRF**：`full_url` 只来自 TikHub `media.full_url`（`tikhub.py:795-813`），handler 无 query/body URL 参数。`open_cdn_stream` 无 host 校验且 `follow_redirects=True`（与流式端点同一函数，存量）。`sph_code` 正则 `^[A-Za-z0-9]{1,64}$` 挡住 `../`、`/`、`?`、`.`、非 ASCII；share_url 为 `https://weixin.qq.com/sph/{sph_code}`，不能改 host。internal 档 SSRF 不是红线。
- **sph 正则**：探针 `../etc`/`a/b` → 路由 404；点号 / 65 字符 / 空白 / 连字符 / 下划线 → 400，且 `media_calls==[]`。`?`/`#` 被 TestClient 截成流式路径，不是正则绕过。
- **`head_b64` 放大**：成功路径 206 拒绝 `declared_end > 131071`，200 例外要求 CL 头 ≤ 窗（131072），成功体顶 128KiB。200 且 CL 头撒谎（1000）但 body=1MiB：`_consume_cdn_body` **先读完再比长度**，再加窗内 3 次重试（本轮实读 3MiB 后 502）。这是存量 `_read_window`，URL 仍须 TikHub 指向恶意源。成功 JSON 不会被放大。
- **并发绕开 `_stream_slot`**：设计如此（spec 5 / README「不占流式并发槽」）。槽 `active=MAX=1` 时 `/direct` 仍 200、`_stream_slot` 进入 `[]`。可放大 TikHub 次数（每次 1–2 次 `_fetch_media`，其内部还有 3 次 lookup 重试）。反着 spec 去加槽不成立。

P1 两问（对抗候选）：
1. 真实使用方式下会被触发吗？internal ≤10 人、持 API key；TikHub 返回 Tencent CDN，不是攻击者 URL。短文件 / 撒谎 CL / 任意 host 均**未在真源量到**。并发打 `/direct` 会（端点本意就是短请求不占槽）。
2. 触发了后果能否接受？SSRF/放大读在真源不触发；并发打满是配额/吵邻居，不是越权、损坏他人数据、崩溃或静默错字节。故 **不是 P1**。

### 与流式端点的跨路径一致性

两边都 200 时，找不到「前 131072 字节明文不同」的输入。加密按对独立；`xor_chunk(cipher_A, key_A) == xor_chunk(cipher_B, key_B) == PLAIN`，混用则崩——`/direct` 在 `media = state["media"]` 之后 xor（R1 MUT2 已锁），流式 `_iter_windows` 用发头前那对 key，而 offset 0 窗 `_read_window_retry` **不会**换链（`start < 131072`），头不会跨对。明文跨对唯一（卡面背景事实）。

成功/失败分叉（不是解密结果分叉）：CDN **200** 且 `131072 < CL ≤ STREAM_WINDOW_BYTES(4MiB)` 时，`/direct` 502 `CDN ignored Range request`，流式会把 CL 当整文件收下。真源是 206；200 例外是存量 `_read_window`。流式在 offset 0 遇 403 直接 502，`/direct` 会换链再读——一边失败一边成功，比的不是同一明文。

### 降层一问

返回 200 之前已发生的不可逆动作：
1. **TikHub 计费调用** `_fetch_media`：成功路径 1 次，过期换链 2 次。`star_length` 502 探针：`media_calls==[sph]` 且已打开 CDN——失败时调用已经发生。
2. **CDN GET** 1 次（过期 2 次），有界 Range。
3. **不写缓存 / 不写库**：handler 源码无 `UsageLog` / `VideoCache` / `db.`（UsageLog 本就只覆盖 resolve；流式端点同样不记账）。
4. **handler 自身不打日志**；aclose 失败才走存量 `logger.error`（字段是 start/end/exc，不含 URL）。TikHub retryable 在 provider 层 WARNING（`sph_code` + `str(exc)`，现网 ProviderError 不含 `full_url`）。

失败时 TikHub 计费已发生、本端点不另记一笔——与流式端点同一账本缺口，不是本 diff 新引入。无删文件 / 无对外通知。

## spec 逐条（H1 后）

1. 有界 `bytes=0-131071` + 过期至多两次 CDN：happy/expired/F1 新测试均锁 Range；expired 2 次打开。窗内非过期失败仍 3 次（F3）。
2. 完整长度缺失 → 502，不猜。
3. 密钥与 `cdn_url` 用成功那一对；非法第二对在第二次 CDN 之前 502。
4. 短文件 `encrypted_head_bytes==1000`；恰好 131072 时三字段均为 131072（探针）。空文件仍 F2。
5. 非法 sph 400；TikHub 502；无 key 401；非法 decode_key 502（第一对 + 第二对）；不占槽。
6. 新测试正文无 `http(s)://`；502 文案固定。无新增 logger。
7. 非目标：未改 resolve/缓存；响应无 `decode_key`；无 302；无服务端续传。
8. 反熵：H1 无新抽象。`WechatChannelsDirectInfo` 仍是 README 字段表的 HTTP 契约。
9. 大文件四步成立（卡面 415MB md5）。短文件四步字面会发越界 Range（F6，P3）。
