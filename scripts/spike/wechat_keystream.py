#!/usr/bin/env python3
"""薄转发：算法实现已搬到 app.services.wechat_channels_crypto。

verify_keystream.py 仍从本路径导入；保持这一入口是为了离线验证脚本不改 import。
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_root = str(_ROOT)
if _root not in sys.path:
    sys.path.insert(0, _root)

from app.services.wechat_channels_crypto import (  # noqa: E402
    KEYSTREAM_SIZE,
    generate_keystream,
    xor_chunk,
)

__all__ = ["KEYSTREAM_SIZE", "generate_keystream", "xor_chunk"]
