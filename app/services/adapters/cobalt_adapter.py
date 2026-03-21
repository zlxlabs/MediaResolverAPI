"""
Cobalt 响应数据适配器

将 Cobalt API 的响应数据转换为统一的 VideoInfo 格式
由于 Cobalt 返回的信息较少，需要填充默认值
"""

from typing import Dict, Optional
from datetime import datetime
from loguru import logger
import re
from urllib.parse import urlparse

from ..platforms.base import VideoInfo


class CobaltAdapter:
    """
    Cobalt 响应数据适配器

    Cobalt 返回的信息非常有限，主要包含：
    - url: 视频下载链接
    - filename: 文件名
    - status: 状态（redirect/tunnel/picker）

    需要填充的字段：
    - title: 从 filename 或 URL 提取
    - description: 使用默认描述
    - author_name: 尝试从 URL 提取或使用默认值
    - author_id: 使用默认值
    - 统计信息: 全部为 None
    - 时间信息: 使用当前时间
    """

    # 平台的用户名提取规则
    USERNAME_PATTERNS = {
        "tiktok": r"@([^/]+)/video",
        "instagram": r"instagram\.com/([^/]+)/",
        "youtube": r"@([^/]+)",
        "pinterest": r"pinterest\.com/([^/]+)/",
    }

    def adapt(self, raw_data: Dict, platform: str, video_id: str, original_url: str) -> Optional[VideoInfo]:
        """
        将 Cobalt 原始响应数据转换为 VideoInfo 对象

        Args:
            raw_data: Cobalt API 返回的原始数据
            platform: 平台名称
            video_id: 视频ID
            original_url: 原始视频URL

        Returns:
            Optional[VideoInfo]: 转换后的视频信息对象
        """
        try:
            status = raw_data.get("status")

            # 处理 picker 类型（多个文件）
            if status == "picker":
                logger.warning(
                    f"Cobalt returned picker type, using first video",
                    platform=platform,
                    video_id=video_id
                )
                picker_items = raw_data.get("picker", [])
                if not picker_items:
                    logger.error("Cobalt picker response has no items")
                    return None

                # 找到第一个视频类型的项
                video_item = next((item for item in picker_items if item.get("type") == "video"), None)
                if not video_item:
                    logger.error("No video found in Cobalt picker response")
                    return None

                video_url = video_item.get("url")
            else:
                video_url = raw_data.get("url")

            if not video_url:
                logger.error("Cobalt response missing video URL")
                return None

            # 提取文件名
            filename = raw_data.get("filename", "")

            # 生成 title
            title = self._generate_title(filename, platform, video_id)

            # 生成 description
            description = self._generate_description(original_url, platform)

            # 尝试提取作者名
            author_name = self._extract_author_name(original_url, platform)

            # 创建 VideoInfo 对象
            video_info = VideoInfo(
                video_id=video_id,
                platform=platform,
                title=title,
                description=description,
                author_name=author_name,
                author_id="",  # Cobalt 不提供
                video_url=video_url,
                width=0,  # Cobalt 不提供
                height=0,  # Cobalt 不提供
                duration=None,  # Cobalt 不提供
                quality="unknown",  # Cobalt 不提供
                view_count=None,  # Cobalt 不提供
                like_count=None,  # Cobalt 不提供
                comment_count=None,  # Cobalt 不提供
                share_count=None,  # Cobalt 不提供
                collect_count=None,  # Cobalt 不提供
                create_time=datetime.now(),  # 使用当前时间
                publish_time=None,  # Cobalt 不提供
                provider="cobalt",  # 数据提供者
                raw_data=raw_data  # 保存原始数据
            )

            logger.info(
                f"Successfully adapted Cobalt response to VideoInfo",
                platform=platform,
                video_id=video_id,
                title=title
            )

            return video_info

        except Exception as e:
            logger.error(
                f"Failed to adapt Cobalt response: {e}",
                platform=platform,
                video_id=video_id
            )
            return None

    def _generate_title(self, filename: str, platform: str, video_id: str) -> str:
        """
        生成视频标题

        Args:
            filename: Cobalt 返回的文件名
            platform: 平台名称
            video_id: 视频ID

        Returns:
            str: 生成的标题
        """
        if filename:
            # 移除文件扩展名
            title = re.sub(r'\.(mp4|mov|avi|webm)$', '', filename, flags=re.IGNORECASE)
            # 移除平台前缀（如 "tiktok_"）
            title = re.sub(r'^[a-z]+_', '', title, flags=re.IGNORECASE)
            return title

        # 使用默认标题
        platform_names = {
            "tiktok": "TikTok",
            "instagram": "Instagram",
            "youtube": "YouTube",
            "xiaohongshu": "Xiaohongshu",
            "pinterest": "Pinterest",
        }
        platform_name = platform_names.get(platform, platform.capitalize())
        return f"Video from {platform_name}"

    def _generate_description(self, original_url: str, platform: str) -> str:
        """
        生成视频描述

        Args:
            original_url: 原始视频URL
            platform: 平台名称

        Returns:
            str: 生成的描述
        """
        return (
            f"This video was downloaded via Cobalt downloader.\n"
            f"Original URL: {original_url}\n\n"
            f"Note: Detailed information is not available from Cobalt API."
        )

    def _extract_author_name(self, url: str, platform: str) -> str:
        """
        尝试从URL中提取作者名

        Args:
            url: 视频URL
            platform: 平台名称

        Returns:
            str: 提取的作者名或默认值
        """
        pattern = self.USERNAME_PATTERNS.get(platform)
        if pattern:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        # 无法提取，返回默认值
        return "Unknown User"
