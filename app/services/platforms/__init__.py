"""
平台解析服务包
"""

from .base import VideoInfo, BasePlatformService
from .douyin import DouyinService
from .tiktok import TikTokService
from .youtube import YouTubeService
from .xiaohongshu import XiaohongshuService
from .kuaishou import KuaishouService
from .instagram import InstagramService
from .pinterest import PinterestService
from .facebook import FacebookService

__all__ = [
    "VideoInfo",
    "BasePlatformService",
    "DouyinService",
    "TikTokService",
    "YouTubeService",
    "XiaohongshuService",
    "KuaishouService",
    "InstagramService",
    "PinterestService",
    "FacebookService",
]