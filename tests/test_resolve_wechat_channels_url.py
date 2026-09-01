"""
视频号 video_url：解析层只存相对路径，API 层按请求 Host / PUBLIC_BASE_URL 补全。

四格：空配置用请求 Host、显式配置优先、缓存命中不含 host、其它平台绝对 URL 不被碰到。
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

import app.api.resolve as resolve_mod
from app.core.config import settings
from app.main import app
from app.services.platforms.base import VideoInfo
from app.services.providers.tikhub import TikHubProvider

FIXTURES = Path(__file__).parent / "fixtures" / "wechat_channels"
OBJECT_ID = "14998022876670594427"
SPH_CODE = "AOzokRxWHz"
SHARE_URL = "https://weixin.qq.com/sph/AOzokRxWHz"
DOUYIN_URL = "https://www.douyin.com/video/7477059950577978636"
DOUYIN_CDN = "https://v3-dy.example.net/aweme/play.mp4?token=abc"


def _load_detail() -> dict:
    return json.loads((FIXTURES / "detail.json").read_text(encoding="utf-8"))


def _post(base_url: str, body: dict):
    with TestClient(app, base_url=base_url, raise_server_exceptions=False) as c:
        return c.post(
            "/api/resolve",
            json=body,
            headers={"X-API-Key": "test-key-123"},
        )


def _patch_wechat_fetch(monkeypatch):
    """parse 出 object_id 以便走缓存查找；TikHub 端点返回 fixture。"""
    monkeypatch.setattr(resolve_mod.url_parser, "is_short_url", lambda u: False)
    monkeypatch.setattr(
        resolve_mod.url_parser, "parse_url", lambda u: ("wechat_channels", OBJECT_ID)
    )

    async def fake_call(self, name, path, params, per_timeout):
        return _load_detail()

    monkeypatch.setattr(TikHubProvider, "_call_endpoint", fake_call)


def test_empty_public_base_uses_request_host(authed_client, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "")
    _patch_wechat_fetch(monkeypatch)
    resp = _post("http://example.com:9000", {"url": SHARE_URL, "translate": False})
    assert resp.status_code == 200 and resp.json()["success"]
    video_url = resp.json()["data"]["video_url"]
    assert video_url.startswith("http://example.com:9000/api/stream/wechat_channels/")
    assert video_url == f"http://example.com:9000/api/stream/wechat_channels/{SPH_CODE}"
    assert resp.json()["data"]["video_id"] == OBJECT_ID


def test_explicit_public_base_url_wins_over_request_host(authed_client, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://media.example.org")
    _patch_wechat_fetch(monkeypatch)
    resp = _post("http://example.com:9000", {"url": SHARE_URL, "translate": False})
    assert resp.status_code == 200 and resp.json()["success"]
    video_url = resp.json()["data"]["video_url"]
    assert video_url.startswith("https://media.example.org/api/stream/wechat_channels/")
    assert video_url == f"https://media.example.org/api/stream/wechat_channels/{SPH_CODE}"
    assert resp.json()["data"]["video_id"] == OBJECT_ID


def test_cache_hit_uses_current_request_host_not_cached_host(authed_client, monkeypatch):
    """缓存必须只存相对路径，第二次换 Host 不能吐出第一次的域名。"""
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "")
    _patch_wechat_fetch(monkeypatch)

    first = _post(
        "http://a.example.com",
        {"url": SHARE_URL, "translate": False, "force_refresh": True},
    )
    assert first.status_code == 200 and first.json()["success"]
    assert first.json()["data"]["video_url"].startswith(
        "http://a.example.com/api/stream/wechat_channels/"
    )

    second = _post(
        "http://b.example.com",
        {"url": SHARE_URL, "translate": False, "force_refresh": False},
    )
    assert second.status_code == 200 and second.json()["success"]
    assert second.json()["data"]["video_url"].startswith(
        "http://b.example.com/api/stream/wechat_channels/"
    )
    assert "a.example.com" not in second.json()["data"]["video_url"]


def test_other_platform_absolute_video_url_untouched(authed_client, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "")
    monkeypatch.setattr(resolve_mod.url_parser, "is_short_url", lambda u: False)
    monkeypatch.setattr(
        resolve_mod.url_parser, "parse_url", lambda u: ("douyin", "7477059950577978636")
    )

    async def fake_resolve(platform, video_id, original_url, use_hybrid=False):
        info = VideoInfo(
            video_id=video_id,
            platform="douyin",
            title="t",
            description="中文描述",
            author_name="a",
            author_id="a",
            video_url=DOUYIN_CDN,
            width=1080,
            height=1920,
            provider="tikhub",
        )
        return info, "tikhub"

    monkeypatch.setattr(resolve_mod, "get_video_resolver", lambda: type(
        "R", (), {"resolve": staticmethod(fake_resolve)}
    )())

    resp = _post("http://example.com:9000", {"url": DOUYIN_URL, "translate": False, "force_refresh": True})
    assert resp.status_code == 200 and resp.json()["success"]
    assert resp.json()["data"]["video_url"] == DOUYIN_CDN
