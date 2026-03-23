"""
Facebook 平台解析服务

注意：Facebook 只能通过 Cobalt 提供者获取视频信息
此服务类主要用于保持架构一致性
"""

from typing import Optional, Dict, Any
from loguru import logger

from .base import BasePlatformService, VideoInfo


class FacebookService(BasePlatformService):
    """
    Facebook 平台服务类

    Facebook 不被 TikHub 支持，只能通过 Cobalt 获取视频
    此类主要用于 URL 解析和平台识别
    """

    def __init__(self, api_key: str = "", api_base: str = ""):
        """
        初始化 Facebook 服务

        Args:
            api_key: API密钥（Facebook 不需要）
            api_base: API基础URL（Facebook 不需要）
        """
        super().__init__(api_key, api_base)

    async def get_video_info(self, video_id: str) -> Optional[VideoInfo]:
        """
        获取 Facebook 视频信息

        注意：此方法不应被直接调用。
        Facebook 视频应通过 VideoResolver 和 Cobalt 提供者获取。

        Args:
            video_id: Facebook video/reel ID

        Returns:
            Optional[VideoInfo]: 始终返回 None
        """
        logger.warning(
            "FacebookService.get_video_info() should not be called directly. "
            "Use VideoResolver instead."
        )
        return None

    def _parse_response(self, response_data: Dict[str, Any]) -> Optional[VideoInfo]:
        """
        解析 API 响应数据

        Facebook 不需要解析 TikHub 响应，因为它不被 TikHub 支持

        Args:
            response_data: API响应数据

        Returns:
            Optional[VideoInfo]: 始终返回 None
        """
        logger.warning("Facebook does not support TikHub API parsing")
        return None

    @staticmethod
    def extract_video_id(url: str) -> Optional[str]:
        """
        从 Facebook URL 中提取视频 ID

        支持的 URL 格式：
        - https://www.facebook.com/reel/622490636870155
        - https://www.facebook.com/watch?v=622490636870155
        - https://www.facebook.com/username/videos/622490636870155
        - https://fb.watch/xxx (短链)
        - https://www.facebook.com/share/v/1Fh5d44vQD/ (分享短链)
        - https://www.facebook.com/share/r/1Fh5d44vQD/ (Reel分享短链)

        Args:
            url: Facebook URL

        Returns:
            Optional[str]: Video ID，提取失败返回 None
        """
        import re
        from urllib.parse import urlparse, parse_qs

        # Reel 格式
        if '/reel/' in url:
            match = re.search(r'/reel/(\d+)', url)
            if match:
                return match.group(1)

        # Watch 格式 (?v=参数)
        if 'watch' in url and 'v=' in url:
            parsed = urlparse(url)
            query_params = parse_qs(parsed.query)
            video_id = query_params.get('v', [None])[0]
            if video_id:
                return video_id

        # Videos 格式
        if '/videos/' in url:
            match = re.search(r'/videos/(\d+)', url)
            if match:
                return match.group(1)

        # 分享短链格式: https://www.facebook.com/share/v/xxx 或 /share/r/xxx
        if '/share/' in url:
            match = re.search(r'/share/[vr]/([a-zA-Z0-9_-]+)', url)
            if match:
                return match.group(1)

        # fb.watch 短链接格式（需要展开）
        if 'fb.watch' in url:
            parsed = urlparse(url)
            shortcode = parsed.path.lstrip('/')
            if shortcode:
                return shortcode

        logger.warning(f"Failed to extract video ID from Facebook URL: {url}")
        return None
