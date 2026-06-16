"""
抖音平台解析服务
"""

from typing import Optional, Dict, Any
from loguru import logger

from .base import BasePlatformService, VideoInfo
from ...utils.http_client import HTTPClient


class DouyinService(BasePlatformService):
    """
    抖音平台服务类
    """

    def __init__(self, api_key: str, api_base: str):
        super().__init__(api_key, api_base)
        self.endpoint = f"{api_base}/api/v1/douyin/web/fetch_one_video"

    async def get_video_info(self, video_id: str) -> Optional[VideoInfo]:
        """
        获取抖音视频信息

        Args:
            video_id: 抖音视频ID (aweme_id)

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
                    logger.error(f"抖音API请求失败: {response.status_code} - {response.text}")
                    return None

        except Exception as e:
            logger.error(f"获取抖音视频信息失败: {e}")
            return None

    def _extract_detail(self, response_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        从 TikHub 响应中定位「作品详情」对象（schema 自适应）。

        实测三种响应结构（见 docs/douyin-fallback-design.md §12）：
        - web v1/v2 与 app v3：详情在 ``data.aweme_detail``
        - hybrid/video_data：详情字段直接铺在 ``data`` 上（无 aweme_detail 包裹）

        Returns:
            Optional[Dict]: 作品详情字典；无法定位时返回 None
        """
        data = self._safe_get(response_data, "data")
        if not isinstance(data, dict):
            return None
        aweme_detail = data.get("aweme_detail")
        if isinstance(aweme_detail, dict) and aweme_detail:
            return aweme_detail
        # hybrid：data 本身即详情
        if "aweme_id" in data:
            return data
        return None

    def _parse_response(self, response_data: Dict[str, Any]) -> Optional[VideoInfo]:
        """
        解析抖音API响应数据（schema 自适应，通吃 web/app/hybrid 三种结构）

        Args:
            response_data: API响应数据

        Returns:
            Optional[VideoInfo]: 解析后的视频信息对象
        """
        try:
            aweme_detail = self._extract_detail(response_data)
            if not aweme_detail:
                logger.error("抖音响应数据中缺少作品详情字段(aweme_detail / data)")
                return None

            # 基础信息
            video_id = self._safe_get(aweme_detail, "aweme_id", "")
            title = self._safe_get(aweme_detail, "item_title", "")
            description = self._safe_get(aweme_detail, "desc", "")

            # 作者信息
            author = self._safe_get(aweme_detail, "author", {})
            author_name = self._safe_get(author, "nickname", "")
            author_id = self._safe_get(author, "unique_id", "")

            # 视频文件信息
            bit_rate = self._safe_get(aweme_detail, "video.bit_rate.0")
            if not bit_rate:
                logger.error("抖音响应数据中缺少视频下载信息")
                return None

            video_url = self._safe_get(bit_rate, "play_addr.url_list.0", "")
            width = self._safe_get(bit_rate, "play_addr.width", 0)
            height = self._safe_get(bit_rate, "play_addr.height", 0)
            quality = self._safe_get(bit_rate, "gear_name", "")

            # 统计信息
            statistics = self._safe_get(aweme_detail, "statistics", {})
            view_count = self._parse_count(self._safe_get(statistics, "play_count"))
            like_count = self._parse_count(self._safe_get(statistics, "digg_count"))
            comment_count = self._parse_count(self._safe_get(statistics, "comment_count"))
            share_count = self._parse_count(self._safe_get(statistics, "share_count"))
            collect_count = self._parse_count(self._safe_get(statistics, "collect_count"))

            # 时间信息
            create_time = self._parse_timestamp(self._safe_get(aweme_detail, "create_time"))

            return VideoInfo(
                video_id=video_id,
                platform="douyin",
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
            logger.error(f"解析抖音响应数据失败: {e}")
            return None