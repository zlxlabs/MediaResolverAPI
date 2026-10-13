"""
yt-dlp 提供者

进程内以库方式调用 yt-dlp 拿原始提取 JSON（extract_info(download=False)）。
当前唯一消费者是 X Space 回放（twitter_space 平台）：resolve 与 stream 两处
每次现取——playlist URL 短命，绝不缓存（design 不变式 2）。
"""

import asyncio
import re
from typing import Any, Dict

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from ...core.config import settings
from .base import BaseProvider, ProviderError, TerminalError, VideoNotFoundError


class SpaceNotReplayError(TerminalError):
    """X Space 不是可回放状态（直播中/未开播/live_status 缺失）。终态，链上短路。"""


class YtdlpExtractionError(ProviderError):
    """yt-dlp 提取失败（网络/上游/超时），消息已脱敏 playlist URL。"""


# unavailable 类错误（终态、无重试意义）。取自 yt-dlp 2026.08.19
# TwitterSpacesIE._real_extract 的实际报错文案。
_UNAVAILABLE_MARKERS = (
    "not found",
    "not started yet",
    "replay is disabled",
    "not downloadable yet",
    "no longer available",
    "does not exist",
    "unavailable",
)

# 认证类：guest token 失效或 X 收紧匿名访问，保留 login_required 语义映射。
_LOGIN_MARKERS = (
    "login",
    "not authorized",
    "unauthorized",
    "authentication",
    "cookies",
)

_PLAYLIST_URL_RE = re.compile(r"https?://\S*pscp\.tv\S*")


def _redact_playlist_url(text: str) -> str:
    """短命 playlist URL 不得进任何错误消息/API 响应（design 不变式 2）。"""
    return _PLAYLIST_URL_RE.sub("[redacted-playlist-url]", text)


class YtdlpProvider(BaseProvider):
    """yt-dlp 提取提供者：调 yt-dlp 拿原始 JSON，映射由平台服务负责。"""

    SUPPORTED_PLATFORMS = frozenset({"twitter_space"})

    @property
    def provider_name(self) -> str:
        """提供者名称"""
        return "ytdlp"

    def supports_platform(self, platform: str) -> bool:
        return platform in self.SUPPORTED_PLATFORMS

    async def fetch_video_info(
        self, platform: str, video_id: str, original_url: str, **kwargs
    ) -> Dict:
        if not self.supports_platform(platform):
            raise ProviderError(f"ytdlp provider does not support platform: {platform}")

        timeout = settings.YTDLP_EXTRACT_TIMEOUT_SECONDS
        try:
            info = await asyncio.wait_for(
                asyncio.to_thread(self._extract_sync, original_url), timeout=timeout
            )
        except asyncio.TimeoutError:
            raise YtdlpExtractionError(
                f"yt-dlp extraction timed out after {timeout}s: {original_url}"
            )
        except DownloadError as exc:
            raise self._classify_download_error(str(exc)) from exc

        if not isinstance(info, dict):
            raise YtdlpExtractionError(
                f"yt-dlp returned non-dict payload for {original_url}"
            )

        live_status = info.get("live_status")
        if live_status != "was_live":
            # design 非目标：live_status != "was_live" 一律明确报错（含缺失）。
            raise SpaceNotReplayError(
                f"twitter space {video_id} is not a replay "
                f"(live_status={live_status if live_status is not None else 'missing'})"
            )
        return info

    def _extract_sync(self, url: str) -> Any:
        """同步提取，经 asyncio.to_thread 跑在线程池；不落盘、不下载。"""
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": settings.YTDLP_EXTRACT_TIMEOUT_SECONDS,
        }
        with YoutubeDL(options) as ydl:
            return ydl.extract_info(url, download=False)

    @staticmethod
    def _classify_download_error(message: str) -> ProviderError:
        sanitized = _redact_playlist_url(message)
        lowered = sanitized.lower()
        if any(marker in lowered for marker in _UNAVAILABLE_MARKERS):
            return VideoNotFoundError(f"twitter space replay unavailable: {sanitized}")
        if any(marker in lowered for marker in _LOGIN_MARKERS):
            return YtdlpExtractionError(f"login_required: {sanitized}")
        return YtdlpExtractionError(f"yt-dlp extraction failed: {sanitized}")
