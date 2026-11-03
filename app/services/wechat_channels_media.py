"""
微信视频号媒体获取：TikHub 优先，失败降级 Just One API 明文直链。

返回的 media dict 带显式 decrypt 标记（stream 层据此决定是否 XOR 解密）：
- decrypt=True（TikHub 命中）：full_url 为加密 CDN 直链，须按 decode_key 解密。
- decrypt=False（Just One API 命中）：full_url 为明文 mp4 直链，禁止 XOR
  （误走 XOR 会损坏片头）。
"""

from loguru import logger

from ..core.config import settings
from .providers.base import ProviderError
from .providers.justoneapi import JustOneApiProvider
from .providers.tikhub import TikHubProvider


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
        return await JustOneApiProvider().fetch_wechat_channels_media(sph_code)
    media["decrypt"] = True
    return media
