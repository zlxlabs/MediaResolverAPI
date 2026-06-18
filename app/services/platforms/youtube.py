"""
YouTube平台解析服务
"""

import re
from typing import Optional, Dict, Any, List
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

            # 自适应基础信息，兼容两套 schema（评审 codex Issue 10）：
            #   web/get_video_info     → data.id/title/channel/viewCount/...
            #   web/get_video_info_v2  → data.videoDetails.{videoId,title,author,channelId,viewCount}
            video_details = data.get("videoDetails") if isinstance(data.get("videoDetails"), dict) else None
            if video_details:
                video_id = self._safe_get(video_details, "videoId", "")
                title = self._safe_get(video_details, "title", "")
                description = self._safe_get(video_details, "shortDescription", "")
                author_name = self._safe_get(video_details, "author", "")
                author_id = self._safe_get(video_details, "channelId", "")
                view_count = self._parse_count(self._safe_get(video_details, "viewCount"))
                like_count = None
                publish_time = ""
            else:
                video_id = self._safe_get(data, "id", "")
                title = self._safe_get(data, "title", "")
                description = self._safe_get(data, "description", "")
                channel = self._safe_get(data, "channel", {})
                author_name = self._safe_get(channel, "name", "")
                author_id = self._safe_get(channel, "id", "")
                view_count = self._parse_count(self._safe_get(data, "viewCount"))
                like_count = self._parse_count(self._safe_get(data, "likeCount"))
                publish_time = self._safe_get(data, "publishedTime", "")

            # 视频文件信息 - 选择最佳质量的有音频视频（自适应两套 schema）
            videos = self._adaptive_video_streams(data)
            best_video = self._select_best_video(videos)

            if not best_video:
                logger.error("YouTube响应数据中没有可用的视频流")
                return None

            video_url = self._safe_get(best_video, "url", "")
            width = self._safe_get(best_video, "width", 0)
            height = self._safe_get(best_video, "height", 0)
            quality = self._safe_get(best_video, "quality", "")

            # 评论数（仅 current schema 有）
            comment_count = self._parse_count(self._safe_get(data, "commentCountText", ""))

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

    @staticmethod
    def _adaptive_video_streams(data: Dict[str, Any]) -> list:
        """
        归一化视频流列表，兼容 TikHub 多端点的不同 schema（评审 codex Issue 10）：

          web/get_video_info     → data.videos.items（已含 url/width/height/quality/hasAudio）
          web/get_video_info_v2  → data.streamingData.formats（muxed 合流，含 url，无 cipher 时可直用）

        v2 只取 formats（音视频合流），不取 adaptiveFormats（纯视频/纯音频）；
        跳过 signatureCipher（无 url）的格式——拿不到直链就让链降级到下一端点/cobalt。
        """
        items = data.get("videos")
        if isinstance(items, dict) and items.get("items"):
            return items["items"]

        streaming = data.get("streamingData")
        if isinstance(streaming, dict):
            normalized = []
            for fmt in (streaming.get("formats") or []):
                url = fmt.get("url")
                if not url:
                    continue  # signatureCipher 格式无直链，跳过
                normalized.append({
                    "url": url,
                    "width": fmt.get("width", 0),
                    "height": fmt.get("height", 0),
                    "quality": fmt.get("qualityLabel") or fmt.get("quality") or "",
                    "hasAudio": True,  # formats[] 为合流
                })
            return normalized
        return []

    def _select_best_video(self, videos: list) -> Optional[Dict[str, Any]]:
        """
        选择最佳质量的视频流
        优先级: 1. 清晰度 (1080p > 4K > 2K > 720p > ...)  2. 有音频优先

        Args:
            videos: 视频流列表

        Returns:
            Optional[Dict[str, Any]]: 最佳视频流
        """
        if not videos:
            logger.warning("YouTube: No video streams available in response")
            return None

        # Log all available video streams
        self._log_available_streams(videos)

        # Sort by: 1) quality priority (higher first), 2) has audio (True first)
        sorted_videos = sorted(
            videos,
            key=lambda v: (self._get_quality_score(v), self._safe_get(v, "hasAudio", False)),
            reverse=True
        )

        best_video = sorted_videos[0]
        has_audio = self._safe_get(best_video, "hasAudio", False)

        # Count streams with audio for logging
        audio_count = sum(1 for v in videos if self._safe_get(v, "hasAudio", False))
        logger.info(f"YouTube: {audio_count} streams with audio out of {len(videos)} total")

        self._log_selected_stream(best_video, has_audio=has_audio)
        return best_video

    def _get_quality_score(self, video: Dict[str, Any]) -> int:
        """
        Calculate quality score for a video stream.
        Priority: 1080p > 4K > 2K > 720p > other resolutions by height

        Args:
            video: Video stream info dict

        Returns:
            int: Quality score (higher is better)
        """
        # Custom priority: 1080p is preferred, then 4K, 2K, 720p, etc.
        # Higher score = higher priority
        quality_priority = {
            "1080p": 10000,  # Top priority
            "2160p": 9000,   # 4K
            "1440p": 8000,   # 2K
            "720p": 7000,    # HD
            "4320p": 6000,   # 8K (usually too large)
            "480p": 5000,    # SD
            "360p": 4000,
            "240p": 3000,
            "144p": 2000,
        }

        quality = self._safe_get(video, "quality", "")

        # Check predefined priorities first
        if quality in quality_priority:
            return quality_priority[quality]

        # Try to parse numeric resolution from quality string (e.g., "1080p60" -> 1080)
        match = re.search(r"(\d+)p", quality)
        if match:
            resolution = int(match.group(1))
            # Map parsed resolution to priority score
            if resolution == 1080:
                return 10000
            elif resolution == 2160:
                return 9000
            elif resolution == 1440:
                return 8000
            elif resolution == 720:
                return 7000
            else:
                # Other resolutions: use height value as base score
                return resolution

        # Fallback: try to use height as score
        height = self._safe_get(video, "height", 0)
        if height:
            return height

        # Unknown quality, return lowest priority
        return 0

    def _log_available_streams(self, videos: List[Dict[str, Any]]) -> None:
        """
        Log all available video streams with their quality and format info.

        Args:
            videos: List of video stream dicts
        """
        logger.info(f"YouTube: Available video streams ({len(videos)} total):")

        for i, v in enumerate(videos):
            quality = self._safe_get(v, "quality", "unknown")
            width = self._safe_get(v, "width", 0)
            height = self._safe_get(v, "height", 0)
            has_audio = self._safe_get(v, "hasAudio", False)
            mime_type = self._safe_get(v, "mimeType", "unknown")
            bitrate = self._safe_get(v, "bitrate", 0)

            # Format bitrate for readability
            bitrate_str = f"{bitrate // 1000}kbps" if bitrate else "N/A"
            audio_str = "audio:yes" if has_audio else "audio:no"

            logger.info(
                f"  [{i+1}] {quality} ({width}x{height}) {audio_str} {bitrate_str} [{mime_type}]"
            )

    def _log_selected_stream(self, video: Dict[str, Any], has_audio: bool) -> None:
        """
        Log the selected video stream details.

        Args:
            video: Selected video stream dict
            has_audio: Whether the stream has audio
        """
        quality = self._safe_get(video, "quality", "unknown")
        width = self._safe_get(video, "width", 0)
        height = self._safe_get(video, "height", 0)
        mime_type = self._safe_get(video, "mimeType", "unknown")
        bitrate = self._safe_get(video, "bitrate", 0)
        bitrate_str = f"{bitrate // 1000}kbps" if bitrate else "N/A"

        logger.info(
            f"YouTube: Selected stream -> {quality} ({width}x{height}) "
            f"has_audio:{has_audio} {bitrate_str} [{mime_type}]"
        )