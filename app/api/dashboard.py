"""
Dashboard API endpoints.

Provides statistics, cache management, and provider health data
for the web dashboard.
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from loguru import logger
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from .deps import verify_api_key
from ..core.config import settings
from ..core.database import get_db
from ..models.usage_log import UsageLog
from ..models.video_cache import VideoCache
from ..services.cache import CacheService

router = APIRouter(
    prefix="/dashboard",
    dependencies=[Depends(verify_api_key)],
    tags=["dashboard"],
)


# --- Response Models ---


class StatsOverview(BaseModel):
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    cache_hit_count: int = 0
    cache_hit_rate: float = 0.0
    avg_duration_ms: Optional[float] = None
    platform_distribution: dict = {}
    provider_distribution: dict = {}
    daily_counts: list = []


class RecentRequest(BaseModel):
    id: int
    platform: Optional[str] = None
    video_id: Optional[str] = None
    provider: Optional[str] = None
    cache_hit: bool = False
    success: bool = True
    error_msg: Optional[str] = None
    duration_ms: Optional[int] = None
    created_at: str


class ProviderHealth(BaseModel):
    provider: str
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    success_rate: float = 0.0
    avg_duration_ms: Optional[float] = None
    status: str = "unknown"  # healthy / degraded / down / unknown


class CacheStats(BaseModel):
    total_cached: int = 0
    expired_count: int = 0
    platform_distribution: dict = {}


# --- Endpoints ---


@router.get("/stats/overview", response_model=StatsOverview)
async def stats_overview(
    period: str = Query("7d", pattern="^(24h|7d|30d)$"),
    db: Session = Depends(get_db),
):
    """Get usage statistics overview for the given period."""
    try:
        cutoff = _period_to_cutoff(period)

        base_query = db.query(UsageLog).filter(UsageLog.created_at >= cutoff)

        total = base_query.count()
        if total == 0:
            return StatsOverview()

        successful = base_query.filter(UsageLog.success == True).count()
        cache_hits = base_query.filter(UsageLog.cache_hit == True).count()

        avg_dur = db.query(sa_func.avg(UsageLog.duration_ms)).filter(
            UsageLog.created_at >= cutoff,
            UsageLog.duration_ms.isnot(None),
        ).scalar()

        # Platform distribution
        platform_rows = (
            db.query(UsageLog.platform, sa_func.count())
            .filter(UsageLog.created_at >= cutoff, UsageLog.platform.isnot(None))
            .group_by(UsageLog.platform)
            .all()
        )
        platform_dist = {row[0]: row[1] for row in platform_rows}

        # Provider distribution (non-cache-hit only)
        provider_rows = (
            db.query(UsageLog.provider, sa_func.count())
            .filter(
                UsageLog.created_at >= cutoff,
                UsageLog.provider.isnot(None),
                UsageLog.cache_hit == False,
            )
            .group_by(UsageLog.provider)
            .all()
        )
        provider_dist = {row[0]: row[1] for row in provider_rows}

        # Daily counts
        daily_rows = (
            db.query(
                sa_func.date(UsageLog.created_at).label("day"),
                sa_func.count().label("count"),
            )
            .filter(UsageLog.created_at >= cutoff)
            .group_by("day")
            .order_by("day")
            .all()
        )
        daily_counts = [{"date": str(row[0]), "count": row[1]} for row in daily_rows]

        return StatsOverview(
            total_requests=total,
            successful_requests=successful,
            failed_requests=total - successful,
            cache_hit_count=cache_hits,
            cache_hit_rate=round(cache_hits / total * 100, 1) if total else 0,
            avg_duration_ms=round(avg_dur, 1) if avg_dur else None,
            platform_distribution=platform_dist,
            provider_distribution=provider_dist,
            daily_counts=daily_counts,
        )
    except Exception as e:
        logger.error(f"Failed to get stats overview: {e}")
        raise HTTPException(status_code=500, detail="Failed to load statistics")


@router.get("/stats/recent", response_model=list[RecentRequest])
async def stats_recent(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Get recent resolve requests."""
    try:
        rows = (
            db.query(UsageLog)
            .order_by(UsageLog.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            RecentRequest(
                id=r.id,
                platform=r.platform,
                video_id=r.video_id,
                provider=r.provider,
                cache_hit=r.cache_hit or False,
                success=r.success if r.success is not None else True,
                error_msg=r.error_msg,
                duration_ms=r.duration_ms,
                created_at=r.created_at.isoformat() if r.created_at else "",
            )
            for r in rows
        ]
    except Exception as e:
        logger.error(f"Failed to get recent requests: {e}")
        raise HTTPException(status_code=500, detail="Failed to load recent requests")


@router.get("/stats/provider-health", response_model=list[ProviderHealth])
async def provider_health(db: Session = Depends(get_db)):
    """Get provider health status based on recent usage data."""
    try:
        # Look at last 1 hour of non-cache-hit requests
        cutoff = datetime.utcnow() - timedelta(hours=1)

        providers = ["tikhub", "cobalt"]
        results = []

        for provider_name in providers:
            base = db.query(UsageLog).filter(
                UsageLog.created_at >= cutoff,
                UsageLog.provider == provider_name,
                UsageLog.cache_hit == False,
            )
            total = base.count()
            success = base.filter(UsageLog.success == True).count()
            avg_dur = (
                db.query(sa_func.avg(UsageLog.duration_ms))
                .filter(
                    UsageLog.created_at >= cutoff,
                    UsageLog.provider == provider_name,
                    UsageLog.cache_hit == False,
                    UsageLog.duration_ms.isnot(None),
                )
                .scalar()
            )

            if total == 0:
                status = "unknown"
            elif success / total >= 0.9:
                status = "healthy"
            elif success / total >= 0.5:
                status = "degraded"
            else:
                status = "down"

            results.append(
                ProviderHealth(
                    provider=provider_name,
                    total_calls=total,
                    successful_calls=success,
                    failed_calls=total - success,
                    success_rate=round(success / total * 100, 1) if total else 0,
                    avg_duration_ms=round(avg_dur, 1) if avg_dur else None,
                    status=status,
                )
            )

        return results
    except Exception as e:
        logger.error(f"Failed to get provider health: {e}")
        raise HTTPException(status_code=500, detail="Failed to load provider health")


@router.get("/cache/stats", response_model=CacheStats)
async def cache_stats(db: Session = Depends(get_db)):
    """Get cache statistics."""
    try:
        now = datetime.utcnow()
        total = db.query(VideoCache).count()
        expired = db.query(VideoCache).filter(VideoCache.expires_at < now).count()

        platform_rows = (
            db.query(VideoCache.platform, sa_func.count())
            .group_by(VideoCache.platform)
            .all()
        )
        platform_dist = {row[0]: row[1] for row in platform_rows}

        return CacheStats(
            total_cached=total,
            expired_count=expired,
            platform_distribution=platform_dist,
        )
    except Exception as e:
        logger.error(f"Failed to get cache stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to load cache stats")


@router.post("/cache/clear-expired")
async def clear_expired_cache(db: Session = Depends(get_db)):
    """Clear all expired cache entries."""
    try:
        cache_service = CacheService(db)
        count = cache_service.clear_expired_cache()
        return {"cleared": count}
    except Exception as e:
        logger.error(f"Failed to clear expired cache: {e}")
        raise HTTPException(status_code=500, detail="Failed to clear cache")


@router.delete("/cache/{platform}/{video_id}")
async def delete_cache_entry(
    platform: str, video_id: str, db: Session = Depends(get_db)
):
    """Delete a specific cache entry."""
    try:
        cache_service = CacheService(db)
        deleted = cache_service.clear_cache(platform, video_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Cache entry not found")
        return {"deleted": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete cache: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete cache entry")


# --- Helpers ---


def _period_to_cutoff(period: str) -> datetime:
    """Convert period string to datetime cutoff."""
    now = datetime.utcnow()
    if period == "24h":
        return now - timedelta(hours=24)
    elif period == "7d":
        return now - timedelta(days=7)
    else:  # 30d
        return now - timedelta(days=30)
