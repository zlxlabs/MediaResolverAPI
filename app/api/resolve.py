"""
Media resolve API endpoint.

Core API for resolving social media URLs into direct download links.
"""

import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from loguru import logger
from sqlalchemy.orm import Session

from ..core.config import settings
from .deps import verify_api_key
from ..core.database import get_db
from ..services.url_parser import url_parser
from ..services.video_resolver import VideoResolver, VideoResolverError
from ..services.cache import CacheService
from ..services.translation.openai import TranslationService

router = APIRouter(dependencies=[Depends(verify_api_key)])

# 平台已识别但 video_id 提取失败时，可凭原始 url 兜底的平台集合：
# 其降级链含吃 url 的端点（kuaishou web_share=share_text / instagram v2 code_or_url + v1 post_url），
# 故无需 video_id 也能解析；不在此集合的平台（tiktok/youtube/xiaohongshu）仍按 400 处理。
# 抖音另由 use_hybrid 兜底，不在此列。（评审 Issue 5）
URL_FALLBACK_PLATFORMS = frozenset({"kuaishou", "instagram"})

# Shared service instances
_video_resolver: Optional[VideoResolver] = None
_translation_service: Optional[TranslationService] = None


def get_video_resolver() -> VideoResolver:
    """Get or create VideoResolver singleton."""
    global _video_resolver
    if _video_resolver is None:
        _video_resolver = VideoResolver()
    return _video_resolver


def get_translation_service() -> TranslationService:
    """Get or create TranslationService singleton."""
    global _translation_service
    if _translation_service is None:
        _translation_service = TranslationService()
    return _translation_service


class ResolveRequest(BaseModel):
    """Request body for /api/resolve endpoint."""

    url: str = Field(..., description="Social media video URL")
    translate: bool = Field(
        default=True, description="Whether to translate description to Chinese"
    )
    force_refresh: bool = Field(
        default=False, description="Skip cache and fetch fresh data"
    )


class VideoInfoResponse(BaseModel):
    """Video info in the response."""

    platform: str
    video_id: str
    title: str
    description: str
    translated_description: Optional[str] = None
    author_name: str
    author_id: str
    video_url: str
    width: int
    height: int
    duration: Optional[int] = None
    quality: Optional[str] = None
    view_count: Optional[int] = None
    like_count: Optional[int] = None
    comment_count: Optional[int] = None
    share_count: Optional[int] = None
    collect_count: Optional[int] = None
    create_time: Optional[str] = None
    publish_time: Optional[str] = None
    provider: Optional[str] = None


class ResolveResponse(BaseModel):
    """Response body for /api/resolve endpoint."""

    success: bool
    data: Optional[VideoInfoResponse] = None
    error: Optional[str] = None


@router.post("/resolve", response_model=ResolveResponse)
async def resolve_url(
    request: ResolveRequest,
    db: Session = Depends(get_db),
):
    """
    Resolve a social media URL into a direct download link with metadata.

    Supports: Douyin, TikTok, Kuaishou, YouTube, Xiaohongshu, Instagram, Pinterest.
    """
    original_url = request.url.strip()
    logger.info(f"Resolve request: {original_url}")

    # Usage tracking context
    start_time = time.monotonic()
    log_data = {
        "platform": None,
        "video_id": None,
        "provider": None,
        "cache_hit": False,
        "success": False,
        "error_msg": None,
    }

    try:
        platform = None
        video_id = None
        use_hybrid = False  # 抖音入口兜底：拿不到 aweme_id 时改走 hybrid/video_data

        # Step 1: Resolve short URL if needed
        if url_parser.is_short_url(original_url):
            logger.info(f"Resolving short URL: {original_url}")
            resolved = await url_parser.resolve_short_url(original_url)
            if resolved:
                original_url = resolved
            elif url_parser.identify_platform(original_url) == "douyin":
                # 抖音短链展开失败：不 400，改走 hybrid（其内部自带短链展开）
                logger.info("Short URL expand failed, falling back to douyin hybrid")
                platform, use_hybrid = "douyin", True
            else:
                log_data["error_msg"] = "Failed to resolve short URL"
                raise HTTPException(
                    status_code=400,
                    detail="Failed to resolve short URL",
                )

        # Step 2: Parse URL to identify platform and video ID
        if not use_hybrid:
            platform, video_id = url_parser.parse_url(original_url)
            if platform == "douyin" and not video_id:
                # 抖音但 ID 提取失败（如新链接格式）：改走 hybrid 兜底
                logger.info("Douyin id extraction failed, falling back to hybrid")
                use_hybrid = True
            elif platform in URL_FALLBACK_PLATFORMS and not video_id:
                # 平台已识别但 ID 提取失败（如新链接格式）：不 400，放行让该平台降级链的
                # by_url 端点用原始 url 兜底（kuaishou web_share / instagram v1+v2）。
                # video_id 保持空，chain 的 build_params 改喂 original_url（评审 Issue 5）。
                logger.info(f"{platform} id extraction failed, falling back to by_url chain")
            elif not platform or not video_id:
                log_data["platform"] = platform
                log_data["error_msg"] = "Unsupported URL"
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported URL or could not extract video ID: {original_url}",
                )

        log_data["platform"] = platform
        log_data["video_id"] = video_id

        cache_service = CacheService(db)
        translated_desc = None

        # Step 3: Check cache（仅在已知 video_id 时；hybrid / by_url 兜底要解析后才拿到 id）
        if not use_hybrid and video_id and not request.force_refresh:
            cached_info, cached_translation = cache_service.get_cached_video(
                platform, video_id
            )
            if cached_info:
                logger.info(f"Cache hit: {platform}:{video_id}")
                log_data["cache_hit"] = True
                log_data["success"] = True
                log_data["provider"] = cached_info.provider
                return ResolveResponse(
                    success=True,
                    data=_build_response(
                        cached_info, cached_translation
                    ),
                )

        # Step 4: Resolve video info
        resolver = get_video_resolver()
        video_info, provider_name = await resolver.resolve(
            platform=platform,
            video_id=video_id or "",
            original_url=original_url,
            use_hybrid=use_hybrid,
        )
        log_data["provider"] = provider_name

        # hybrid / by_url 兜底路径：用解析出的真实 id 归一化回填（缓存语义 codex #8 / 评审 Issue 5）
        if use_hybrid or not video_id:
            video_id = video_info.video_id
            log_data["video_id"] = video_id

        # Step 5: Translate description (if requested and not Chinese)
        if request.translate and settings.TRANSLATION_ENABLED:
            translation = get_translation_service()
            if video_info.description and not translation.is_chinese(
                video_info.description
            ):
                translated_desc = await translation.translate_to_chinese(
                    video_info.description
                )

        # Step 6: Cache result
        cache_service.cache_video(
            platform, video_id, video_info, translated_desc
        )

        # Step 7: Return response
        log_data["success"] = True
        return ResolveResponse(
            success=True,
            data=_build_response(video_info, translated_desc),
        )

    except HTTPException:
        raise
    except VideoResolverError as e:
        logger.error(f"Video resolution failed: {e}")
        log_data["error_msg"] = str(e)[:500]
        return ResolveResponse(success=False, error=str(e))
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        log_data["error_msg"] = str(e)[:500]
        return ResolveResponse(success=False, error=f"Internal error: {str(e)}")
    finally:
        # Write usage log (never blocks response)
        duration_ms = int((time.monotonic() - start_time) * 1000)
        try:
            from ..models.usage_log import UsageLog
            usage_entry = UsageLog(
                platform=log_data["platform"],
                video_id=log_data["video_id"],
                provider=log_data["provider"],
                cache_hit=log_data["cache_hit"],
                success=log_data["success"],
                error_msg=log_data["error_msg"],
                duration_ms=duration_ms,
            )
            db.add(usage_entry)
            db.commit()
        except Exception as log_err:
            logger.warning(f"Failed to write usage log: {log_err}")


def _build_response(video_info, translated_desc=None) -> VideoInfoResponse:
    """Build VideoInfoResponse from VideoInfo object."""
    return VideoInfoResponse(
        platform=video_info.platform,
        video_id=video_info.video_id,
        title=video_info.title,
        description=video_info.description,
        translated_description=translated_desc,
        author_name=video_info.author_name,
        author_id=video_info.author_id,
        video_url=video_info.video_url,
        width=video_info.width,
        height=video_info.height,
        duration=video_info.duration,
        quality=video_info.quality,
        view_count=video_info.view_count,
        like_count=video_info.like_count,
        comment_count=video_info.comment_count,
        share_count=video_info.share_count,
        collect_count=video_info.collect_count,
        create_time=video_info.create_time.isoformat()
        if video_info.create_time
        else None,
        publish_time=video_info.publish_time,
        provider=video_info.provider,
    )


@router.get("/platforms")
async def list_platforms():
    """List all supported platforms and their providers."""
    resolver = get_video_resolver()
    platforms = {}
    for platform in resolver.get_supported_platforms():
        platforms[platform] = resolver.get_providers_for_platform(platform)
    return {"platforms": platforms}
