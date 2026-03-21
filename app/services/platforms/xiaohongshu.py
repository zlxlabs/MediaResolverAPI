"""
小红书平台解析服务
"""

from typing import Optional, Dict, Any
from loguru import logger
import urllib.parse

from .base import BasePlatformService, VideoInfo
from ...utils.http_client import HTTPClient


class XiaohongshuService(BasePlatformService):
    """
    小红书平台服务类
    """

    def __init__(self, api_key: str, api_base: str):
        super().__init__(api_key, api_base)
        self.endpoint = f"{api_base}/api/v1/xiaohongshu/web/get_note_info_v3"

    async def get_video_info(self, video_id_or_url: str) -> Optional[VideoInfo]:
        """
        获取小红书视频信息

        Args:
            video_id_or_url: 小红书笔记ID或完整URL

        Returns:
            Optional[VideoInfo]: 视频信息对象
        """
        try:
            # 如果传入的是完整URL，直接使用；否则报错
            if video_id_or_url.startswith('http'):
                share_url = video_id_or_url
                # 检查URL是否包含必要的token参数
                if 'xsec_token' not in share_url or 'xsec_source' not in share_url:
                    logger.error(f"小红书URL缺少必要的token参数: {share_url}")
                    raise Exception("小红书URL缺少必要的xsec_token和xsec_source参数，请使用完整的分享链接")
            else:
                # 如果只有video_id，直接报错
                logger.error(f"小红书平台需要完整的URL（包含token），不支持仅使用video_id: {video_id_or_url}")
                raise Exception("小红书平台需要完整的分享URL（包含xsec_token），不支持仅使用video_id")

            async with HTTPClient() as client:
                response = await client.get(
                    self.endpoint,
                    params={"share_text": share_url},
                    headers={"Authorization": f"Bearer {self.api_key}"}
                )

                if response.status_code == 200:
                    data = response.json()
                    return self._parse_response(data)
                else:
                    logger.error(f"小红书API请求失败: {response.status_code} - {response.text}")
                    return None

        except Exception as e:
            logger.error(f"获取小红书视频信息失败: {e}")
            return None

    def _parse_response(self, response_data: Dict[str, Any]) -> Optional[VideoInfo]:
        """
        解析小红书API响应数据

        Args:
            response_data: API响应数据

        Returns:
            Optional[VideoInfo]: 解析后的视频信息对象
        """
        try:
            data = self._safe_get(response_data, "data")
            if not data:
                logger.error("小红书响应数据中缺少data字段")
                return None

            # 基础信息
            video_id = self._safe_get(data, "note_id", "")
            description = self._safe_get(data, "desc", "")

            # 作者信息
            user = self._safe_get(data, "user", {})
            author_name = self._safe_get(user, "nickname", "")
            author_id = self._safe_get(user, "user_id", "")

            # 检查是否为视频类型
            video_info = self._safe_get(data, "video")
            if not video_info:
                logger.error("该小红书笔记不是视频类型")
                return None

            # 视频文件信息
            media = self._safe_get(video_info, "media")
            if not media:
                logger.error("小红书响应数据中缺少视频媒体信息")
                return None

            stream = self._safe_get(media, "stream")
            if not stream:
                logger.error("小红书响应数据中缺少视频流信息")
                return None

            h264_streams = self._safe_get(stream, "h264")
            if not h264_streams or not isinstance(h264_streams, list) or len(h264_streams) == 0:
                logger.error("小红书响应数据中缺少H264视频流")
                return None

            # 取第一个H264流
            h264_stream = h264_streams[0]
            video_url = self._safe_get(h264_stream, "master_url", "")
            width = self._safe_get(h264_stream, "weight", 0)  # 注意：API文档中是"weight"而不是"width"
            height = self._safe_get(h264_stream, "height", 0)
            quality = self._safe_get(h264_stream, "stream_desc", "")

            # 统计信息
            interact_info = self._safe_get(data, "interact_info", {})
            like_count = self._parse_count_text(self._safe_get(interact_info, "liked_count"))
            comment_count = self._parse_count_text(self._safe_get(interact_info, "comment_count"))
            share_count = self._parse_count_text(self._safe_get(interact_info, "share_count"))
            collect_count = self._parse_count_text(self._safe_get(interact_info, "collected_count"))

            # 时间信息 (毫秒时间戳)
            create_time_ms = self._safe_get(data, "time")
            create_time = None
            if create_time_ms:
                # 使用父类的_parse_timestamp方法将时间戳转换为datetime对象
                create_time = self._parse_timestamp(create_time_ms)
                if not create_time:
                    logger.warning(f"无法解析小红书创建时间: {create_time_ms}")

            # 小红书没有明确的标题字段，使用描述前50个字符作为标题
            title = description[:50] + "..." if len(description) > 50 else description

            return VideoInfo(
                video_id=video_id,
                platform="xiaohongshu",
                title=title,
                description=description,
                author_name=author_name,
                author_id=author_id,
                video_url=video_url,
                width=width,
                height=height,
                quality=quality,
                view_count=0,  # 小红书API不提供观看次数
                like_count=like_count,
                comment_count=comment_count,
                share_count=share_count,
                collect_count=collect_count,
                create_time=create_time,
                raw_data=response_data
            )

        except Exception as e:
            logger.error(f"解析小红书响应数据失败: {e}")
            return None

    def _parse_count_text(self, count_text: Any) -> int:
        """
        解析小红书的文本类型计数
        小红书的统计数据是文本类型，如"1.2万"、"345"、"10+"、"1千+"、"10k+"等

        Args:
            count_text: 文本类型的计数

        Returns:
            int: 解析后的数字
        """
        if not count_text:
            return 0

        try:
            count_str = str(count_text).strip().lower()  # 转为小写便于处理

            # 移除末尾的+号
            if count_str.endswith('+'):
                count_str = count_str[:-1].strip()

            # 处理中文数字单位
            if "万" in count_str:
                # 提取数字部分
                number_part = count_str.replace("万", "").strip()
                return int(float(number_part) * 10000)
            elif "千" in count_str:
                number_part = count_str.replace("千", "").strip()
                return int(float(number_part) * 1000)
            # 处理英文数字单位
            elif count_str.endswith('m'):
                # m = million = 百万
                number_part = count_str.replace("m", "").strip()
                return int(float(number_part) * 1000000)
            elif count_str.endswith('k'):
                # k = thousand = 千
                number_part = count_str.replace("k", "").strip()
                return int(float(number_part) * 1000)
            else:
                # 直接转换为整数
                return int(float(count_str))

        except (ValueError, TypeError):
            logger.warning(f"无法解析小红书计数文本: {count_text}")
            return 0