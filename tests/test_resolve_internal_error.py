"""resolve 的兜底异常写服务端日志，但不回显给调用方。"""

from io import StringIO

from loguru import logger

import app.api.resolve as resolve_mod


VIDEO_URL = "https://www.tiktok.com/@author/video/7592102779420115355"


class _FailingResolver:
    async def resolve(self, **_kwargs):
        raise RuntimeError("secret-url?token=abc")


def test_unexpected_error_is_logged_but_not_returned(authed_client, monkeypatch):
    monkeypatch.setattr(
        resolve_mod, "get_video_resolver", lambda: _FailingResolver()
    )
    output = StringIO()
    sink_id = logger.add(output, level="ERROR", format="{message}")
    try:
        response = authed_client.post(
            "/api/resolve", json={"url": VIDEO_URL, "translate": False}
        )
    finally:
        logger.remove(sink_id)

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] == "Internal error"
    assert "secret-url" not in payload["error"]
    assert "token=abc" not in payload["error"]
    logged = output.getvalue()
    assert "RESOLVE-UNHANDLED-ERROR" in logged
    assert "RuntimeError: secret-url?token=abc" in logged
