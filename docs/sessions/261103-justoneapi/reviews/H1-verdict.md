# Just One API H1 独立审查

<!-- delegate-outcome: succeeded -->

failure-visibility: clean

## 结论

**H0 的空地址 P1 已关闭；本轮不阻断交付，未发现新 P1。** H1 在缺少 `PlayAddrStruct` 时返回 fixture 中的 bitrate 地址，真实 HTTP `/api/resolve` 不再出现已登记的空地址假成功；有 H264 时仍选原直链。此结论限定于固定 H1、入库 fixture 与本地真实解析/HTTP 路径，不代表生产上游或远端 CI 已验证。

- 固定 H0：`57db231a795a05c39f889ebe082ee9fda0eb5055`。
- 固定 H1：`67f39322f7e6e2a0d1dbf5eb51391b2279029a12`。
- 全量参照：`b80e35152b74289c835d308e0c64946f1d03f135..67f39322f7e6e2a0d1dbf5eb51391b2279029a12`。
- 规格：H1 的 `docs/sessions/261103-justoneapi/design.md`；`risk-tier: internal`。
- 已读卡面指定的 `review-discipline/SKILL.md`、`test-discipline/SKILL.md` 及审查方法增补；未读实现报告、实现进度、咨询报告或主脑会话，未派子代理。
- `git ls-remote origin refs/heads/card/MediaResolverAPI-261103-justoneapi` 与 `gh pr view 60 --json headRefOid` 均实读 H1。
- OCR 采用卡面提供的 `reviewed / 0 findings` 前置证据，未自行重跑；结论来自独立增量审、全量生产 diff 扫描和下述复验。

## 增量与不变式

H0..H1 仅 adapter 与一条测试，`+27/-8`。`app/services/adapters/justoneapi_adapter.py:178` 在无 H264 时保留首条有效 bitrate；`:184` 仅在 H264 URL 非空时生成 `play_addr_h264`。既有 TikTok 解析器由此实际进入 bitrate 选址路径。没有新增抽象、状态、配置、请求、重试或第二套解析路径；首条 bitrate 的选择是卡面已接受决策。

| 卡面不变式 | 实现与验证证据 | 裁决 |
|---|---|---|
| 1 无 H264、有 bitrate 必须得到列表内非空 URL | adapter `:178–190`；新增 `test_adapter_tiktok_uses_bitrate_when_play_addr_struct_missing`；独立 adapt 与真实 HTTP 探针均返回 `https://cdn.example.test/tiktok/h264_720.mp4`，并断言属于 fixture URL 集合 | 通过，原 P1 关闭 |
| 2 有 H264 时保持原直链优先 | adapter `:184`；既有 `test_adapter_tiktok_maps_pascal_case_bitrate`；独立 adapt/HTTP 返回 `https://cdn.example.test/tiktok/video.mp4` | 通过 |
| 3 不改成功判据，不传空 H264 节点 | H0..H1 对 `platforms/tiktok.py`、`video_resolver.py` diff 为空；新增测试显式断言空节点不存在，且实际 `adapt()` 的 `video_url` 非空 | 通过 |
| 4 已接受非阻断项与首条 bitrate | 未重开 Instagram、YouTube audio、快手作者、SQLite、`/direct` 项；未把 720 而非 1080 升级 | 遵守锁定决策 |
| 5 原有链路与边界 | H1 增量未触碰 provider、stream、配置；全量生产 diff 复核空 token、默认链、单枪、非终态、视频号门口/XOR/缓存两枪；责任链 30 测通过，HTTP 两次探针各记录 JOA×1 | 未见增量破坏 |

全量机械核对：`stream.py +23/-25`，净 -2；`kuaishou.py`、`test_stream_wechat_channels.py`、`test_stream_wechat_channels_direct.py` 相对全量 base 均零改动；`git diff --check <base> <H1>` 通过。未新增超出卡面范围的阻断项。

## 独立复验与红验

所有代码检查均用规则入口 `scratch-worktree.sh <本卡仓> <全SHA> -- <命令>`，显式 cwd 与 SHA；`app.__file__` 也断言位于对应 scratch。主仓既有 venv 的解释器仅作只读依赖来源，无安装、无 venv 软链；`PYTHONPATH` 已移除，禁止写字节码。完整绝对路径、脚本和原始输出保存在派发报告目录。

- H1 验证树：`$HOME/scratchpad/vt-O7DlnI/worktree`；`git rev-parse HEAD` = H1 全 SHA。
- H0 红验树：`$HOME/scratchpad/vt-4o6Hpl/worktree`；`git rev-parse HEAD` = H0 全 SHA。
- 适配器探针：`copy.deepcopy(fixture)` 后 `video.pop("PlayAddrStruct")`，执行 `JustOneApiAdapter().adapt(...)`，断言非空且属于原 fixture bitrate URL 集合；H1 打印上述 720 URL，H0 同输入打印 `''`。保留 H264 的对照组两树均打印原直链。
- H1：`python -m pytest tests/test_justoneapi_resolver_chain.py -q --tb=short --disable-warnings` → **30 passed, 12 warnings in 0.34s**。
- H1：临时 HTTP 探针穿过真实 `/api/resolve`、resolver、adapter、平台解析器及响应序列化；仅替换上游返回 fixture，分别断言 HTTP 200、`success=true`、`provider=justoneapi`、非空 URL 与 JOA×1 → **2 passed**。
- H0：AST 原样抽取 H1 唯一新增测试，只带 fixture loader/import，不复制生产代码；`python -m pytest tests/test_review_h1_red_sample.py -q --tb=short --disable-warnings` → **1 failed**。
- 失败断言：`assert "play_addr_h264" not in aligned["data"]["aweme_detail"]["video"]`；实际 H0 节点含 `url_list: []`，为 **AssertionError**，无 ImportError/SyntaxError。它先于公开 URL 断言失败；另用独立 adapt 探针直接确认公开空地址，未把内部断言当成全部行为证据。
- 抽取测试函数 SHA256：`c644cedfb2ad421f7eec84032d5993c82e5918b43c5cd3bbdf2cb820bb575562`；原 fixture 保留两条 bitrate，无伪造期望值。
- 五棵本轮 scratch 均已由脚本回收，路径不存在且不再注册；主仓 venv 无指向 scratch 或 `/tmp` 的 shebang。
- 未跑 187 全窄测、全仓套件、生产、systemd 或远端 CI。继承红：主干基线不可用，未能判定；H1 新红：无。H0 故意红验不计为 H1 回归。

### 新增测试判据
无

## 踩到的坑

额外 HTTP 探针首次用顶层测试模块名导入，收集失败；改为 `tests.test_justoneapi_resolver_chain` 后两例通过。此临时探针错误与 H0 的有效 AssertionError 红验分别记录。

## 闸与绕过

无绕过。只新增本 verdict；不修改实现、测试或配置，不改 PR、不标 ready、不合并、不部署。

## 与卡面的偏差

使用主仓已有解释器直接执行 pytest，未运行 `uv run`，避免临时环境安装改写共享 venv；已验证导入绑定目标树。其余无。

## 最贵的一步

把同一真实 fixture 的有/无 H264 输入送入真实 HTTP 消费路径，再用 H0 独立红验排除测试假绿；无收费上游请求。
