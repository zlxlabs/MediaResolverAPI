"""
TikHub 响应数据适配器

将 TikHub API 的响应数据转换为统一的 VideoInfo 格式
复用现有平台服务的解析逻辑
"""

from typing import Dict, Optional
from loguru import logger

from ..platforms.base import VideoInfo
from ..platforms.douyin import DouyinService
from ..platforms.tiktok import TikTokService
from ..platforms.kuaishou import KuaishouService
from ..platforms.youtube import YouTubeService
from ..platforms.xiaohongshu import XiaohongshuService
from ..platforms.instagram import InstagramService


class TikHubAdapter:
    """
    TikHub 响应数据适配器

    将 TikHub API 返回的原始数据转换为统一的 VideoInfo 对象
    """

    # 平台服务映射
    PLATFORM_SERVICES = {
        "douyin": DouyinService,
        "tiktok": TikTokService,
        "kuaishou": KuaishouService,
        "youtube": YouTubeService,
        "xiaohongshu": XiaohongshuService,
        "instagram": InstagramService,
    }

    def __init__(self, api_key: str, api_base: str):
        """
        初始化适配器

        Args:
            api_key: TikHub API key (用于初始化服务类)
            api_base: TikHub API base URL
        """
        self.api_key = api_key
        self.api_base = api_base

    def adapt(self, raw_data: Dict, platform: str, video_id: str) -> Optional[VideoInfo]:
        """
        将 TikHub 原始响应数据转换为 VideoInfo 对象

        Args:
            raw_data: TikHub API 返回的原始数据
            platform: 平台名称
            video_id: 视频ID

        Returns:
            Optional[VideoInfo]: 转换后的视频信息对象

        Raises:
            ValueError: 不支持的平台或数据格式错误
        """
        platform = platform.lower()

        if platform not in self.PLATFORM_SERVICES:
            raise ValueError(f"Unsupported platform: {platform}")

        try:
            # 获取对应平台的服务类
            service_class = self.PLATFORM_SERVICES[platform]

            # 创建服务实例（用于调用解析方法）
            service = service_class(self.api_key, self.api_base)

            # 调用平台服务的 _parse_response 方法解析数据
            video_info = service._parse_response(raw_data)

            if not video_info:
                logger.error(f"Failed to parse TikHub response for platform: {platform}")
                return None

            # 设置 provider 字段
            video_info.provider = "tikhub"

            logger.info(
                f"Successfully adapted TikHub response to VideoInfo",
                platform=platform,
                video_id=video_id
            )

            return video_info

        except Exception as e:
            logger.error(
                f"Failed to adapt TikHub response: {e}",
                platform=platform,
                video_id=video_id
            )
            return None
