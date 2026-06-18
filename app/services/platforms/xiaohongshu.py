"""
小红书平台解析服务

取数由 TikHubProvider 的多级降级链负责（app_v2 → web_v3），本服务只提供
schema 自适应解析器：一个 `_parse_response` 通吃三种响应结构。

响应结构落点（详见 docs/xiaohongshu-fallback-design.md §2/§8）::

    app_v2:  data.data[0].video_info_v2.media.stream.h264[].master_url   (snake)
    web_v3:  data.data.items[0].noteCard.video.media.stream.h264[].masterUrl  (camel)
    旧(死):  data.video.media.stream.h264[].master_url                   (snake, 保留兜底)

字段命名 snake/camel 混杂，统一用 `_pick(d, *names)` 双名兼容。
"""

from typing import Optional, Dict, Any

from loguru import logger

from .base import BasePlatformService, VideoInfo


class XiaohongshuService(BasePlatformService):
    """小红书平台服务类（仅解析；取数见 TikHubProvider._fetch_xiaohongshu）。"""

    async def get_video_info(self, video_id: str) -> Optional[VideoInfo]:
        """
        已废弃：小红书取数由 TikHubProvider 多级降级链负责，不再由本服务直接请求。

        保留以满足 BasePlatformService 抽象接口；调用方应走 provider 链。
        """
        raise NotImplementedError(
            "小红书取数已迁移到 TikHubProvider._fetch_xiaohongshu（多级降级链）"
        )

    # ------------------------------------------------------------------
    # schema 自适应：定位 note 节点 + 取字段
    # ------------------------------------------------------------------

    @staticmethod
    def _pick(d: Any, *keys: str, default: Any = None) -> Any:
        """从 dict 取第一个存在且非 None 的键值（双名/多名兼容）。"""
        if not isinstance(d, dict):
            return default
        for k in keys:
            if d.get(k) is not None:
                return d[k]
        return default

    @staticmethod
    def extract_note(response_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        从 TikHub 响应中定位「笔记」节点（schema 自适应）。

        Returns:
            Optional[Dict]: 笔记节点（noteCard / app_v2 item / 旧 data）；定位不到返回 None
        """
        if not isinstance(response_data, dict):
            return None
        data = response_data.get("data")
        if not isinstance(data, dict):
            return None

        inner = data.get("data")
        # web_v3: data.data.items[0].noteCard
        if isinstance(inner, dict):
            items = inner.get("items")
            if isinstance(items, list) and items and isinstance(items[0], dict):
                note_card = items[0].get("noteCard")
                if isinstance(note_card, dict):
                    return note_card
        # app_v2: data.data 是列表
        if isinstance(inner, list) and inner and isinstance(inner[0], dict):
            return inner[0]
        # 旧端点: data 自身即笔记节点
        if data.get("note_id") or data.get("desc") or data.get("video"):
            return data
        return None

    def _count(self, interact: Dict[str, Any], node: Dict[str, Any], *keys: str) -> int:
        """从 interactInfo/interact_info 或 node 顶层取计数，解析文本为整数。"""
        for src in (interact, node):
            value = self._pick(src, *keys)
            if value is not None:
                return self._parse_count_text(value)
        return 0

    def _parse_response(self, response_data: Dict[str, Any]) -> Optional[VideoInfo]:
        """解析小红书 API 响应（三结构自适应）为 VideoInfo；无视频流返回 None。"""
        try:
            node = self.extract_note(response_data)
            if not node:
                logger.error("小红书响应数据中无法定位笔记节点")
                return None

            # 视频容器：web_v3/旧用 video，app_v2 用 video_info_v2
            video_info = self._pick(node, "video", "video_info_v2")
            if not isinstance(video_info, dict):
                logger.info("该小红书笔记不是视频类型（无视频容器）")
                return None

            h264_streams = self._safe_get(video_info, "media.stream.h264")
            if not isinstance(h264_streams, list) or not h264_streams:
                logger.info("小红书响应数据中缺少 H264 视频流")
                return None

            h264 = h264_streams[0]
            video_url = self._pick(h264, "masterUrl", "master_url", default="")
            if not video_url:
                logger.info("小红书 H264 流缺少 master_url")
                return None

            width = self._to_int(self._pick(h264, "width", "weight", default=0))
            height = self._to_int(self._pick(h264, "height", default=0))
            quality = self._pick(h264, "streamDesc", "stream_desc", default="")

            # 基础信息（双名兼容）
            video_id = self._pick(node, "note_id", "noteId", "id", default="")
            description = self._pick(node, "desc", default="")

            user = self._pick(node, "user", default={})
            author_name = self._pick(user, "nickname", "name", default="")
            author_id = self._pick(user, "userid", "userId", "user_id", "id", default="")

            # 统计信息：app_v2 在 node 顶层；web_v3 在 interactInfo；旧在 interact_info
            interact = self._pick(node, "interactInfo", "interact_info", default={})
            like_count = self._count(interact, node, "likedCount", "liked_count")
            collect_count = self._count(interact, node, "collectedCount", "collected_count")
            comment_count = self._count(
                interact, node, "commentCount", "comment_count", "comments_count"
            )
            share_count = self._count(
                interact, node, "shareCount", "share_count", "shared_count"
            )

            create_time = self._parse_timestamp(self._pick(node, "time"))

            title = self._pick(node, "title", default="") or (
                description[:50] + "..." if len(description) > 50 else description
            )

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
                view_count=0,  # 小红书 API 不提供观看次数
                like_count=like_count,
                comment_count=comment_count,
                share_count=share_count,
                collect_count=collect_count,
                create_time=create_time,
                raw_data=response_data,
            )

        except Exception as e:
            logger.error(f"解析小红书响应数据失败: {e}")
            return None

    @staticmethod
    def _to_int(value: Any) -> int:
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0

    def _parse_count_text(self, count_text: Any) -> int:
        """
        解析小红书的文本类型计数
        如"1.2万"、"345"、"10+"、"1千+"、"10k+"等，也兼容已是 int 的值。
        """
        if count_text is None or count_text == "":
            return 0

        if isinstance(count_text, (int, float)):
            return int(count_text)

        try:
            count_str = str(count_text).strip().lower()

            if count_str.endswith('+'):
                count_str = count_str[:-1].strip()

            if "万" in count_str:
                return int(float(count_str.replace("万", "").strip()) * 10000)
            elif "千" in count_str:
                return int(float(count_str.replace("千", "").strip()) * 1000)
            elif count_str.endswith('m'):
                return int(float(count_str.replace("m", "").strip()) * 1000000)
            elif count_str.endswith('k'):
                return int(float(count_str.replace("k", "").strip()) * 1000)
            else:
                return int(float(count_str))

        except (ValueError, TypeError):
            logger.warning(f"无法解析小红书计数文本: {count_text}")
            return 0
