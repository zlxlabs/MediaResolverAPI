"""
TikHub 视频信息提供者

封装对 TikHub API 的调用，支持多个平台的视频信息获取
"""

import asyncio
from typing import Callable, Dict, List, Optional, Tuple
import httpx

import re
import urllib.parse

from .base import (
    BaseProvider,
    ProviderError,
    VideoNotFoundError,
    TerminalError,
    DouyinTerminalError,
    XhsTerminalError,
    KuaishouTerminalError,
)
from ...core.config import settings
from ...utils.http_client import HTTPClient
from ..platforms.douyin import DouyinService
from ..platforms.xiaohongshu import XiaohongshuService
from ..platforms.kuaishou import KuaishouService
from ..platforms.tiktok import TikTokService
from ..platforms.instagram import InstagramService
from ..platforms.youtube import YouTubeService
from ..platforms.wechat_channels import WechatChannelsService


class TikHubProvider(BaseProvider):
    """
    TikHub API 提供者

    支持平台：
    - douyin (抖音)
    - tiktok
    - kuaishou (快手)
    - youtube
    - xiaohongshu (小红书)
    - instagram
    """

    # 支持的平台集合（supports_platform 的唯一真相来源）。
    # 各平台的实际端点不再是单值，而是多级降级链（见 *_CHAIN 常量 + _fetch_<plat>），
    # 原 PLATFORM_ENDPOINTS/PLATFORM_PARAMS 已随通用 GET 路径删除（评审 Issue 3）。
    SUPPORTED_PLATFORMS = frozenset({
        "douyin", "tiktok", "kuaishou", "youtube", "xiaohongshu", "instagram",
        "wechat_channels",
    })

    # 抖音终态 reason（私密/部分可见）—— 再降级也拿不到，立即短路
    DOUYIN_TERMINAL_REASONS = {5, 10}

    # 抖音端点降级链：(名称, 路径, 入参名)。按序串行尝试直到命中。
    # web v1 → web v2(同源备份) → app v3-v3(跨源 + 解版权限制 reason=8)
    DOUYIN_CHAIN: List[Tuple[str, str, str]] = [
        ("web_v1", "/api/v1/douyin/web/fetch_one_video", "aweme_id"),
        ("web_v2", "/api/v1/douyin/web/fetch_one_video_v2", "aweme_id"),
        ("app_v3", "/api/v1/douyin/app/v3/fetch_one_video_v3", "aweme_id"),
    ]
    # hybrid 入口兜底：吃原始 url/分享文本，内部自带短链展开（由路由层在 id 提取失败时触发）
    DOUYIN_HYBRID: Tuple[str, str, str] = (
        "hybrid", "/api/v1/hybrid/video_data", "url",
    )

    # 单端超时与整链总预算（秒）。单端调用 max_retries=0，靠链本身做重试，
    # 避免 HTTPClient 默认 max_retries=3 把耗时放大到 ~180s（codex #3）。
    DOUYIN_PER_ENDPOINT_TIMEOUT = 25
    DOUYIN_TOTAL_BUDGET = 50.0

    # 小红书端点降级链：(名称, 路径, 入参模式)。串行尝试直到命中。
    # app_v2 仅凭 note_id 即可、不依赖 xsec_token（更鲁棒）→ 首选；
    # web_v3 需 note_id + xsec_token（token 缺失时跳过）→ 补强。
    XHS_CHAIN: List[Tuple[str, str, str]] = [
        ("app_v2", "/api/v1/xiaohongshu/app_v2/get_video_note_detail", "note_id"),
        ("web_v3", "/api/v1/xiaohongshu/web_v3/fetch_note_detail", "note_id_token"),
    ]
    XHS_PER_ENDPOINT_TIMEOUT = 25
    XHS_TOTAL_BUDGET = 50.0

    # 快手端点降级链：(名称, 路径, 入参名)。串行尝试直到命中。
    # web_v2 仅凭 photo_id（现状，data.photo）→ 首选；
    # web_share 吃 share_text=原始url（data 为 list），覆盖 id 提取失败的兜底，
    # 与 web_v2 同一套 camelCase manifest schema，解析器自适应（见 KuaishouService._extract_photo）。
    # app/* 端点为另一套 snake_case schema，暂不入链（见 TODOS）。
    KUAISHOU_CHAIN: List[Tuple[str, str, str]] = [
        ("web_v2", "/api/v1/kuaishou/web/fetch_one_video_v2", "photo_id"),
        ("web_share", "/api/v1/kuaishou/web/fetch_one_video", "share_text"),
    ]
    # 2 端点 × 25s = 50s，总预算留 5s 余量保证两端都能跑（codex/Issue 4）。
    KUAISHOU_PER_ENDPOINT_TIMEOUT = 25
    KUAISHOU_TOTAL_BUDGET = 55.0

    # TikTok 端点降级链：(名称, 路径, 入参名)。同源抖音 schema（data.aweme_detail），
    # 三端同 schema 由 TikTokService 解析（保 play_addr_h264 优先，评审 Issue 7）。
    # share_url(aweme_details 复数) / web(itemId) 为另一套 schema，暂不入链（见 TODOS）。
    # TikTok 有 cobalt 兜底 → 分类器不出终态（_classify_tiktok），链走完落 cobalt。
    TIKTOK_CHAIN: List[Tuple[str, str, str]] = [
        ("app_v3", "/api/v1/tiktok/app/v3/fetch_one_video", "aweme_id"),
        ("app_v3_v2", "/api/v1/tiktok/app/v3/fetch_one_video_v2", "aweme_id"),
        ("app_v3_v3", "/api/v1/tiktok/app/v3/fetch_one_video_v3", "aweme_id"),
    ]
    # 长链（3 端点）单端降至 18s，总预算 60s（3×18=54<60，保证三端都能跑，Issue 4）。
    TIKTOK_PER_ENDPOINT_TIMEOUT = 18
    TIKTOK_TOTAL_BUDGET = 60.0

    # Instagram 端点降级链：(名称, 路径, 入参名)。两端入参都喂原始 url（code_or_url
    # 接受 shortcode 或 url；post_url 接受 url），仅参数名不同。
    # v3(400 flaky) / v1_by_id(需数字 post_id) 暂不入链（见 TODOS）。
    # IG 有 cobalt 兜底 → 分类器不出终态（_classify_instagram，含轮播非视频 Issue 8）。
    INSTAGRAM_CHAIN: List[Tuple[str, str, str]] = [
        ("v2", "/api/v1/instagram/v2/fetch_post_info", "code_or_url"),
        ("v1_by_url", "/api/v1/instagram/v1/fetch_post_by_url", "post_url"),
    ]
    INSTAGRAM_PER_ENDPOINT_TIMEOUT = 25
    INSTAGRAM_TOTAL_BUDGET = 55.0

    # YouTube 端点降级链：(名称, 路径, 入参名)。两端 video_id 入参。
    # web(data.videos.items, 预解析直链) → web_v2(data.streamingData.formats, muxed)。
    # 实测 schema 不同，YouTubeService 自适应（_adaptive_video_streams）。
    # v3(playerResponse) / web_v2/get_video_info(snake_case) 另一套 schema，暂不入链（见 TODOS）。
    # YouTube 有 cobalt 兜底 → 分类器不出终态（_classify_youtube）。
    YOUTUBE_CHAIN: List[Tuple[str, str, str]] = [
        ("web", "/api/v1/youtube/web/get_video_info", "video_id"),
        ("web_v2", "/api/v1/youtube/web/get_video_info_v2", "video_id"),
    ]
    YOUTUBE_PER_ENDPOINT_TIMEOUT = 25
    YOUTUBE_TOTAL_BUDGET = 55.0

    # 微信视频号：TikHub 单源、单端点 POST。classify 只出 ok/retryable（无终态异常类）。
    WECHAT_CHANNELS_CHAIN: List[Tuple[str, str, str]] = [
        (
            "fetch_video_detail",
            "/api/v1/wechat_channels/v2/fetch_video_detail",
            "object_id",
        ),
    ]
    WECHAT_CHANNELS_PER_ENDPOINT_TIMEOUT = 25
    WECHAT_CHANNELS_TOTAL_BUDGET = 30.0

    @staticmethod
    def _classify_youtube(response: Dict) -> str:
        """
        YouTube 三态分类。YouTube 有 cobalt 兜底 → 永不出终态（评审 Issue 6）。

        Returns:
            "retryable" : 定位不到 envelope 数据（空/错误/区域限制等）
            "ok"        : 有 envelope 数据，交由解析器判定是否含可用视频流
        """
        if not isinstance(response, dict):
            return "retryable"
        data = response.get("data")
        return "ok" if isinstance(data, dict) and data else "retryable"

    @staticmethod
    def _classify_instagram(response: Dict) -> str:
        """
        IG 三态分类。IG 有 cobalt 兜底 → 永不出终态（评审 Issue 6）；非视频（图文/
        轮播）≠不可用——轮播子节点可能含视频（codex Issue 8），统一交 has_playable
        判定，判不出 → retryable 落 cobalt。

        Returns:
            "retryable" : 定位不到 envelope 数据（空/错误）
            "ok"        : 有 envelope 数据，交由解析器判定是否含视频
        """
        if not isinstance(response, dict):
            return "retryable"
        data = response.get("data")
        return "ok" if isinstance(data, dict) and data else "retryable"

    @staticmethod
    def _classify_kuaishou(response: Dict) -> str:
        """
        快手响应三态分类。快手为 tikhub 单源平台，但当前无已确认的终态样本
        （私密/删除），故只出 ok/retryable，不臆造终态（私密/删除会走完链 →
        VideoNotFoundError，零误报；见评审 Issue 2）。

        Returns:
            "retryable" : 定位不到视频节点（空/错误 envelope），试下一端点
            "ok"        : 定位到视频节点，交由解析器进一步校验
        """
        if not isinstance(response, dict):
            return "retryable"
        node = KuaishouService._extract_photo(response)
        return "ok" if node else "retryable"

    @staticmethod
    def _classify_xhs(response: Dict) -> str:
        """
        对小红书响应做三态分类。

        Returns:
            "terminal"  : 图文笔记/删除/私密等无视频终态，立即短路
            "retryable" : 端点错误/空/无法定位笔记，试下一端点
            "ok"        : 视频笔记，交由解析器进一步校验
        """
        if not isinstance(response, dict):
            return "retryable"
        node = XiaohongshuService.extract_note(response)
        if not node:
            return "retryable"
        note_type = str(node.get("type") or "").lower()
        # 有明确类型且非视频（小红书图文笔记 type 为 normal/multi 等）→ 终态
        if note_type and note_type != "video":
            return "terminal"
        return "ok"

    @staticmethod
    def _extract_xsec_token(url: str) -> Optional[str]:
        """从原始 URL 提取并解码 xsec_token（缺失返回 None）。"""
        if not url:
            return None
        match = re.search(r"xsec_token=([^&]+)", url)
        if not match:
            return None
        return urllib.parse.unquote(match.group(1))

    @staticmethod
    def _classify_aweme(response: Dict, *, allow_terminal: bool) -> str:
        """
        抖音/TikTok 同源 envelope 三态分类（aweme_detail/aweme_id/filter_list）。
        codex #9：扫整个 filter_list，不假设 index 0。

        allow_terminal：
          True  — 单源平台（抖音，无 cobalt）：命中终态 reason 立即短路。
          False — 有 cobalt 兜底的平台（TikTok）：终态降级为 retryable，让链走完后
                  落到 cobalt（评审 Issue 6：误判终态会让 cobalt 永远跑不到）。

        Returns:
            "terminal"  : 仅 allow_terminal 时，私密/部分可见等终态
            "retryable" : 版权受限(reason=8)/空/异常 envelope，应试下一端点
            "ok"        : 含作品详情，交由解析器进一步校验
        """
        if not isinstance(response, dict):
            return "retryable"
        payload = response.get("data")
        if not isinstance(payload, dict):
            return "retryable"

        filters = payload.get("filter_list")
        if isinstance(filters, list) and filters:
            reasons = {
                f.get("reason") for f in filters if isinstance(f, dict)
            }
            if allow_terminal and (reasons & TikHubProvider.DOUYIN_TERMINAL_REASONS):
                return "terminal"
            return "retryable"

        # 有作品详情（web/app 的 aweme_detail，或 hybrid 的 data 根）
        if isinstance(payload.get("aweme_detail"), dict) and payload.get("aweme_detail"):
            return "ok"
        if "aweme_id" in payload:
            return "ok"
        return "retryable"

    @staticmethod
    def _classify_douyin(response: Dict) -> str:
        """抖音（tikhub 单源）：允许终态短路。"""
        return TikHubProvider._classify_aweme(response, allow_terminal=True)

    @staticmethod
    def _classify_tiktok(response: Dict) -> str:
        """TikTok（有 cobalt 兜底）：终态降级为 retryable，链走完落 cobalt（Issue 6）。"""
        return TikHubProvider._classify_aweme(response, allow_terminal=False)

    def __init__(self):
        """初始化 TikHub 提供者"""
        super().__init__()
        self.api_key = settings.TIKHUB_API_KEY
        self.api_base = settings.TIKHUB_API_BASE

        if not self.api_key:
            self.log_warning("TikHub API key is not configured")

    @property
    def provider_name(self) -> str:
        """提供者名称"""
        return "tikhub"

    def supports_platform(self, platform: str) -> bool:
        """
        检查是否支持指定平台

        Args:
            platform: 平台名称

        Returns:
            bool: 是否支持
        """
        return platform.lower() in self.SUPPORTED_PLATFORMS

    async def fetch_video_info(
        self,
        platform: str,
        video_id: str,
        original_url: str,
        **kwargs
    ) -> Dict:
        """
        从 TikHub API 获取视频信息

        Args:
            platform: 平台名称
            video_id: 视频ID
            original_url: 原始视频URL
            **kwargs: 其他参数

        Returns:
            Dict: TikHub API 返回的原始数据

        Raises:
            VideoNotFoundError: 视频不存在
            ProviderError: API 调用失败
        """
        platform = platform.lower()

        if not self.supports_platform(platform):
            raise ProviderError(f"Platform '{platform}' is not supported by TikHub provider")

        if not self.api_key:
            raise ProviderError("TikHub API key is not configured")

        # 抖音走多级端点降级链（取→分类→解析校验→重试，全在 provider 内闭环）
        if platform == "douyin":
            return await self._fetch_douyin(
                video_id, original_url, use_hybrid=kwargs.get("use_hybrid", False)
            )

        # 小红书走多级端点降级链（app_v2 → web_v3）
        if platform == "xiaohongshu":
            return await self._fetch_xiaohongshu(video_id, original_url)

        # 快手走多级端点降级链（web_v2 → web_share）
        if platform == "kuaishou":
            return await self._fetch_kuaishou(video_id, original_url)

        # TikTok 走多级端点降级链（app/v3 → _v2 → _v3）
        if platform == "tiktok":
            return await self._fetch_tiktok(video_id, original_url)

        # Instagram 走多级端点降级链（v2 → v1_by_url）
        if platform == "instagram":
            return await self._fetch_instagram(video_id, original_url)

        # YouTube 走多级端点降级链（web → web_v2）
        if platform == "youtube":
            return await self._fetch_youtube(video_id, original_url)

        # 微信视频号走单端点链（POST fetch_video_detail）
        if platform == "wechat_channels":
            return await self._fetch_wechat_channels(video_id, original_url)

        # 全平台均已走通用引擎多级降级链；到此说明 SUPPORTED_PLATFORMS 加了平台
        # 却漏接 dispatch 分支 —— 显式报错而非静默返回 None（防隐藏路径）。
        raise ProviderError(f"No fallback chain wired for platform '{platform}'")

    # ================= 通用多级降级引擎（抖音/小红书/快手/TikTok/IG/YT 共用） =================
    #
    #   取 token/参数 → build_chain(按条件裁剪) ──┐
    #                                            ▼
    #   ┌──────────────── _run_chain（asyncio.timeout 总预算兜底）────────────────┐
    #   │  for endpoint in chain:                                                 │
    #   │     data = _call_endpoint(name, path, build_params(endpoint))  # 单端    │
    #   │              · max_retries=0（重试交给链，避免超时放大 codex #3）          │
    #   │              · 4xx 取 body 交分类器（终态信息常藏 body codex #4）          │
    #   │     decision = classify(data)                                           │
    #   │        terminal  → raise terminal_exc        # 立即短路                   │
    #   │        retryable → 记录, 下一端点                                         │
    #   │        ok        → has_playable(data)?                                   │
    #   │                       return data            # 命中即停                   │
    #   │                       记 parse_failed, 下一端点                          │
    #   └────────────────────────────────────────────────────────────────────────┘
    #   超时 → ProviderError("timed out")   全链未命中 → VideoNotFoundError
    #
    # 各平台只配置差异：chain（端点表）/ build_params（入参构造）/ classify（三态分类器）
    # / has_playable（解析校验）/ terminal_exc（终态异常类）。骨架在此唯一实现（DRY）。

    async def _run_chain(
        self,
        *,
        chain: List[Tuple],
        build_params: Callable[[Tuple], Dict],
        classify: Callable[[Dict], str],
        has_playable: Callable[[Dict], bool],
        terminal_exc: type,
        total_budget: float,
        per_timeout: float,
        target: str,
        label: str,
    ) -> Dict:
        """
        通用多级端点降级引擎：串行尝试 chain，命中即返回；终态立即短路；全失败抛错。

        Args:
            chain: 已按条件裁剪好的端点表，元素至少为 (name, path, ...)。
            build_params: 端点元组 → TikHub query 参数 dict（含 id/url/token 等差异）。
            classify: 响应 → "terminal" | "retryable" | "ok" 三态分类器。
            has_playable: 响应 → 能否解析出无水印直链（ok 后的最终校验）。
            terminal_exc: 该平台终态异常类（须为 TerminalError 子类，立即短路）。
            total_budget / per_timeout: 整链总预算 / 单端超时（秒）。
            target: 日志用标识（video_id / note_id / url）。
            label: 日志用平台名（"Douyin"/"Xiaohongshu"/...）。

        Raises:
            terminal_exc: 内容终态不可恢复，立即短路。
            VideoNotFoundError: 全链未命中。
            ProviderError: 总预算超时或认证失败。
        """
        attempts: List[Dict] = []
        try:
            async with asyncio.timeout(total_budget):
                for endpoint in chain:
                    name, path = endpoint[0], endpoint[1]
                    try:
                        data = await self._call_endpoint(
                            name, path, build_params(endpoint), per_timeout
                        )
                    except ProviderError as e:
                        attempts.append({"endpoint": name, "decision": "http_error", "error": str(e)})
                        self.log_warning(f"{label} endpoint {name} http error: {e}")
                        continue

                    decision = classify(data)
                    if decision == "terminal":
                        self.log_info(
                            f"{label} terminal response, short-circuit",
                            endpoint=name, target=target,
                        )
                        raise terminal_exc(
                            f"{label} content unavailable (terminal): {target}"
                        )
                    if decision == "retryable":
                        attempts.append({"endpoint": name, "decision": "retryable"})
                        continue

                    # ok：解析校验，解析不出可播放直链也算可重试（codex #10）
                    if has_playable(data):
                        self.log_info(f"{label} endpoint hit", endpoint=name, target=target)
                        return data
                    attempts.append({"endpoint": name, "decision": "parse_failed"})
                    self.log_warning(f"{label} endpoint {name} ok but no playable url")
        except asyncio.TimeoutError:
            self.log_error(
                f"{label} chain timed out after {total_budget}s",
                target=target, attempts=attempts,
            )
            raise ProviderError(
                f"{label} endpoint chain timed out after {total_budget}s "
                f"[attempts={attempts}]"
            )

        raise VideoNotFoundError(
            f"{label} all endpoints failed for '{target}' [attempts={attempts}]"
        )

    async def _call_endpoint(
        self, name: str, path: str, params: Dict, per_timeout: float
    ) -> Dict:
        """
        调单个 TikHub 端点（合并自原 _call_douyin_endpoint / _call_xhs_endpoint，仅差
        参数构造，已上移到各平台的 build_params 回调）。

        单端 max_retries=0（重试交给链本身，避免 HTTPClient 默认重试把超时放大）。
        catch httpx.HTTPStatusError 并尽量取出错误体交给分类器（codex #4）：终态/受限
        信息常藏在 4xx body 的 filter_list 里，取出来而非吞成不透明错误。
        """
        url = f"{self.api_base}{path}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        # 视频号 TikHub 端点是 POST JSON（raw 必须是 bool false）；其余平台保持 GET query。
        use_post = path.startswith("/api/v1/wechat_channels/")
        try:
            async with HTTPClient(timeout=per_timeout, max_retries=0) as client:
                if use_post:
                    response = await client.post(url, json=params, headers=headers)
                else:
                    response = await client.get(url, params=params, headers=headers)
                return response.json()
        except httpx.HTTPStatusError as e:
            resp = e.response
            status = resp.status_code if resp is not None else None
            if status == 401:
                raise ProviderError("TikHub API authentication failed")
            body = None
            if resp is not None:
                try:
                    body = resp.json()
                except Exception:
                    body = None
            if isinstance(body, dict):
                return body  # 交给分类器判定 terminal/retryable
            raise ProviderError(f"{name} HTTP {status}")
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"{name} request failed: {e}")

    async def _fetch_douyin(
        self, video_id: str, original_url: str, use_hybrid: bool = False
    ) -> Dict:
        """
        抖音多级降级（薄封装，骨架见 _run_chain）。

        链：web v1 → web v2(同源备份) → app v3-v3(跨源, 解版权 reason=8)。
        use_hybrid=True 时只走 hybrid 入口兜底（吃原始 url，由路由层在 id 提取失败时触发）。
        抖音为 tikhub 单源平台，允许终态短路（DouyinTerminalError）。
        """
        chain = [self.DOUYIN_HYBRID] if use_hybrid else list(self.DOUYIN_CHAIN)

        def build_params(endpoint: Tuple) -> Dict:
            _name, _path, param = endpoint
            # hybrid 端点入参为 url，其余端点为 aweme_id
            return {param: original_url if param == "url" else video_id}

        return await self._run_chain(
            chain=chain,
            build_params=build_params,
            classify=self._classify_douyin,
            has_playable=self._douyin_has_playable,
            terminal_exc=DouyinTerminalError,
            total_budget=self.DOUYIN_TOTAL_BUDGET,
            per_timeout=self.DOUYIN_PER_ENDPOINT_TIMEOUT,
            target=video_id or original_url,
            label="Douyin",
        )

    def _douyin_has_playable(self, data: Dict) -> bool:
        """用 DouyinService 的 schema 自适应解析器校验是否能解析出无水印直链。"""
        info = DouyinService(self.api_key, self.api_base)._parse_response(data)
        return bool(info and info.video_url)

    async def _fetch_xiaohongshu(self, note_id: str, original_url: str) -> Dict:
        """
        小红书多级降级（薄封装，骨架见 _run_chain）。

        链：app_v2(note_id, 不依赖 token, 首选) → web_v3(note_id + xsec_token)。
        token 缺失时 web_v3 端点在链构造阶段被裁剪掉。
        小红书为 tikhub 单源平台，允许终态短路（XhsTerminalError，图文/删除/私密）。
        """
        token = self._extract_xsec_token(original_url)
        # token 缺失时跳过需要 token 的端点（web_v3）
        chain = [
            (name, path, mode)
            for name, path, mode in self.XHS_CHAIN
            if mode != "note_id_token" or token
        ]

        def build_params(endpoint: Tuple) -> Dict:
            _name, _path, mode = endpoint
            params: Dict[str, str] = {"note_id": note_id}
            if mode == "note_id_token":
                params["xsec_token"] = token or ""
            return params

        return await self._run_chain(
            chain=chain,
            build_params=build_params,
            classify=self._classify_xhs,
            has_playable=self._xhs_has_playable,
            terminal_exc=XhsTerminalError,
            total_budget=self.XHS_TOTAL_BUDGET,
            per_timeout=self.XHS_PER_ENDPOINT_TIMEOUT,
            target=note_id,
            label="Xiaohongshu",
        )

    def _xhs_has_playable(self, data: Dict) -> bool:
        """用 XiaohongshuService 的 schema 自适应解析器校验是否能解析出无水印直链。"""
        info = XiaohongshuService(self.api_key, self.api_base)._parse_response(data)
        return bool(info and info.video_url)

    async def _fetch_kuaishou(self, video_id: str, original_url: str) -> Dict:
        """
        快手多级降级（薄封装，骨架见 _run_chain）。

        链：web_v2(photo_id, data.photo) → web_share(share_text=url, data[0])。
        两端同一 camelCase manifest schema，解析器自适应。快手为单源平台（无 cobalt），
        但当前分类器不出终态（无真实终态样本），私密/删除走完链 → VideoNotFoundError。
        """
        chain = list(self.KUAISHOU_CHAIN)

        def build_params(endpoint: Tuple) -> Dict:
            _name, _path, param = endpoint
            # web_share 入参为 share_text=原始url，其余为 photo_id
            return {param: original_url if param == "share_text" else video_id}

        return await self._run_chain(
            chain=chain,
            build_params=build_params,
            classify=self._classify_kuaishou,
            has_playable=self._kuaishou_has_playable,
            terminal_exc=KuaishouTerminalError,
            total_budget=self.KUAISHOU_TOTAL_BUDGET,
            per_timeout=self.KUAISHOU_PER_ENDPOINT_TIMEOUT,
            target=video_id or original_url,
            label="Kuaishou",
        )

    def _kuaishou_has_playable(self, data: Dict) -> bool:
        """用 KuaishouService 的 schema 自适应解析器校验是否能解析出可播放直链。"""
        info = KuaishouService(self.api_key, self.api_base)._parse_response(data)
        return bool(info and info.video_url)

    async def _fetch_tiktok(self, video_id: str, original_url: str) -> Dict:
        """
        TikTok 多级降级（薄封装，骨架见 _run_chain）。

        链：app/v3/fetch_one_video → _v2 → _v3，三端同 data.aweme_detail schema。
        has_playable 用 TikTokService（保 play_addr_h264 优先，评审 Issue 7）；
        classify 复用同源 aweme envelope 逻辑但不出终态（有 cobalt 兜底，Issue 6）。
        terminal_exc 实际不会被触发（_classify_tiktok 永不返回 terminal）。
        """
        chain = list(self.TIKTOK_CHAIN)

        def build_params(endpoint: Tuple) -> Dict:
            _name, _path, param = endpoint
            return {param: video_id}

        return await self._run_chain(
            chain=chain,
            build_params=build_params,
            classify=self._classify_tiktok,
            has_playable=self._tiktok_has_playable,
            terminal_exc=TerminalError,  # 占位：cobalt 兜底平台不出终态，不会被 raise
            total_budget=self.TIKTOK_TOTAL_BUDGET,
            per_timeout=self.TIKTOK_PER_ENDPOINT_TIMEOUT,
            target=video_id or original_url,
            label="TikTok",
        )

    def _tiktok_has_playable(self, data: Dict) -> bool:
        """用 TikTokService（非 DouyinService）校验直链，保 play_addr_h264 优先（Issue 7）。"""
        svc = TikTokService(self.api_key, self.api_base)
        # 校验路径抑制失败落盘，避免降级链每个端点解析失败都写文件（评审 Issue 12）。
        svc.suppress_error_save = True
        info = svc._parse_response(data)
        return bool(info and info.video_url)

    async def _fetch_instagram(self, video_id: str, original_url: str) -> Dict:
        """
        Instagram 多级降级（薄封装，骨架见 _run_chain）。

        链：v2/fetch_post_info(code_or_url) → v1/fetch_post_by_url(post_url)，两端
        入参都喂 original_url（仅参数名不同），InstagramService 自动识别 v1/v2 schema。
        IG 有 cobalt 兜底 → 不出终态（非视频 → has_playable False → retryable → 落 cobalt）。
        """
        chain = list(self.INSTAGRAM_CHAIN)

        def build_params(endpoint: Tuple) -> Dict:
            _name, _path, param = endpoint
            # code_or_url 接受 shortcode 或完整 url；post_url 接受完整 url。统一喂原始 url。
            return {param: original_url}

        return await self._run_chain(
            chain=chain,
            build_params=build_params,
            classify=self._classify_instagram,
            has_playable=self._instagram_has_playable,
            terminal_exc=TerminalError,  # 占位：cobalt 兜底平台不出终态，不会被 raise
            total_budget=self.INSTAGRAM_TOTAL_BUDGET,
            per_timeout=self.INSTAGRAM_PER_ENDPOINT_TIMEOUT,
            target=video_id or original_url,
            label="Instagram",
        )

    def _instagram_has_playable(self, data: Dict) -> bool:
        """用 InstagramService（自动识别 v1/v2 schema）校验是否能解析出视频直链。"""
        info = InstagramService(self.api_key, self.api_base)._parse_response(data)
        return bool(info and info.video_url)

    async def _fetch_youtube(self, video_id: str, original_url: str) -> Dict:
        """
        YouTube 多级降级（薄封装，骨架见 _run_chain）。

        链：web/get_video_info(data.videos.items) → web/get_video_info_v2(streamingData)。
        两端 video_id 入参，YouTubeService 自适应两套 schema。
        YouTube 有 cobalt 兜底 → 不出终态（区域限制/无流 → has_playable False → retryable → cobalt）。
        """
        chain = list(self.YOUTUBE_CHAIN)

        def build_params(endpoint: Tuple) -> Dict:
            _name, _path, param = endpoint
            return {param: video_id}

        return await self._run_chain(
            chain=chain,
            build_params=build_params,
            classify=self._classify_youtube,
            has_playable=self._youtube_has_playable,
            terminal_exc=TerminalError,  # 占位：cobalt 兜底平台不出终态，不会被 raise
            total_budget=self.YOUTUBE_TOTAL_BUDGET,
            per_timeout=self.YOUTUBE_PER_ENDPOINT_TIMEOUT,
            target=video_id or original_url,
            label="YouTube",
        )

    def _youtube_has_playable(self, data: Dict) -> bool:
        """用 YouTubeService（自适应 videos.items / streamingData）校验是否能解析出视频流。"""
        info = YouTubeService(self.api_key, self.api_base)._parse_response(data)
        return bool(info and info.video_url)

    @staticmethod
    def _classify_wechat_channels(response: Dict) -> str:
        """
        视频号两态分类。单源单端点，不出终态（无 WechatChannelsTerminalError）。

        Returns:
            "retryable" : 定位不到可播放 data（空/非 dict/object_type != 0）
            "ok"        : 有可播放 data 节点，交由解析器判定是否含 media
        """
        node = WechatChannelsService.extract_data(response)
        return "ok" if node else "retryable"

    async def _fetch_wechat_channels(self, video_id: str, original_url: str) -> Dict:
        """
        微信视频号单端点链（薄封装，骨架见 _run_chain）。

        POST /api/v1/wechat_channels/v2/fetch_video_detail；
        video_id 非空传 object_id，否则传 share_url=original_url；始终 raw: false。
        """
        chain = list(self.WECHAT_CHANNELS_CHAIN)

        def build_params(_endpoint: Tuple) -> Dict:
            params: Dict[str, object] = {"raw": False}
            if video_id:
                params["object_id"] = video_id
            else:
                params["share_url"] = original_url
            return params

        return await self._run_chain(
            chain=chain,
            build_params=build_params,
            classify=self._classify_wechat_channels,
            has_playable=self._wechat_channels_has_playable,
            terminal_exc=TerminalError,  # 占位：classify 永不返回 terminal
            total_budget=self.WECHAT_CHANNELS_TOTAL_BUDGET,
            per_timeout=self.WECHAT_CHANNELS_PER_ENDPOINT_TIMEOUT,
            target=video_id or original_url,
            label="WechatChannels",
        )

    def _wechat_channels_has_playable(self, data: Dict) -> bool:
        """用 WechatChannelsService._parse_response 校验，不另写一套判断。"""
        info = WechatChannelsService(self.api_key, self.api_base)._parse_response(data)
        return bool(info and info.video_url)

    async def fetch_wechat_channels_media(self, object_id: str) -> dict:
        """取当次配套的 media 信息。返回至少含 full_url / decode_key / file_size。

        每次调用都走一次新的 TikHub detail 请求，不缓存返回值。
        (full_url, decode_key) 必须成对使用，跨次混用必然解密失败。

        object_id 查询偶发返回微信错误包（无 object_type），单端点链会记 retryable
        后立刻 VideoNotFoundError。这里对这一瞬态做有限次重试并打 WARNING，
        耗尽后仍把错误抛给调用方（端点转 5xx JSON），不静默当成功。
        """
        if not object_id:
            raise VideoNotFoundError("wechat_channels object_id is empty")
        data = None
        last_exc: Optional[BaseException] = None
        attempts = 3
        for attempt in range(1, attempts + 1):
            try:
                data = await self._fetch_wechat_channels(object_id, "")
                break
            except VideoNotFoundError as exc:
                last_exc = exc
                if attempt >= attempts:
                    raise
                self.log_warning(
                    "wechat_channels media lookup retryable, retrying",
                    object_id=object_id,
                    attempt=attempt,
                    max_attempts=attempts,
                    error=str(exc),
                )
                await asyncio.sleep(0.3)
        if data is None:
            raise last_exc if last_exc else VideoNotFoundError(
                f"wechat_channels media missing for object_id={object_id}"
            )
        node = data.get("data") if isinstance(data, dict) else None
        media = node.get("media") if isinstance(node, dict) else None
        if not isinstance(media, dict):
            raise ProviderError(
                f"wechat_channels media missing for object_id={object_id}"
            )
        full_url = media.get("full_url")
        decode_key = media.get("decode_key")
        file_size = media.get("file_size")
        if not full_url or decode_key in (None, ""):
            raise ProviderError(
                f"wechat_channels media incomplete for object_id={object_id}"
            )
        try:
            size = int(file_size)
        except (TypeError, ValueError) as exc:
            raise ProviderError(
                f"wechat_channels file_size missing for object_id={object_id}"
            ) from exc
        if size < 0:
            raise ProviderError(
                f"wechat_channels file_size invalid for object_id={object_id}"
            )
        return {
            "full_url": str(full_url),
            "decode_key": decode_key,
            "file_size": size,
        }

    # 注：原 _validate_response / _save_failed_response（通用 GET 路径的响应校验与
    # 失败落盘）已随全平台迁移到引擎而删除——有效性现由各链的 classify + has_playable
    # 唯一判定（评审 Issue 3：消除两套有效性来源）。
