"""
Just One API 响应适配器

把 JOA data 节点信封对齐到现有平台 service 认识的结构，复用平台解析逻辑；
平台 service 本身不因 JOA 做任何修改（快手只在适配器把 photos[0].streamManifest
改写成 manifest，不改 TikHub 快手链）。
"""

import re
from typing import Any, Callable, Dict, Optional

from loguru import logger

from ..platforms.base import VideoInfo
from ..platforms.douyin import DouyinService
from ..platforms.tiktok import TikTokService
from ..platforms.kuaishou import KuaishouService
from ..platforms.youtube import YouTubeService
from ..platforms.xiaohongshu import XiaohongshuService
from ..platforms.instagram import InstagramService
from ..platforms.wechat_channels import WechatChannelsService
from ..platforms.twitter import TwitterService


class JustOneApiAdapter:
    """
    Just One API 响应数据适配器

    adapt() 输入是 JustOneApiProvider 返回的完整信封 {"code": 0, "data": {...}}，
    输出统一 VideoInfo（provider=justoneapi）。解析失败返回 None，由责任链按
    可重试失败处理（JOA 本轮不启用终态短路）。
    """

    _SERVICE_MAP = {
        "douyin": DouyinService,
        "tiktok": TikTokService,
        "kuaishou": KuaishouService,
        "youtube": YouTubeService,
        "xiaohongshu": XiaohongshuService,
        "instagram": InstagramService,
        "wechat_channels": WechatChannelsService,
        "twitter": TwitterService,
    }

    _SPH_PATTERN = re.compile(r"weixin\.qq\.com/sph/([A-Za-z0-9]{1,64})")

    def adapt(
        self,
        raw_data: Dict,
        platform: str,
        video_id: str,
        original_url: str = "",
        download_mode: str = "video",
        quality: Optional[str] = None,
        **kwargs,
    ) -> Optional[VideoInfo]:
        """
        将 JOA 原始响应数据转换为 VideoInfo 对象

        Returns:
            Optional[VideoInfo]: provider=justoneapi；解析不出可播放直链时 None
        """
        platform = platform.lower()
        service_class = self._SERVICE_MAP.get(platform)
        if service_class is None:
            logger.error(f"JustOneAPI adapter: unsupported platform {platform}")
            return None
        if download_mode == "audio":
            # JOA 音频路径本轮不接（非目标）；按失败处理让链收尾。
            logger.info(
                f"JustOneAPI adapter: audio mode not supported for {platform}"
            )
            return None

        data = raw_data.get("data") if isinstance(raw_data, dict) else None
        if not isinstance(data, dict) or not data:
            logger.error("JustOneAPI response missing data node")
            return None

        aligner: Callable[..., Optional[Dict]] = getattr(
            self, f"_align_{platform}"
        )
        aligned = aligner(data, video_id=video_id, original_url=original_url)
        if aligned is None:
            logger.error(
                f"JustOneAPI adapter: cannot align envelope for {platform}"
            )
            return None

        service = service_class("", "")
        if isinstance(service, TikTokService):
            # 校验路径抑制失败落盘，避免每次解析失败都写盘（同 TikHub 评审 Issue 12）。
            service.suppress_error_save = True
        if isinstance(service, YouTubeService):
            video_info = service._parse_response(
                aligned, download_mode="video", quality=quality
            )
        elif isinstance(
            service,
            (
                DouyinService,
                TikTokService,
                KuaishouService,
                XiaohongshuService,
                TwitterService,
            ),
        ):
            video_info = service._parse_response(aligned, quality=quality)
        else:
            video_info = service._parse_response(aligned)

        if video_info is None:
            logger.error(
                f"JustOneAPI adapter: parse failed for {platform} video_id={video_id}"
            )
            return None
        video_info.provider = "justoneapi"
        return video_info

    # ------------------------- 信封对齐 -------------------------

    def _align_douyin(self, data: Dict, **_) -> Optional[Dict]:
        """抖音 v2 与现网 TikHub aweme 同形（data.aweme_detail），直接透传。"""
        return {"data": data}

    def _align_kuaishou(self, data: Dict, **_) -> Optional[Dict]:
        """只在 JOA 侧把 photos[0].streamManifest 改写成 manifest（camelCase
        adaptationSet schema），KuaishouService 不为 JOA 改一行。"""
        photos = data.get("photos")
        if not isinstance(photos, list) or not photos:
            return None
        photo = photos[0]
        if not isinstance(photo, dict):
            return None
        manifest = photo.get("streamManifest")
        if not isinstance(manifest, dict) or not manifest:
            return None
        # 改写：去掉原 streamManifest 键，避免同一内容双键并存。
        rest = {k: v for k, v in photo.items() if k != "streamManifest"}
        return {"data": {"photo": {**rest, "manifest": manifest}}}

    def _align_xiaohongshu(self, data: Dict, **_) -> Optional[Dict]:
        """小红书 v6 的 data 节点即笔记（video_info_v2.media.stream.h264），
        包成 app_v2 的 data.data[0] 信封走 XiaohongshuService 自适应解析。"""
        return {"data": {"data": [data]}}

    def _align_youtube(self, data: Dict, **_) -> Optional[Dict]:
        """YouTube 的 data.videos.items 与现网 web/get_video_info 同形，透传。"""
        return {"data": data}

    def _align_twitter(self, data: Dict, **_) -> Optional[Dict]:
        """Twitter 的 data.media.video[].variants 与现网 tweet detail 同形，透传。"""
        return {"data": data}

    def _align_tiktok(self, data: Dict, video_id: str = "", **_) -> Optional[Dict]:
        """TikTok get-post-detail v1 返回官方 PascalCase 视频字段
        （PlayAddrStruct.UrlList / bitrateInfo[].PlayAddr），改写成
        TikTokService 认的 snake_case aweme_detail 信封。"""
        video = data.get("video") if isinstance(data.get("video"), dict) else {}
        h264_urls = self._url_list(video.get("PlayAddrStruct"))
        bit_rate = []
        for entry in video.get("bitrateInfo") or []:
            if not isinstance(entry, dict):
                continue
            play = entry.get("PlayAddr") if isinstance(entry.get("PlayAddr"), dict) else {}
            urls = self._url_list(play)
            if not urls:
                continue
            bit_rate.append({
                "play_addr": {
                    "url_list": urls,
                    "width": self._int(play.get("Width")) or self._int(video.get("width")),
                    "height": self._int(play.get("Height")) or self._int(video.get("height")),
                },
                "bit_rate": self._int(entry.get("BandWidth")),
                "gear_name": str(entry.get("GearName") or ""),
            })
        if not h264_urls and not bit_rate:
            return None
        aweme = {
            "aweme_id": str(
                data.get("aweme_id") or data.get("awemeId") or video_id or ""
            ),
            "desc": str(data.get("desc") or ""),
            "author": data.get("author") if isinstance(data.get("author"), dict) else {},
            "statistics": (
                data.get("statistics") if isinstance(data.get("statistics"), dict) else {}
            ),
            "create_time": data.get("create_time"),
            "video": {
                "play_addr_h264": {
                    "url_list": h264_urls,
                    "width": self._int(video.get("width")),
                    "height": self._int(video.get("height")),
                },
                "bit_rate": bit_rate,
            },
        }
        return {"data": {"aweme_detail": aweme}}

    def _align_instagram(
        self, data: Dict, video_id: str = "", **_
    ) -> Optional[Dict]:
        """Instagram get-post-detail v1 的 data 直接是帖子节点；包成 v1 信封
        （data.video_url / is_video），并补 owner 供作者字段展示。"""
        if not (data.get("video_url") or data.get("video_versions")):
            return None
        node = {**data, "is_video": True}
        if not node.get("id"):
            node["id"] = str(video_id or "")
        if not isinstance(node.get("owner"), dict):
            node["owner"] = {"id": str(data.get("user_id") or ""), "username": str(data.get("username") or "")}
        return {"data": node}

    def _align_wechat_channels(
        self, data: Dict, video_id: str = "", original_url: str = "", **_
    ) -> Optional[Dict]:
        """视频号 basic-info 节点 → WechatChannelsService 认的 TikHub 扁平信封。

        params.share_url 由本仓拼回（sph 短码来自原始链接），object_type 恒 0。
        """
        code = self._sph_code(video_id, original_url)
        if not code:
            return None
        media = data.get("media") if isinstance(data.get("media"), dict) else {}

        def count(*names: str) -> Any:
            for name in names:
                if data.get(name) is not None:
                    return data[name]
            return None

        node = {
            "id": str(data.get("objectId") or data.get("object_id") or video_id or ""),
            "object_type": 0,
            "username": str(data.get("username") or ""),
            "nickname": str(data.get("nickname") or data.get("author") or ""),
            "title": str(data.get("title") or ""),
            "description": str(data.get("description") or data.get("desc") or ""),
            "create_time": count("createTime", "create_time"),
            "read_count": count("readCount", "read_count"),
            "like_count": count("likeCount", "like_count"),
            "fav_count": count("favCount", "fav_count"),
            "forward_count": count("forwardCount", "forward_count"),
            "comment_count": count("commentCount", "comment_count"),
            "media": media,
        }
        # TikHub 原始信封里 params.share_url 在顶层（非 data 节点内），
        # WechatChannelsService._parse_response 按顶层读取。
        return {
            "params": {"share_url": f"https://weixin.qq.com/sph/{code}"},
            "data": node,
        }

    # ------------------------- 小工具 -------------------------

    @classmethod
    def _sph_code(cls, video_id: str, original_url: str) -> str:
        match = cls._SPH_PATTERN.search(original_url or "")
        if match:
            return match.group(1)
        code = str(video_id or "").strip()
        # 纯数字 video_id 可能是 objectId，不当 sph 短码拼流端点。
        if (
            code
            and re.fullmatch(r"[A-Za-z0-9]{1,64}", code)
            and not code.isdigit()
        ):
            return code
        return ""

    @staticmethod
    def _url_list(node: Any) -> list:
        if isinstance(node, dict):
            urls = node.get("UrlList") or node.get("url_list")
            if isinstance(urls, list):
                return [u for u in urls if isinstance(u, str) and u]
        return []

    @staticmethod
    def _int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
