# 执行器报告：修复 xor_chunk 分块偏移（gate P1）

**结论：已修。** `decrypt_head` 改名为 `xor_chunk(..., absolute_offset=0)`；非首块按文件绝对偏移取密钥流。`python3 scripts/spike/verify_keystream.py` 退出码 0，三条判据全绿。未改 `generate_keystream` / ISAAC64，未改 `_parse_decode_key`。

Dispatch：`dlg-20260901-093522-053502`。分支沿用 `card/MediaResolverAPI-20260901-01`（HEAD 原 `5fa9910`）。同 id 的 systemd unit 是本派发自身。本工作区无交接单。

## 缺陷判断

同意主审：旧 `decrypt_head` 固定 `ks[i]`，没有文件偏移。上一张卡的验证只喂整段 131072 字节（等价 offset=0），所以当时绿；分块调用会静默解错。这不是「非首块不可达」，而是契约自相矛盾导致验证没覆盖。未抗命、未把缺陷说成误报。

## 行为

- `absolute_offset` = 本块首字节的文件偏移。
- 整块 `>= KEYSTREAM_SIZE`：原样返回，不生成密钥流、不 XOR。
- 跨 128KB 边界：只 XOR 落在 `[0, KEYSTREAM_SIZE)` 的前缀。

## 验收（绿）

```
constraint_A: 131072/131072 bytes match
sample1_plain[:16] 000000206674797069736f6d00000200
sample2_plain[:16] 000000206674797069736f6d00000200
constraint_B: sample1[4:8]==ftyp and sample2[4:8]==ftyp
constraint_C: 131072/131072 bytes match (chunked, 7000B)
```

`python3 -m py_compile scripts/spike/wechat_keystream.py scripts/spike/verify_keystream.py` 通过。

## 红验（判据三）

在 `xor_chunk` 里、负偏移检查之后插入一行：

```python
    absolute_offset = 0  # REDTEST: ignore caller offset to prove constraint_C is not tautology
```

文件路径 `scripts/spike/wechat_keystream.py` 约第 194 行。注入后重跑：

```
constraint_A: 131072/131072 bytes match
sample1_plain[:16] 000000206674797069736f6d00000200
sample2_plain[:16] 000000206674797069736f6d00000200
constraint_B: sample1[4:8]==ftyp and sample2[4:8]==ftyp
constraint_C: 7524/131072 bytes match (chunked, 7000B)
red_exit:1
```

A/B 仍绿（它们本来就用 offset=0）；C 从 131072 掉到 7524 且退出码 1。确认注入生效：`rg REDTEST` 命中该行。随后删掉该行，再跑恢复三条全绿、退出码 0。入库 diff 不含 `REDTEST`。

## git

```
$ git log --oneline -1
3c604b8 fix(spike): xor_chunk uses file offset so streamed blocks decrypt correctly

$ git show --stat --format= HEAD
 scripts/spike/README.md           |  7 +++++--
 scripts/spike/verify_keystream.py | 29 ++++++++++++++++++++++++-----
 scripts/spike/wechat_keystream.py | 26 ++++++++++++++++----------
 3 files changed, 45 insertions(+), 17 deletions(-)
```

`report.md` 本笔之后另提交；最终 HEAD 见 delegate 报告副本。
