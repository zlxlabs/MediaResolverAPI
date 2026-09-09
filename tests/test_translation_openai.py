"""翻译上游失败时的领域结果契约。"""

import httpx

from app.core.config import settings
from app.services.translation.openai import TranslationService
import app.services.translation.openai as translation_module


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


class _ModelResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = "upstream response"

    def json(self):
        return self._payload


class _ModelClient:
    def __init__(self, responses):
        self.responses = responses
        self.models = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def post(self, *args, **kwargs):
        model = kwargs["json"]["model"]
        self.models.append(model)
        return self.responses[model]


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


async def test_dead_primary_model_is_skipped_on_next_request(monkeypatch):
    """model_not_found 只冷却死模型，备用模型成功后复用名单结果。"""
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "TRANSLATION_ENABLED", True)
    monkeypatch.setattr(settings, "OPENAI_MODEL", "model-a")
    monkeypatch.setitem(settings.__dict__, "OPENAI_MODEL_FALLBACKS", "model-b")
    client = _ModelClient(
        {
            "model-a": _ModelResponse(
                503, {"error": {"code": "model_not_found"}}
            ),
            "model-b": _ModelResponse(
                200,
                {"choices": [{"message": {"content": "中文译文"}}]},
            ),
        }
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)

    service = TranslationService()
    first = await service.translate_to_chinese("An English description")
    second = await service.translate_to_chinese("Another English description")

    assert first.status == "ok"
    assert first.text == "中文译文"
    assert first.model == "model-b"
    assert second.status == "ok"
    assert client.models == ["model-a", "model-b", "model-b"]


async def test_all_dead_models_open_circuit_and_alert_once(monkeypatch):
    """名单全灭后冷却内不再请求上游，熔断只告警一次。"""
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "TRANSLATION_ENABLED", True)
    monkeypatch.setattr(settings, "OPENAI_MODEL", "model-a")
    monkeypatch.setitem(settings.__dict__, "OPENAI_MODEL_FALLBACKS", "model-b")
    client = _ModelClient(
        {
            model: _ModelResponse(
                503, {"error": {"code": "model_not_found"}}
            )
            for model in ("model-a", "model-b")
        }
    )
    alerts = []
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(
        translation_module,
        "report_translation_unavailable",
        lambda models, error_code: alerts.append((models, error_code)),
        raising=False,
    )

    service = TranslationService()
    first = await service.translate_to_chinese("An English description")
    second = await service.translate_to_chinese("Another English description")

    assert first.status == "failed"
    assert first.text is None
    assert second.status == "failed"
    assert getattr(service, "circuit_open", False) is True
    assert client.models == ["model-a", "model-b"]
    assert alerts == [(["model-a", "model-b"], "model_not_found")]


def test_translation_unavailable_reports_sentry_event(monkeypatch):
    """告警出口要发送固定指纹、错误级别和可行动文案。"""
    reporter = getattr(translation_module, "report_translation_unavailable", None)
    assert callable(reporter)

    events = []
    monkeypatch.setattr(
        translation_module.sentry_sdk,
        "capture_message",
        lambda message, **kwargs: events.append((message, kwargs)),
    )
    reporter(["model-a", "model-b"], "model_not_found")

    assert len(events) == 1
    message, kwargs = events[0]
    assert "model-a" in message and "model-b" in message
    assert "model_not_found" in message
    assert "检查网关渠道或改 OPENAI_MODEL / OPENAI_MODEL_FALLBACKS 后重启 media-resolver-api" in message
    assert kwargs["fingerprint"] == ["translation_upstream_unavailable"]
    assert kwargs["level"] == "error"


def test_translation_unavailable_alert_is_fail_open(monkeypatch):
    """Sentry 上报异常不能让翻译调用方崩溃。"""
    reporter = getattr(translation_module, "report_translation_unavailable", None)
    assert callable(reporter)

    def fail_capture(*args, **kwargs):
        raise RuntimeError("sentry unavailable")

    monkeypatch.setattr(
        translation_module.sentry_sdk, "capture_message", fail_capture
    )
    reporter(["model-a"], "model_not_found")
