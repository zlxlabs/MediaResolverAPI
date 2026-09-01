"""
微信视频号平台解析服务

取数由 TikHubProvider 的单端点链负责（POST fetch_video_detail），
本服务只提供 $.data 定位与 VideoInfo 映射。video_url 拼成本服务稳定端点，
不回传 TikHub 带时效的 media.full_url / url_token / decode_key。
video_url 只填相对路径，host 由 API 层按请求补全，避免缓存钉死旧域名。
"""

from copy import deepcopy
from typing import Any, Dict, Optional

from loguru import logger

from .base import BasePlatformService, VideoInfo


# 凭据类字段：不得进入 API 响应，raw_data 落缓存前也一律替换。
_REDACT_KEYS = frozenset({
    "decode_key",
    "full_url",
    "url_token",
    "url",
    "cover_url",
    "cover_url_token",
    "cover_img_url",
    "request_id",
    "cache_url",
    "debug_info",
})


def _redact_raw_data(payload: Any) -> Any:
    """递归把凭据类字段替换成 REDACTED，避免落入 cache / to_dict。"""
    if isinstance(payload, dict):
        out = {}
        for key, value in payload.items():
            if key in _REDACT_KEYS:
                out[key] = "REDACTED"
            else:
                out[key] = _redact_raw_data(value)
        return out
    if isinstance(payload, list):
        return [_redact_raw_data(item) for item in payload]
    return payload


class WechatChannelsService(BasePlatformService):
    """微信视频号平台服务类（仅解析；取数见 TikHubProvider._fetch_wechat_channels）。"""

    async def get_video_info(self, video_id: str) -> Optional[VideoInfo]:
        """
        已废弃：视频号取数由 TikHubProvider._fetch_wechat_channels 负责。

        保留以满足 BasePlatformService 抽象接口；调用方应走 provider 链。
        """
        raise NotImplementedError(
            "视频号取数已迁移到 TikHubProvider._fetch_wechat_channels"
        )

    @staticmethod
    def extract_data(response_data: dict) -> dict | None:
        """
        定位 $.data 节点；非 dict、缺失、object_type != 0 时返回 None。

        供 provider 的 classify / has_playable 复用。
        """
        if not isinstance(response_data, dict):
            return None
        data = response_data.get("data")
        if not isinstance(data, dict):
            return None
        if data.get("object_type") != 0:
            return None
        return data

    def _parse_response(self, response_data: Dict[str, Any]) -> Optional[VideoInfo]:
        """
        解析视频号 TikHub 响应（raw=false 的扁平结构）。

        缺 media 节点视为无可播放内容（返回 None，链上记 retryable）。
        """
        try:
            node = self.extract_data(response_data)
            if not node:
                logger.error("微信视频号响应缺少可播放 data 节点")
                return None

            media = node.get("media")
            if not isinstance(media, dict) or not media:
                logger.error("微信视频号响应缺少 media 节点")
                return None

            object_id = node.get("id")
            if object_id is None or object_id == "":
                logger.error("微信视频号响应缺少 id")
                return None
            video_id = str(object_id)
            author_id = str(node.get("username") or "")

            # read_count 实测恒为 0，不是真实播放量；填 0 会误导调用方。
            read_count = self._parse_count(node.get("read_count"))
            view_count = None if not read_count else read_count

            # 相对路径：缓存里不含 host。绝对地址由 API 层按请求补全。
            video_url = f"/api/stream/wechat_channels/{video_id}"

            return VideoInfo(
                video_id=video_id,
                platform="wechat_channels",
                title=node.get("title") or "",
                description=node.get("description") or "",
                author_name=node.get("nickname") or "",
                author_id=author_id,
                video_url=video_url,
                width=self._parse_count(media.get("width")) or 0,
                height=self._parse_count(media.get("height")) or 0,
                duration=self._parse_count(media.get("duration")),
                view_count=view_count,
                like_count=self._parse_count(node.get("like_count")),
                comment_count=self._parse_count(node.get("comment_count")),
                share_count=self._parse_count(node.get("forward_count")),
                collect_count=self._parse_count(node.get("fav_count")),
                create_time=self._parse_timestamp(node.get("create_time")),
                raw_data=_redact_raw_data(deepcopy(response_data)),
            )
        except Exception as e:
            logger.error(f"解析微信视频号响应数据失败: {e}")
            return None
