"""
/api/resolve 路由层 hybrid 兜底测试（T7 / E2 / codex #6/#7/#8）

抖音 URL 但拿不到 aweme_id（短链展开失败 / ID 提取失败）时，不直接 400，
而是改走 hybrid 路径（resolver.resolve(use_hybrid=True)），并用返回的 aweme_id 归一化。
"""

import pytest

import app.api.resolve as resolve_mod
from app.services.platforms.base import VideoInfo


def _video_info(video_id="7592102779420115355"):
    return VideoInfo(
        video_id=video_id, platform="douyin", title="t", description="中文描述",
        author_name="a", author_id="a", video_url="http://x.mp4",
        width=1080, height=1920, provider="tikhub",
    )


class FakeResolver:
    def __init__(self):
        self.calls = []

    async def resolve(self, platform, video_id, original_url, use_hybrid=False):
        self.calls.append(
            {"platform": platform, "video_id": video_id,
             "original_url": original_url, "use_hybrid": use_hybrid}
        )
        return _video_info(), "tikhub"


@pytest.fixture()
def fake_resolver(monkeypatch):
    r = FakeResolver()
    monkeypatch.setattr(resolve_mod, "get_video_resolver", lambda: r)
    return r


def test_normal_path_uses_video_id(authed_client, fake_resolver):
    """正常抖音长链接：真实解析拿到 id，走普通路径（非 hybrid）。"""
    resp = authed_client.post(
        "/api/resolve",
        json={"url": "https://www.douyin.com/video/7592102779420115355", "translate": False},
    )
    assert resp.status_code == 200 and resp.json()["success"]
    assert fake_resolver.calls[0]["use_hybrid"] is False
    assert fake_resolver.calls[0]["video_id"] == "7592102779420115355"


def test_short_link_expansion_failure_falls_back_to_hybrid(authed_client, fake_resolver, monkeypatch):
    """抖音短链展开失败：不 400，改走 hybrid；平台识别使用真实 URL parser。"""

    async def fail_expansion(url):
        return None

    monkeypatch.setattr(resolve_mod.url_parser, "resolve_short_url", fail_expansion)
    resp = authed_client.post("/api/resolve", json={"url": "https://v.douyin.com/broken/", "translate": False})
    assert resp.status_code == 200 and resp.json()["success"]
    assert fake_resolver.calls[0]["use_hybrid"] is True
    # 用返回的 aweme_id 归一化
    assert resp.json()["data"]["video_id"] == "7592102779420115355"


def test_id_extraction_failure_falls_back_to_hybrid(authed_client, fake_resolver):
    """抖音用户主页没有视频 ID：真实解析后改走 hybrid。"""
    resp = authed_client.post(
        "/api/resolve",
        json={"url": "https://www.douyin.com/user/MS4wLjABAAAAxxxx", "translate": False},
    )
    assert resp.status_code == 200 and resp.json()["success"]
    assert fake_resolver.calls[0]["use_hybrid"] is True


def test_id_extraction_failure_with_no_playable_result_is_http_200_failure(
    authed_client, fake_resolver, monkeypatch
):
    """Owner 接受的抖音无 ID 行为：hybrid 无可播放结果仍 HTTP 200、success=false。"""

    async def fail_hybrid(platform, video_id, original_url, use_hybrid=False):
        fake_resolver.calls.append(
            {"platform": platform, "video_id": video_id,
             "original_url": original_url, "use_hybrid": use_hybrid}
        )
        raise resolve_mod.VideoResolverError("no playable result")

    monkeypatch.setattr(fake_resolver, "resolve", fail_hybrid)
    resp = authed_client.post(
        "/api/resolve",
        json={"url": "https://www.douyin.com/user/MS4wLjABAAAAxxxx", "translate": False},
    )
    assert resp.status_code == 200
    assert resp.json() == {"success": False, "data": None, "error": "no playable result"}
    assert fake_resolver.calls[0]["use_hybrid"] is True


def test_non_douyin_expansion_failure_still_400(authed_client, fake_resolver, monkeypatch):
    """非抖音短链展开失败：维持原 400；平台识别使用真实 URL parser。"""

    async def fail_expansion(url):
        return None

    monkeypatch.setattr(resolve_mod.url_parser, "resolve_short_url", fail_expansion)
    resp = authed_client.post("/api/resolve", json={"url": "https://vm.tiktok.com/broken/", "translate": False})
    assert resp.status_code == 400
    assert fake_resolver.calls == []
