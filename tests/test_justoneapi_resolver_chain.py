"""
JustOneApi 责任链 + 信封对齐 + resolve 端到端测试

覆盖（对应卡面约束 2/4/5/9）：
- token 非空时默认链顺序：抖音/快手/小红书/视频号 = tikhub,justoneapi；
  TikTok/IG/YouTube/Twitter = tikhub,cobalt,justoneapi；PROVIDER_PRIORITY 可覆盖；
- 适配器信封对齐：快手只改写 photos[0].streamManifest → manifest，平台 service
  不改一行；七平台都能选出可播放直链；
- E2E（只打桩 provider 最外层 HTTP，经 POST /api/resolve）：TikHub 全败 + Cobalt
  全败 + JOA code==0 → success 且 provider=justoneapi，七平台 video_url 为第三方
  CDN，视频号 video_url 仍为本服务流端点。

TikHub 桩打在 TikHubProvider._call_endpoint（仓内既有 E2E 惯例，如
test_resolve_wechat_channels_url），Cobalt 桩打在 fetch_video_info，JOA 桩打在
JustOneApiProvider._get（其内部才是 httpx transport）。
"""

import asyncio
import json
from pathlib import Path

import pytest

import app.api.resolve as resolve_mod
from app.core.config import settings
from app.services.adapters.justoneapi_adapter import JustOneApiAdapter
from app.services.providers.base import ProviderError
from app.services.providers.cobalt import CobaltProvider
from app.services.providers.justoneapi import JustOneApiProvider
from app.services.providers.tikhub import TikHubProvider
from app.services.video_resolver import VideoResolver

FIXTURES = Path(__file__).parent / "fixtures" / "justoneapi"

JOA_PATH_FIXTURES = {
    "/api/douyin/get-video-detail/v2": "douyin_video",
    "/api/kuaishou/get-video-detail/v2": "kuaishou_video",
    "/api/xiaohongshu/get-note-detail/v6": "xiaohongshu_note",
    "/api/tiktok/get-post-detail/v1": "tiktok_post",
    "/api/youtube/get-video-detail/v1": "youtube_video",
    "/api/instagram/get-post-detail/v1": "instagram_post",
    "/api/twitter/get-post-detail/v1": "twitter_post",
    "/api/weixin-channels/get-video-basic-info/v1": "wechat_basic_info",
    "/api/weixin-channels/get-video-download-url/v1": "wechat_download_url",
}


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# ----------------------------- 默认链顺序（约束 2） -----------------------------

@pytest.fixture()
def joa_enabled_resolver(justoneapi_token):
    """token 非空时新建 resolver（链在 __init__ 一次性构建）。"""
    return VideoResolver()


def test_default_chain_four_platforms_tikhub_then_justoneapi(joa_enabled_resolver):
    for platform in ("douyin", "kuaishou", "xiaohongshu", "wechat_channels"):
        assert joa_enabled_resolver.get_providers_for_platform(platform) == [
            "tikhub",
            "justoneapi",
        ]


def test_default_chain_cobalt_platforms_three_sources(joa_enabled_resolver):
    for platform in ("tiktok", "instagram", "youtube", "twitter"):
        assert joa_enabled_resolver.get_providers_for_platform(platform) == [
            "tikhub",
            "cobalt",
            "justoneapi",
        ]


def test_other_platforms_unchanged(joa_enabled_resolver):
    assert joa_enabled_resolver.get_providers_for_platform("pinterest") == ["cobalt"]
    assert joa_enabled_resolver.get_providers_for_platform("facebook") == ["cobalt"]
    assert joa_enabled_resolver.get_providers_for_platform("twitter_space") == ["ytdlp"]


def test_priority_override_still_wins(justoneapi_token, monkeypatch):
    """PROVIDER_PRIORITY_TIKTOK=cobalt,tikhub：覆盖仍生效，justoneapi 不被强插。"""
    monkeypatch.setattr(settings, "PROVIDER_PRIORITY_TIKTOK", "cobalt,tikhub")
    try:
        resolver = VideoResolver()
        assert resolver.get_providers_for_platform("tiktok") == ["cobalt", "tikhub"]
    finally:
        monkeypatch.setattr(settings, "PROVIDER_PRIORITY_TIKTOK", "")


# ----------------------------- 信封对齐（约束 9） -----------------------------

# (fixture, video_id, 入站 URL, JOA 路径, 可播放直链)；视频号 video_url 为 None（本服务流端点）。
PLATFORM_CASES = {
    "douyin": ("douyin_video", "7477059950577978636", "https://www.douyin.com/video/7477059950577978636", "/api/douyin/get-video-detail/v2", "https://cdn.example.test/douyin/video.mp4"),
    "kuaishou": ("kuaishou_video", "3xfdpkqyzabcdef", "https://www.kuaishou.com/short-video/3xfdpkqyzabcdef", "/api/kuaishou/get-video-detail/v2", "https://cdn.example.test/kuaishou/video.mp4"),
    "xiaohongshu": ("xiaohongshu_note", "65f2d1a3000000002201cb3b", "https://www.xiaohongshu.com/explore/65f2d1a3000000002201cb3b", "/api/xiaohongshu/get-note-detail/v6", "https://sns-video.example.test/xhs/note.mp4"),
    "tiktok": ("tiktok_post", "7267439653153003782", "https://www.tiktok.com/@someone/video/7267439653153003782", "/api/tiktok/get-post-detail/v1", "https://cdn.example.test/tiktok/video.mp4"),
    "youtube": ("youtube_video", "dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "/api/youtube/get-video-detail/v1", "https://cdn.example.test/youtube/videoplayback_720.mp4"),
    "instagram": ("instagram_post", "C0abcdefghIj", "https://www.instagram.com/reel/C0abcdefghIj/", "/api/instagram/get-post-detail/v1", "https://cdn.example.test/instagram/video.mp4"),
    "twitter": ("twitter_post", "1700000000000000000", "https://x.com/twauthor/status/1700000000000000000", "/api/twitter/get-post-detail/v1", "https://cdn.example.test/twitter/720x1280.mp4"),
    "wechat_channels": ("wechat_basic_info", "", "https://weixin.qq.com/sph/AOzokRxWHz", "/api/weixin-channels/get-video-basic-info/v1", None),
}


@pytest.mark.parametrize("platform", sorted(PLATFORM_CASES))
def test_adapter_aligns_and_picks_playable_url(platform):
    fixture, video_id, resolve_url, _joa_path, expected_url = PLATFORM_CASES[platform]
    # original_url 喂入站 URL：视频号对齐依赖其中的 sph 短码。
    info = JustOneApiAdapter().adapt(
        load(fixture), platform, video_id, original_url=resolve_url
    )
    assert info is not None
    assert info.provider == "justoneapi"
    assert info.platform == platform
    if platform == "wechat_channels":
        assert info.video_url == "/api/stream/wechat_channels/AOzokRxWHz"
    else:
        assert info.video_url == expected_url
    assert info.video_id


def test_adapter_kuaishou_rewrites_only_stream_manifest():
    """快手 JOA 信封：photos[0].streamManifest → data.photo.manifest，其余键保留。"""
    adapter = JustOneApiAdapter()
    data = load("kuaishou_video")["data"]
    aligned = adapter._align_kuaishou(data)
    photo = aligned["data"]["photo"]
    assert "manifest" in photo and "streamManifest" not in photo
    assert photo["manifest"] == data["photos"][0]["streamManifest"]


def test_adapter_wechat_channels_builds_stream_url():
    """视频号：adapter 按原始分享链接拼 params.share_url，video_url 仍为本服务流端点，
    JOA download_url 不回传客户端。"""
    info = JustOneApiAdapter().adapt(
        load("wechat_basic_info"),
        "wechat_channels",
        "",
        original_url="https://weixin.qq.com/sph/AOzokRxWHz",
    )
    assert info is not None
    assert info.provider == "justoneapi"
    assert info.video_url == "/api/stream/wechat_channels/AOzokRxWHz"
    assert info.video_id == "14998022876670594427"
    raw = json.dumps(info.raw_data)
    assert "media.example.test" not in raw  # download_url 不进 VideoInfo


def test_adapter_audio_mode_not_supported():
    assert (
        JustOneApiAdapter().adapt(
            load("youtube_video"), "youtube", "dQw4w9WgXcQ", download_mode="audio"
        )
        is None
    )


def test_adapter_unsupported_platform_returns_none():
    assert JustOneApiAdapter().adapt({"code": 0, "data": {}}, "pinterest", "1") is None


def test_adapter_missing_data_returns_none():
    assert JustOneApiAdapter().adapt({"code": 0}, "douyin", "1") is None


def test_adapter_tiktok_maps_pascal_case_bitrate():
    info = JustOneApiAdapter().adapt(
        load("tiktok_post"), "tiktok", "7267439653153003782", original_url=""
    )
    assert info.video_url == "https://cdn.example.test/tiktok/video.mp4"  # play_addr_h264 优先
    assert info.width == 1080 and info.height == 1920


def test_adapter_tiktok_uses_bitrate_when_play_addr_struct_missing():
    fixture = load("tiktok_post")
    video = fixture["data"]["video"]
    expected_url = video["bitrateInfo"][0]["PlayAddr"]["UrlList"][0]
    assert expected_url == "https://cdn.example.test/tiktok/h264_720.mp4"
    video.pop("PlayAddrStruct")

    adapter = JustOneApiAdapter()
    aligned = adapter._align_tiktok(fixture["data"])
    assert "play_addr_h264" not in aligned["data"]["aweme_detail"]["video"]
    info = adapter.adapt(fixture, "tiktok", "7267439653153003782")
    assert info is not None
    assert info.video_url
    assert info.video_url == expected_url


def test_adapter_kuaishou_fails_without_stream_manifest():
    fixture = load("kuaishou_video")
    fixture["data"]["photos"][0].pop("streamManifest")
    assert JustOneApiAdapter().adapt(fixture, "kuaishou", "3x") is None


# ----------------------------- E2E：POST /api/resolve（约束 4/5） -----------------------------

@pytest.fixture()
def joa_e2e(monkeypatch, justoneapi_token):
    """TikHub/Cobalt 全败、JOA 回 fixture；重置 resolve 单例让新链生效。"""
    joa_calls: list[dict] = []

    async def tikhub_down(self, name, path, params, per_timeout):
        raise ProviderError("stub: tikhub down")

    async def cobalt_down(self, *args, **kwargs):
        raise ProviderError("stub: cobalt down")

    async def joa_stub(self, path, params):
        joa_calls.append({"path": path, "params": dict(params)})
        fixture = JOA_PATH_FIXTURES.get(path)
        assert fixture, f"unexpected JOA path {path}"
        return load(fixture)

    monkeypatch.setattr(TikHubProvider, "_call_endpoint", tikhub_down)
    monkeypatch.setattr(CobaltProvider, "fetch_video_info", cobalt_down)
    monkeypatch.setattr(JustOneApiProvider, "_get", joa_stub)
    monkeypatch.setattr(resolve_mod, "_video_resolver", None)
    yield joa_calls
    resolve_mod._video_resolver = None


@pytest.mark.parametrize("platform", sorted(PLATFORM_CASES))
def test_resolve_falls_back_to_justoneapi(authed_client, joa_e2e, platform):
    fixture, _vid, url, expected_path, expected_video_url = PLATFORM_CASES[platform]
    resp = authed_client.post(
        "/api/resolve",
        json={"url": url, "translate": False, "force_refresh": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True, body
    assert body["data"]["provider"] == "justoneapi"

    paths = [c["path"] for c in joa_e2e]
    assert paths == [expected_path]  # 每平台一次成功只打一枪
    assert all("share-url-transfer" not in p for p in paths)

    video_url = body["data"]["video_url"]
    if platform == "wechat_channels":
        assert video_url.endswith("/api/stream/wechat_channels/AOzokRxWHz")
    else:
        assert video_url == expected_video_url
        assert not video_url.startswith("http://testserver/")
        assert "/api/" not in video_url


def test_wechat_resolve_never_hits_download_endpoint(authed_client, joa_e2e):
    """resolve 级视频号只要元数据：download-url 留给 stream 层，不重复付费。"""
    resp = authed_client.post(
        "/api/resolve",
        json={
            "url": "https://weixin.qq.com/sph/AOzokRxWHz",
            "translate": False,
            "force_refresh": True,
        },
    )
    assert resp.status_code == 200 and resp.json()["success"]
    assert [c["path"] for c in joa_e2e] == [
        "/api/weixin-channels/get-video-basic-info/v1"
    ]


def test_all_sources_down_returns_failure(authed_client, monkeypatch, justoneapi_token):
    """TikHub/Cobalt/JOA 全败：resolve 返回 success=false（JOA 失败当 retryable）。"""
    async def tikhub_down(self, name, path, params, per_timeout):
        raise ProviderError("stub: tikhub down")

    async def cobalt_down(self, *args, **kwargs):
        raise ProviderError("stub: cobalt down")

    async def joa_down(self, path, params):
        raise ProviderError("JustOneAPI business error code=500 (path=x)")

    monkeypatch.setattr(TikHubProvider, "_call_endpoint", tikhub_down)
    monkeypatch.setattr(CobaltProvider, "fetch_video_info", cobalt_down)
    monkeypatch.setattr(JustOneApiProvider, "_get", joa_down)
    monkeypatch.setattr(resolve_mod, "_video_resolver", None)
    try:
        resp = authed_client.post(
            "/api/resolve",
            json={
                "url": "https://www.douyin.com/video/7477059950577978636",
                "translate": False,
                "force_refresh": True,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "justoneapi" in body["error"]
    finally:
        resolve_mod._video_resolver = None
