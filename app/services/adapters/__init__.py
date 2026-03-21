"""
数据适配器模块

将不同提供者的响应数据转换为统一的 VideoInfo 格式
"""

from .tikhub_adapter import TikHubAdapter
from .cobalt_adapter import CobaltAdapter

__all__ = [
    "TikHubAdapter",
    "CobaltAdapter",
]
