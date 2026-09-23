import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app


def _get_log_lines(tmp_path, pattern):
    log_files = list(tmp_path.glob("app_*.log"))
    assert len(log_files) == 1, f"Expected 1 app log file, found {len(log_files)}"
    content = log_files[0].read_text(encoding="utf-8")
    return [line for line in content.splitlines() if pattern in line]


@pytest.mark.parametrize("empty_val", ["", "   "])
def test_public_base_url_warning_when_empty(monkeypatch, tmp_path, empty_val):
    monkeypatch.setattr(settings, "LOG_FILE_PATH", str(tmp_path))
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", empty_val)

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.json().get("status") == "running"

    matches = _get_log_lines(tmp_path, "PUBLIC_BASE_URL 未设置")
    assert len(matches) == 1
    assert "data.video_url 将按请求 Host 推导，跨机调用会拿到不可用地址" in matches[0]


def test_public_base_url_no_warning_when_set(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "LOG_FILE_PATH", str(tmp_path))
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://api.example.com")

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/")
        assert resp.status_code == 200

    matches = _get_log_lines(tmp_path, "PUBLIC_BASE_URL 未设置")
    assert len(matches) == 0
