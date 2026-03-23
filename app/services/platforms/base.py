"""
平台解析基础类
定义统一的视频信息数据结构和接口
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class VideoInfo:
    """
    统一的视频信息数据结构
    """
    # 基础信息
    video_id: str
    platform: str
    title: str
    description: str

    # 作者信息
    author_name: str
    author_id: str

    # 视频文件信息
    video_url: str
    width: int
    height: int
    duration: Optional[int] = None
    quality: Optional[str] = None

    # 统计信息
    view_count: Optional[int] = None
    like_count: Optional[int] = None
    comment_count: Optional[int] = None
    share_count: Optional[int] = None
    collect_count: Optional[int] = None

    # 时间信息
    create_time: Optional[datetime] = None
    publish_time: Optional[str] = None

    # 数据源信息
    provider: Optional[str] = None  # 数据提供者：tikhub, cobalt

    # 原始数据
    raw_data: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'video_id': self.video_id,
            'platform': self.platform,
            'title': self.title,
            'description': self.description,
            'author_name': self.author_name,
            'author_id': self.author_id,
            'video_url': self.video_url,
            'width': self.width,
            'height': self.height,
            'duration': self.duration,
            'quality': self.quality,
            'view_count': self.view_count,
            'like_count': self.like_count,
            'comment_count': self.comment_count,
            'share_count': self.share_count,
            'collect_count': self.collect_count,
            'create_time': self.create_time.isoformat() if isinstance(self.create_time, datetime) else None,
            'publish_time': self.publish_time,
            'provider': self.provider,
            'raw_data': self.raw_data
        }

    def has_limited_info(self) -> bool:
        """
        检查是否为信息有限的视频（来自 Cobalt 等提供者）

        Returns:
            bool: 如果多个关键字段为空或默认值，则返回 True
        """
        limited_info_indicators = [
            not self.author_id,
            self.width == 0,
            self.height == 0,
            self.view_count is None,
            self.like_count is None,
        ]
        # 如果有3个或以上指标为True，认为是信息有限
        return sum(limited_info_indicators) >= 3


class BasePlatformService(ABC):
    """
    平台服务基类
    定义统一的接口规范
    """

    def __init__(self, api_key: str, api_base: str):
        """
        初始化平台服务

        Args:
            api_key: API密钥
            api_base: API基础URL
        """
        self.api_key = api_key
        self.api_base = api_base

    @abstractmethod
    async def get_video_info(self, video_id: str) -> Optional[VideoInfo]:
        """
        获取视频信息的抽象方法

        Args:
            video_id: 视频ID

        Returns:
            Optional[VideoInfo]: 视频信息对象，获取失败返回None
        """
        pass

    @abstractmethod
    def _parse_response(self, response_data: Dict[str, Any]) -> Optional[VideoInfo]:
        """
        解析API响应数据的抽象方法

        Args:
            response_data: API响应数据

        Returns:
            Optional[VideoInfo]: 解析后的视频信息对象
        """
        pass

    def _safe_get(self, data: Dict[str, Any], path: str, default: Any = None) -> Any:
        """
        安全地从嵌套字典中获取值

        Args:
            data: 嵌套字典
            path: 键路径，用点分隔，如 "data.video.url"
            default: 默认值

        Returns:
            Any: 获取到的值或默认值
        """
        try:
            keys = path.split('.')
            current = data
            for key in keys:
                if isinstance(current, dict):
                    current = current.get(key)
                elif isinstance(current, list) and key.isdigit():
                    index = int(key)
                    current = current[index] if 0 <= index < len(current) else None
                else:
                    return default

                if current is None:
                    return default

            return current
        except (KeyError, IndexError, TypeError, ValueError):
            return default

    def _parse_timestamp(self, timestamp: Any) -> Optional[datetime]:
        """
        解析时间戳为datetime对象

        Args:
            timestamp: 时间戳（秒或毫秒）

        Returns:
            Optional[datetime]: 解析后的datetime对象
        """
        if not timestamp:
            return None

        try:
            # 转换为整数
            ts = int(timestamp)

            # 判断是秒还是毫秒
            if ts > 10**10:  # 毫秒时间戳
                ts = ts / 1000

            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError):
            return None

    def _parse_count(self, count_str: Any) -> Optional[int]:
        """
        解析计数字符串为整数

        Args:
            count_str: 计数字符串或数字

        Returns:
            Optional[int]: 解析后的整数
        """
        if isinstance(count_str, int):
            return count_str

        if isinstance(count_str, str):
            try:
                # 移除逗号等分隔符
                clean_str = count_str.replace(',', '').replace(' ', '')
                return int(clean_str)
            except ValueError:
                pass

        return None