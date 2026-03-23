"""
Tests for dashboard API endpoints — stats, cache management, usage log.
"""

from datetime import datetime, timedelta

import pytest
from app.core.config import settings
from app.models.usage_log import UsageLog
from app.models.video_cache import VideoCache


def _seed_usage_logs(db, count=5, success=True, platform="tiktok", provider="tikhub"):
    """Helper to seed usage log entries."""
    for i in range(count):
        db.add(
            UsageLog(
                platform=platform,
                video_id=f"vid_{i}",
                provider=provider,
                cache_hit=(i % 3 == 0),
                success=success,
                duration_ms=100 + i * 50,
                created_at=datetime.utcnow() - timedelta(hours=i),
            )
        )
    db.commit()


def _seed_cache(db, count=3, platform="tiktok", expired=False):
    """Helper to seed cache entries."""
    for i in range(count):
        expires = datetime.utcnow() + timedelta(hours=-1 if expired else 12)
        db.add(
            VideoCache(
                platform=platform,
                video_id=f"cached_{i}",
                video_data={"title": f"test_{i}"},
                provider="tikhub",
                cached_at=datetime.utcnow(),
                expires_at=expires,
            )
        )
    db.commit()


# T4: Stats overview returns correct aggregation
class TestStatsOverview:

    def test_overview_with_data(self, authed_client, db):
        _seed_usage_logs(db, count=10, platform="tiktok")
        resp = authed_client.get("/api/dashboard/stats/overview?period=7d")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_requests"] == 10
        assert data["successful_requests"] == 10
        assert data["failed_requests"] == 0
        assert "tiktok" in data["platform_distribution"]
        assert data["cache_hit_rate"] > 0

    # T5: Empty data returns defaults
    def test_overview_empty(self, authed_client):
        resp = authed_client.get("/api/dashboard/stats/overview?period=7d")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_requests"] == 0
        assert data["cache_hit_rate"] == 0.0

    def test_overview_period_filter(self, authed_client, db):
        # Add old log (8 days ago)
        db.add(
            UsageLog(
                platform="youtube",
                video_id="old",
                success=True,
                created_at=datetime.utcnow() - timedelta(days=8),
            )
        )
        # Add recent log
        db.add(
            UsageLog(
                platform="youtube",
                video_id="new",
                success=True,
                created_at=datetime.utcnow(),
            )
        )
        db.commit()

        resp = authed_client.get("/api/dashboard/stats/overview?period=7d")
        data = resp.json()
        assert data["total_requests"] == 1  # Only recent one


# T6: Recent requests
class TestRecentRequests:

    def test_recent_returns_records(self, authed_client, db):
        _seed_usage_logs(db, count=5)
        resp = authed_client.get("/api/dashboard/stats/recent?limit=3")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        assert data[0]["platform"] == "tiktok"

    def test_recent_empty(self, authed_client):
        resp = authed_client.get("/api/dashboard/stats/recent")
        assert resp.status_code == 200
        assert resp.json() == []


# T7: Provider health
class TestProviderHealth:

    def test_provider_health_with_data(self, authed_client, db):
        _seed_usage_logs(db, count=5, provider="tikhub")
        resp = authed_client.get("/api/dashboard/stats/provider-health")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2  # tikhub + cobalt
        tikhub = next(p for p in data if p["provider"] == "tikhub")
        assert tikhub["status"] in ("healthy", "degraded", "down", "unknown")

    def test_provider_health_no_data(self, authed_client):
        resp = authed_client.get("/api/dashboard/stats/provider-health")
        assert resp.status_code == 200
        data = resp.json()
        for p in data:
            assert p["status"] == "unknown"


# T8: Cache management
class TestCacheManagement:

    def test_clear_expired_cache(self, authed_client, db):
        _seed_cache(db, count=3, expired=True)
        _seed_cache(db, count=2, platform="youtube", expired=False)
        resp = authed_client.post("/api/dashboard/cache/clear-expired")
        assert resp.status_code == 200
        assert resp.json()["cleared"] == 3

    def test_cache_stats(self, authed_client, db):
        _seed_cache(db, count=5)
        resp = authed_client.get("/api/dashboard/cache/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_cached"] == 5

    def test_delete_specific_cache(self, authed_client, db):
        _seed_cache(db, count=1, platform="douyin")
        resp = authed_client.delete("/api/dashboard/cache/douyin/cached_0")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_delete_nonexistent_cache(self, authed_client, db):
        resp = authed_client.delete("/api/dashboard/cache/tiktok/nonexistent")
        assert resp.status_code == 404
