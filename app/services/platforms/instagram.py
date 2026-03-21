"""
Instagram平台解析服务
"""

from typing import Optional, Dict, Any
from loguru import logger
import urllib.parse

from .base import BasePlatformService, VideoInfo
from ...utils.http_client import HTTPClient


class InstagramService(BasePlatformService):
    """
    Instagram平台服务类
    """

    def __init__(self, api_key: str, api_base: str):
        super().__init__(api_key, api_base)
        # 新方法端点（优先使用）
        self.new_endpoint = f"{api_base}/api/v1/instagram/web_app/fetch_post_media_by_url"
        # 旧方法端点（降级使用）
        self.old_endpoint = f"{api_base}/api/v1/instagram/web_app/fetch_post_info_by_url"

    async def get_video_info(self, post_shortcode: str) -> Optional[VideoInfo]:
        """
        获取Instagram视频信息（优先使用新方法，失败则降级到旧方法）

        Args:
            post_shortcode: Instagram帖子短代码

        Returns:
            Optional[VideoInfo]: 视频信息对象
        """
        try:
            # 构建完整的Instagram URL (使用reel格式，也兼容p格式)
            post_url = f"https://www.instagram.com/reel/{post_shortcode}/"

            # 1. 优先尝试新方法
            video_info = await self._try_new_method(post_url)
            if video_info:
                logger.info("Successfully fetched Instagram video info using new method")
                return video_info

            # 2. 降级到旧方法
            logger.warning("New method failed, falling back to old method")
            video_info = await self._try_old_method(post_url)
            if video_info:
                logger.info("Successfully fetched Instagram video info using old method")
                return video_info

            logger.error("Both new and old methods failed to fetch Instagram video info")
            return None

        except Exception as e:
            logger.error(f"Failed to get Instagram video info: {e}")
            return None

    async def _try_new_method(self, post_url: str) -> Optional[VideoInfo]:
        """
        尝试使用新API端点获取视频信息

        Args:
            post_url: Instagram帖子完整URL

        Returns:
            Optional[VideoInfo]: 视频信息对象，失败则返回None
        """
        try:
            async with HTTPClient() as client:
                response = await client.get(
                    self.new_endpoint,
                    params={"url": post_url},
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=60  # 60秒超时
                )

                if response.status_code == 200:
                    data = response.json()
                    return self._parse_new_response(data)
                else:
                    logger.warning(f"New method API request failed: {response.status_code}")
                    return None

        except Exception as e:
            logger.warning(f"New method request exception: {e}")
            return None

    async def _try_old_method(self, post_url: str) -> Optional[VideoInfo]:
        """
        尝试使用旧API端点获取视频信息

        Args:
            post_url: Instagram帖子完整URL

        Returns:
            Optional[VideoInfo]: 视频信息对象，失败则返回None
        """
        try:
            async with HTTPClient() as client:
                response = await client.get(
                    self.old_endpoint,
                    params={"url": post_url},
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=60  # 60秒超时
                )

                if response.status_code == 200:
                    data = response.json()
                    return self._parse_response(data)
                else:
                    logger.error(f"Old method API request failed: {response.status_code} - {response.text}")
                    return None

        except Exception as e:
            logger.error(f"Old method request exception: {e}")
            return None

    def _parse_new_response(self, response_data: Dict[str, Any]) -> Optional[VideoInfo]:
        """
        解析新API的响应数据

        Args:
            response_data: 新API响应数据

        Returns:
            Optional[VideoInfo]: 解析后的视频信息对象
        """
        try:
            # 提取 data.data 层级
            outer_data = self._safe_get(response_data, "data", {})
            inner_data = self._safe_get(outer_data, "data")

            if not inner_data:
                logger.warning("New method response missing data.data field")
                return None

            # 提取 medias 数组
            medias = self._safe_get(inner_data, "medias", [])
            if not medias or len(medias) == 0:
                logger.warning("New method response has empty medias array")
                return None

            # 只取第一个媒体
            first_media = medias[0]

            # 检查类型是否为 video
            media_type = self._safe_get(first_media, "type", "")
            if media_type != "video":
                logger.warning(f"First media type is not video, but: {media_type}")
                return None

            # 提取视频下载地址
            video_url = self._safe_get(first_media, "link", "")
            if not video_url:
                logger.warning("New method response missing video download link")
                return None

            # 提取其他字段（能取到就取，取不到留空）
            username = self._safe_get(inner_data, "username", "")
            caption = self._safe_get(inner_data, "caption", "")
            taken_at_timestamp = self._safe_get(inner_data, "taken_at_timestamp")

            # 统计信息（-1 和 null 转为 0）
            like_count = self._safe_get(inner_data, "like_count", 0)
            like_count = 0 if like_count in [-1, None] else like_count

            comment_count = self._safe_get(inner_data, "comment_count", 0)
            comment_count = 0 if comment_count is None else comment_count

            # 时间转换
            create_time = None
            if taken_at_timestamp:
                create_time = self._parse_timestamp(taken_at_timestamp)
                if not create_time:
                    logger.warning(f"Failed to parse timestamp: {taken_at_timestamp}")

            # 生成标题
            title = caption[:50] + "..." if caption and len(caption) > 50 else caption
            if not title:
                title = f"Instagram Video - {username}" if username else "Instagram Video"

            return VideoInfo(
                video_id="",  # 新方法没有提供 id，留空
                platform="instagram",
                title=title,
                description=caption,
                author_name=username,
                author_id="",  # 新方法没有提供 author_id，留空
                video_url=video_url,
                width=0,  # 新方法没有提供尺寸信息
                height=0,
                quality="",
                view_count=0,  # 新方法没有提供播放量
                like_count=like_count,
                comment_count=comment_count,
                share_count=0,
                collect_count=0,
                create_time=create_time,
                raw_data=response_data
            )

        except Exception as e:
            logger.warning(f"Failed to parse new method response: {e}")
            return None

    def _parse_response(self, response_data: Dict[str, Any]) -> Optional[VideoInfo]:
        """
        解析Instagram API响应数据（自动检测新旧格式）

        Args:
            response_data: API响应数据

        Returns:
            Optional[VideoInfo]: 解析后的视频信息对象
        """
        try:
            data = self._safe_get(response_data, "data")
            if not data:
                logger.error("Instagram响应数据中缺少data字段")
                return None

            # 检测是否为新API格式 (data.data.medias)
            inner_data = self._safe_get(data, "data")
            if inner_data and "medias" in inner_data:
                logger.info("Detected new Instagram API response format, using new parser")
                return self._parse_new_response(response_data)

            # 基础信息
            post_id = self._safe_get(data, "id", "")
            shortcode = self._safe_get(data, "shortcode", "")

            # 检查是否为视频
            is_video = self._safe_get(data, "is_video", False)
            if not is_video:
                logger.error("该Instagram帖子不是视频类型")
                return None

            # 视频URL
            video_url = self._safe_get(data, "video_url", "")
            if not video_url:
                logger.error("Instagram响应数据中缺少video_url字段")
                return None

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

            # 统计信息
            like_info = self._safe_get(data, "edge_media_preview_like", {})
            like_count = self._parse_count(self._safe_get(like_info, "count"))

            view_count = self._parse_count(self._safe_get(data, "video_view_count"))
            play_count = self._parse_count(self._safe_get(data, "video_play_count"))

            # 时间信息
            taken_at_timestamp = self._safe_get(data, "taken_at_timestamp")
            create_time = None
            if taken_at_timestamp:
                # 使用父类的_parse_timestamp方法将时间戳转换为datetime对象
                create_time = self._parse_timestamp(taken_at_timestamp)
                if not create_time:
                    logger.warning(f"无法解析Instagram创建时间: {taken_at_timestamp}")

            # 视频尺寸信息
            dimensions = self._safe_get(data, "dimensions", {})
            width = self._safe_get(dimensions, "width", 0)
            height = self._safe_get(dimensions, "height", 0)

            # Instagram没有明确的标题字段，使用描述前50个字符作为标题
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
                quality="",  # Instagram API不提供具体的清晰度信息
                view_count=max(view_count, play_count),  # 取播放次数和观看次数的最大值
                like_count=like_count,
                comment_count=0,  # Instagram API响应中没有直接的评论数
                share_count=0,    # Instagram API响应中没有直接的分享数
                collect_count=0,  # Instagram API响应中没有直接的收藏数
                create_time=create_time,
                raw_data=response_data
            )

        except Exception as e:
            logger.error(f"解析Instagram响应数据失败: {e}")
            return None