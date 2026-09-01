"""
TikHub 微信视频号解析与单端点链测试。

覆盖：extract_data / _parse_response 字段映射、classify 两态、has_playable 与
_parse_response 同源、build_params 逐字段硬断言（含 raw: false）、空响应/缺 media
视为 retryable、全失败抛型、总预算超时。全部 mock _call_endpoint，无真实网络。
"""

import asyncio
import json
from pathlib import Path

import pytest

from app.api.resolve import _build_response
from app.core.config import settings
from app.services.platforms.wechat_channels import WechatChannelsService
from app.services.providers.base import ProviderError, TerminalError, VideoNotFoundError
from app.services.providers.tikhub import TikHubProvider

FIXTURES = Path(__file__).parent / "fixtures" / "wechat_channels"

OBJECT_ID = "14998022876670594427"
SHARE_URL = "https://weixin.qq.com/sph/AOzokRxWHz"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# ----------------------------- 解析器 -----------------------------

def test_extract_data_ok_for_video():
    node = WechatChannelsService.extract_data(load("detail"))
    assert isinstance(node, dict)
    assert node["id"] == OBJECT_ID
    assert node["object_type"] == 0


def test_extract_data_none_for_empty_and_image():
    assert WechatChannelsService.extract_data(load("empty")) is None
    assert WechatChannelsService.extract_data(load("image_note")) is None
    assert WechatChannelsService.extract_data({}) is None
    assert WechatChannelsService.extract_data({"data": None}) is None


def test_parse_response_maps_sample_fields():
    info = WechatChannelsService("k", "b")._parse_response(load("detail"))
    assert info is not None
    assert info.platform == "wechat_channels"
    assert info.video_id == OBJECT_ID
    assert isinstance(info.video_id, str)
    assert info.title == "对谈张笑宇：AI重塑组织与生活方式"
    assert info.description == "对谈张笑宇：AI重塑组织与生活方式"
    assert info.author_name == "晓辉博士"
    assert info.author_id.startswith("v2_060000231003b20f")
    assert isinstance(info.author_id, str)
    assert info.like_count == 42
    assert info.collect_count == 61
    assert info.share_count == 171
    assert info.comment_count == 8
    assert info.view_count is None
    assert info.duration == 7352
    assert info.width == 912
    assert info.height == 1920
    assert info.create_time is not None
    assert int(info.create_time.timestamp()) == 1787903651
    expected_url = (
        f"{settings.PUBLIC_BASE_URL.rstrip('/')}/api/stream/wechat_channels/{OBJECT_ID}"
    )
    assert info.video_url == expected_url
    # 同一 object_id 两次解析必须得到完全相同的 video_url（缓存安全）
    again = WechatChannelsService("k", "b")._parse_response(load("detail"))
    assert again is not None and again.video_url == info.video_url


def test_parse_response_redacts_credentials_in_raw_data():
    info = WechatChannelsService("k", "b")._parse_response(load("detail"))
    assert info is not None and info.raw_data is not None
    media = info.raw_data["data"]["media"]
    assert media["url"] == "REDACTED"
    assert media["url_token"] == "REDACTED"
    assert media["full_url"] == "REDACTED"
    assert media["decode_key"] == "REDACTED"
    assert media["cover_url"] == "REDACTED"
    assert media["cover_url_token"] == "REDACTED"
    dumped = json.dumps(info.to_dict())
    assert "decode_key" in dumped  # 键还在，值必须是占位
    assert media["decode_key"] == "REDACTED"


def test_build_response_excludes_credential_fields():
    info = WechatChannelsService("k", "b")._parse_response(load("detail"))
    resp = _build_response(info)
    payload = resp.model_dump()
    blob = json.dumps(payload)
    assert "decode_key" not in blob
    assert "url_token" not in blob
    assert "full_url" not in blob
    assert "raw_data" not in payload
    assert resp.video_id == OBJECT_ID
    assert resp.author_id == info.author_id
    assert resp.view_count is None


def test_parse_response_none_without_media_or_non_video():
    assert WechatChannelsService("k", "b")._parse_response(load("empty")) is None
    assert WechatChannelsService("k", "b")._parse_response(load("image_note")) is None
    missing_media = {"data": {"id": OBJECT_ID, "object_type": 0, "title": "x"}}
    assert WechatChannelsService("k", "b")._parse_response(missing_media) is None


# ----------------------------- 分类器 -----------------------------

def test_classify_ok_for_video():
    assert TikHubProvider._classify_wechat_channels(load("detail")) == "ok"


@pytest.mark.parametrize("payload", [
    load("empty"),
    load("image_note"),
    {},
    {"data": None},
    "not-a-dict",
])
def test_classify_retryable(payload):
    assert TikHubProvider._classify_wechat_channels(payload) == "retryable"


def test_classify_never_terminal():
    assert TikHubProvider._classify_wechat_channels(load("empty")) != "terminal"
    assert TikHubProvider._classify_wechat_channels(load("image_note")) != "terminal"
    assert TikHubProvider._classify_wechat_channels(load("detail")) != "terminal"


# ----------------------------- has_playable 同源 -----------------------------

def test_has_playable_uses_parse_response(monkeypatch):
    """has_playable 必须走 WechatChannelsService._parse_response，禁止另写一套判断。"""
    calls = []
    original = WechatChannelsService._parse_response

    def wrapped(self, data):
        calls.append(data)
        return original(self, data)

    monkeypatch.setattr(WechatChannelsService, "_parse_response", wrapped)
    provider = TikHubProvider()
    assert provider._wechat_channels_has_playable(load("detail")) is True
    assert provider._wechat_channels_has_playable(load("empty")) is False
    assert provider._wechat_channels_has_playable(load("image_note")) is False
    missing_media = {"data": {"id": OBJECT_ID, "object_type": 0, "title": "x"}}
    assert provider._wechat_channels_has_playable(missing_media) is False
    assert len(calls) == 4


def test_missing_media_is_ok_then_not_playable():
    """缺 media：extract_data 仍能定位节点（classify=ok），解析失败 → has_playable False。"""
    missing_media = {"data": {"id": OBJECT_ID, "object_type": 0, "title": "x"}}
    assert TikHubProvider._classify_wechat_channels(missing_media) == "ok"
    assert TikHubProvider()._wechat_channels_has_playable(missing_media) is False


# ----------------------------- 端点链 -----------------------------

def _provider_with(monkeypatch, mapping):
    provider = TikHubProvider()
    calls = []

    async def fake_call(self, name, path, params, per_timeout):
        calls.append({"name": name, "path": path, "params": params})
        resp = mapping[name]
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(TikHubProvider, "_call_endpoint", fake_call)
    return provider, calls


async def test_chain_hit_returns_detail(monkeypatch):
    provider, calls = _provider_with(monkeypatch, {"fetch_video_detail": load("detail")})
    data = await provider.fetch_video_info("wechat_channels", OBJECT_ID, SHARE_URL)
    assert data == load("detail")
    assert [c["name"] for c in calls] == ["fetch_video_detail"]
    assert calls[0]["path"] == "/api/v1/wechat_channels/v2/fetch_video_detail"


async def test_chain_empty_raises_not_found_not_terminal(monkeypatch):
    provider, calls = _provider_with(monkeypatch, {"fetch_video_detail": load("empty")})
    with pytest.raises(VideoNotFoundError) as ei:
        await provider.fetch_video_info("wechat_channels", OBJECT_ID, SHARE_URL)
    assert not isinstance(ei.value, TerminalError)
    assert [c["name"] for c in calls] == ["fetch_video_detail"]


async def test_chain_image_note_raises_not_found_not_terminal(monkeypatch):
    provider, calls = _provider_with(monkeypatch, {"fetch_video_detail": load("image_note")})
    with pytest.raises(VideoNotFoundError) as ei:
        await provider.fetch_video_info("wechat_channels", OBJECT_ID, SHARE_URL)
    assert not isinstance(ei.value, TerminalError)
    assert calls


async def test_chain_missing_media_raises_not_found(monkeypatch):
    missing_media = {"data": {"id": OBJECT_ID, "object_type": 0, "title": "x"}}
    provider, _calls = _provider_with(monkeypatch, {"fetch_video_detail": missing_media})
    with pytest.raises(VideoNotFoundError) as ei:
        await provider.fetch_video_info("wechat_channels", OBJECT_ID, SHARE_URL)
    assert not isinstance(ei.value, TerminalError)


async def test_chain_http_error_then_not_found(monkeypatch):
    provider, calls = _provider_with(
        monkeypatch, {"fetch_video_detail": ProviderError("boom")}
    )
    with pytest.raises(VideoNotFoundError):
        await provider.fetch_video_info("wechat_channels", OBJECT_ID, SHARE_URL)
    assert [c["name"] for c in calls] == ["fetch_video_detail"]


async def test_unsupported_platform_still_raises(monkeypatch):
    """新增分支不得破坏未接链平台的防呆 raise。"""
    provider = TikHubProvider()
    with pytest.raises(ProviderError, match="not supported"):
        await provider.fetch_video_info("not_a_platform", "id", "http://x")


# ----------------- 逐字段 param 构造（防静默 422） -----------------

async def test_build_params_with_object_id(monkeypatch):
    provider = TikHubProvider()
    seen = {}

    async def capture(self, name, path, params, per_timeout):
        seen[name] = params
        return load("empty")

    monkeypatch.setattr(TikHubProvider, "_call_endpoint", capture)
    with pytest.raises(VideoNotFoundError):
        await provider.fetch_video_info("wechat_channels", OBJECT_ID, SHARE_URL)

    assert seen["fetch_video_detail"] == {"object_id": OBJECT_ID, "raw": False}
    assert seen["fetch_video_detail"]["raw"] is False


async def test_build_params_with_share_url_only(monkeypatch):
    provider = TikHubProvider()
    seen = {}

    async def capture(self, name, path, params, per_timeout):
        seen[name] = params
        return load("empty")

    monkeypatch.setattr(TikHubProvider, "_call_endpoint", capture)
    with pytest.raises(VideoNotFoundError):
        await provider.fetch_video_info("wechat_channels", "", SHARE_URL)

    assert seen["fetch_video_detail"] == {"share_url": SHARE_URL, "raw": False}
    assert "object_id" not in seen["fetch_video_detail"]
    assert seen["fetch_video_detail"]["raw"] is False


async def test_chain_total_budget_timeout(monkeypatch):
    provider = TikHubProvider()
    monkeypatch.setattr(TikHubProvider, "WECHAT_CHANNELS_TOTAL_BUDGET", 0.05)

    async def slow_call(self, name, path, params, per_timeout):
        await asyncio.sleep(1)
        return load("detail")

    monkeypatch.setattr(TikHubProvider, "_call_endpoint", slow_call)
    with pytest.raises(ProviderError, match="timed out"):
        await provider.fetch_video_info("wechat_channels", OBJECT_ID, SHARE_URL)


async def test_adapter_and_platforms_endpoint(authed_client, monkeypatch):
    """GET /api/platforms 含 wechat_channels: [tikhub]；adapter 能产出 VideoInfo。"""
    from app.services.adapters.tikhub_adapter import TikHubAdapter

    info = TikHubAdapter("k", "b").adapt(load("detail"), "wechat_channels", OBJECT_ID)
    assert info is not None
    assert info.provider == "tikhub"
    assert info.video_id == OBJECT_ID
    assert info.view_count is None

    resp = authed_client.get("/api/platforms")
    assert resp.status_code == 200
    platforms = resp.json()["platforms"]
    assert platforms["wechat_channels"] == ["tikhub"]


async def test_resolve_fallback_without_video_id(authed_client, monkeypatch):
    """sph 短链 parse 出平台但无 id：路由放行，链用 share_url 兜底后回填 object_id。"""
    import app.api.resolve as resolve_mod
    from app.services.platforms.base import VideoInfo
    from app.services.video_resolver import VideoResolver

    async def fake_resolve(self, platform, video_id, original_url, force_refresh=False, use_hybrid=False):
        assert platform == "wechat_channels"
        assert video_id == ""
        assert original_url == SHARE_URL
        info = VideoInfo(
            video_id=OBJECT_ID, platform="wechat_channels", title="t",
            description="中文描述", author_name="a", author_id="a",
            video_url=f"http://localhost:8000/api/stream/wechat_channels/{OBJECT_ID}",
            width=912, height=1920, provider="tikhub", view_count=None,
        )
        return info, "tikhub"

    monkeypatch.setattr(VideoResolver, "resolve", fake_resolve)
    # 清掉路由层单例，确保用到带 wechat_channels 链的实例
    resolve_mod._video_resolver = None
    resp = authed_client.post("/api/resolve", json={"url": SHARE_URL, "translate": False})
    assert resp.status_code == 200 and resp.json()["success"]
    assert resp.json()["data"]["video_id"] == OBJECT_ID
    assert resp.json()["data"]["platform"] == "wechat_channels"
    assert resp.json()["data"]["view_count"] is None
