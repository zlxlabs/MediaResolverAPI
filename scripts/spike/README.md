# 微信视频号密钥流（spike）

`decode_key` → 标准 **ISAAC64**（Bob Jenkins）→ 131072 字节密钥流。纯 Python 标准库，同 key 恒定，可按 `decode_key` 缓存。

## 算法（已用两条硬判据锁死）

1. 把 `decode_key` 当十进制整数（`stoull` / `int(s, 10)`），写入 `randrsl[0]`（其余 255 个 u64 为 0）。
2. `randinit(flag=true)`：两遍 mix + 一次 `isaac64()`。`ind()` 用原版 C 的**字节偏移**寻址：`mm[(x & 0x7F8) >> 3]`，不是 `mm[x & 255]`。
3. 消费 16384 个 u64：引用实现的倒序（`randrsl[255] … [0]`，耗尽再 `isaac64()`）。
4. 每个 u64 按 **big-endian** 落 8 字节。

这与公开工具里「WASM 写出缓冲再 `Uint8Array.reverse()`」字节级等价：WASM 把每批 256 个 u64 以 little-endian 从缓冲**尾部往前**排；整体 reverse 之后，等价于倒序消费 + big-endian。

依据：`python3 scripts/spike/verify_keystream.py`

- 约束 A：两把 key 的密钥流 XOR == 两段密文 XOR，全量 131072 字节。
- 约束 B：解密后偏移 4 为 `ftyp`（本样本 box size 为 `0x20`，major brand `isom`）。
- 约束 C：按 7000 字节分块（不整除 131072 也不整除 8）调用 `xor_chunk(..., absolute_offset=块首文件偏移)`，拼接结果与一次性整块解密逐字节相同。

## API

```python
from wechat_keystream import KEYSTREAM_SIZE, generate_keystream, xor_chunk

plain = xor_chunk(chunk, decode_key, absolute_offset=file_pos)
```

不接线到 `app/`。XOR 只覆盖文件前 128KB：`absolute_offset` 是本块首字节的文件偏移；完全落在 `>= KEYSTREAM_SIZE` 的块原样返回。旧名 `decrypt_head` 已删除（它没有偏移参数，分块时会静默解错）。

## 来源

- [Evil0ctal/WeChat-Channels-Video-File-Decryption](https://github.com/Evil0ctal/WeChat-Channels-Video-File-Decryption)：流程与 `Module.WxIsaac64(decodeKey).generate(131072)`。
- 官方 worker：`new Module.WxIsaac64(seed)` 的 C++ 侧是 `std::string` → `stoull`。
- ISAAC64：Bob Jenkins `isaac64.c`（public domain）。
