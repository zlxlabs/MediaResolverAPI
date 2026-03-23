"""
Instagram平台解析服务
"""

from typing import Optional, Dict, Any
from loguru import logger

from .base import BasePlatformService, VideoInfo
from ...utils.http_client import HTTPClient


class InstagramService(BasePlatformService):
    """
    Instagram平台服务类
    """

    def __init__(self, api_key: str, api_base: str):
        super().__init__(api_key, api_base)
        # v1 API 端点（优先使用）
        self.v1_endpoint = f"{api_base}/api/v1/instagram/v1/fetch_post_by_url"
        # v2 API 端点（降级使用）
        self.v2_endpoint = f"{api_base}/api/v1/instagram/v2/fetch_post_info"

    async def get_video_info(self, post_shortcode: str) -> Optional[VideoInfo]:
        """
        获取Instagram视频信息（优先使用v1 API，失败则降级到v2 API）

        Args:
            post_shortcode: Instagram帖子短代码

        Returns:
            Optional[VideoInfo]: 视频信息对象
        """
        try:
            # 构建完整的Instagram URL (使用reel格式，也兼容p格式)
            post_url = f"https://www.instagram.com/reel/{post_shortcode}/"

            # 1. 优先尝试 v1 API
            video_info = await self._try_v1_api(post_url)
            if video_info:
                logger.info("Successfully fetched Instagram video info using v1 API")
                return video_info

            # 2. 降级到 v2 API
            logger.warning("v1 API failed, falling back to v2 API")
            video_info = await self._try_v2_api(post_url)
            if video_info:
                logger.info("Successfully fetched Instagram video info using v2 API")
                return video_info

            logger.error("Both v1 and v2 APIs failed to fetch Instagram video info")
            return None

        except Exception as e:
            logger.error(f"Failed to get Instagram video info: {e}")
            return None

    async def _try_v1_api(self, post_url: str) -> Optional[VideoInfo]:
        """
        尝试使用 v1 API 端点获取视频信息

        API: /api/v1/instagram/v1/fetch_post_by_url
        参数: post_url

        Args:
            post_url: Instagram帖子完整URL

        Returns:
            Optional[VideoInfo]: 视频信息对象，失败则返回None
        """
        try:
            async with HTTPClient() as client:
                response = await client.get(
                    self.v1_endpoint,
                    params={"post_url": post_url},
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=60  # 60秒超时
                )

                if response.status_code == 200:
                    data = response.json()
                    return self._parse_v1_response(data)
                else:
                    logger.warning(f"v1 API request failed: {response.status_code}")
                    return None

        except Exception as e:
            logger.warning(f"v1 API request exception: {e}")
            return None

    async def _try_v2_api(self, post_url: str) -> Optional[VideoInfo]:
        """
        尝试使用 v2 API 端点获取视频信息

        API: /api/v1/instagram/v2/fetch_post_info
        参数: code_or_url

        Args:
            post_url: Instagram帖子完整URL

        Returns:
            Optional[VideoInfo]: 视频信息对象，失败则返回None
        """
        try:
            async with HTTPClient() as client:
                response = await client.get(
                    self.v2_endpoint,
                    params={"code_or_url": post_url},
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=60  # 60秒超时
                )

                if response.status_code == 200:
                    data = response.json()
                    return self._parse_v2_response(data)
                else:
                    logger.error(f"v2 API request failed: {response.status_code} - {response.text}")
                    return None

        except Exception as e:
            logger.error(f"v2 API request exception: {e}")
            return None

    def _parse_v1_response(self, response_data: Dict[str, Any]) -> Optional[VideoInfo]:
        """
        解析 v1 API 的响应数据

        API: /api/v1/instagram/v1/fetch_post_by_url
        响应结构: data 直接包含视频信息

        Args:
            response_data: v1 API 响应数据

        Returns:
            Optional[VideoInfo]: 解析后的视频信息对象
        """
        try:
            data = self._safe_get(response_data, "data")
            if not data:
                logger.warning("v1 API response missing data field")
                return None

            # 检查是否为视频
            is_video = self._safe_get(data, "is_video", False)
            if not is_video:
                logger.warning("This Instagram post is not a video")
                return None

            # 提取视频下载地址
            video_url = self._safe_get(data, "video_url", "")
            if not video_url:
                logger.warning("v1 API response missing video_url field")
                return None

            # 基础信息
            post_id = self._safe_get(data, "id", "")
            shortcode = self._safe_get(data, "shortcode", "")

            # 作者信息
            owner = self._safe_get(data, "owner", {})
            author_id = self._safe_get(owner, "id", "")
            author_name = self._safe_get(owner, "username", "")

            # 描述信息
            edge_media_to_caption = self._safe_get(data, "edge_media_to_caption", {})
            edges = self._safe_get(edge_media_to_caption, "edges", [])
            description = ""
            if edges and len(edges) > 0:
                caption_node = self._safe_get(edges[0], "node", {})
                description = self._safe_get(caption_node, "text", "")

            # 视频尺寸信息
            dimensions = self._safe_get(data, "dimensions", {})
            width = self._safe_get(dimensions, "width", 0)
            height = self._safe_get(dimensions, "height", 0)

            # 统计信息
            like_info = self._safe_get(data, "edge_media_preview_like", {})
            like_count = self._parse_count(self._safe_get(like_info, "count"))

            view_count = self._parse_count(self._safe_get(data, "video_view_count"))
            play_count = self._parse_count(self._safe_get(data, "video_play_count"))

            # 时间信息
            taken_at_timestamp = self._safe_get(data, "taken_at_timestamp")
            create_time = None
            if taken_at_timestamp:
                create_time = self._parse_timestamp(taken_at_timestamp)
                if not create_time:
                    logger.warning(f"Failed to parse timestamp: {taken_at_timestamp}")

            # 生成标题
            title = description[:50] + "..." if len(description) > 50 else description
            if not title:
                title = f"Instagram Video - {shortcode}"

            return VideoInfo(
                video_id=post_id,
                platform="instagram",
                title=title,
                description=description,
                author_name=author_name,
                author_id=author_id,
                video_url=video_url,
                width=width,
                height=height,
                quality="",  # Instagram API does not provide quality info
                view_count=max(view_count, play_count),  # Use max of view_count and play_count
                like_count=like_count,
                comment_count=0,  # Not directly available in v1 API
                share_count=0,
                collect_count=0,
                create_time=create_time,
                raw_data=response_data
            )

        except Exception as e:
            logger.warning(f"Failed to parse v1 API response: {e}")
            return None

    def _parse_v2_response(self, response_data: Dict[str, Any]) -> Optional[VideoInfo]:
        """
        解析 v2 API 的响应数据

        API: /api/v1/instagram/v2/fetch_post_info
        响应结构: data.data 包含视频信息

        Args:
            response_data: v2 API 响应数据

        Returns:
            Optional[VideoInfo]: 解析后的视频信息对象
        """
        try:
            outer_data = self._safe_get(response_data, "data", {})
            data = self._safe_get(outer_data, "data")

            if not data:
                logger.warning("v2 API response missing data.data field")
                return None

            # 检查是否为视频
            is_video = self._safe_get(data, "is_video", False)
            if not is_video:
                logger.warning("This Instagram post is not a video")
                return None

            # 提取视频下载地址
            video_url = self._safe_get(data, "video_url", "")
            if not video_url:
                logger.warning("v2 API response missing video_url field")
                return None

            # 基础信息
            post_id = self._safe_get(data, "id", "") or self._safe_get(data, "pk", "")
            shortcode = self._safe_get(data, "code", "")

            # 作者信息
            user = self._safe_get(data, "user", {})
            author_id = self._safe_get(user, "id", "")
            author_name = self._safe_get(user, "username", "")

            # 描述信息
            caption_obj = self._safe_get(data, "caption", {})
            description = self._safe_get(caption_obj, "text", "") if caption_obj else ""

            # 视频尺寸信息
            width = self._safe_get(data, "original_width", 0)
            height = self._safe_get(data, "original_height", 0)

            # 统计信息
            metrics = self._safe_get(data, "metrics", {})
            like_count = self._parse_count(self._safe_get(metrics, "like_count"))
            play_count = self._parse_count(self._safe_get(metrics, "play_count"))
            comment_count = self._parse_count(self._safe_get(metrics, "comment_count"))

            # 时间信息
            taken_at_timestamp = self._safe_get(data, "taken_at") or self._safe_get(data, "taken_at_ts")
            create_time = None
            if taken_at_timestamp:
                create_time = self._parse_timestamp(taken_at_timestamp)
                if not create_time:
                    logger.warning(f"Failed to parse timestamp: {taken_at_timestamp}")

            # 生成标题
            title = description[:50] + "..." if len(description) > 50 else description
            if not title:
                title = f"Instagram Video - {shortcode}"

            return VideoInfo(
                video_id=post_id,
                platform="instagram",
                title=title,
                description=description,
                author_name=author_name,
                author_id=author_id,
                video_url=video_url,
                width=width,
                height=height,
                quality="",  # Instagram API does not provide quality info
                view_count=play_count,
                like_count=like_count,
                comment_count=comment_count,
                share_count=0,
                collect_count=0,
                create_time=create_time,
                raw_data=response_data
            )

        except Exception as e:
            logger.warning(f"Failed to parse v2 API response: {e}")
            return None

    def _parse_response(self, response_data: Dict[str, Any]) -> Optional[VideoInfo]:
        """
        解析 Instagram API 响应数据（自动检测 v1/v2 格式）

        实现基类的抽象方法，根据响应结构自动选择合适的解析器。

        Args:
            response_data: API 响应数据

        Returns:
            Optional[VideoInfo]: 解析后的视频信息对象
        """
        # Check if it's v2 format (has data.data structure)
        outer_data = self._safe_get(response_data, "data", {})
        if isinstance(outer_data, dict) and "data" in outer_data:
            return self._parse_v2_response(response_data)

        # Otherwise try v1 format
        return self._parse_v1_response(response_data)
