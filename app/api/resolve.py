"""
Media resolve API endpoint.

Core API for resolving social media URLs into direct download links.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from loguru import logger
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.database import get_db
from ..services.url_parser import url_parser
from ..services.video_resolver import VideoResolver, VideoResolverError
from ..services.cache import CacheService
from ..services.translation.openai import TranslationService

router = APIRouter()

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

    try:
        # Step 1: Resolve short URL if needed
        if url_parser.is_short_url(original_url):
            logger.info(f"Resolving short URL: {original_url}")
            resolved = await url_parser.resolve_short_url(original_url)
            if resolved:
                original_url = resolved
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Failed to resolve short URL",
                )

        # Step 2: Parse URL to identify platform and video ID
        platform, video_id = url_parser.parse_url(original_url)
        if not platform or not video_id:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported URL or could not extract video ID: {original_url}",
            )

        # Step 3: Check cache
        cache_service = CacheService(db)
        translated_desc = None

        if not request.force_refresh:
            cached_info, cached_translation = cache_service.get_cached_video(
                platform, video_id
            )
            if cached_info:
                logger.info(f"Cache hit: {platform}:{video_id}")
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
            video_id=video_id,
            original_url=original_url,
        )

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
        return ResolveResponse(
            success=True,
            data=_build_response(video_info, translated_desc),
        )

    except HTTPException:
        raise
    except VideoResolverError as e:
        logger.error(f"Video resolution failed: {e}")
        return ResolveResponse(success=False, error=str(e))
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return ResolveResponse(success=False, error=f"Internal error: {str(e)}")


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
