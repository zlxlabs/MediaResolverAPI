"""
TikTok平台解析服务
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
from loguru import logger

from .base import BasePlatformService, VideoInfo
from ...utils.http_client import HTTPClient


class TikTokService(BasePlatformService):
    """
    TikTok平台服务类
    """

    def __init__(self, api_key: str, api_base: str):
        super().__init__(api_key, api_base)
        self.endpoint = f"{api_base}/api/v1/tiktok/app/v3/fetch_one_video"

    async def get_video_info(self, video_id: str) -> Optional[VideoInfo]:
        """
        获取TikTok视频信息

        Args:
            video_id: TikTok视频ID (aweme_id)

        Returns:
            Optional[VideoInfo]: 视频信息对象
        """
        try:
            async with HTTPClient() as client:
                response = await client.get(
                    self.endpoint,
                    params={"aweme_id": video_id},
                    headers={"Authorization": f"Bearer {self.api_key}"}
                )

                if response.status_code == 200:
                    data = response.json()
                    return self._parse_response(data)
                else:
                    logger.error(f"TikTok API request failed: {response.status_code}")

                    # 尝试解析错误响应并保存
                    try:
                        error_data = response.json()
                    except:
                        error_data = {"status_code": response.status_code, "text": response.text}

                    filepath = self._save_error_response(error_data, f"http_{response.status_code}")
                    logger.error(f"Error response saved to: {filepath}")
                    return None

        except Exception as e:
            logger.error(f"Failed to get TikTok video info: {e}")
            return None

    def _save_error_response(self, response_data: Dict[str, Any], error_type: str) -> str:
        """
        保存错误响应到JSON文件

        Args:
            response_data: 响应数据
            error_type: 错误类型描述

        Returns:
            str: 保存的文件路径
        """
        try:
            # 创建错误日志目录
            error_dir = Path("./logs/error_responses")
            error_dir.mkdir(parents=True, exist_ok=True)

            # 生成文件名（包含时间戳）
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"tiktok_error_{error_type}_{timestamp}.json"
            filepath = error_dir / filename

            # 保存响应数据
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(response_data, f, ensure_ascii=False, indent=2)

            logger.info(f"Error response saved to: {filepath}")
            return str(filepath)

        except Exception as e:
            logger.error(f"Failed to save error response: {e}")
            return ""

    def _parse_response(self, response_data: Dict[str, Any]) -> Optional[VideoInfo]:
        """
        解析TikTok API响应数据
        兼容两种响应格式：
        1. 旧格式：data.aweme_detail.*
        2. 新格式：data.*

        Args:
            response_data: API响应数据

        Returns:
            Optional[VideoInfo]: 解析后的视频信息对象
        """
        try:
            # 获取数据源：先尝试旧格式(data.aweme_detail)，如果不存在则使用新格式(data)
            aweme_detail = self._safe_get(response_data, "data.aweme_detail")
            if aweme_detail:
                logger.info("Using old TikTok API response format (data.aweme_detail)")
                data_source = aweme_detail
            else:
                # 尝试新格式
                data_source = self._safe_get(response_data, "data")
                if not data_source:
                    logger.error("TikTok response missing both 'data.aweme_detail' and 'data' fields")

                    # 保存错误响应到文件
                    filepath = self._save_error_response(response_data, "missing_data")
                    logger.error(f"Full response saved to: {filepath}")
                    return None

                logger.info("Using new TikTok API response format (data)")

            # 基础信息
            video_id = self._safe_get(data_source, "aweme_id", "")
            title = self._safe_get(data_source, "desc", "")  # TikTok没有单独的title字段
            description = self._safe_get(data_source, "desc", "")

            # 作者信息
            author = self._safe_get(data_source, "author", {})
            author_name = self._safe_get(author, "nickname", "")
            author_id = self._safe_get(author, "unique_id", "")

            # 视频文件信息
            bit_rate = self._safe_get(data_source, "video.bit_rate.0")
            if not bit_rate:
                logger.error("TikTok response missing video download info")

                # 保存错误响应到文件
                filepath = self._save_error_response(response_data, "missing_video_info")
                logger.error(f"Full response saved to: {filepath}")
                return None

            video_url = self._safe_get(bit_rate, "play_addr.url_list.0", "")
            width = self._safe_get(bit_rate, "play_addr.width", 0)
            height = self._safe_get(bit_rate, "play_addr.height", 0)
            quality = self._safe_get(bit_rate, "gear_name", "")

            # 统计信息
            statistics = self._safe_get(data_source, "statistics", {})
            view_count = self._parse_count(self._safe_get(statistics, "play_count"))
            like_count = self._parse_count(self._safe_get(statistics, "digg_count"))
            comment_count = self._parse_count(self._safe_get(statistics, "comment_count"))
            share_count = self._parse_count(self._safe_get(statistics, "share_count"))
            collect_count = self._parse_count(self._safe_get(statistics, "collect_count"))

            # 时间信息
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

            # 保存错误响应到文件
            filepath = self._save_error_response(response_data, "parse_exception")
            logger.error(f"Full response saved to: {filepath}")
            return None