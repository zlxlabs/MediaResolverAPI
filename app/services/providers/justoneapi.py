"""
Just One API 视频信息提供者（按次计费备源）

只在 TikHub（及有 Cobalt 平台的 Cobalt）失败后作为责任链尾源使用：
- 抖音 / 快手 / 小红书 / 视频号：tikhub → justoneapi
- TikTok / Instagram / YouTube / Twitter：tikhub → cobalt → justoneapi

计费语义（官方文档）：业务 code==0 才扣费；301 可重试且不扣费；
100/302/303/400/500/600/601/602 不扣费。本 provider 每平台一次成功只打一枪，
不对同一业务结果叠打。HTTP 走裸异步 httpx 而不是仓内 HTTPClient，两条真实理由：
- HTTPClient 默认 max_retries=3 会对 HTTP 5xx 叠打（实测 HTTP 500 打 4 次），
  一次业务结果因此可能发出多次请求；业务码 301 只打 1 次，不受影响。
- HTTPClient 的错误日志会写出 httpx 异常原文，其中可能内嵌带 token 的完整 URL。

与 CobaltProvider 一致直接用异步 httpx，单次超时独立预算。

安全约束：token 只作 query 参数；日志与异常字符串禁止出现带 token 的完整 URL
（httpx 异常文本可能内嵌完整 URL，故只记异常类型名与 path）。
"""

import re
from typing import Dict, Tuple

import httpx

from ...core.config import settings
from .base import BaseProvider, ProviderError, VideoNotFoundError


class JustOneApiProvider(BaseProvider):
    """
    Just One API 提供者

    每平台只打锁定的一枪（有平台 ID 时禁止 share-url-transfer，短链展开本仓已做，
    再打是重复付费）。响应信封由 JustOneApiAdapter 对齐到现有平台 service 解析。
    """

    SUPPORTED_PLATFORMS = frozenset({
        "douyin", "kuaishou", "xiaohongshu", "tiktok", "youtube",
        "instagram", "twitter", "wechat_channels",
    })

    # 每平台一次成功只打这一枪：(端点路径, 入参名)。
    PLATFORM_ENDPOINTS: Dict[str, Tuple[str, str]] = {
        "douyin": ("/api/douyin/get-video-detail/v2", "videoId"),
        "kuaishou": ("/api/kuaishou/get-video-detail/v2", "videoId"),
        "xiaohongshu": ("/api/xiaohongshu/get-note-detail/v6", "noteId"),
        "tiktok": ("/api/tiktok/get-post-detail/v1", "postId"),
        "youtube": ("/api/youtube/get-video-detail/v1", "videoId"),
        "instagram": ("/api/instagram/get-post-detail/v1", "code"),
        "twitter": ("/api/twitter/get-post-detail/v1", "tweetId"),
    }

    # 视频号：已有 objectId 只打 download-url；只有分享链接/sph_code 时先
    # basic-info 拿 objectId 再 download（stream 层允许这两枪）。
    WECHAT_CHANNELS_BASIC_INFO = "/api/weixin-channels/get-video-basic-info/v1"
    WECHAT_CHANNELS_DOWNLOAD = "/api/weixin-channels/get-video-download-url/v1"
    WECHAT_CHANNELS_SHARE_URL_BASE = "https://weixin.qq.com/sph"

    # 官方文档列出的不计费失败码；301 亦可重试，但单次调用内不叠打（一次即止）。
    NOT_BILLED_CODES = frozenset({100, 301, 302, 303, 400, 500, 600, 601, 602})

    def __init__(self):
        """初始化提供者（token 留空时仅记降级日志，链构建层会跳过本源）"""
        super().__init__()
        self.api_base = settings.JUSTONEAPI_API_BASE.rstrip("/")
        self.api_token = settings.JUSTONEAPI_TOKEN
        self.timeout_seconds = settings.JUSTONEAPI_TIMEOUT_SECONDS
        if not self.api_token:
            self.log_info(
                "JUSTONEAPI-SKIP-EMPTY-TOKEN justoneapi disabled: "
                "JUSTONEAPI_TOKEN is empty"
            )

    @property
    def provider_name(self) -> str:
        """提供者名称"""
        return "justoneapi"

    def supports_platform(self, platform: str) -> bool:
        """检查是否支持指定平台"""
        return platform.lower() in self.SUPPORTED_PLATFORMS

    async def fetch_video_info(
        self,
        platform: str,
        video_id: str,
        original_url: str,
        **kwargs
    ) -> Dict:
        """
        从 Just One API 获取视频信息

        Returns:
            Dict: JOA 完整响应信封 {"code": 0, "data": {...}}，由适配器对齐解析

        Raises:
            VideoNotFoundError: 缺少平台 ID / sph 链接，或上游报内容不可用
            ProviderError: 上游服务错误 / 业务码非 0
        """
        platform = platform.lower()

        if not self.supports_platform(platform):
            raise ProviderError(
                f"Platform '{platform}' is not supported by JustOneAPI provider"
            )
        if not self.api_token:
            raise ProviderError("JustOneAPI token is not configured")

        if platform == "wechat_channels":
            return await self._fetch_wechat_channels_detail(video_id, original_url)

        endpoint, param = self.PLATFORM_ENDPOINTS[platform]
        if not video_id:
            # 无平台 ID 不打 share-url-transfer（短链展开本仓已做，重复付费）
            raise VideoNotFoundError(
                f"justoneapi requires a platform id for '{platform}', got none"
            )
        return await self._get(endpoint, {param: video_id})

    async def _get(self, path: str, params: Dict[str, object]) -> Dict:
        """
        单次 GET：token 只进 query；一次成功只打一枪，业务码非 0 一律不叠打。

        code==0 才算成功；301/100/302/303/400/500/600/601/602 不扣费，直接上抛
        由责任链决定去向（JOA 侧不重试、不打第二枪）。
        """
        try:
            async with httpx.AsyncClient(timeout=float(self.timeout_seconds)) as client:
                response = await client.get(
                    f"{self.api_base}{path}",
                    params={**{k: v for k, v in params.items() if v is not None},
                            "token": self.api_token},
                )
        except httpx.HTTPError as exc:
            # 异常文本可能内嵌带 token 的完整 URL：只记类型名与 path。
            self.log_warning(
                f"JustOneAPI request failed type={type(exc).__name__} path={path}"
            )
            raise ProviderError(
                f"JustOneAPI request failed: {type(exc).__name__} (path={path})"
            ) from exc
        if response.status_code != 200:
            raise ProviderError(f"JustOneAPI HTTP {response.status_code} (path={path})")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("JustOneAPI returned non-JSON body") from exc
        if not isinstance(payload, dict):
            raise ProviderError("JustOneAPI returned unexpected envelope type")
        code = payload.get("code")
        if code != 0:
            billed = "billed" if code not in self.NOT_BILLED_CODES else "not_billed"
            message = str(payload.get("message") or "")[:200]
            raise ProviderError(
                f"JustOneAPI business error code={code} billing={billed} "
                f"(path={path}) message={message}"
            )
        data = payload.get("data")
        if not isinstance(data, dict) or not data:
            raise ProviderError(f"JustOneAPI empty data (path={path})")
        self.log_info(f"JustOneAPI hit path={path}")
        return payload

    # ------------------------- 微信视频号 -------------------------

    async def _fetch_wechat_channels_detail(
        self, video_id: str, original_url: str
    ) -> Dict:
        """
        resolve 级元数据：只打 basic-info 一枪。

        video_url 恒为本服务流端点（由适配器按 sph 拼出），resolve 层不需要
        download-url；stream 层取媒体时才按需补 download 一枪（见
        fetch_wechat_channels_media）。只有 objectId、没有 sph 链接时连
        basic-info 也不打（拿不到 sph 拼不出流端点，成功不可能，先省这一枪）。
        """
        share_url = self._normalize_share_url(video_id, original_url)
        if not share_url:
            raise VideoNotFoundError(
                "justoneapi wechat_channels requires a sph share link or code"
            )
        return await self._get(
            self.WECHAT_CHANNELS_BASIC_INFO, {"shareUrl": share_url}
        )

    def _normalize_share_url(self, video_id: str, original_url: str) -> str:
        """优先从分享链接提取 sph 短码，其次接受本身就是短码的 video_id。"""
        match = re.search(
            r"weixin\.qq\.com/sph/([A-Za-z0-9]{1,64})", original_url or ""
        )
        if match:
            return f"{self.WECHAT_CHANNELS_SHARE_URL_BASE}/{match.group(1)}"
        code = str(video_id or "").strip()
        # 纯数字 video_id 可能是 objectId（非 sph 短码）：不打 basic-info（约束：
        # 已有 objectId 时禁止 get-video-basic-info），让链按失败继续。
        if (
            code
            and re.fullmatch(r"[A-Za-z0-9]{1,64}", code)
            and not code.isdigit()
        ):
            return f"{self.WECHAT_CHANNELS_SHARE_URL_BASE}/{code}"
        return ""

    async def fetch_wechat_channels_media(
        self, sph_code: str = "", object_id: str = ""
    ) -> dict:
        """
        stream 层取媒体：已有 objectId 只打 download-url；只有 sph_code 时先
        basic-info 再 download（两枪）。

        Returns:
            dict: full_url=明文 mp4 直链、decrypt=False（跳过 XOR 的显式标记）。
            download_url 不回传客户端，仅由流式层内部拉取。
        """
        if not self.api_token:
            raise ProviderError("JustOneAPI token is not configured")
        if object_id:
            payload = await self._get(
                self.WECHAT_CHANNELS_DOWNLOAD, {"objectId": object_id}
            )
        elif sph_code:
            share_url = f"{self.WECHAT_CHANNELS_SHARE_URL_BASE}/{sph_code}"
            info = await self._get(
                self.WECHAT_CHANNELS_BASIC_INFO, {"shareUrl": share_url}
            )
            object_id = str(info.get("data", {}).get("objectId") or "")
            if not object_id:
                raise ProviderError("JustOneAPI wechat_channels objectId missing")
            payload = await self._get(
                self.WECHAT_CHANNELS_DOWNLOAD, {"objectId": object_id}
            )
        else:
            raise VideoNotFoundError("wechat_channels sph_code is empty")

        download_url = str(payload.get("data", {}).get("download_url") or "")
        if not download_url:
            raise ProviderError("JustOneAPI wechat_channels download_url missing")
        media = {"full_url": download_url, "decrypt": False}
        size = payload.get("data", {}).get("file_size")
        if isinstance(size, int) and size >= 0:
            media["file_size"] = size
        return media
