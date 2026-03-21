"""
YouTube平台解析服务
"""

from typing import Optional, Dict, Any
from loguru import logger

from .base import BasePlatformService, VideoInfo
from ...utils.http_client import HTTPClient


class YouTubeService(BasePlatformService):
    """
    YouTube平台服务类
    """

    def __init__(self, api_key: str, api_base: str):
        super().__init__(api_key, api_base)
        self.endpoint = f"{api_base}/api/v1/youtube/web/get_video_info"

    async def get_video_info(self, video_id: str) -> Optional[VideoInfo]:
        """
        获取YouTube视频信息

        Args:
            video_id: YouTube视频ID

        Returns:
            Optional[VideoInfo]: 视频信息对象
        """
        try:
            async with HTTPClient() as client:
                response = await client.get(
                    self.endpoint,
                    params={"video_id": video_id},
                    headers={"Authorization": f"Bearer {self.api_key}"}
                )

                if response.status_code == 200:
                    data = response.json()
                    return self._parse_response(data)
                else:
                    logger.error(f"YouTube API请求失败: {response.status_code} - {response.text}")
                    return None

        except Exception as e:
            logger.error(f"获取YouTube视频信息失败: {e}")
            return None

    def _parse_response(self, response_data: Dict[str, Any]) -> Optional[VideoInfo]:
        """
        解析YouTube API响应数据

        Args:
            response_data: API响应数据

        Returns:
            Optional[VideoInfo]: 解析后的视频信息对象
        """
        try:
            data = self._safe_get(response_data, "data")
            if not data:
                logger.error("YouTube响应数据中缺少data字段")
                return None

            # 基础信息
            video_id = self._safe_get(data, "id", "")
            title = self._safe_get(data, "title", "")
            description = self._safe_get(data, "description", "")

            # 作者信息
            channel = self._safe_get(data, "channel", {})
            author_name = self._safe_get(channel, "name", "")
            author_id = self._safe_get(channel, "id", "")

            # 视频文件信息 - 选择最佳质量的有音频视频
            videos = self._safe_get(data, "videos.items", [])
            best_video = self._select_best_video(videos)

            if not best_video:
                logger.error("YouTube响应数据中没有可用的视频流")
                return None

            video_url = self._safe_get(best_video, "url", "")
            width = self._safe_get(best_video, "width", 0)
            height = self._safe_get(best_video, "height", 0)
            quality = self._safe_get(best_video, "quality", "")

            # 统计信息
            view_count = self._parse_count(self._safe_get(data, "viewCount"))
            like_count = self._parse_count(self._safe_get(data, "likeCount"))
            comment_count_text = self._safe_get(data, "commentCountText", "")
            comment_count = self._parse_count(comment_count_text)

            # 发布时间
            publish_time = self._safe_get(data, "publishedTime", "")

            return VideoInfo(
                video_id=video_id,
                platform="youtube",
                title=title,
                description=description,
                author_name=author_name,
                author_id=author_id,
                video_url=video_url,
                width=width,
                height=height,
                quality=quality,
                view_count=view_count,
                like_count=like_count,
                comment_count=comment_count,
                publish_time=publish_time,
                raw_data=response_data
            )

        except Exception as e:
            logger.error(f"解析YouTube响应数据失败: {e}")
            return None

    def _select_best_video(self, videos: list) -> Optional[Dict[str, Any]]:
        """
        选择最佳质量的视频流
        优先选择有音频且质量在720p-1080p之间的视频

        Args:
            videos: 视频流列表

        Returns:
            Optional[Dict[str, Any]]: 最佳视频流
        """
        if not videos:
            return None

        # 筛选有音频的视频
        with_audio = [v for v in videos if self._safe_get(v, "hasAudio")]
        if not with_audio:
            # 如果没有带音频的，选择第一个可用的
            return videos[0] if videos else None

        # 按质量排序，优先选择720p-1080p
        quality_priority = {
            "1080p": 100,
            "720p": 90,
            "480p": 80,
            "360p": 70,
            "240p": 60,
        }

        def get_quality_score(video):
            quality = self._safe_get(video, "quality", "")
            return quality_priority.get(quality, 0)

        # 选择质量分数最高的
        best_video = max(with_audio, key=get_quality_score)
        return best_video