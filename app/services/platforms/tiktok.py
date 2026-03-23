"""
TikTok platform resolver service.
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
from loguru import logger

from .base import BasePlatformService, VideoInfo
from ...utils.http_client import HTTPClient
from ...core.config import settings


class TikTokService(BasePlatformService):
    """
    TikTok platform service.

    Supports primary/fallback API degradation:
    - Primary API: fetch_one_video
    - Fallback API: fetch_one_video_v3 (supports region parameter)
    """

    def __init__(self, api_key: str, api_base: str):
        super().__init__(api_key, api_base)
        self.primary_endpoint = f"{api_base}/api/v1/tiktok/app/v3/fetch_one_video"
        self.fallback_endpoint = f"{api_base}/api/v1/tiktok/app/v3/fetch_one_video_v3"
        # Backward compatibility
        self.endpoint = self.primary_endpoint
        # Parse fallback region list from config
        self.fallback_regions = self._parse_fallback_regions()

    def _parse_fallback_regions(self) -> list:
        """
        Parse fallback API region list from config.

        Returns:
            list: Region code list, e.g. ["SA", "US", "JP"]
        """
        regions_str = settings.TIKTOK_FALLBACK_REGIONS
        if not regions_str:
            return []
        return [r.strip().upper() for r in regions_str.split(",") if r.strip()]

    async def get_video_info(self, video_id: str) -> Optional[VideoInfo]:
        """
        Get TikTok video info with automatic fallback to backup API.

        Args:
            video_id: TikTok video ID (aweme_id)

        Returns:
            Optional[VideoInfo]: Video info object
        """
        # Try primary API first
        result = await self._fetch_with_primary_api(video_id)
        if result:
            return result

        # Primary API failed, try fallback API
        logger.warning(f"Primary API failed for video_id={video_id}, trying fallback API...")
        result = await self._fetch_with_fallback_api(video_id)
        if result:
            return result

        logger.error(f"All APIs failed for TikTok video_id={video_id}")
        return None

    async def _fetch_with_primary_api(self, video_id: str) -> Optional[VideoInfo]:
        """
        Fetch video info using the primary API.

        Args:
            video_id: TikTok video ID (aweme_id)

        Returns:
            Optional[VideoInfo]: Video info object, None on failure
        """
        try:
            async with HTTPClient() as client:
                logger.info(f"Fetching TikTok video with primary API: video_id={video_id}")
                response = await client.get(
                    self.primary_endpoint,
                    params={"aweme_id": video_id},
                    headers={"Authorization": f"Bearer {self.api_key}"}
                )

                if response.status_code == 200:
                    data = response.json()
                    result = self._parse_response(data)
                    if result:
                        logger.info(f"Primary API succeeded for video_id={video_id}")
                        return result
                    else:
                        logger.warning(f"Primary API returned 200 but parsing failed for video_id={video_id}")
                        return None
                else:
                    logger.warning(f"Primary API request failed: status_code={response.status_code}")
                    self._log_error_response(response, "primary")
                    return None

        except Exception as e:
            logger.warning(f"Primary API exception: {e}")
            return None

    async def _fetch_with_fallback_api(self, video_id: str) -> Optional[VideoInfo]:
        """
        Fetch video info using the fallback API (fetch_one_video_v3).
        Tries different region parameters sequentially.

        Args:
            video_id: TikTok video ID (aweme_id)

        Returns:
            Optional[VideoInfo]: Video info object, None on failure
        """
        for region in self.fallback_regions:
            try:
                async with HTTPClient() as client:
                    logger.info(f"Fetching TikTok video with fallback API: video_id={video_id}, region={region}")
                    response = await client.get(
                        self.fallback_endpoint,
                        params={"aweme_id": video_id, "region": region},
                        headers={"Authorization": f"Bearer {self.api_key}"}
                    )

                    if response.status_code == 200:
                        data = response.json()
                        result = self._parse_response(data)
                        if result:
                            logger.info(f"Fallback API succeeded for video_id={video_id}, region={region}")
                            return result
                        else:
                            logger.warning(f"Fallback API returned 200 but parsing failed, region={region}")
                            continue
                    else:
                        logger.warning(f"Fallback API request failed: status_code={response.status_code}, region={region}")
                        self._log_error_response(response, f"fallback_region_{region}")
                        continue

            except Exception as e:
                logger.warning(f"Fallback API exception for region={region}: {e}")
                continue

        logger.error(f"All fallback API attempts failed for video_id={video_id}")
        return None

    def _log_error_response(self, response, api_type: str) -> None:
        """
        Log error response and save to file.

        Args:
            response: HTTP response object
            api_type: API type identifier (primary/fallback_region_XX)
        """
        try:
            error_data = response.json()
        except Exception:
            error_data = {"status_code": response.status_code, "text": response.text}

        filepath = self._save_error_response(error_data, f"{api_type}_http_{response.status_code}")
        logger.debug(f"Error response saved to: {filepath}")

    def _save_error_response(self, response_data: Dict[str, Any], error_type: str) -> str:
        """
        Save error response to JSON file.

        Args:
            response_data: Response data
            error_type: Error type description

        Returns:
            str: Saved file path
        """
        try:
            # Create error log directory
            error_dir = Path("./logs/error_responses")
            error_dir.mkdir(parents=True, exist_ok=True)

            # Generate filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"tiktok_error_{error_type}_{timestamp}.json"
            filepath = error_dir / filename

            # Save response data
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(response_data, f, ensure_ascii=False, indent=2)

            logger.info(f"Error response saved to: {filepath}")
            return str(filepath)

        except Exception as e:
            logger.error(f"Failed to save error response: {e}")
            return ""

    def _parse_response(self, response_data: Dict[str, Any]) -> Optional[VideoInfo]:
        """
        Parse TikTok API response data.

        Supports two response formats:
        1. Old format: data.aweme_detail.*
        2. New format: data.*

        Video source selection priority:
        1. play_addr_h264 (H264 codec, usually higher quality)
        2. Highest bit_rate entry from the bit_rate array

        Args:
            response_data: API response data

        Returns:
            Optional[VideoInfo]: Parsed video info object
        """
        try:
            # Get data source: try old format first, then new format
            aweme_detail = self._safe_get(response_data, "data.aweme_detail")
            if aweme_detail:
                logger.info("Using old TikTok API response format (data.aweme_detail)")
                data_source = aweme_detail
            else:
                data_source = self._safe_get(response_data, "data")
                if not data_source:
                    logger.error("TikTok response missing both 'data.aweme_detail' and 'data' fields")

                    filepath = self._save_error_response(response_data, "missing_data")
                    logger.error(f"Full response saved to: {filepath}")
                    return None

                logger.info("Using new TikTok API response format (data)")

            # Basic info
            video_id = self._safe_get(data_source, "aweme_id", "")
            title = self._safe_get(data_source, "desc", "")
            description = self._safe_get(data_source, "desc", "")

            # Author info
            author = self._safe_get(data_source, "author", {})
            author_name = self._safe_get(author, "nickname", "")
            author_id = self._safe_get(author, "unique_id", "")

            # Video file info
            # Priority 1: Use play_addr_h264 (H264 codec, usually higher quality)
            play_addr_h264 = self._safe_get(data_source, "video.play_addr_h264")
            if play_addr_h264:
                video_url = self._safe_get(play_addr_h264, "url_list.0", "")
                width = self._safe_get(play_addr_h264, "width", 0)
                height = self._safe_get(play_addr_h264, "height", 0)
                quality = "h264_original"
                logger.info(f"Using play_addr_h264: {width}x{height}")
            else:
                # Priority 2: Select highest bit_rate from bit_rate array
                bit_rate_list = self._safe_get(data_source, "video.bit_rate", [])
                if not bit_rate_list:
                    logger.error("TikTok response missing video download info")

                    filepath = self._save_error_response(response_data, "missing_video_info")
                    logger.error(f"Full response saved to: {filepath}")
                    return None

                # Find the highest bit_rate entry
                best_bit_rate = max(bit_rate_list, key=lambda x: x.get("bit_rate", 0))
                video_url = self._safe_get(best_bit_rate, "play_addr.url_list.0", "")
                width = self._safe_get(best_bit_rate, "play_addr.width", 0)
                height = self._safe_get(best_bit_rate, "play_addr.height", 0)
                quality = self._safe_get(best_bit_rate, "gear_name", "")
                selected_bitrate = best_bit_rate.get("bit_rate", 0)
                logger.info(f"Using best bit_rate: {quality}, {width}x{height}, bitrate={selected_bitrate}")

            # Statistics
            statistics = self._safe_get(data_source, "statistics", {})
            view_count = self._parse_count(self._safe_get(statistics, "play_count"))
            like_count = self._parse_count(self._safe_get(statistics, "digg_count"))
            comment_count = self._parse_count(self._safe_get(statistics, "comment_count"))
            share_count = self._parse_count(self._safe_get(statistics, "share_count"))
            collect_count = self._parse_count(self._safe_get(statistics, "collect_count"))

            # Time info
            create_time = self._parse_timestamp(self._safe_get(data_source, "create_time"))

            return VideoInfo(
                video_id=video_id,
                platform="tiktok",
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
                share_count=share_count,
                collect_count=collect_count,
                create_time=create_time,
                raw_data=response_data
            )

        except Exception as e:
            logger.error(f"Failed to parse TikTok response: {e}")

            filepath = self._save_error_response(response_data, "parse_exception")
            logger.error(f"Full response saved to: {filepath}")
            return None
