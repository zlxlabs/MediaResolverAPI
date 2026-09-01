"""
视频解析协调器

管理多个数据源提供者的降级策略，实现责任链模式
"""

from typing import List, Dict, Optional, Tuple
from loguru import logger

from .providers import (
    BaseProvider,
    TikHubProvider,
    CobaltProvider,
    ProviderError,
    VideoNotFoundError,
    TerminalError,
)
from .adapters import TikHubAdapter, CobaltAdapter
from .platforms.base import VideoInfo
from ..core.config import settings


class VideoResolverError(Exception):
    """视频解析错误"""
    pass


class VideoResolver:
    """
    视频解析协调器

    负责：
    1. 根据平台选择合适的提供者
    2. 实现提供者降级策略（责任链模式）
    3. 将原始数据适配为统一的 VideoInfo 格式
    4. 记录详细的解析日志
    """

    def __init__(self):
        """初始化视频解析协调器"""
        # 初始化提供者
        self.tikhub_provider = TikHubProvider()
        self.cobalt_provider = CobaltProvider()

        # 初始化适配器
        self.tikhub_adapter = TikHubAdapter(
            api_key=settings.TIKHUB_API_KEY,
            api_base=settings.TIKHUB_API_BASE
        )
        self.cobalt_adapter = CobaltAdapter()

        # 定义每个平台的提供者优先级
        # 从配置文件读取，如果配置文件中没有，则使用默认配置
        self.provider_chains = self._build_provider_chains()

        logger.info(f"VideoResolver initialized with provider chains: {self.provider_chains}")

    def _build_provider_chains(self) -> Dict[str, List[BaseProvider]]:
        """
        构建每个平台的提供者优先级链

        优先读取环境变量中的自定义配置，如果没有配置则使用默认值。
        环境变量格式: PROVIDER_PRIORITY_<PLATFORM>="provider1,provider2"
        例如: PROVIDER_PRIORITY_TIKTOK="cobalt" 表示 TikTok 只使用 Cobalt

        Returns:
            Dict[str, List[BaseProvider]]: 平台到提供者列表的映射
        """
        # 提供者名称到实例的映射
        provider_map = {
            "tikhub": self.tikhub_provider,
            "cobalt": self.cobalt_provider,
        }

        # 默认配置
        default_chains = {
            # Pinterest 和 Facebook 只支持 Cobalt
            "pinterest": [self.cobalt_provider],
            "facebook": [self.cobalt_provider],

            # 其他平台优先使用 TikHub，失败后降级到 Cobalt
            "tiktok": [self.tikhub_provider, self.cobalt_provider],
            "instagram": [self.tikhub_provider, self.cobalt_provider],
            # 小红书：Cobalt 实测不支持(link.invalid)，移除无效兜底，走 tikhub 单 provider
            # (tikhub 内部已有 app_v2 → web_v3 多级降级)
            "xiaohongshu": [self.tikhub_provider],
            "youtube": [self.tikhub_provider, self.cobalt_provider],

            # 抖音和快手可能不被 Cobalt 支持，只使用 TikHub
            "douyin": [self.tikhub_provider],
            "kuaishou": [self.tikhub_provider],
            "wechat_channels": [self.tikhub_provider],
        }

        # 从配置文件读取自定义优先级并覆盖默认配置
        platform_config_map = {
            "tiktok": settings.PROVIDER_PRIORITY_TIKTOK,
            "instagram": settings.PROVIDER_PRIORITY_INSTAGRAM,
            "xiaohongshu": settings.PROVIDER_PRIORITY_XIAOHONGSHU,
            "youtube": settings.PROVIDER_PRIORITY_YOUTUBE,
            "pinterest": settings.PROVIDER_PRIORITY_PINTEREST,
            "facebook": settings.PROVIDER_PRIORITY_FACEBOOK,
            "douyin": settings.PROVIDER_PRIORITY_DOUYIN,
            "kuaishou": settings.PROVIDER_PRIORITY_KUAISHOU,
            "wechat_channels": settings.PROVIDER_PRIORITY_WECHAT_CHANNELS,
        }

        for platform, priority_config in platform_config_map.items():
            if priority_config:
                # 解析配置字符串，例如 "cobalt" 或 "tikhub,cobalt"
                provider_names = [p.strip().lower() for p in priority_config.split(",") if p.strip()]
                providers = []

                for name in provider_names:
                    if name in provider_map:
                        providers.append(provider_map[name])
                    else:
                        logger.warning(f"Unknown provider '{name}' in PROVIDER_PRIORITY_{platform.upper()}, skipping")

                if providers:
                    logger.info(f"Custom provider priority for {platform}: {[p.provider_name for p in providers]}")
                    default_chains[platform] = providers
                else:
                    logger.warning(f"No valid providers in PROVIDER_PRIORITY_{platform.upper()}, using default")

        return default_chains

    async def resolve(
        self,
        platform: str,
        video_id: str,
        original_url: str,
        force_refresh: bool = False,
        use_hybrid: bool = False
    ) -> Tuple[VideoInfo, str]:
        """
        解析视频信息

        按照提供者优先级依次尝试，直到成功或全部失败

        Args:
            platform: 平台名称
            video_id: 视频ID
            original_url: 原始视频URL
            force_refresh: 是否强制刷新（跳过缓存）

        Returns:
            Tuple[VideoInfo, str]: (视频信息对象, 使用的提供者名称)

        Raises:
            VideoResolverError: 所有提供者都失败
        """
        platform = platform.lower()

        # 获取该平台的提供者链
        providers = self.provider_chains.get(platform, [])

        if not providers:
            raise VideoResolverError(f"No provider available for platform: {platform}")

        logger.info(
            f"Starting video resolution",
            platform=platform,
            video_id=video_id,
            providers=[p.provider_name for p in providers]
        )

        # 记录每个提供者的尝试结果
        resolution_log = {
            "platform": platform,
            "video_id": video_id,
            "original_url": original_url,
            "attempts": []
        }

        # 按优先级依次尝试每个提供者
        for provider in providers:
            provider_name = provider.provider_name

            try:
                logger.info(
                    f"Attempting to resolve with {provider_name}",
                    platform=platform,
                    video_id=video_id
                )

                # 调用提供者获取原始数据
                raw_data = await provider.fetch_video_info(
                    platform=platform,
                    video_id=video_id,
                    original_url=original_url,
                    use_hybrid=use_hybrid
                )

                # 使用对应的适配器转换数据
                video_info = self._adapt_data(
                    raw_data=raw_data,
                    provider_name=provider_name,
                    platform=platform,
                    video_id=video_id,
                    original_url=original_url
                )

                if not video_info:
                    raise ProviderError(f"Failed to adapt {provider_name} response")

                # 成功！记录日志并返回
                logger.info(
                    f"Successfully resolved video with {provider_name}",
                    platform=platform,
                    video_id=video_id,
                    title=video_info.title
                )

                resolution_log["attempts"].append({
                    "provider": provider_name,
                    "success": True,
                    "error": None
                })

                # 记录完整的解析日志（用于分析和优化）
                logger.debug(f"Resolution log: {resolution_log}")

                return video_info, provider_name

            except TerminalError as e:
                # 终态失败（抖音私密/部分可见、小红书图文无视频等）：立即停止责任链，不再 fallback
                # 即便 env 覆盖了链加入了其它 provider 也不试（codex #5）
                logger.info(
                    f"Terminal failure, stopping provider chain: {e} "
                    f"[platform={platform}, video_id={video_id}]"
                )
                resolution_log["attempts"].append({
                    "provider": provider_name,
                    "success": False,
                    "error": f"terminal: {str(e)}",
                })
                raise VideoResolverError(
                    f"Video unavailable (terminal) for platform '{platform}', "
                    f"video_id '{video_id}': {str(e)}"
                )

            except (VideoNotFoundError, ProviderError) as e:
                # Record failure, try next provider
                # Escape braces in error message to prevent loguru format issues
                error_str = str(e).replace("{", "{{").replace("}", "}}")
                logger.warning(
                    f"{provider_name} failed to resolve video: {error_str} "
                    f"[platform={platform}, video_id={video_id}]"
                )

                resolution_log["attempts"].append({
                    "provider": provider_name,
                    "success": False,
                    "error": str(e)
                })

                continue  # 尝试下一个提供者

            except Exception as e:
                # Unexpected error, log but continue to try next provider
                # Escape braces in error message to prevent loguru format issues
                error_str = str(e).replace("{", "{{").replace("}", "}}")
                logger.error(
                    f"Unexpected error with {provider_name}: {error_str} "
                    f"[platform={platform}, video_id={video_id}]",
                    exc_info=True
                )

                resolution_log["attempts"].append({
                    "provider": provider_name,
                    "success": False,
                    "error": f"Unexpected error: {str(e)}"
                })

                continue

        # All providers failed
        # Build detailed error message for debugging
        error_details = "; ".join([
            f"{a['provider']}: {a['error']}"
            for a in resolution_log["attempts"]
            if not a["success"]
        ])

        logger.error(
            f"All providers failed to resolve video",
            platform=platform,
            video_id=video_id,
            resolution_log=resolution_log
        )

        raise VideoResolverError(
            f"All providers failed for platform '{platform}', video_id '{video_id}'. "
            f"Errors: [{error_details}]"
        )

    def _adapt_data(
        self,
        raw_data: Dict,
        provider_name: str,
        platform: str,
        video_id: str,
        original_url: str
    ) -> Optional[VideoInfo]:
        """
        使用对应的适配器转换原始数据

        Args:
            raw_data: 提供者返回的原始数据
            provider_name: 提供者名称
            platform: 平台名称
            video_id: 视频ID
            original_url: 原始URL

        Returns:
            Optional[VideoInfo]: 转换后的视频信息对象
        """
        try:
            if provider_name == "tikhub":
                return self.tikhub_adapter.adapt(raw_data, platform, video_id)
            elif provider_name == "cobalt":
                return self.cobalt_adapter.adapt(raw_data, platform, video_id, original_url)
            else:
                logger.error(f"Unknown provider: {provider_name}")
                return None

        except Exception as e:
            logger.error(
                f"Failed to adapt data from {provider_name}: {e}",
                platform=platform,
                video_id=video_id
            )
            return None

    def get_supported_platforms(self) -> List[str]:
        """
        获取所有支持的平台列表

        Returns:
            List[str]: 支持的平台名称列表
        """
        return list(self.provider_chains.keys())

    def get_providers_for_platform(self, platform: str) -> List[str]:
        """
        获取指定平台支持的提供者列表

        Args:
            platform: 平台名称

        Returns:
            List[str]: 提供者名称列表
        """
        providers = self.provider_chains.get(platform.lower(), [])
        return [p.provider_name for p in providers]
