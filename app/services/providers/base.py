"""
视频信息提供者基类

定义所有提供者必须实现的接口
"""

from abc import ABC, abstractmethod
from typing import Dict, Optional
from loguru import logger


class ProviderError(Exception):
    """提供者错误基类"""
    pass


class VideoNotFoundError(ProviderError):
    """视频未找到错误"""
    pass


class TerminalError(VideoNotFoundError):
    """
    终态失败基类：内容不可恢复（私密/删除/图文无视频等），再降级也无意义。

    继承 VideoNotFoundError 以兼容现有捕获；VideoResolver 单独识别此基类，
    命中即停止责任链，不再 fallback 到下一个 provider（见 video_resolver.py）。
    各平台的终态异常继承本类，统一被 `except TerminalError` 捕获。
    """
    pass


class DouyinTerminalError(TerminalError):
    """抖音终态失败：视频私密/部分可见/不可恢复。"""
    pass


class XhsTerminalError(TerminalError):
    """小红书终态失败：图文笔记（无视频）/笔记删除/私密。"""
    pass


class BaseProvider(ABC):
    """
    视频信息提供者基类

    所有提供者都必须继承此类并实现其抽象方法
    """

    def __init__(self):
        """初始化提供者"""
        self.logger = logger.bind(provider=self.provider_name)

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """
        提供者名称

        Returns:
            str: 提供者的唯一标识名称，如 'tikhub', 'cobalt'
        """
        pass

    @abstractmethod
    def supports_platform(self, platform: str) -> bool:
        """
        检查是否支持指定平台

        Args:
            platform: 平台名称，如 'douyin', 'tiktok', 'instagram' 等

        Returns:
            bool: 是否支持该平台
        """
        pass

    @abstractmethod
    async def fetch_video_info(
        self,
        platform: str,
        video_id: str,
        original_url: str,
        **kwargs
    ) -> Dict:
        """
        获取视频信息

        这是提供者的核心方法，返回原始的视频信息字典。
        返回的数据格式由各提供者定义，需要通过适配器转换为统一格式。

        Args:
            platform: 平台名称
            video_id: 视频ID
            original_url: 原始视频URL
            **kwargs: 其他可选参数

        Returns:
            Dict: 原始的视频信息字典，格式由具体提供者定义

        Raises:
            VideoNotFoundError: 视频不存在或无法访问
            ProviderError: 提供者服务错误
        """
        pass

    def log_info(self, message: str, **kwargs):
        """记录信息日志"""
        self.logger.info(message, **kwargs)

    def log_warning(self, message: str, **kwargs):
        """记录警告日志"""
        self.logger.warning(message, **kwargs)

    def log_error(self, message: str, **kwargs):
        """记录错误日志"""
        self.logger.error(message, **kwargs)
