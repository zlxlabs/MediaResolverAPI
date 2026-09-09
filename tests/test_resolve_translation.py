"""翻译结果在解析响应和缓存中的诚实性测试。"""

from datetime import datetime, timezone

from app.models.video_cache import VideoCache
from app.services.platforms.base import VideoInfo


def _video_info(video_id="7592102779420115355"):
    return VideoInfo(
        video_id=video_id,
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


class _FailedTranslation:
    status = "failed"
    text = None


class _SuccessfulTranslation:
    status = "ok"
    text = "一段中文描述"


class _TranslationStub:
    def is_chinese(self, text):
        return False

    async def translate_to_chinese(self, text):
        return _FailedTranslation()


class _SuccessfulTranslationStub(_TranslationStub):
    async def translate_to_chinese(self, text):
        return _SuccessfulTranslation()


class _Resolver:
    async def resolve(self, **kwargs):
        return _video_info(), "tikhub"


def test_failed_translation_keeps_resolve_success_and_does_not_cache_source(
    authed_client, db, monkeypatch
):
    import app.api.resolve as resolve_mod

    monkeypatch.setattr(resolve_mod, "get_video_resolver", lambda: _Resolver())
    monkeypatch.setattr(
        resolve_mod, "get_translation_service", lambda: _TranslationStub()
    )

    response = authed_client.post(
        "/api/resolve",
        json={"url": "https://www.tiktok.com/@author/video/7592102779420115355"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["translated_description"] is None
    assert payload["data"]["translation_status"] == "failed"

    cached = (
        db.query(VideoCache)
        .filter(
            VideoCache.platform == "tiktok",
            VideoCache.video_id == "7592102779420115355",
        )
        .one()
    )
    assert cached.translated_desc is None
    assert cached.translated_desc != "An English description"


def test_cache_hit_with_empty_translation_is_retranslated_and_written_back(
    authed_client, db, monkeypatch
):
    import app.api.resolve as resolve_mod

    info = _video_info()
    db.add(
        VideoCache(
            platform="tiktok",
            video_id=info.video_id,
            video_data=info.to_dict(),
            translated_desc=None,
            provider="tikhub",
            cached_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    class _CacheResolver:
        async def resolve(self, **kwargs):
            raise AssertionError("cache hit should not resolve video again")

    monkeypatch.setattr(
        resolve_mod, "get_video_resolver", lambda: _CacheResolver()
    )
    monkeypatch.setattr(
        resolve_mod,
        "get_translation_service",
        lambda: _SuccessfulTranslationStub(),
    )

    response = authed_client.post(
        "/api/resolve",
        json={"url": "https://www.tiktok.com/@author/video/7592102779420115355"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["translated_description"] == "一段中文描述"
    assert payload["data"]["translation_status"] == "ok"
    db.refresh(db.query(VideoCache).filter(VideoCache.video_id == info.video_id).one())
    cached = db.query(VideoCache).filter(VideoCache.video_id == info.video_id).one()
    assert cached.translated_desc == "一段中文描述"


def test_cache_hit_with_chinese_source_does_not_expose_stale_translation(
    authed_client, db, monkeypatch
):
    import app.api.resolve as resolve_mod

    info = _video_info()
    info.description = "这已经是一段中文简介"
    db.add(
        VideoCache(
            platform="tiktok",
            video_id=info.video_id,
            video_data=info.to_dict(),
            translated_desc="历史译文",
            provider="tikhub",
            cached_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    class _ChineseAwareTranslation:
        def is_chinese(self, text):
            return text == info.description

        async def translate_to_chinese(self, text):
            raise AssertionError("Chinese source should not be translated")

    monkeypatch.setattr(
        resolve_mod,
        "get_translation_service",
        lambda: _ChineseAwareTranslation(),
    )

    response = authed_client.post(
        "/api/resolve",
        json={"url": "https://www.tiktok.com/@author/video/7592102779420115355"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["translated_description"] is None
    assert response.json()["data"]["translation_status"] == "skipped_chinese"
