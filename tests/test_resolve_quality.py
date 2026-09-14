"""resolve quality 请求参数校验契约测试。"""

import json
import httpx
import pytest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.resolve as resolve_mod
from app.models.video_cache import VideoCache, ensure_video_cache_schema
from app.services.adapters.tikhub_adapter import TikHubAdapter
from app.services.cache import CacheService
from app.services.platforms.base import VideoInfo
from app.services.platforms.douyin import DouyinService
from app.services.platforms.kuaishou import KuaishouService
from app.services.platforms.tiktok import TikTokService
from app.services.platforms.twitter import TwitterService
from app.services.platforms.xiaohongshu import XiaohongshuService
from app.services.platforms.youtube import YouTubeService
from app.services.providers.cobalt import CobaltProvider
from app.services.video_resolver import VideoResolver


YOUTUBE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
QUALITY_FIXTURES = Path(__file__).parent / "fixtures" / "quality"


def _load_quality(name: str) -> dict:
    return json.loads((QUALITY_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _load_platform(platform: str, name: str) -> dict:
    path = Path(__file__).parent / "fixtures" / platform / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


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


@pytest.mark.parametrize(
    ("quality", "expected"),
    [
        ("1080p", "youtube-1080.mp4"),
        ("720p", "youtube-720-high.mp4"),
        ("240p", "youtube-360.mp4"),
        ("2160p", "youtube-2160.mp4"),
    ],
)
def test_youtube_quality_cap_selects_expected_stream(quality, expected):
    info = YouTubeService("key", "base")._parse_response(
        _load_quality("youtube_multi"), quality=quality
    )

    assert info is not None
    assert info.video_url.endswith(expected)


def test_youtube_without_quality_keeps_1080p_default():
    info = YouTubeService("key", "base")._parse_response(
        _load_quality("youtube_multi")
    )

    assert info is not None
    assert info.video_url.endswith("youtube-1080.mp4")


@pytest.mark.parametrize(
    ("quality", "expected"),
    [
        ("1080p", "twitter-1080.mp4"),
        ("720p", "twitter-720-high.mp4"),
        ("240p", "twitter-360.mp4"),
    ],
)
def test_twitter_quality_cap_selects_expected_stream(quality, expected):
    info = TwitterService("key", "base")._parse_response(
        _load_quality("twitter_multi"), quality=quality
    )

    assert info is not None
    assert info.video_url.endswith(expected)


def test_twitter_quality_2160p_overrides_default_1080p_cap():
    fixture = _load_quality("twitter_multi")
    default_info = TwitterService("key", "base")._parse_response(fixture)
    capped_info = TwitterService("key", "base")._parse_response(
        fixture, quality="2160p"
    )

    assert default_info is not None
    assert capped_info is not None
    assert default_info.video_url.endswith("twitter-1080.mp4")
    assert capped_info.video_url.endswith("twitter-2160.mp4")


def _twitter_fixture_with_unknown_variant(known_above_cap_only=False):
    fixture = _load_quality("twitter_multi")
    variants = fixture["data"]["media"]["video"][0]["variants"]
    if known_above_cap_only:
        del variants[:4]
    variants.append(
        {
            "content_type": "video/mp4",
            "bitrate": 6000000,
            "url": "https://video.twimg.com/fake/twitter-unknown.mp4",
        }
    )
    return fixture


def test_twitter_quality_prefers_unknown_over_known_above_cap():
    info = TwitterService("key", "base")._parse_response(
        _twitter_fixture_with_unknown_variant(known_above_cap_only=True),
        quality="1080p",
    )

    assert info is not None
    assert info.video_url.endswith("twitter-unknown.mp4")


def test_twitter_quality_prefers_known_at_cap_over_unknown():
    info = TwitterService("key", "base")._parse_response(
        _twitter_fixture_with_unknown_variant(), quality="720p"
    )

    assert info is not None
    assert info.video_url.endswith("twitter-720-high.mp4")


@pytest.mark.asyncio
async def test_cobalt_quality_is_mapped_to_upstream_video_quality(monkeypatch):
    seen = []
    payload = {"status": "redirect", "url": "https://cdn.example/video.mp4"}

    class FakeResponse:
        status_code = 200
        text = json.dumps(payload)
        headers = {"content-type": "application/json"}

        def json(self):
            return payload

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, _url, **kwargs):
            seen.append(kwargs["json"])
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    provider = CobaltProvider()
    provider.api_base = "https://cobalt.example"

    await provider.fetch_video_info(
        "twitter", "id", "https://x.com/u/status/id", quality="720p"
    )

    assert seen == [
        {
            "url": "https://x.com/u/status/id",
            "videoQuality": "720",
        }
    ]


def test_api_quality_e2e_uses_quality_specific_cache_entry(
    authed_client, db, monkeypatch
):
    monkeypatch.setattr(resolve_mod.url_parser, "is_short_url", lambda _url: False)
    monkeypatch.setattr(
        resolve_mod.url_parser,
        "parse_url",
        lambda _url: ("youtube", "quality-youtube"),
    )
    resolver = VideoResolver()
    calls = []

    async def tikhub_fetch(*args, **kwargs):
        calls.append(kwargs.get("quality"))
        return _load_quality("youtube_multi")

    monkeypatch.setattr(resolver.tikhub_provider, "fetch_video_info", tikhub_fetch)
    monkeypatch.setattr(resolve_mod, "get_video_resolver", lambda: resolver)

    response_720 = authed_client.post(
        "/api/resolve",
        json={"url": YOUTUBE_URL, "translate": False, "quality": "720p"},
    )
    response_1080 = authed_client.post(
        "/api/resolve",
        json={"url": YOUTUBE_URL, "translate": False, "quality": "1080p"},
    )
    cached_720 = authed_client.post(
        "/api/resolve",
        json={"url": YOUTUBE_URL, "translate": False, "quality": "720p"},
    )

    assert response_720.status_code == 200
    assert response_720.json()["data"]["video_url"].endswith("youtube-720-high.mp4")
    assert response_1080.status_code == 200
    assert response_1080.json()["data"]["video_url"].endswith("youtube-1080.mp4")
    assert response_720.json()["data"]["variants"] is None
    assert cached_720.json()["data"]["video_url"].endswith("youtube-720-high.mp4")
    assert calls == ["720p", "1080p"]
    assert {
        record.quality
        for record in db.query(VideoCache).filter(VideoCache.video_id == "quality-youtube")
    } == {"720p", "1080p"}


def test_api_twitter_variants_survive_cache_round_trip(
    authed_client, monkeypatch
):
    monkeypatch.setattr(resolve_mod.url_parser, "is_short_url", lambda _url: False)
    monkeypatch.setattr(
        resolve_mod.url_parser,
        "parse_url",
        lambda _url: ("twitter", "twitter-variants"),
    )
    resolver = VideoResolver()
    calls = []

    async def tikhub_fetch(*args, **kwargs):
        calls.append(kwargs)
        return _load_quality("twitter_multi")

    monkeypatch.setattr(resolver.tikhub_provider, "fetch_video_info", tikhub_fetch)
    monkeypatch.setattr(resolve_mod, "get_video_resolver", lambda: resolver)

    first = authed_client.post(
        "/api/resolve",
        json={
            "url": "https://x.com/0xCodez/status/twitter-variants",
            "translate": False,
            "force_refresh": True,
        },
    )
    cached = authed_client.post(
        "/api/resolve",
        json={
            "url": "https://x.com/0xCodez/status/twitter-variants",
            "translate": False,
        },
    )

    assert first.status_code == 200
    assert cached.status_code == 200
    first_variants = first.json()["data"]["variants"]
    expected_keys = ("url", "bitrate", "width", "height", "quality")
    expected_values = [
        ("https://video.twimg.com/fake/640x360/twitter-360.mp4", 500000, 640, 360, "360p"),
        ("https://video.twimg.com/fake/1280x720/twitter-720-low.mp4", 1000000, 1280, 720, "720p"),
        ("https://video.twimg.com/fake/1280x720/twitter-720-high.mp4", 2000000, 1280, 720, "720p"),
        ("https://video.twimg.com/fake/1920x1080/twitter-1080.mp4", 3000000, 1920, 1080, "1080p"),
        ("https://video.twimg.com/fake/2560x1440/twitter-1440.mp4", 4000000, 2560, 1440, "1440p"),
        ("https://video.twimg.com/fake/3840x2160/twitter-2160.mp4", 5000000, 3840, 2160, "2160p"),
    ]
    assert first_variants == [dict(zip(expected_keys, values)) for values in expected_values]
    assert cached.json()["data"]["variants"] == first_variants
    assert len(calls) == 1


def test_douyin_fixture_quality_cap_selects_available_lower_stream():
    raw = _load_platform("douyin", "app_v3")
    default_info = DouyinService("key", "base")._parse_response(raw)
    capped_info = DouyinService("key", "base")._parse_response(raw, quality="540p")

    assert default_info is not None
    assert capped_info is not None
    assert default_info.quality == "adapt_lowest_720_1"
    assert capped_info.quality == "adapt_540_1"
    assert capped_info.width == 576
    assert capped_info.height == 1024


@pytest.mark.parametrize(
    ("service", "platform", "fixture"),
    [
        (TikTokService, "tiktok", "app_v3_video"),
        (KuaishouService, "kuaishou", "web_v2_video"),
        (XiaohongshuService, "xiaohongshu", "app_v2_video"),
    ],
)
def test_quality_is_noop_for_single_stream_fixtures(service, platform, fixture):
    raw = _load_platform(platform, fixture)
    parser = service("key", "base")
    default_info = parser._parse_response(raw)
    requested_info = parser._parse_response(raw, quality="720p")

    assert default_info is not None
    assert requested_info is not None
    assert requested_info.video_url == default_info.video_url


def test_quality_is_noop_for_instagram_adapter():
    raw = _load_platform("instagram", "v2_video")
    adapter = TikHubAdapter("key", "base")
    default_info = adapter.adapt(raw, "instagram", "id")
    requested_info = adapter.adapt(raw, "instagram", "id", quality="720p")

    assert default_info is not None
    assert requested_info is not None
    assert requested_info.video_url == default_info.video_url
    assert requested_info.quality == default_info.quality
