# 执行器报告：微信视频号 decode_key → 128KB 密钥流（纯 Python spike）

**结论：可行，判据全绿。** `python3 scripts/spike/verify_keystream.py` 退出码 0；约束 A 全量 131072 字节相等，约束 B 两份样本解密后偏移 4 均为 `ftyp`。不需要 Node / 无头浏览器侧车。

Dispatch：`dlg-20260901-085751-57cc08`。分支 `card/MediaResolverAPI-20260901-01`。同 id 的 systemd unit 是本派发自身，未当作他人占用。

## 现场（pickup）

- 工作区干净，HEAD `befd6fb`，本工作区无交接单。
- `/tmp/sph-spike/` 四份样本只读存在，E1/E2 前 16 字节与任务卡一致。
- 本仓无 open issue；`AGENTS.md` / `CLAUDE.md` 不存在。存活探针因缺会话 id 失败；未记账派卡跨仓 6 仓 14 条（无法分有主/无主）。要处理请另开对话，不要在本次接手里顺手补账。
- `repo-settings-doctor.sh --hookspath` 无异常输出。

## 数据侧探测（E1^E2）

131072 字节、16384 个互异 little-endian u64，无整段周期、无首尾镜像、两半段不相等。比特 1 的比例 ~0.5004，像随机流。结构信息不足以单独定字节序；方向改为对照官方 `WxIsaac64` 的调用约定，再拿 WASM 产出当 oracle 去对 ISAAC64 组合（oracle 只用于逆算法，交付物仍是纯 Python）。

## 试过的组合（对照 WASM 真值 `/tmp/ks1.raw` 前 16 字节）

在确认 `stoull` 之前，按任务卡穷举过种子位置 × `randinit` flag × `ind()` 字节/字寻址 × LE/BE × 正序/倒序。最高碰巧命中 13/16 字节（随机碰撞量级），**没有一组在确认种子约定前通过约束 A**。

锁定约定之后：

| 组合 | 结果 |
|---|---|
| `randrsl[0]=stoull(key)`，flag=true，`ind` 字节偏移，倒序消费，u64 **BE** | **131072/131072**（最终方案） |
| 同上但 u64 LE | ~598/131072（对最终密钥流） |
| 正序消费 LE / BE | ~442–492/131072 |
| `ind` 用 `mm[x & 255]`（错误移植） | 对象状态对不上 `randrsl[0]` |
| flag=false | 对象 `randrsl[0]` 对不上 WASM |
| 十进制字符串字节填 `mm[]`/`randrsl[]` | 未命中；且空字符串让 WASM 报 `stoull: no conversion` 后 abort |
| 把 JS 的 `reverse()` 理解成「不要倒序消费」 | 密钥流从头对不齐 `ftyp` |

## 最终算法

1. `decode_key`：`int` 或十进制字符串 → `stoull` 语义。C++ 构造函数是 `WxIsaac64(std::string)`，传入 JS number 会 BindingError。
2. 标准 ISAAC64（Jenkins `isaac64.c`），黄金分割常数 `0x9e3779b97f4a7c13`，`randinit(true)`。
3. 消费：`randrsl[--cnt]`，耗尽再 `isaac64()`；每值 **big-endian** 8 字节。共 16384 个 u64。
4. 与公开 README 的「WASM 缓冲 + `reverse()`」字节级等价：WASM 按 LE 把每批 256 个 u64 从缓冲尾往前写；整体 reverse 后就是倒序 + BE。

**关于「Reverse」**：任务卡猜「可能是每批 256 个内部倒序」——作为 Python 视角成立。JS 字面做的是整段 `Uint8Array.reverse()`。两者同一串字节。未改判据。

加密范围仍是前 131072 字节 XOR，样本解密明文以 `00 00 00 20 ftyp isom` 开头，后接 `iso2 avc1 mp41`。

密钥流是 `decode_key` 的纯函数，可缓存。

## 验收

```
$ python3 scripts/spike/verify_keystream.py
constraint_A: 131072/131072 bytes match
sample1_plain[:16] 000000206674797069736f6d00000200
sample2_plain[:16] 000000206674797069736f6d00000200
constraint_B: sample1[4:8]==ftyp and sample2[4:8]==ftyp
```

`python3 -m py_compile scripts/spike/wechat_keystream.py scripts/spike/verify_keystream.py` 通过。

## git

```
$ git log --oneline befd6fb..HEAD
0204812 docs(spike): record WxIsaac64 seeding, byte order, and reverse equivalence
8272a62 test(spike): verify WeChat keystream against frozen sph-spike samples
0a51568 feat(spike): add pure-Python WeChat Channels ISAAC64 keystream

$ git show --stat --format= 0204812
 scripts/spike/README.md | 31 +++++++++++++++++++++++++++++++
 1 file changed, 31 insertions(+)

$ git diff --stat befd6fb...HEAD
 scripts/spike/README.md           |  31 ++++++
 scripts/spike/verify_keystream.py |  81 ++++++++++++++++
 scripts/spike/wechat_keystream.py | 196 ++++++++++++++++++++++++++++++++++++++
 3 files changed, 308 insertions(+)
```

`report.md` 本笔提交后 HEAD 以 `git log --oneline -1` 为准（见 delegate 报告副本）。

## 若再给一轮

不必。纯 Python 已过两条硬判据。后续卡只需 `import` `scripts/spike/wechat_keystream.py`（或把该模块挪进包路径）。Node 只做过研究用 oracle，不要当运行时依赖。
