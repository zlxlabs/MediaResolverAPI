"""
TikHub 视频信息提供者

封装对 TikHub API 的调用，支持多个平台的视频信息获取
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
from loguru import logger

from .base import BaseProvider, ProviderError, VideoNotFoundError
from ...core.config import settings
from ...utils.http_client import HTTPClient


class TikHubProvider(BaseProvider):
    """
    TikHub API 提供者

    支持平台：
    - douyin (抖音)
    - tiktok
    - kuaishou (快手)
    - youtube
    - xiaohongshu (小红书)
    - instagram
    """

    # 平台与 TikHub API 端点的映射
    PLATFORM_ENDPOINTS = {
        "douyin": "/api/v1/douyin/web/fetch_one_video",
        "tiktok": "/api/v1/tiktok/app/v3/fetch_one_video",
        "kuaishou": "/api/v1/kuaishou/web/fetch_one_video_v2",
        "youtube": "/api/v1/youtube/web/get_video_info",
        "xiaohongshu": "/api/v1/xiaohongshu/web/get_note_info_v3",
        "instagram": "/api/v1/instagram/web_app/fetch_post_media_by_url",
    }

    # 平台与视频ID参数名的映射
    PLATFORM_PARAMS = {
        "douyin": "aweme_id",
        "tiktok": "aweme_id",
        "kuaishou": "photo_id",
        "youtube": "video_id",
        "xiaohongshu": "share_text",  # 小红书使用完整URL
        "instagram": "url",  # Instagram使用完整URL
    }

    def __init__(self):
        """初始化 TikHub 提供者"""
        super().__init__()
        self.api_key = settings.TIKHUB_API_KEY
        self.api_base = settings.TIKHUB_API_BASE

        if not self.api_key:
            self.log_warning("TikHub API key is not configured")

    @property
    def provider_name(self) -> str:
        """提供者名称"""
        return "tikhub"

    def supports_platform(self, platform: str) -> bool:
        """
        检查是否支持指定平台

        Args:
            platform: 平台名称

        Returns:
            bool: 是否支持
        """
        return platform.lower() in self.PLATFORM_ENDPOINTS

    async def fetch_video_info(
        self,
        platform: str,
        video_id: str,
        original_url: str,
        **kwargs
    ) -> Dict:
        """
        从 TikHub API 获取视频信息

        Args:
            platform: 平台名称
            video_id: 视频ID
            original_url: 原始视频URL
            **kwargs: 其他参数

        Returns:
            Dict: TikHub API 返回的原始数据

        Raises:
            VideoNotFoundError: 视频不存在
            ProviderError: API 调用失败
        """
        platform = platform.lower()

        if not self.supports_platform(platform):
            raise ProviderError(f"Platform '{platform}' is not supported by TikHub provider")

        if not self.api_key:
            raise ProviderError("TikHub API key is not configured")

        endpoint = self.PLATFORM_ENDPOINTS[platform]
        param_name = self.PLATFORM_PARAMS[platform]
        url = f"{self.api_base}{endpoint}"

        # 小红书和Instagram需要传递完整URL，其他平台传递video_id
        if platform in ["xiaohongshu", "instagram"]:
            param_value = original_url
        else:
            param_value = video_id

        self.log_info(
            f"Fetching video info from TikHub",
            platform=platform,
            video_id=video_id,
            endpoint=endpoint,
            param_name=param_name,
            param_value=param_value
        )

        try:
            async with HTTPClient() as client:
                response = await client.get(
                    url,
                    params={param_name: param_value},
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=60.0  # 60秒超时
                )

                if response.status_code == 404:
                    raise VideoNotFoundError(f"Video not found: {video_id}")
                elif response.status_code == 401:
                    raise ProviderError("TikHub API authentication failed")
                elif response.status_code == 429:
                    raise ProviderError("TikHub API rate limit exceeded")
                elif response.status_code != 200:
                    raise ProviderError(
                        f"TikHub API returned error: {response.status_code} - {response.text}"
                    )

                data = response.json()

                # 检查响应数据的有效性
                if not self._validate_response(data, platform):
                    self.log_error(
                        f"Invalid video data returned from TikHub",
                        platform=platform,
                        video_id=video_id,
                        response_keys=list(data.keys()) if isinstance(data, dict) else None,
                        data_keys=list(data.get("data", {}).keys()) if isinstance(data, dict) and "data" in data else None
                    )
                    # Save failed response to logs folder for debugging
                    self._save_failed_response(data, platform, video_id)
                    raise VideoNotFoundError(f"Invalid video data returned from TikHub")

                self.log_info(
                    f"Successfully fetched video info from TikHub",
                    platform=platform,
                    video_id=video_id
                )

                return data

        except (VideoNotFoundError, ProviderError):
            raise
        except Exception as e:
            self.log_error(
                f"Failed to fetch video info from TikHub: {e}",
                platform=platform,
                video_id=video_id
            )
            raise ProviderError(f"TikHub API request failed: {str(e)}")

    def _validate_response(self, data: Dict, platform: str) -> bool:
        """
        验证 TikHub API 响应数据的有效性

        这里只做最基础的验证，确保响应包含 data 字段。
        更详细的数据结构验证由各平台的 _parse_response 方法负责。

        Args:
            data: API 响应数据
            platform: 平台名称

        Returns:
            bool: 数据是否有效
        """
        if not isinstance(data, dict):
            return False

        # 检查是否有 data 字段
        if "data" not in data:
            return False

        response_data = data.get("data", {})

        # 确保 data 字段不为空
        if not response_data:
            return False

        # 根据平台检查关键字段（宽松验证，兼容多种格式）
        if platform == "douyin" or platform == "tiktok":
            # TikTok 兼容两种格式：data.aweme_detail.* 或 data.*
            # 只要 data 存在且包含 aweme_detail 或 aweme_id 就认为有效
            return "aweme_detail" in response_data or "aweme_id" in response_data
        elif platform == "kuaishou":
            return "photo" in response_data
        elif platform == "youtube":
            # YouTube API 返回的数据包含 title, channel 等字段
            return "title" in response_data or "videoDetails" in response_data
        elif platform == "xiaohongshu":
            # 小红书新API返回的数据包含 user, video, desc 等字段
            return "user" in response_data or "desc" in response_data
        elif platform == "instagram":
            # Instagram新API返回的数据结构: data.data.medias
            # 也兼容旧格式: data.is_video
            inner_data = response_data.get("data", {})
            return "medias" in inner_data or "full_name" in inner_data or "is_video" in response_data

        return True

    def _save_failed_response(self, data: Dict, platform: str, video_id: str) -> None:
        """
        Save failed TikHub response to logs folder for debugging.

        Args:
            data: The response data from TikHub API
            platform: Platform name
            video_id: Video ID
        """
        try:
            # Get logs directory path
            logs_dir = Path(settings.LOG_FILE_PATH)
            if not logs_dir.is_absolute():
                # Find project root (contains pyproject.toml)
                current_file = Path(__file__)
                project_root = current_file
                while project_root.parent != project_root:
                    if (project_root / "pyproject.toml").exists():
                        break
                    project_root = project_root.parent
                if not (project_root / "pyproject.toml").exists():
                    project_root = Path.cwd()
                logs_dir = project_root / logs_dir

            # Create tikhub_failures subdirectory
            failures_dir = logs_dir / "tikhub_failures"
            failures_dir.mkdir(parents=True, exist_ok=True)

            # Generate filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_{platform}_{video_id}.json"
            filepath = failures_dir / filename

            # Save response data
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.info(f"Failed TikHub response saved to {filepath}")

        except Exception as e:
            logger.warning(f"Failed to save TikHub response to file: {e}")
