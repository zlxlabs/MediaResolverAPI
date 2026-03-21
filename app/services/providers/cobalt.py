"""
Cobalt 视频下载服务提供者

Cobalt 是一个通用的视频下载服务，支持多个平台
作为 TikHub 的备用方案
"""

from typing import Dict
from loguru import logger

from .base import BaseProvider, ProviderError, VideoNotFoundError
from ...core.config import settings
from ...utils.http_client import HTTPClient


class CobaltProvider(BaseProvider):
    """
    Cobalt API 提供者

    支持平台：
    - tiktok
    - instagram
    - youtube
    - xiaohongshu
    - pinterest

    注意：Cobalt 返回的信息较少，主要用于获取视频下载链接
    """

    # Cobalt 支持的平台列表
    SUPPORTED_PLATFORMS = [
        "tiktok",
        "instagram",
        "youtube",
        "xiaohongshu",
        "pinterest",
    ]

    def __init__(self):
        """初始化 Cobalt 提供者"""
        super().__init__()
        self.api_base = getattr(settings, "COBALT_API_BASE", "")

        if not self.api_base:
            self.log_warning("Cobalt API base URL is not configured")

    @property
    def provider_name(self) -> str:
        """提供者名称"""
        return "cobalt"

    def supports_platform(self, platform: str) -> bool:
        """
        检查是否支持指定平台

        Args:
            platform: 平台名称

        Returns:
            bool: 是否支持
        """
        return platform.lower() in self.SUPPORTED_PLATFORMS

    async def fetch_video_info(
        self,
        platform: str,
        video_id: str,
        original_url: str,
        **kwargs
    ) -> Dict:
        """
        从 Cobalt API 获取视频下载信息

        Args:
            platform: 平台名称
            video_id: 视频ID
            original_url: 原始视频URL (Cobalt 需要完整的 URL)
            **kwargs: 其他参数

        Returns:
            Dict: Cobalt API 返回的数据，包含：
                - status: "redirect" | "tunnel" | "picker" | "error"
                - url: 视频下载链接
                - filename: 文件名

        Raises:
            VideoNotFoundError: 视频不存在或无法解析
            ProviderError: API 调用失败
        """
        platform = platform.lower()

        if not self.supports_platform(platform):
            raise ProviderError(f"Platform '{platform}' is not supported by Cobalt provider")

        if not self.api_base:
            raise ProviderError("Cobalt API base URL is not configured")

        self.log_info(
            f"Fetching video info from Cobalt",
            platform=platform,
            video_id=video_id,
            url=original_url
        )

        try:
            async with HTTPClient() as client:
                response = await client.post(
                    self.api_base,
                    json={"url": original_url},
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json"
                    },
                    timeout=60.0  # 60秒超时
                )

                if response.status_code != 200:
                    raise ProviderError(
                        f"Cobalt API returned error: {response.status_code} - {response.text}"
                    )

                data = response.json()

                # 检查响应状态
                status = data.get("status")

                if status == "error":
                    error_code = data.get("error", {}).get("code", "unknown")
                    raise VideoNotFoundError(f"Cobalt error: {error_code}")

                if status not in ["redirect", "tunnel", "picker"]:
                    raise ProviderError(f"Unexpected Cobalt response status: {status}")

                # 验证必要字段
                if status in ["redirect", "tunnel"] and "url" not in data:
                    raise ProviderError("Cobalt response missing 'url' field")

                self.log_info(
                    f"Successfully fetched video info from Cobalt",
                    platform=platform,
                    video_id=video_id,
                    status=status
                )

                # 添加额外信息，方便后续处理
                data["_platform"] = platform
                data["_video_id"] = video_id
                data["_original_url"] = original_url

                return data

        except (VideoNotFoundError, ProviderError):
            raise
        except Exception as e:
            self.log_error(
                f"Failed to fetch video info from Cobalt: {e}",
                platform=platform,
                video_id=video_id
            )
            raise ProviderError(f"Cobalt API request failed: {str(e)}")
