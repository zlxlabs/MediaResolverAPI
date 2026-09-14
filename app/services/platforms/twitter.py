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

    def _parse_response(
        self, response_data: Dict[str, Any], quality: Optional[str] = None
    ) -> Optional[VideoInfo]:
        """Parse the highest-bitrate playable MP4 attached to the tweet itself."""
        if not isinstance(response_data, dict):
            return None

        data = response_data.get("data")
        if not isinstance(data, dict):
            return None

        candidates = []
        for video in self._tweet_videos(data):
            for variant in self._mp4_variants(video):
                video_url = str(variant.get("url") or "")
                w, h = self._resolution(video_url, video)
                short_side = min(w, h) if (w > 0 and h > 0) else 0
                bitrate = self._variant_bitrate(variant)
                candidates.append((variant, video, w, h, short_side, bitrate))

        if not candidates:
            entity_video = self._entity_video(data)
            if entity_video:
                for variant in self._mp4_variants(entity_video):
                    video_url = str(variant.get("url") or "")
                    w, h = self._resolution(video_url, entity_video)
                    short_side = min(w, h) if (w > 0 and h > 0) else 0
                    bitrate = self._variant_bitrate(variant)
                    candidates.append((variant, entity_video, w, h, short_side, bitrate))

        if not candidates:
            return None

        if quality is None:
            selected = self._select_default_variant(candidates)
        else:
            cap = int(quality[:-1])
            known = [c for c in candidates if c[4] > 0]
            if not known:
                # No resolution metadata means the explicit cap is a no-op;
                # retain Twitter's existing default pool selection.
                selected = self._select_default_variant(candidates)
            else:
                pool_at_or_below = [c for c in known if c[4] <= cap]
                if pool_at_or_below:
                    selected = max(pool_at_or_below, key=lambda c: (c[4], c[5]))
                elif pool_unknown := [c for c in candidates if c[4] == 0]:
                    selected = max(pool_unknown, key=lambda c: c[5])
                else:
                    selected = min(
                        [c for c in known if c[4] > cap],
                        key=lambda c: (c[4], -c[5]),
                    )

        variant, video, width, height, _short_side, _bitrate = selected
        video_url = str(variant["url"])
        duration_ms = video.get("duration") or video.get("duration_millis")
        duration = None
        if duration_ms is not None:
            try:
                duration = int(int(duration_ms) / 1000)
            except (ValueError, TypeError):
                duration = None

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
    def _select_default_variant(candidates):
        pool_le_1080 = [c for c in candidates if 1 <= c[4] <= 1080]
        if pool_le_1080:
            return max(pool_le_1080, key=lambda c: (c[4], c[5]))

        pool_unknown = [c for c in candidates if c[4] == 0]
        if pool_unknown:
            return max(pool_unknown, key=lambda c: c[5])

        pool_gt_1080 = [c for c in candidates if c[4] > 1080]
        return min(pool_gt_1080, key=lambda c: (c[4], -c[5]))

    @staticmethod
    def _tweet_videos(data: Dict[str, Any]) -> list[Dict[str, Any]]:
        media = data.get("media")
        if not isinstance(media, dict):
            return []
        videos = media.get("video")
        if not isinstance(videos, list):
            return []
        return [video for video in videos if isinstance(video, dict)]

    @staticmethod
    def _entity_video(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
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
    def _mp4_variants(video: Dict[str, Any]) -> list[Dict[str, Any]]:
        variants = video.get("variants")
        if not isinstance(variants, list):
            return []
        return [
            variant for variant in variants
            if isinstance(variant, dict)
            and str(variant.get("content_type") or "").lower() == "video/mp4"
            and variant.get("url")
        ]

    @staticmethod
    def _variant_bitrate(variant: Dict[str, Any]) -> int:
        try:
            return int(variant.get("bitrate") or 0)
        except (ValueError, TypeError):
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
        except (ValueError, TypeError):
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
