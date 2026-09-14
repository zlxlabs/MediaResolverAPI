"""download_mode=audio 的解析、路由、缓存和迁移契约测试。"""

import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.resolve as resolve_mod
from app.models.video_cache import VideoCache
from app.services.adapters.cobalt_adapter import CobaltAdapter
from app.services.cache import CacheService
from app.services.platforms.base import VideoInfo
from app.services.platforms.youtube import YouTubeService
from app.services.providers.cobalt import CobaltProvider
from app.services.video_resolver import VideoResolver


FIXTURES = Path(__file__).parent / "fixtures" / "audio"
YOUTUBE_FIXTURES = Path(__file__).parent / "fixtures" / "youtube"
YOUTUBE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def load_audio(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def load_youtube(name: str) -> dict:
    return json.loads((YOUTUBE_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _patch_platform(monkeypatch, platform: str, video_id: str = "video-id"):
    monkeypatch.setattr(resolve_mod.url_parser, "is_short_url", lambda _url: False)
    monkeypatch.setattr(
        resolve_mod.url_parser, "parse_url", lambda _url: (platform, video_id)
    )


def _video_info(
    *, platform: str = "youtube", video_id: str = "video-id",
    video_url: str = "https://cdn.example/video.mp4", media_type: str = "video",
) -> VideoInfo:
    return VideoInfo(
        video_id=video_id,
        platform=platform,
        title="title",
        description="中文描述",
        author_name="author",
        author_id="author-id",
        video_url=video_url,
        width=0 if media_type == "audio" else 1920,
        height=0 if media_type == "audio" else 1080,
        provider="tikhub",
        media_type=media_type,
    )


def test_youtube_audio_picks_highest_bitrate_audio_track():
    info = YouTubeService("key", "base")._parse_response(
        load_audio("youtube_v2_audio"), download_mode="audio"
    )

    assert info is not None
    assert info.media_type == "audio"
    assert info.video_url.endswith("yt_audio_high.webm")
    assert "yt_video_only" not in info.video_url
    assert info.width == 0
    assert info.height == 0


def test_youtube_audio_without_audio_track_falls_back_to_cobalt(monkeypatch):
    resolver = VideoResolver()
    calls = {"tikhub": [], "cobalt": []}

    async def tikhub_fetch(*args, **kwargs):
        calls["tikhub"].append(kwargs)
        return load_audio("youtube_v2_no_audio")

    async def cobalt_fetch(*args, **kwargs):
        calls["cobalt"].append(kwargs)
        return load_audio("cobalt_audio")

    monkeypatch.setattr(resolver.tikhub_provider, "fetch_video_info", tikhub_fetch)
    monkeypatch.setattr(resolver.cobalt_provider, "fetch_video_info", cobalt_fetch)

    info, provider = __import__("asyncio").run(
        resolver.resolve(
            "youtube", "video-id", YOUTUBE_URL, download_mode="audio"
        )
    )

    assert provider == "cobalt"
    assert info.media_type == "audio"
    assert info.video_url.endswith("audio-track.m4a")
    assert calls["tikhub"][0]["download_mode"] == "audio"
    assert calls["cobalt"][0]["download_mode"] == "audio"


@pytest.mark.asyncio
async def test_cobalt_audio_request_body_only_adds_audio_mode(monkeypatch):
    seen = []

    class FakeResponse:
        status_code = 200
        text = json.dumps(load_audio("cobalt_audio"))
        headers = {"content-type": "application/json"}

        def json(self):
            return load_audio("cobalt_audio")

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            seen.append(kwargs["json"])
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeClient())
    provider = CobaltProvider()
    provider.api_base = "https://cobalt.example"

    await provider.fetch_video_info("twitter", "id", "https://x.com/u/status/id", download_mode="audio")
    await provider.fetch_video_info("twitter", "id", "https://x.com/u/status/id")

    assert seen == [
        {"url": "https://x.com/u/status/id", "downloadMode": "audio"},
        {"url": "https://x.com/u/status/id"},
    ]


@pytest.mark.parametrize("platform", ["douyin", "kuaishou", "xiaohongshu", "wechat_channels"])
def test_audio_unavailable_platforms_return_400_without_provider_calls(
    authed_client, monkeypatch, platform
):
    _patch_platform(monkeypatch, platform)
    resolver = VideoResolver()
    calls = []

    async def provider_call(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("audio-unavailable platform must not call a provider")

    monkeypatch.setattr(resolver.tikhub_provider, "fetch_video_info", provider_call)
    monkeypatch.setattr(resolver.cobalt_provider, "fetch_video_info", provider_call)
    monkeypatch.setattr(resolve_mod, "get_video_resolver", lambda: resolver)

    response = authed_client.post(
        "/api/resolve",
        json={"url": "https://example.com/video", "translate": False, "download_mode": "audio"},
    )

    assert response.status_code == 400
    assert "audio_not_available" in response.text
    assert calls == []


def test_twitter_audio_uses_only_cobalt(authed_client, monkeypatch):
    _patch_platform(monkeypatch, "twitter", "tweet-id")
    resolver = VideoResolver()
    tikhub_calls = []
    cobalt_calls = []

    async def tikhub_call(*args, **kwargs):
        tikhub_calls.append(kwargs)
        raise AssertionError("twitter audio must not call TikHub")

    async def cobalt_call(*args, **kwargs):
        cobalt_calls.append(kwargs)
        return load_audio("cobalt_audio")

    monkeypatch.setattr(resolver.tikhub_provider, "fetch_video_info", tikhub_call)
    monkeypatch.setattr(resolver.cobalt_provider, "fetch_video_info", cobalt_call)
    monkeypatch.setattr(resolve_mod, "get_video_resolver", lambda: resolver)

    response = authed_client.post(
        "/api/resolve",
        json={"url": "https://x.com/user/status/tweet-id", "translate": False, "download_mode": "audio"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["media_type"] == "audio"
    assert response.json()["data"]["provider"] == "cobalt"
    assert tikhub_calls == []
    assert cobalt_calls[0]["download_mode"] == "audio"


def test_youtube_audio_api_e2e_and_video_cache_are_isolated(
    authed_client, db, monkeypatch
):
    _patch_platform(monkeypatch, "youtube", "dQw4w9WgXcQ")
    resolver = VideoResolver()
    calls = []

    async def tikhub_call(*args, **kwargs):
        mode = kwargs.get("download_mode", "video")
        calls.append(mode)
        return load_audio("youtube_v2_audio") if mode == "audio" else load_youtube("web_video")

    monkeypatch.setattr(resolver.tikhub_provider, "fetch_video_info", tikhub_call)
    monkeypatch.setattr(resolve_mod, "get_video_resolver", lambda: resolver)

    audio_response = authed_client.post(
        "/api/resolve",
        json={"url": YOUTUBE_URL, "translate": False, "download_mode": "audio"},
    )
    video_response = authed_client.post(
        "/api/resolve", json={"url": YOUTUBE_URL, "translate": False}
    )
    cached_audio_response = authed_client.post(
        "/api/resolve",
        json={"url": YOUTUBE_URL, "translate": False, "download_mode": "audio"},
    )

    assert audio_response.status_code == 200
    assert audio_response.json()["data"]["media_type"] == "audio"
    assert audio_response.json()["data"]["video_url"].endswith("yt_audio_high.webm")
    assert video_response.status_code == 200
    assert video_response.json()["data"]["media_type"] == "video"
    assert video_response.json()["data"]["video_url"].endswith("yt_web_1080.mp4")
    assert cached_audio_response.json()["data"]["media_type"] == "audio"
    assert calls == ["audio", "video"]

    records = db.query(VideoCache).filter(VideoCache.video_id == "dQw4w9WgXcQ").all()
    assert {record.download_mode for record in records} == {"audio", "video"}


def test_cache_video_and_audio_entries_do_not_overwrite_each_other(db):
    cache = CacheService(db)
    video = _video_info(video_url="https://cdn.example/video.mp4")
    audio = _video_info(
        video_url="https://cdn.example/audio.m4a", media_type="audio"
    )

    assert cache.cache_video("youtube", "same-id", video, download_mode="video")
    assert cache.cache_video("youtube", "same-id", audio, download_mode="audio")

    cached_video, _ = cache.get_cached_video("youtube", "same-id", "video")
    cached_audio, _ = cache.get_cached_video("youtube", "same-id", "audio")
    assert cached_video.video_url.endswith("video.mp4")
    assert cached_video.media_type == "video"
    assert cached_audio.video_url.endswith("audio.m4a")
    assert cached_audio.media_type == "audio"


def test_old_cache_table_is_migrated_and_hits_video_mode():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    old_data = _video_info(video_id="old-id").to_dict()
    old_data.pop("media_type")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE video_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform VARCHAR(20) NOT NULL,
                video_id VARCHAR(100) NOT NULL,
                video_data JSON NOT NULL,
                translated_desc TEXT,
                provider VARCHAR(20) NOT NULL,
                cached_at DATETIME NOT NULL,
                expires_at DATETIME NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX ix_platform_video_id ON video_cache (platform, video_id)"
        )
        connection.exec_driver_sql(
            """
            INSERT INTO video_cache
                (platform, video_id, video_data, translated_desc, provider, cached_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "youtube",
                "old-id",
                json.dumps(old_data),
                None,
                "tikhub",
                "2026-09-14 00:00:00",
                "2099-09-14 00:00:00",
            ),
        )

    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        cache = CacheService(session)
        info, _ = cache.get_cached_video("youtube", "old-id")

        assert info is not None
        assert info.media_type == "video"
        assert info.video_url.endswith("video.mp4")
        with engine.connect() as connection:
            columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info('video_cache')"
                ).fetchall()
            }
            index_columns = [
                row[2]
                for row in connection.exec_driver_sql(
                    "PRAGMA index_info('ix_platform_video_id')"
                ).fetchall()
            ]
        assert "download_mode" in columns
        assert index_columns == ["platform", "video_id", "download_mode"]
    finally:
        session.close()


def test_cobalt_adapter_audio_picker_does_not_select_video_item():
    info = CobaltAdapter().adapt(
        {
            "status": "picker",
            "picker": [
                {"type": "video", "url": "https://cdn.example/video.mp4"},
                {"type": "audio", "url": "https://cdn.example/audio.m4a"},
            ],
        },
        "twitter",
        "id",
        "https://x.com/u/status/id",
        download_mode="audio",
    )

    assert info is not None
    assert info.media_type == "audio"
    assert info.video_url.endswith("audio.m4a")


def test_invalid_download_mode_is_422(authed_client):
    response = authed_client.post(
        "/api/resolve",
        json={"url": YOUTUBE_URL, "download_mode": "mp3"},
    )

    assert response.status_code == 422
