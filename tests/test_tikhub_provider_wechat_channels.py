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
from app.services.platforms.wechat_channels import WechatChannelsService
from app.services.providers.base import ProviderError, TerminalError, VideoNotFoundError
from app.services.providers.tikhub import TikHubProvider

FIXTURES = Path(__file__).parent / "fixtures" / "wechat_channels"

OBJECT_ID = "14998022876670594427"
SPH_CODE = "AOzokRxWHz"
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
    expected_url = f"/api/stream/wechat_channels/{SPH_CODE}"
    assert info.video_url == expected_url
    assert info.video_id == OBJECT_ID
    # 同一 share_url 两次解析必须得到完全相同的相对路径（缓存安全：不含 host）
    again = WechatChannelsService("k", "b")._parse_response(load("detail"))
    assert again is not None and again.video_url == info.video_url
    assert "://" not in info.video_url


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
    assert info.raw_data["data"]["cover_img_url"] == "REDACTED"
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
    assert TikHubProvider._classify_wechat_channels(load("detail")) == ("ok", {})


@pytest.mark.parametrize("payload", [
    load("empty"),
    load("image_note"),
    {},
    {"data": None},
    "not-a-dict",
])
def test_classify_retryable(payload):
    decision, _reason = TikHubProvider._classify_wechat_channels(payload)
    assert decision == "retryable"


def test_classify_never_terminal():
    for payload in (load("empty"), load("image_note"), load("detail")):
        decision, _reason = TikHubProvider._classify_wechat_channels(payload)
        assert decision != "terminal"


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
    decision, _reason = TikHubProvider._classify_wechat_channels(missing_media)
    assert decision == "ok"
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
    # 瞬态 retryable 由引擎重试：单端点链 3 次尝试
    assert [c["name"] for c in calls] == ["fetch_video_detail"] * 3


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


async def test_download_media_uses_sph_share_url(monkeypatch):
    provider = TikHubProvider()
    seen = {}

    async def capture(self, name, path, params, per_timeout):
        seen[name] = params
        return load("detail")

    monkeypatch.setattr(TikHubProvider, "_call_endpoint", capture)
    media = await provider.fetch_wechat_channels_media(SPH_CODE)

    assert media["file_size"] > 0
    assert seen["fetch_video_detail"] == {"share_url": SHARE_URL, "raw": False}
    assert "object_id" not in seen["fetch_video_detail"]


@pytest.mark.parametrize(
    "share_url",
    [None, "https://example.com/sph/AOzokRxWHz", "https://weixin.qq.com/sph/a-b"],
)
def test_parse_response_rejects_missing_or_invalid_sph_share_url(share_url):
    payload = load("detail")
    if share_url is None:
        payload["params"].pop("share_url")
    else:
        payload["params"]["share_url"] = share_url
    assert WechatChannelsService("k", "b")._parse_response(payload) is None


async def test_chain_total_budget_timeout(monkeypatch):
    provider = TikHubProvider()
    monkeypatch.setattr(TikHubProvider, "WECHAT_CHANNELS_TOTAL_BUDGET", 0.05)

    async def slow_call(self, name, path, params, per_timeout):
        await asyncio.sleep(1)
        return load("detail")

    monkeypatch.setattr(TikHubProvider, "_call_endpoint", slow_call)
    with pytest.raises(ProviderError, match="timed out"):
        await provider.fetch_video_info("wechat_channels", OBJECT_ID, SHARE_URL)


# ----------------------------- 瞬态重试 + 归因（#34 + #33） -----------------------------

async def _nosleep(monkeypatch):
    async def no_sleep(_delay):
        return None
    monkeypatch.setattr("app.services.providers.tikhub.asyncio.sleep", no_sleep)


async def test_chain_transient_then_hit_records_three_attempts(monkeypatch):
    responses = [load("empty"), load("empty"), load("detail")]
    calls = []
    async def flaky(self, name, path, params, per_timeout):
        calls.append(name)
        return responses[len(calls) - 1]
    monkeypatch.setattr(TikHubProvider, "_call_endpoint", flaky)
    await _nosleep(monkeypatch)
    data = await TikHubProvider().fetch_video_info("wechat_channels", OBJECT_ID, SHARE_URL)
    assert data == load("detail") and len(calls) == 3


async def test_chain_exhausted_raises_not_found_with_attempts(monkeypatch):
    calls = []
    async def always_empty(self, name, path, params, per_timeout):
        calls.append(name)
        return load("empty")
    monkeypatch.setattr(TikHubProvider, "_call_endpoint", always_empty)
    await _nosleep(monkeypatch)
    with pytest.raises(VideoNotFoundError) as ei:
        await TikHubProvider().fetch_video_info("wechat_channels", OBJECT_ID, SHARE_URL)
    assert type(ei.value) is VideoNotFoundError and len(calls) == 3
    assert "attempts" in str(ei.value) and "data_missing" in str(ei.value)
    assert str(ei.value).count("'decision': 'retryable'") == 3


def test_wechat_failure_tri_states_are_distinct():
    d0, r0 = TikHubProvider._classify_wechat_channels(load("empty"))
    d1, r1 = TikHubProvider._classify_wechat_channels({"data": {"id": OBJECT_ID, "object_type": 1}})
    r2 = TikHubProvider._error_body_reason(400, {"code": 400, "message": "invalid object_id"})
    assert (d0, d1) == ("retryable", "retryable")
    assert r0.get("reason") == "data_missing"
    assert r1.get("reason") == "object_type_mismatch" and r1["object_type"] == 1
    assert r2["reason"] == "error_body" and r2["http_status"] == 400
    assert len({r0["reason"], r1["reason"], r2["reason"]}) == 3


@pytest.mark.parametrize(
    ("object_type", "expected_value", "expected_type"),
    [
        ("1", "1", None),
        ("video", "video", None),
        ({"full_url": "SECRET"}, None, "dict"),
        ("x" * 33, None, "str"),
        ("bad value", None, "str"),
        ('"quoted"', None, "str"),
        (True, None, "bool"),
        (1.5, None, "float"),
        (None, None, "NoneType"),
    ],
)
def test_wechat_object_type_keeps_only_bounded_safe_scalars(
    object_type, expected_value, expected_type
):
    reason = WechatChannelsService.describe_failure(
        {"data": {"object_type": object_type}}
    )
    assert reason["reason"] == "object_type_mismatch"
    if expected_type is None:
        assert reason["object_type"] == expected_value
        assert "object_type_type" not in reason
    else:
        assert reason["object_type_type"] == expected_type
        assert "object_type" not in reason


async def test_chain_http_status_body_records_error_body(monkeypatch):
    from app.services.providers.tikhub import EndpointHttpError
    calls = []
    async def error_body(self, name, path, params, per_timeout):
        calls.append(name)
        raise EndpointHttpError(name, 400, {"code": 400, "message": "invalid object_id"})
    monkeypatch.setattr(TikHubProvider, "_call_endpoint", error_body)
    await _nosleep(monkeypatch)
    with pytest.raises(VideoNotFoundError) as ei:
        await TikHubProvider().fetch_video_info("wechat_channels", OBJECT_ID, SHARE_URL)
    assert len(calls) == 3 and "error_body" in str(ei.value) and "400" in str(ei.value)


async def test_chain_http_error_parse_failed_keeps_error_body_attribution(monkeypatch):
    from app.services.providers.tikhub import EndpointHttpError
    body = {
        "code": 400,
        "message": "missing media",
        "data": {"id": OBJECT_ID, "object_type": 0},
    }

    async def error_body(self, name, path, params, per_timeout):
        raise EndpointHttpError(name, 400, body)

    monkeypatch.setattr(TikHubProvider, "_call_endpoint", error_body)
    with pytest.raises(VideoNotFoundError) as ei:
        await TikHubProvider().fetch_video_info("wechat_channels", OBJECT_ID, SHARE_URL)
    text = str(ei.value)
    assert "'decision': 'parse_failed'" in text
    assert "'reason': 'error_body'" in text
    assert "'http_status': 400" in text
    assert "'upstream_message': 'missing media'" in text


async def test_wechat_bool_object_type_is_mismatch_and_retries(monkeypatch):
    payload = {"data": {"object_type": False, "id": "1"}}
    assert WechatChannelsService.describe_failure(payload) == {
        "reason": "object_type_mismatch",
        "object_type_type": "bool",
    }
    assert WechatChannelsService.extract_data(payload) is None

    provider, calls = _provider_with(monkeypatch, {"fetch_video_detail": payload})
    await _nosleep(monkeypatch)
    with pytest.raises(VideoNotFoundError) as ei:
        await provider.fetch_video_info("wechat_channels", OBJECT_ID, SHARE_URL)
    assert len(calls) == 3
    text = str(ei.value)
    assert text.count("'decision': 'retryable'") == 3
    assert text.count("'object_type_type': 'bool'") == 3


async def test_chain_desensitization_error_string_and_api(authed_client, monkeypatch):
    import copy
    import app.api.resolve as resolve_mod
    from app.services.providers.tikhub import EndpointHttpError
    secret_full = "https://secret-cdn.example.com/video-SECRET123.mp4"
    secret_key = "SECRET-DECODE-KEY-xyz"
    secret_token = "SECRET-URL-TOKEN-abc"
    payload = copy.deepcopy(load("empty"))
    payload["message"] = "upstream says invalid object_id marker-MSG42"
    payload["data"] = {"full_url": secret_full, "decode_key": secret_key, "url_token": secret_token}
    _d2, r2 = TikHubProvider._classify_wechat_channels(
        {"code": 200, "data": {"id": "1", "object_type": payload["data"]}})
    assert r2.get("reason") == "object_type_mismatch"
    assert "object_type" not in r2 and r2.get("object_type_type") == "dict"
    async def error_body(self, name, path, params, per_timeout):
        raise EndpointHttpError(name, 400, payload)
    monkeypatch.setattr(TikHubProvider, "_call_endpoint", error_body)
    await _nosleep(monkeypatch)
    with pytest.raises(VideoNotFoundError) as ei:
        await TikHubProvider().fetch_video_info("wechat_channels", OBJECT_ID, SHARE_URL)
    text = str(ei.value)
    assert secret_full not in text and secret_key not in text and secret_token not in text
    assert "marker-MSG42" in text
    resolve_mod._video_resolver = None
    resp = authed_client.post("/api/resolve", json={"url": SHARE_URL, "translate": False})
    blob = resp.text
    assert secret_full not in blob and secret_key not in blob and secret_token not in blob
    assert "marker-MSG42" in blob
    # F2 链路：200 + dict object_type 走三跳耗尽，异常串同样零凭据
    payload2 = {"code": 200, "data": {"id": "1", "object_type": payload["data"]}}
    async def mismatch2(self, name, path, params, per_timeout):
        return payload2
    monkeypatch.setattr(TikHubProvider, "_call_endpoint", mismatch2)
    await _nosleep(monkeypatch)
    with pytest.raises(VideoNotFoundError) as ei2:
        await TikHubProvider().fetch_video_info("wechat_channels", OBJECT_ID, SHARE_URL)
    assert secret_key not in str(ei2.value) and secret_full not in str(ei2.value)


async def test_chain_provider_error_not_retried(monkeypatch):
    calls = []
    async def boom(self, name, path, params, per_timeout):
        calls.append(name)
        raise ProviderError("boom")
    monkeypatch.setattr(TikHubProvider, "_call_endpoint", boom)
    with pytest.raises(VideoNotFoundError) as ei:
        await TikHubProvider().fetch_video_info("wechat_channels", OBJECT_ID, SHARE_URL)
    assert len(calls) == 1 and "http_error" in str(ei.value)


async def test_chain_attempt_provider_error_is_not_chain_timeout(monkeypatch):
    """attempt 级 ProviderError 记为 http_error，不冒充链级超时。"""
    calls = []

    async def raise_http_error(self, name, path, params, per_timeout):
        calls.append(name)
        raise ProviderError(f"{name} HTTP 500")

    monkeypatch.setattr(TikHubProvider, "_call_endpoint", raise_http_error)
    with pytest.raises(VideoNotFoundError) as ei:
        await TikHubProvider().fetch_video_info("wechat_channels", OBJECT_ID, SHARE_URL)
    text = str(ei.value)
    assert type(ei.value) is VideoNotFoundError
    assert calls == ["fetch_video_detail"]
    assert "decision': 'http_error'" in text
    assert "fetch_video_detail HTTP 500" in text
    assert "endpoint chain timed out" not in text


async def test_chain_budget_timeout_raises_provider_error_not_not_found(monkeypatch):
    """总预算在某次尝试中途触发 → ProviderError(timed out)，不得改判成 VideoNotFoundError。"""
    import asyncio as _aio

    async def slow_empty(self, name, path, params, per_timeout):
        await _aio.sleep(0.4)
        return {"code": 200, "data": None}
    monkeypatch.setattr(TikHubProvider, "_call_endpoint", slow_empty)
    monkeypatch.setattr(TikHubProvider, "WECHAT_CHANNELS_RETRY_BACKOFF", 0)
    monkeypatch.setattr(TikHubProvider, "WECHAT_CHANNELS_TOTAL_BUDGET", 0.7)
    monkeypatch.setattr(TikHubProvider, "WECHAT_CHANNELS_PER_ENDPOINT_TIMEOUT", 0.05)
    with pytest.raises(ProviderError) as ei:
        await TikHubProvider().fetch_video_info("wechat_channels", OBJECT_ID, SHARE_URL)
    assert type(ei.value) is ProviderError
    assert "timed out" in str(ei.value)
    assert not isinstance(ei.value, VideoNotFoundError)


async def test_chain_budget_does_not_start_attempt_that_does_not_fit(monkeypatch):
    """剩余预算装不下完整下一次尝试时，只执行当前尝试并走未找到。"""
    calls = []

    async def slow_empty(self, name, path, params, per_timeout):
        calls.append(name)
        await asyncio.sleep(0.2)
        return {"code": 200, "data": None}

    monkeypatch.setattr(TikHubProvider, "_call_endpoint", slow_empty)
    monkeypatch.setattr(TikHubProvider, "WECHAT_CHANNELS_TOTAL_BUDGET", 0.7)
    monkeypatch.setattr(TikHubProvider, "WECHAT_CHANNELS_PER_ENDPOINT_TIMEOUT", 0.6)
    monkeypatch.setattr(TikHubProvider, "WECHAT_CHANNELS_RETRY_BACKOFF", 0)
    with pytest.raises(VideoNotFoundError) as ei:
        await TikHubProvider().fetch_video_info("wechat_channels", OBJECT_ID, SHARE_URL)
    assert len(calls) == 1
    assert type(ei.value) is VideoNotFoundError
    assert "timed out" not in str(ei.value)


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
            video_url=f"http://localhost:8000/api/stream/wechat_channels/{SPH_CODE}",
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
