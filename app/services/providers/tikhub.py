"""
TikHub 视频信息提供者

封装对 TikHub API 的调用，支持多个平台的视频信息获取
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from loguru import logger
import httpx

from .base import BaseProvider, ProviderError, VideoNotFoundError, DouyinTerminalError
from ...core.config import settings
from ...utils.http_client import HTTPClient
from ..platforms.douyin import DouyinService


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

    # 平台与 TikHub API 端点的映射
    PLATFORM_ENDPOINTS = {
        "douyin": "/api/v1/douyin/web/fetch_one_video",
        "tiktok": "/api/v1/tiktok/app/v3/fetch_one_video",
        "kuaishou": "/api/v1/kuaishou/web/fetch_one_video_v2",
        "youtube": "/api/v1/youtube/web/get_video_info",
        "xiaohongshu": "/api/v1/xiaohongshu/web/get_note_info_v3",
        "instagram": "/api/v1/instagram/web_app/fetch_post_media_by_url",
    }

    # 平台与视频ID参数名的映射
    PLATFORM_PARAMS = {
        "douyin": "aweme_id",
        "tiktok": "aweme_id",
        "kuaishou": "photo_id",
        "youtube": "video_id",
        "xiaohongshu": "share_text",  # 小红书使用完整URL
        "instagram": "url",  # Instagram使用完整URL
    }

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

    @staticmethod
    def _classify_douyin(response: Dict) -> str:
        """
        对抖音响应做三态分类（codex #9：扫整个 filter_list，不假设 index 0）。

        Returns:
            "terminal"  : 私密/部分可见等终态，应立即短路不再试后续端点
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
            if reasons & TikHubProvider.DOUYIN_TERMINAL_REASONS:
                return "terminal"
            return "retryable"

        # 有作品详情（web/app 的 aweme_detail，或 hybrid 的 data 根）
        if isinstance(payload.get("aweme_detail"), dict) and payload.get("aweme_detail"):
            return "ok"
        if "aweme_id" in payload:
            return "ok"
        return "retryable"

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
        return platform.lower() in self.PLATFORM_ENDPOINTS

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

        endpoint = self.PLATFORM_ENDPOINTS[platform]
        param_name = self.PLATFORM_PARAMS[platform]
        url = f"{self.api_base}{endpoint}"

        # 小红书和Instagram需要传递完整URL，其他平台传递video_id
        if platform in ["xiaohongshu", "instagram"]:
            param_value = original_url
        else:
            param_value = video_id

        self.log_info(
            f"Fetching video info from TikHub",
            platform=platform,
            video_id=video_id,
            endpoint=endpoint,
            param_name=param_name,
            param_value=param_value
        )

        try:
            async with HTTPClient() as client:
                response = await client.get(
                    url,
                    params={param_name: param_value},
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=60.0  # 60秒超时
                )

                if response.status_code == 404:
                    raise VideoNotFoundError(f"Video not found: {video_id}")
                elif response.status_code == 401:
                    raise ProviderError("TikHub API authentication failed")
                elif response.status_code == 429:
                    raise ProviderError("TikHub API rate limit exceeded")
                elif response.status_code != 200:
                    raise ProviderError(
                        f"TikHub API returned error: {response.status_code} - {response.text}"
                    )

                data = response.json()

                # 检查响应数据的有效性
                if not self._validate_response(data, platform):
                    self.log_error(
                        f"Invalid video data returned from TikHub",
                        platform=platform,
                        video_id=video_id,
                        response_keys=list(data.keys()) if isinstance(data, dict) else None,
                        data_keys=list(data.get("data", {}).keys()) if isinstance(data, dict) and "data" in data else None
                    )
                    # Save failed response to logs folder for debugging
                    self._save_failed_response(data, platform, video_id)
                    raise VideoNotFoundError(f"Invalid video data returned from TikHub")

                self.log_info(
                    f"Successfully fetched video info from TikHub",
                    platform=platform,
                    video_id=video_id
                )

                return data

        except (VideoNotFoundError, ProviderError):
            raise
        except Exception as e:
            self.log_error(
                f"Failed to fetch video info from TikHub: {e}",
                platform=platform,
                video_id=video_id
            )
            raise ProviderError(f"TikHub API request failed: {str(e)}")

    async def _fetch_douyin(
        self, video_id: str, original_url: str, use_hybrid: bool = False
    ) -> Dict:
        """
        抖音多级端点降级链：串行尝试，命中即返回；终态立即短路；全失败抛错。

        Args:
            video_id: aweme_id（hybrid 模式下可为空）
            original_url: 原始 url/分享文本（hybrid 模式使用）
            use_hybrid: True 则只走 hybrid 入口兜底（由路由层在 id 提取失败时触发）

        Returns:
            Dict: 命中端点的原始响应（交由 adapter 用 schema 自适应解析器解析）

        Raises:
            DouyinTerminalError: 私密/部分可见等终态，立即短路
            VideoNotFoundError: 全链失败
            ProviderError: 总预算超时或认证失败
        """
        chain = [self.DOUYIN_HYBRID] if use_hybrid else list(self.DOUYIN_CHAIN)
        attempts: List[Dict] = []
        target = video_id or original_url

        try:
            async with asyncio.timeout(self.DOUYIN_TOTAL_BUDGET):
                for name, path, param in chain:
                    try:
                        data = await self._call_douyin_endpoint(
                            name, path, param, video_id, original_url
                        )
                    except DouyinTerminalError:
                        raise
                    except ProviderError as e:
                        attempts.append({"endpoint": name, "decision": "http_error", "error": str(e)})
                        self.log_warning(f"Douyin endpoint {name} http error: {e}")
                        continue

                    decision = self._classify_douyin(data)
                    if decision == "terminal":
                        self.log_info(
                            "Douyin terminal response, short-circuit",
                            endpoint=name, target=target,
                        )
                        raise DouyinTerminalError(
                            f"Douyin video unavailable (private/partial): {target}"
                        )
                    if decision == "retryable":
                        attempts.append({"endpoint": name, "decision": "retryable"})
                        continue

                    # ok：解析校验，解析不出可播放直链也算可重试（codex #10）
                    if self._douyin_has_playable(data):
                        self.log_info(
                            "Douyin endpoint hit", endpoint=name, target=target
                        )
                        return data
                    attempts.append({"endpoint": name, "decision": "parse_failed"})
                    self.log_warning(f"Douyin endpoint {name} ok but no playable url")
        except asyncio.TimeoutError:
            self.log_error(
                f"Douyin chain timed out after {self.DOUYIN_TOTAL_BUDGET}s",
                target=target, attempts=attempts,
            )
            raise ProviderError(
                f"Douyin endpoint chain timed out after {self.DOUYIN_TOTAL_BUDGET}s "
                f"[attempts={attempts}]"
            )

        raise VideoNotFoundError(
            f"Douyin all endpoints failed for '{target}' [attempts={attempts}]"
        )

    async def _call_douyin_endpoint(
        self, name: str, path: str, param: str, video_id: str, original_url: str
    ) -> Dict:
        """
        调单个抖音端点。单端 max_retries=0（重试交给链本身，避免超时放大）。

        catch httpx.HTTPStatusError 并尽量取出错误体（codex #4）：终态/受限信息常藏在
        4xx body 的 filter_list 里，取出来交给分类器，而非吞成不透明错误。
        """
        url = f"{self.api_base}{path}"
        param_value = original_url if param == "url" else video_id
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            async with HTTPClient(
                timeout=self.DOUYIN_PER_ENDPOINT_TIMEOUT, max_retries=0
            ) as client:
                response = await client.get(
                    url, params={param: param_value}, headers=headers
                )
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

    def _douyin_has_playable(self, data: Dict) -> bool:
        """用 DouyinService 的 schema 自适应解析器校验是否能解析出无水印直链。"""
        info = DouyinService(self.api_key, self.api_base)._parse_response(data)
        return bool(info and info.video_url)

    def _validate_response(self, data: Dict, platform: str) -> bool:
        """
        验证 TikHub API 响应数据的有效性

        这里只做最基础的验证，确保响应包含 data 字段。
        更详细的数据结构验证由各平台的 _parse_response 方法负责。

        Args:
            data: API 响应数据
            platform: 平台名称

        Returns:
            bool: 数据是否有效
        """
        if not isinstance(data, dict):
            return False

        # 检查是否有 data 字段
        if "data" not in data:
            return False

        response_data = data.get("data", {})

        # 确保 data 字段不为空
        if not response_data:
            return False

        # 根据平台检查关键字段（宽松验证，兼容多种格式）
        if platform == "douyin" or platform == "tiktok":
            # TikTok 兼容两种格式：data.aweme_detail.* 或 data.*
            # 只要 data 存在且包含 aweme_detail 或 aweme_id 就认为有效
            return "aweme_detail" in response_data or "aweme_id" in response_data
        elif platform == "kuaishou":
            return "photo" in response_data
        elif platform == "youtube":
            # YouTube API 返回的数据包含 title, channel 等字段
            return "title" in response_data or "videoDetails" in response_data
        elif platform == "xiaohongshu":
            # 小红书新API返回的数据包含 user, video, desc 等字段
            return "user" in response_data or "desc" in response_data
        elif platform == "instagram":
            # Instagram新API返回的数据结构: data.data.medias
            # 也兼容旧格式: data.is_video
            inner_data = response_data.get("data", {})
            return "medias" in inner_data or "full_name" in inner_data or "is_video" in response_data

        return True

    def _save_failed_response(self, data: Dict, platform: str, video_id: str) -> None:
        """
        Save failed TikHub response to logs folder for debugging.

        Args:
            data: The response data from TikHub API
            platform: Platform name
            video_id: Video ID
        """
        try:
            # Get logs directory path
            logs_dir = Path(settings.LOG_FILE_PATH)
            if not logs_dir.is_absolute():
                # Find project root (contains pyproject.toml)
                current_file = Path(__file__)
                project_root = current_file
                while project_root.parent != project_root:
                    if (project_root / "pyproject.toml").exists():
                        break
                    project_root = project_root.parent
                if not (project_root / "pyproject.toml").exists():
                    project_root = Path.cwd()
                logs_dir = project_root / logs_dir

            # Create tikhub_failures subdirectory
            failures_dir = logs_dir / "tikhub_failures"
            failures_dir.mkdir(parents=True, exist_ok=True)

            # Generate filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_{platform}_{video_id}.json"
            filepath = failures_dir / filename

            # Save response data
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.info(f"Failed TikHub response saved to {filepath}")

        except Exception as e:
            logger.warning(f"Failed to save TikHub response to file: {e}")
