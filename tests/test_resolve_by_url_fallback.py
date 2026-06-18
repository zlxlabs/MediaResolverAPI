"""
/api/resolve 路由层 by_url 兜底测试（评审 Issue 5 / T5）

平台已识别但 video_id 提取失败时（如新链接格式），对「降级链含吃 url 端点」的平台
（kuaishou web_share / instagram v1+v2）不再直接 400，而是放行，让 provider 链用
原始 url 兜底；其余平台（tiktok/youtube）维持 400。
"""

import pytest

import app.api.resolve as resolve_mod
from app.services.platforms.base import VideoInfo


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


def _patch_urlparser(monkeypatch, *, parsed, platform_id):
    up = resolve_mod.url_parser
    monkeypatch.setattr(up, "is_short_url", lambda u: False)
    monkeypatch.setattr(up, "parse_url", lambda u: parsed)
    monkeypatch.setattr(up, "identify_platform", lambda u: platform_id)


@pytest.mark.parametrize(
    "platform,url",
    [
        ("kuaishou", "https://www.kuaishou.com/new-format/"),
        ("instagram", "https://www.instagram.com/reels/NEWFORMAT/"),
    ],
)
def test_id_extraction_failure_falls_back_to_by_url(authed_client, fake_resolver, monkeypatch, platform, url):
    """kuaishou/instagram 识别成功但 ID 提取失败：不 400，放行让链用原始 url 兜底。"""
    _patch_urlparser(monkeypatch, parsed=(platform, None), platform_id=platform)
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
        ("tiktok", "https://www.tiktok.com/new-format/"),
        ("youtube", "https://www.youtube.com/new-format/"),
    ],
)
def test_id_extraction_failure_still_400_for_non_byurl_platforms(authed_client, fake_resolver, monkeypatch, platform, url):
    """tiktok/youtube 链只吃 id，ID 提取失败维持 400，不调 resolver。"""
    _patch_urlparser(monkeypatch, parsed=(platform, None), platform_id=platform)
    resp = authed_client.post("/api/resolve", json={"url": url, "translate": False})
    assert resp.status_code == 400
    assert fake_resolver.calls == []


def test_normal_path_unaffected(authed_client, fake_resolver, monkeypatch):
    """kuaishou 正常链接（有 id）：走普通路径，video_id 正常传递。"""
    _patch_urlparser(
        monkeypatch, parsed=("kuaishou", "3xh683uyhqbzan4"), platform_id="kuaishou"
    )
    resp = authed_client.post(
        "/api/resolve",
        json={"url": "https://www.kuaishou.com/short-video/3xh683uyhqbzan4", "translate": False, "force_refresh": True},
    )
    assert resp.status_code == 200 and resp.json()["success"]
    assert fake_resolver.calls[0]["video_id"] == "3xh683uyhqbzan4"
