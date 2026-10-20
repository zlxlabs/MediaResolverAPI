"""缓存命中重新翻译时不得触碰缓存 TTL。"""

from datetime import datetime, timedelta, timezone

import pytest

import app.api.resolve as resolve_mod
from app.models.video_cache import VideoCache
from app.services.platforms.base import VideoInfo
from app.services.translation.openai import TranslationResult, TranslationStatus


VIDEO_ID = "7592102779420115355"
VIDEO_URL = f"https://www.tiktok.com/@author/video/{VIDEO_ID}"


def _video_info() -> VideoInfo:
    return VideoInfo(
        video_id=VIDEO_ID,
        platform="tiktok",
        title="A video",
        description="An English description",
        author_name="author",
        author_id="author-id",
        video_url="https://cdn.example/video.mp4",
        width=1080,
        height=1920,
        provider="tikhub",
    )


class _TranslationStub:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def is_chinese(self, _text):
        return False

    async def translate_to_chinese(self, _text):
        self.calls += 1
        return self.result


class _UnexpectedResolver:
    async def resolve(self, **_kwargs):
        raise AssertionError("cache hit must not resolve video again")


def _seed_cache(db, translated_desc="既有译文"):
    now = datetime.now(timezone.utc)
    record = VideoCache(
        platform="tiktok",
        video_id=VIDEO_ID,
        video_data=_video_info().to_dict(),
        translated_desc=translated_desc,
        provider="tikhub",
        cached_at=now - timedelta(hours=3),
        expires_at=now + timedelta(hours=4),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record.cached_at, record.expires_at


@pytest.mark.parametrize(
    "result",
    [
        TranslationResult(TranslationStatus.failed, None),
        TranslationResult(TranslationStatus.skipped_disabled, None),
    ],
)
def test_repeated_cache_hits_without_translation_do_not_mutate_cache(
    authed_client, db, monkeypatch, result
):
    before_cached_at, before_expires_at = _seed_cache(db)
    translation = _TranslationStub(result)
    monkeypatch.setattr(resolve_mod, "get_video_resolver", lambda: _UnexpectedResolver())
    monkeypatch.setattr(resolve_mod, "get_translation_service", lambda: translation)

    for _ in range(2):
        response = authed_client.post("/api/resolve", json={"url": VIDEO_URL})
        assert response.status_code == 200
        assert response.json()["data"]["translation_status"] == result.status.value
        db.expire_all()
        cached = db.query(VideoCache).filter_by(video_id=VIDEO_ID).one()
        assert cached.cached_at == before_cached_at
        assert cached.expires_at == before_expires_at
        assert cached.translated_desc == "既有译文"

    assert translation.calls == 2


def test_successful_cache_hit_updates_translation_without_extending_ttl(
    authed_client, db, monkeypatch
):
    before_cached_at, before_expires_at = _seed_cache(db, translated_desc=None)
    translation = _TranslationStub(
        TranslationResult(TranslationStatus.ok, "新的中文译文")
    )
    monkeypatch.setattr(resolve_mod, "get_video_resolver", lambda: _UnexpectedResolver())
    monkeypatch.setattr(resolve_mod, "get_translation_service", lambda: translation)

    response = authed_client.post("/api/resolve", json={"url": VIDEO_URL})

    assert response.status_code == 200
    assert response.json()["data"]["translated_description"] == "新的中文译文"
    assert response.json()["data"]["translation_status"] == "ok"
    db.expire_all()
    cached = db.query(VideoCache).filter_by(video_id=VIDEO_ID).one()
    assert cached.cached_at == before_cached_at
    assert cached.expires_at == before_expires_at
    assert cached.translated_desc == "新的中文译文"
    assert translation.calls == 1
