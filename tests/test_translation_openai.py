"""翻译上游失败时的领域结果契约。"""

import httpx

from app.core.config import settings
from app.services.translation.openai import TranslationService


class _Response:
    status_code = 503
    text = "upstream unavailable"

    @staticmethod
    def json():
        return {"error": {"code": "model_not_found"}}


class _Client:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def post(self, *args, **kwargs):
        return _Response()


async def test_translation_failure_is_structured_and_has_no_text(monkeypatch):
    """失败不能返回原文，必须能被路由识别为失败结果。"""
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client())
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "TRANSLATION_ENABLED", True)

    service = TranslationService()
    result = await service.translate_to_chinese("An English description")

    assert result.__class__.__name__ == "TranslationResult"
    assert getattr(result, "status", None) == "failed"
    assert getattr(result, "text", "sentinel") is None

