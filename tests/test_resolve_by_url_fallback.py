"""
/api/resolve 路由层 by_url 兜底测试（评审 Issue 5 / T5）

平台已识别但 video_id 提取失败时（如新链接格式），对「降级链含吃 url 端点」的平台
（kuaishou web_share / instagram v1+v2）不再直接 400，而是放行，让 provider 链用
原始 url 兜底；其余平台（tiktok/youtube）维持 400。
"""

import json
from pathlib import Path

import pytest

import app.api.resolve as resolve_mod
from app.services.platforms.base import VideoInfo
from app.services.providers.tikhub import TikHubProvider
from app.services.video_resolver import VideoResolver

FIXTURES = Path(__file__).parent / "fixtures"


def _video_info(platform, video_id):
    return VideoInfo(
        video_id=video_id, platform=platform, title="t", description="中文描述",
        author_name="a", author_id="a", video_url="http://x.mp4",
        width=1080, height=1920, provider="tikhub",
    )


class FakeResolver:
    def __init__(self, resolved_id="REAL_ID"):
        self.calls = []
        self._resolved_id = resolved_id

    async def resolve(self, platform, video_id, original_url, use_hybrid=False):
        self.calls.append(
            {"platform": platform, "video_id": video_id,
             "original_url": original_url, "use_hybrid": use_hybrid}
        )
        return _video_info(platform, self._resolved_id), "tikhub"


@pytest.fixture()
def fake_resolver(monkeypatch):
    r = FakeResolver()
    monkeypatch.setattr(resolve_mod, "get_video_resolver", lambda: r)
    return r


@pytest.mark.parametrize(
    "platform,url",
    [
        ("kuaishou", "https://v.kuaishou.com/2xAbCdE"),
        ("kuaishou", "https://v.kuaishou.com/KU3jplGP"),
        ("kuaishou", "https://www.kuaishou.com/f/X9aBcDeF"),
        ("instagram", "https://www.instagram.com/share/BAcDeFgHi"),
    ],
)
def test_id_extraction_failure_falls_back_to_by_url(authed_client, fake_resolver, platform, url):
    """真实 URL parser 识别平台但拿不到 ID 时，by_url 平台仍不 400。"""
    resp = authed_client.post("/api/resolve", json={"url": url, "translate": False})
    assert resp.status_code == 200 and resp.json()["success"]
    call = fake_resolver.calls[0]
    assert call["platform"] == platform
    assert call["video_id"] == ""          # 空 id 传给 resolver
    assert call["original_url"] == url       # 原始 url 透传供 by_url 端点兜底
    assert call["use_hybrid"] is False
    # 用解析出的真实 id 归一化回填
    assert resp.json()["data"]["video_id"] == "REAL_ID"


@pytest.mark.parametrize(
    "platform,url",
    [
        ("tiktok", "https://www.tiktok.com/@user"),
        ("youtube", "https://www.youtube.com/channel/UCabc"),
        ("xiaohongshu", "https://www.xiaohongshu.com/user/profile/abc"),
        ("pinterest", "https://www.pinterest.com/user/name/"),
        ("facebook", "https://www.facebook.com/profile.php?id=123"),
        ("twitter", "https://x.com/0xCodez"),
        ("twitter", "https://x.com/i/spaces/"),
    ],
)
def test_id_extraction_failure_still_400_for_non_byurl_platforms(authed_client, fake_resolver, platform, url):
    """非 by_url 平台的可识别无 ID URL 维持 400，不调 resolver。"""
    resp = authed_client.post("/api/resolve", json={"url": url, "translate": False})
    assert resp.status_code == 400
    assert fake_resolver.calls == []


def test_normal_path_unaffected(authed_client, fake_resolver):
    """kuaishou 正常链接（有 id）：走普通路径，video_id 正常传递。"""
    resp = authed_client.post(
        "/api/resolve",
        json={"url": "https://www.kuaishou.com/short-video/3xh683uyhqbzan4", "translate": False, "force_refresh": True},
    )
    assert resp.status_code == 200 and resp.json()["success"]
    assert fake_resolver.calls[0]["video_id"] == "3xh683uyhqbzan4"


@pytest.mark.parametrize(
    "url,fixture_name,expected_endpoint,expected_path,expected_params",
    [
        (
            "https://v.kuaishou.com/2xAbCdE",
            "kuaishou/web_share_video.json",
            "web_share",
            "/api/v1/kuaishou/web/fetch_one_video",
            {"share_text": "https://v.kuaishou.com/2xAbCdE"},
        ),
        (
            "https://v.kuaishou.com/KU3jplGP",
            "kuaishou/web_share_video.json",
            "web_share",
            "/api/v1/kuaishou/web/fetch_one_video",
            {"share_text": "https://v.kuaishou.com/KU3jplGP"},
        ),
        (
            "https://www.kuaishou.com/f/X9aBcDeF",
            "kuaishou/web_share_video.json",
            "web_share",
            "/api/v1/kuaishou/web/fetch_one_video",
            {"share_text": "https://www.kuaishou.com/f/X9aBcDeF"},
        ),
        (
            "https://www.kuaishou.com/short-video/3xgktqzhbkse2fe",
            "kuaishou/web_v2_video.json",
            "web_v2",
            "/api/v1/kuaishou/web/fetch_one_video_v2",
            {"photo_id": "3xgktqzhbkse2fe"},
        ),
        (
            "https://www.instagram.com/share/BAcDeFgHi",
            "instagram/v2_video.json",
            "v2",
            "/api/v1/instagram/v2/fetch_post_info",
            {"code_or_url": "https://www.instagram.com/share/BAcDeFgHi"},
        ),
    ],
)
def test_route_to_tikhub_http_keeps_original_url(
    authed_client, monkeypatch, url, fixture_name, expected_endpoint, expected_path, expected_params
):
    """真实 /api/resolve、parse_url 和 provider 链到 TikHub HTTP 边界。"""
    requests = []

    async def fake_http(self, name, path, params, per_timeout):
        requests.append((name, path, params))
        return json.loads((FIXTURES / fixture_name).read_text(encoding="utf-8"))

    monkeypatch.setattr(resolve_mod, "get_video_resolver", VideoResolver)
    monkeypatch.setattr(TikHubProvider, "_call_endpoint", fake_http)

    response = authed_client.post(
        "/api/resolve",
        json={"url": url, "translate": False, "force_refresh": True},
    )

    assert response.status_code == 200 and response.json()["success"]
    assert requests == [(expected_endpoint, expected_path, expected_params)]
