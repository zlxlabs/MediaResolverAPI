"""resolve quality 请求参数校验契约测试。"""

import pytest
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.resolve import ResolveRequest
from app.models.video_cache import VideoCache, ensure_video_cache_schema
from app.services.cache import CacheService
from app.services.platforms.base import VideoInfo


YOUTUBE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.mark.parametrize("quality", ["720p", "1080p", "2160p"])
def test_quality_accepts_resolution_cap(quality):
    request = ResolveRequest.model_validate({"url": YOUTUBE_URL, "quality": quality})

    assert request.quality == quality


@pytest.mark.parametrize("quality", ["720", "720P", "p720", "720p60", ""])
def test_quality_rejects_invalid_format(authed_client, quality):
    response = authed_client.post(
        "/api/resolve",
        json={"url": YOUTUBE_URL, "quality": quality},
    )

    assert response.status_code == 422


def test_quality_cannot_be_combined_with_audio(authed_client):
    response = authed_client.post(
        "/api/resolve",
        json={"url": YOUTUBE_URL, "download_mode": "audio", "quality": "720p"},
    )

    assert response.status_code == 422


def _video_info(video_url: str) -> VideoInfo:
    return VideoInfo(
        video_id="same-id",
        platform="youtube",
        title="title",
        description="中文描述",
        author_name="author",
        author_id="author-id",
        video_url=video_url,
        width=1920,
        height=1080,
        provider="tikhub",
    )


def test_cache_quality_entries_are_isolated(db):
    cache = CacheService(db)

    assert cache.cache_video(
        "youtube", "same-id", _video_info("https://cdn.example/720.mp4"), quality="720p"
    )
    assert cache.cache_video(
        "youtube", "same-id", _video_info("https://cdn.example/1080.mp4"), quality="1080p"
    )
    assert cache.cache_video(
        "youtube", "same-id", _video_info("https://cdn.example/default.mp4")
    )

    cached_720, _ = cache.get_cached_video("youtube", "same-id", quality="720p")
    cached_1080, _ = cache.get_cached_video("youtube", "same-id", quality="1080p")
    cached_default, _ = cache.get_cached_video("youtube", "same-id")

    assert cached_720.video_url.endswith("720.mp4")
    assert cached_1080.video_url.endswith("1080.mp4")
    assert cached_default.video_url.endswith("default.mp4")
    assert db.query(VideoCache).count() == 3


def test_card1_cache_table_migrates_quality_and_hits_default(db):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    old_data = _video_info("https://cdn.example/old.mp4").to_dict()
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE video_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform VARCHAR(20) NOT NULL,
                video_id VARCHAR(100) NOT NULL,
                download_mode VARCHAR(10) NOT NULL DEFAULT 'video',
                video_data JSON NOT NULL,
                translated_desc TEXT,
                provider VARCHAR(20) NOT NULL,
                cached_at DATETIME NOT NULL,
                expires_at DATETIME NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX ix_platform_video_id "
            "ON video_cache (platform, video_id, download_mode)"
        )
        connection.exec_driver_sql(
            """
            INSERT INTO video_cache
                (platform, video_id, download_mode, video_data, translated_desc,
                 provider, cached_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "youtube",
                "same-id",
                "video",
                json.dumps(old_data),
                None,
                "tikhub",
                "2026-09-14 00:00:00",
                "2099-09-14 00:00:00",
            ),
        )

    ensure_video_cache_schema(engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        cache = CacheService(session)
        info, _ = cache.get_cached_video("youtube", "same-id")

        assert info is not None
        assert info.video_url.endswith("old.mp4")
        with engine.connect() as connection:
            columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info('video_cache')"
                ).fetchall()
            }
            indexes = connection.exec_driver_sql(
                "PRAGMA index_list('video_cache')"
            ).fetchall()
            quality_index = next(
                row for row in indexes if row[1] == "ix_platform_video_id"
            )
            index_columns = [
                row[2]
                for row in connection.exec_driver_sql(
                    "PRAGMA index_info('ix_platform_video_id')"
                ).fetchall()
            ]
        assert "quality" in columns
        assert quality_index[2] == 1
        assert index_columns == ["platform", "video_id", "download_mode", "quality"]
    finally:
        session.close()
