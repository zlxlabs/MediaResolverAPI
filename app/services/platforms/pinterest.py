"""
Pinterest 平台解析服务

注意：Pinterest 只能通过 Cobalt 提供者获取视频信息
此服务类主要用于保持架构一致性
"""

from typing import Optional, Dict, Any
from loguru import logger

from .base import BasePlatformService, VideoInfo


class PinterestService(BasePlatformService):
    """
    Pinterest 平台服务类

    Pinterest 不被 TikHub 支持，只能通过 Cobalt 获取视频
    此类主要用于 URL 解析和平台识别
    """

    def __init__(self, api_key: str = "", api_base: str = ""):
        """
        初始化 Pinterest 服务

        Args:
            api_key: API密钥（Pinterest 不需要）
            api_base: API基础URL（Pinterest 不需要）
        """
        super().__init__(api_key, api_base)

    async def get_video_info(self, video_id: str) -> Optional[VideoInfo]:
        """
        获取 Pinterest 视频信息

        注意：此方法不应被直接调用。
        Pinterest 视频应通过 VideoResolver 和 Cobalt 提供者获取。

        Args:
            video_id: Pinterest pin ID

        Returns:
            Optional[VideoInfo]: 始终返回 None
        """
        logger.warning(
            "PinterestService.get_video_info() should not be called directly. "
            "Use VideoResolver instead."
        )
        return None

    def _parse_response(self, response_data: Dict[str, Any]) -> Optional[VideoInfo]:
        """
        解析 API 响应数据

        Pinterest 不需要解析 TikHub 响应，因为它不被 TikHub 支持

        Args:
            response_data: API响应数据

        Returns:
            Optional[VideoInfo]: 始终返回 None
        """
        logger.warning("Pinterest does not support TikHub API parsing")
        return None

    @staticmethod
    def extract_pin_id(url: str) -> Optional[str]:
        """
        从 Pinterest URL 中提取 Pin ID

        支持的 URL 格式：
        - https://www.pinterest.com/pin/123456789/
        - https://pin.it/abc123
        - https://ca.pinterest.com/pin/904731012654724079/

        Args:
            url: Pinterest URL

        Returns:
            Optional[str]: Pin ID，提取失败返回 None
        """
        import re

        # 标准 URL 格式
        match = re.search(r'pinterest\.com/pin/(\d+)', url)
        if match:
            return match.group(1)

        # 短链接格式（需要展开）
        match = re.search(r'pin\.it/([a-zA-Z0-9]+)', url)
        if match:
            # 短链接需要展开才能获取 Pin ID
            # 这将由 URL 解析服务处理
            return match.group(1)

        logger.warning(f"Failed to extract Pin ID from URL: {url}")
        return None
