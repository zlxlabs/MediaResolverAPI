"""翻译健康口和启动探测测试。"""

import time

import httpx
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.translation.openai import TranslationService


def _service(monkeypatch, *, enabled=True, api_key="test-key"):
    monkeypatch.setattr(settings, "TRANSLATION_ENABLED", enabled)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", api_key)
    return TranslationService()


def test_translation_circuit_health_is_separate_from_liveness(monkeypatch):
    import app.api.resolve as resolve_module
    import app.main as main_module

    service = _service(monkeypatch)
    service._circuit_opened_at = time.monotonic()
    monkeypatch.setattr(resolve_module, "get_translation_service", lambda: service)
    monkeypatch.setattr(
        main_module, "get_translation_service", lambda: service, raising=False
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        liveness = client.get("/health")
        translation = client.get("/health/translation")

    assert liveness.status_code == 200
    assert liveness.json() == {"status": "ok"}
    assert translation.status_code == 200
    assert translation.json() == {
        "status": "unavailable",
        "circuit_open": True,
    }


def test_translation_health_reports_disabled_without_key(monkeypatch):
    import app.main as main_module

    service = _service(monkeypatch, api_key="")
    monkeypatch.setattr(
        main_module, "get_translation_service", lambda: service, raising=False
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/health/translation")

    assert response.status_code == 200
    assert response.json() == {"status": "disabled", "circuit_open": False}


class _ProbeResponse:
    status_code = 503
    text = "upstream unavailable"

    @staticmethod
    def json():
        return {"error": {"code": "model_not_found"}}


class _ProbeClient:
    calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def post(self, *args, **kwargs):
        type(self).calls += 1
        return _ProbeResponse()


def test_startup_probe_failure_opens_circuit_but_does_not_block_startup(monkeypatch):
    import app.api.resolve as resolve_module

    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "TRANSLATION_ENABLED", True)
    monkeypatch.setattr(settings, "TRANSLATION_STARTUP_PROBE", True)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _ProbeClient())
    resolve_module._translation_service = None

    with TestClient(app, raise_server_exceptions=False) as client:
        liveness = client.get("/health")
        translation = client.get("/health/translation")

    assert _ProbeClient.calls == 1
    assert liveness.status_code == 200
    assert liveness.json() == {"status": "ok"}
    assert translation.status_code == 200
    assert translation.json() == {
        "status": "unavailable",
        "circuit_open": True,
    }


def test_startup_probe_exception_does_not_break_application(monkeypatch):
    import app.main as main_module

    assert callable(getattr(main_module, "probe_translation_upstream", None))
    service = _service(monkeypatch)
    monkeypatch.setattr(
        main_module, "get_translation_service", lambda: service, raising=False
    )

    async def fail_probe(_service):
        raise TimeoutError("probe timeout")

    monkeypatch.setattr(
        main_module, "probe_translation_upstream", fail_probe, raising=False
    )
    monkeypatch.setattr(settings, "TRANSLATION_STARTUP_PROBE", True)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
