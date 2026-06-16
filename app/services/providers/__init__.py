"""
视频信息提供者模块

提供者是获取视频信息的数据源，目前支持：
- TikHub: 主要的视频信息提供者
- Cobalt: 作为备用的视频下载服务
"""

from .base import BaseProvider, ProviderError, VideoNotFoundError, DouyinTerminalError
from .tikhub import TikHubProvider
from .cobalt import CobaltProvider

__all__ = [
    "BaseProvider",
    "ProviderError",
    "VideoNotFoundError",
    "DouyinTerminalError",
    "TikHubProvider",
    "CobaltProvider",
]
