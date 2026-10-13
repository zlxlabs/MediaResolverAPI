"""X Space 回放响应解析（yt-dlp twitter:spaces 提取结果 → VideoInfo）。"""

from typing import Any, Dict, Optional

from ..providers.ytdlp import redact_playlist_url
from .base import BasePlatformService, VideoInfo


def _redact_raw_data(data: Any) -> Any:
    """playlist URL 短命凭证绝不持久化（锁定决策 4 / design 不变式 2）：
    缓存经 VideoInfo.to_dict() 把 raw_data 落库，映射层先脱敏。

    递归遍历所有字符串值（url 字段形态不止顶层 url 与 formats[].url，
    实测 requested_downloads[].url 同样携带 playlist），不做字段枚举。"""
    if isinstance(data, str):
        return redact_playlist_url(data)
    if isinstance(data, dict):
        return {k: _redact_raw_data(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_redact_raw_data(item) for item in data]
    return data


class TwitterSpaceService(BasePlatformService):
    """解析 yt-dlp 提取的 X Space 元数据。

    提取由 YtdlpProvider 负责（provider 链分工，同 twitter.py）；
    本类只做 JSON → VideoInfo 映射。
    """

    async def get_video_info(self, video_id: str) -> Optional[VideoInfo]:
        """提取走 provider 链，本方法不拉数据。"""
        return None

    def _parse_response(
        self, response_data: Dict[str, Any], quality: Optional[str] = None
    ) -> Optional[VideoInfo]:
        if not isinstance(response_data, dict):
            return None

        space_id = str(response_data.get("id") or "")
        playlist_url = response_data.get("url")
        if not space_id or not playlist_url:
            return None

        # quality 参数为 no-op：实测回放恒为单一音频 format（design 不变式 5），
        # 对齐 Instagram 单流先例，不报 422 也不伪造清晰度。
        return VideoInfo(
            video_id=space_id,
            platform="twitter_space",
            title=str(response_data.get("title") or ""),
            description=str(response_data.get("description") or ""),
            author_name=str(response_data.get("uploader") or ""),
            author_id=str(response_data.get("uploader_id") or ""),
            # 相对路径：host 由 API 层按 PUBLIC_BASE_URL/请求补全（wechat_channels.py:152 先例）。
            # playlist URL 短命，绝不进 video_url（design 不变式 2）。
            video_url=f"/api/stream/twitter_space/{space_id}",
            width=0,
            height=0,
            duration=self._parse_count(response_data.get("duration")),
            quality=None,
            media_type="audio",
            variants=None,
            view_count=self._parse_count(response_data.get("view_count")),
            like_count=self._parse_count(response_data.get("like_count")),
            comment_count=self._parse_count(response_data.get("comment_count")),
            create_time=self._parse_timestamp(response_data.get("timestamp")),
            publish_time=str(response_data.get("upload_date") or ""),
            raw_data=_redact_raw_data(response_data),
        )
