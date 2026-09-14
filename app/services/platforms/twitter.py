"""X (Twitter) video response parsing."""

import re
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from .base import BasePlatformService, VideoInfo


class TwitterService(BasePlatformService):
    """Parse TikHub's public X/Twitter tweet detail response."""

    async def get_video_info(self, video_id: str) -> Optional[VideoInfo]:
        """Twitter data is fetched by TikHubProvider; use the provider chain."""
        return None

    def _parse_response(self, response_data: Dict[str, Any]) -> Optional[VideoInfo]:
        """Parse the first playable MP4 attached to the tweet itself."""
        if not isinstance(response_data, dict):
            return None

        data = response_data.get("data")
        if not isinstance(data, dict):
            return None

        video = self._first_video(data)
        if not video:
            return None

        variants = video.get("variants")
        if not isinstance(variants, list):
            return None
        mp4_variants = [
            variant for variant in variants
            if isinstance(variant, dict)
            and str(variant.get("content_type") or "").lower() == "video/mp4"
            and variant.get("url")
        ]
        if not mp4_variants:
            return None

        selected = max(mp4_variants, key=self._variant_bitrate)
        video_url = str(selected["url"])
        width, height = self._resolution(video_url, video)
        duration_ms = video.get("duration") or video.get("duration_millis")
        duration = int(int(duration_ms) / 1000) if duration_ms is not None else None

        title = str(data.get("display_text") or data.get("text") or "")
        created_at = self._parse_created_at(data.get("created_at"))
        author = data.get("author")
        author = author if isinstance(author, dict) else {}

        return VideoInfo(
            video_id=str(data.get("id") or ""),
            platform="twitter",
            title=title,
            description=str(data.get("text") or data.get("display_text") or ""),
            author_name=str(author.get("name") or ""),
            author_id=str(author.get("screen_name") or ""),
            video_url=video_url,
            width=width,
            height=height,
            duration=duration,
            quality=f"{height}p",
            view_count=self._parse_count(data.get("views")),
            like_count=self._parse_count(data.get("likes")),
            comment_count=self._parse_count(data.get("replies")),
            share_count=self._parse_count(data.get("retweets")),
            collect_count=self._parse_count(data.get("bookmarks")),
            create_time=created_at,
            publish_time=data.get("created_at"),
            raw_data=response_data,
        )

    @staticmethod
    def _first_video(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        media = data.get("media")
        if isinstance(media, dict):
            videos = media.get("video")
            if isinstance(videos, list) and videos and isinstance(videos[0], dict):
                return videos[0]

        entities = data.get("entities")
        if isinstance(entities, dict):
            entity_media = entities.get("media")
            if isinstance(entity_media, list):
                for item in entity_media:
                    if not isinstance(item, dict) or item.get("type") != "video":
                        continue
                    video_info = item.get("video_info")
                    if isinstance(video_info, dict):
                        return video_info
        return None

    @staticmethod
    def _variant_bitrate(variant: Dict[str, Any]) -> int:
        try:
            return int(variant.get("bitrate") or 0)
        except (TypeError, ValueError):
            return 0

    def _resolution(self, video_url: str, video: Dict[str, Any]) -> tuple[int, int]:
        match = re.search(r"/(\d+)x(\d+)(?:/|$)", urlparse(video_url).path)
        if match:
            return int(match.group(1)), int(match.group(2))
        original_info = video.get("original_info")
        if not isinstance(original_info, dict):
            return 0, 0
        return (
            self._parse_count(original_info.get("width")) or 0,
            self._parse_count(original_info.get("height")) or 0,
        )

    @staticmethod
    def _parse_created_at(value: Any):
        if not value:
            return None
        try:
            parsed = parsedate_to_datetime(str(value))
        except (TypeError, ValueError):
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
