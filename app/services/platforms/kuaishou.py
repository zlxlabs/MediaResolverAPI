"""
快手平台解析服务
"""

from typing import Optional, Dict, Any
from loguru import logger

from .base import BasePlatformService, VideoInfo
from ...utils.http_client import HTTPClient


class KuaishouService(BasePlatformService):
    """
    快手平台服务类
    """

    def __init__(self, api_key: str, api_base: str):
        super().__init__(api_key, api_base)
        self.endpoint = f"{api_base}/api/v1/kuaishou/web/fetch_one_video_v2"

    async def get_video_info(self, video_id: str) -> Optional[VideoInfo]:
        """
        获取快手视频信息

        Args:
            video_id: 快手视频ID (photo_id)

        Returns:
            Optional[VideoInfo]: 视频信息对象
        """
        try:
            async with HTTPClient() as client:
                response = await client.get(
                    self.endpoint,
                    params={"photo_id": video_id},
                    headers={"Authorization": f"Bearer {self.api_key}"}
                )

                if response.status_code == 200:
                    data = response.json()
                    return self._parse_response(data)
                else:
                    logger.error(f"快手API请求失败: {response.status_code} - {response.text}")
                    return None

        except Exception as e:
            logger.error(f"获取快手视频信息失败: {e}")
            return None

    @staticmethod
    def _extract_photo(response_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        定位快手「视频节点」，兼容 TikHub 多端点的不同 schema（camelCase manifest 系）：

          web/fetch_one_video_v2 → data.photo   （单数 dict，photo_id 入参）
          web/fetch_one_video    → data[0]       （data 为 list，元素即视频节点，share_text 入参）

        app/* 端点为另一套 snake_case schema（streamManifest/main_mv_urls），暂不在链内，
        见 TODOS「快手 app 端点 schema 接入」。
        """
        if not isinstance(response_data, dict):
            return None
        data = response_data.get("data")
        if isinstance(data, list):
            node = data[0] if data else None
        elif isinstance(data, dict):
            node = data.get("photo") or (data if "manifest" in data else None)
        else:
            node = None
        return node if isinstance(node, dict) and node else None

    def _parse_response(self, response_data: Dict[str, Any]) -> Optional[VideoInfo]:
        """
        解析快手API响应数据

        Args:
            response_data: API响应数据

        Returns:
            Optional[VideoInfo]: 解析后的视频信息对象
        """
        try:
            photo = self._extract_photo(response_data)
            if not photo:
                logger.error("快手响应数据中缺少photo字段")
                return None

            # 基础信息
            # 实测真实响应：视频 id 在 photoId（非 id，id 常为 None）；userId 为 int、
            # userEid 为稳定外部 id（与分享链 authorId 一致）。统一 str 化，避免 int/None
            # 流入 VideoInfoResponse(author_id/video_id: str) 触发 500（生产实测踩中）。
            video_id = str(
                self._safe_get(photo, "photoId", "")
                or self._safe_get(photo, "photo_id", "")
                or self._safe_get(photo, "id", "")
                or ""
            )
            caption = self._safe_get(photo, "caption", "")
            description = caption  # 快手的caption就是描述

            # 作者信息（userEid 为稳定字符串 id，优先；userId 为 int 兜底并 str 化）
            user_name = self._safe_get(photo, "userName", "")
            user_id = str(
                self._safe_get(photo, "userEid", "")
                or self._safe_get(photo, "userId", "")
                or ""
            )

            # 视频文件信息
            manifest = self._safe_get(photo, "manifest")
            if not manifest:
                logger.error("快手响应数据中缺少manifest字段")
                return None

            adaptation_set = self._safe_get(manifest, "adaptationSet")
            if not adaptation_set or not isinstance(adaptation_set, list) or len(adaptation_set) == 0:
                logger.error("快手响应数据中缺少adaptationSet字段")
                return None

            # 取第一个adaptationSet
            first_adaptation = adaptation_set[0]
            representation = self._safe_get(first_adaptation, "representation")
            if not representation or not isinstance(representation, list) or len(representation) == 0:
                logger.error("快手响应数据中缺少representation字段")
                return None

            # 取第一个representation
            first_repr = representation[0]
            video_url = self._safe_get(first_repr, "url", "")
            width = self._safe_get(first_repr, "width", 0)
            height = self._safe_get(first_repr, "height", 0)
            quality_type = self._safe_get(first_repr, "qualityType", "")

            # 统计信息
            like_count = self._parse_count(self._safe_get(photo, "likeCount"))
            comment_count = self._parse_count(self._safe_get(photo, "commentCount"))
            forward_count = self._parse_count(self._safe_get(photo, "forwardCount"))
            view_count = self._parse_count(self._safe_get(photo, "viewCount"))

            # 时间信息 (毫秒时间戳)
            timestamp_ms = self._safe_get(photo, "timestamp")
            create_time = None
            if timestamp_ms:
                # 使用父类的_parse_timestamp方法将时间戳转换为datetime对象
                create_time = self._parse_timestamp(timestamp_ms)
                if not create_time:
                    logger.warning(f"无法解析快手创建时间: {timestamp_ms}")

            # 快手没有明确的标题字段，使用描述前50个字符作为标题
            title = description[:50] + "..." if len(description) > 50 else description

            return VideoInfo(
                video_id=video_id,
                platform="kuaishou",
                title=title,
                description=description,
                author_name=user_name,
                author_id=user_id,
                video_url=video_url,
                width=width,
                height=height,
                quality=quality_type,
                view_count=view_count,
                like_count=like_count,
                comment_count=comment_count,
                share_count=forward_count,  # 快手的forwardCount对应分享数
                collect_count=0,  # 快手API不提供收藏数
                create_time=create_time,
                raw_data=response_data
            )

        except Exception as e:
            logger.error(f"解析快手响应数据失败: {e}")
            return None