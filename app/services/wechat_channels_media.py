"""
微信视频号媒体获取：TikHub 优先，失败降级 Just One API 明文直链。

返回的 media dict 带显式 decrypt 标记（stream 层据此决定是否 XOR 解密）：
- decrypt=True（TikHub 命中）：full_url 为加密 CDN 直链，须按 decode_key 解密。
- decrypt=False（Just One API 命中）：full_url 为明文 mp4 直链，禁止 XOR
  （误走 XOR 会损坏片头）。

计费：resolve 层命中 Just One API 时已打过 basic-info（拿到 objectId）并把结果
落进 VideoCache；流层据此只补 download 一枪，不重复为同一次成功付 basic-info。
"""

from datetime import datetime, timezone

from loguru import logger

from ..core import database
from ..core.config import settings
from ..models.video_cache import VideoCache
from .providers.base import ProviderError
from .providers.justoneapi import JustOneApiProvider
from .providers.tikhub import TikHubProvider


def _cached_object_id(sph_code: str) -> str:
    """按 sph 短码反查视频号 objectId（查既有 VideoCache 行，不新建表）。

    视频号 URL 解析不出 video_id，resolve 落库时用 VideoInfo.video_id（=objectId）
    当键，行内 video_url 记着 /api/stream/wechat_channels/{sph}——按它反查即可复用
    resolve 已经付费换来的 basic-info 结果。查不到返回空串（流层退化为
    basic-info + download 两枪）。
    """
    db = database.SessionLocal()
    try:
        row = (
            db.query(VideoCache)
            .filter(
                VideoCache.platform == "wechat_channels",
                VideoCache.expires_at > datetime.now(timezone.utc),
                VideoCache.video_data["video_url"].as_string()
                == f"/api/stream/wechat_channels/{sph_code}",
            )
            .first()
        )
        if row is None:
            return ""
        return str((row.video_data or {}).get("video_id") or "")
    finally:
        db.close()


async def fetch_wechat_channels_media(sph_code: str) -> dict:
    """TikHub 先取媒体；失败且 JUSTONEAPI_TOKEN 非空时才降级 Just One API。"""
    try:
        media = await TikHubProvider().fetch_wechat_channels_media(sph_code)
    except ProviderError as exc:
        if not settings.JUSTONEAPI_TOKEN:
            raise
        logger.warning(
            "wechat channels tikhub media failed, trying justoneapi fallback: {}",
            exc,
        )
        object_id = _cached_object_id(sph_code)
        return await JustOneApiProvider().fetch_wechat_channels_media(
            sph_code, object_id
        )
    media["decrypt"] = True
    return media
