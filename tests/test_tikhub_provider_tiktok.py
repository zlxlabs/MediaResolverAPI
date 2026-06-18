"""
TikHub TikTok 多级降级链测试

覆盖：同源 aweme 分类器复用但「有 cobalt 兜底 → 不出终态」（评审 Issue 6）、
端点链命中/降级/全失败、逐端点 param 构造、has_playable 走 TikTokService
（保 play_addr_h264 优先，Issue 7）、整链总预算超时。全部 mock _call_endpoint。
"""

import asyncio
import json
from pathlib import Path

import pytest

from app.services.providers.base import ProviderError, VideoNotFoundError
from app.services.providers.tikhub import TikHubProvider
from app.services.platforms.tiktok import TikTokService

FIXTURES = Path(__file__).parent / "fixtures" / "tiktok"

AID = "7636256306424253729"
URL = "https://www.tiktok.com/@crescentmart/video/7636256306424253729"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# ----------------------------- 分类器 -----------------------------

@pytest.mark.parametrize("fixture", ["app_v3_video", "app_v3_v2_video"])
def test_classify_ok(fixture):
    assert TikHubProvider._classify_tiktok(load(fixture)) == "ok"


def test_classify_empty_retryable():
    assert TikHubProvider._classify_tiktok(load("empty")) == "retryable"


def test_classify_filter_list_is_retryable_not_terminal():
    """
    回归铁律（Issue 6）：TikTok 有 cobalt 兜底，filter_list 终态 reason 必须降级为
    retryable，让链走完后落到 cobalt——绝不能像抖音那样短路。
    """
    assert TikHubProvider._classify_tiktok(load("filtered")) == "retryable"
    # 对照：同样的响应在抖音（单源）会判终态
    assert TikHubProvider._classify_douyin(load("filtered")) == "terminal"


# --------------------- has_playable 用 TikTokService（Issue 7） ---------------------

def test_has_playable_uses_tiktok_parser_h264():
    """TikTokService 优先取 play_addr_h264（DouyinService 不会），解析出直链。"""
    info = TikTokService("k", "b")._parse_response(load("app_v3_video"))
    assert info is not None
    assert info.video_url.endswith("test_app_v3.mp4")
    assert info.quality == "h264_original"


# ----------------------------- 端点降级链 -----------------------------

def _provider_with(monkeypatch, mapping):
    provider = TikHubProvider()
    calls = []

    async def fake_call(self, name, path, params, per_timeout):
        calls.append(name)
        resp = mapping[name]
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(TikHubProvider, "_call_endpoint", fake_call)
    return provider, calls


async def test_chain_app_v3_hit_short_circuits(monkeypatch):
    provider, calls = _provider_with(monkeypatch, {"app_v3": load("app_v3_video")})
    data = await provider.fetch_video_info("tiktok", AID, URL)
    assert data == load("app_v3_video")
    assert calls == ["app_v3"]


async def test_chain_falls_through_to_v2(monkeypatch):
    provider, calls = _provider_with(
        monkeypatch, {"app_v3": load("empty"), "app_v3_v2": load("app_v3_v2_video")}
    )
    data = await provider.fetch_video_info("tiktok", AID, URL)
    assert data == load("app_v3_v2_video")
    assert calls == ["app_v3", "app_v3_v2"]


async def test_chain_filter_list_falls_through_not_short_circuit(monkeypatch):
    """filter_list 不短路（Issue 6），继续试后续端点，最终命中 v3。"""
    provider, calls = _provider_with(
        monkeypatch,
        {
            "app_v3": load("filtered"),
            "app_v3_v2": load("filtered"),
            "app_v3_v3": load("app_v3_video"),
        },
    )
    data = await provider.fetch_video_info("tiktok", AID, URL)
    assert data == load("app_v3_video")
    assert calls == ["app_v3", "app_v3_v2", "app_v3_v3"]


async def test_chain_all_fail_raises_not_found(monkeypatch):
    provider, calls = _provider_with(
        monkeypatch,
        {"app_v3": load("empty"), "app_v3_v2": load("empty"), "app_v3_v3": load("empty")},
    )
    with pytest.raises(VideoNotFoundError):
        await provider.fetch_video_info("tiktok", AID, URL)
    assert calls == ["app_v3", "app_v3_v2", "app_v3_v3"]


async def test_chain_http_error_continues(monkeypatch):
    provider, calls = _provider_with(
        monkeypatch,
        {"app_v3": ProviderError("boom"), "app_v3_v2": load("app_v3_v2_video")},
    )
    data = await provider.fetch_video_info("tiktok", AID, URL)
    assert data == load("app_v3_v2_video")
    assert calls == ["app_v3", "app_v3_v2"]


# ----------------- 逐端点 param 构造（硬要求 T11） -----------------

async def test_param_construction_per_endpoint(monkeypatch):
    """三端点都必须收到 {aweme_id}。"""
    provider = TikHubProvider()
    seen = {}

    async def capture(self, name, path, params, per_timeout):
        seen[name] = params
        return load("empty")

    monkeypatch.setattr(TikHubProvider, "_call_endpoint", capture)
    with pytest.raises(VideoNotFoundError):
        await provider.fetch_video_info("tiktok", AID, URL)

    assert seen["app_v3"] == {"aweme_id": AID}
    assert seen["app_v3_v2"] == {"aweme_id": AID}
    assert seen["app_v3_v3"] == {"aweme_id": AID}


def test_has_playable_suppresses_error_file_writes(monkeypatch):
    """
    评审 Issue 12：校验路径解析失败不得落盘（降级链每端点都写会放大写盘）。
    _tiktok_has_playable 必须置 suppress_error_save，使 _save_error_response 跳过写文件。
    """
    provider = TikHubProvider()
    wrote = []
    # 若真去落盘，open 会被调用——这里断言根本不应触达落盘逻辑
    monkeypatch.setattr(
        "app.services.platforms.tiktok.open",
        lambda *a, **k: wrote.append(a) or (_ for _ in ()).throw(AssertionError("不应写文件")),
        raising=False,
    )
    # aweme_detail 存在但无 video → 解析失败路径（会触发 _save_error_response）
    bad = {"data": {"aweme_detail": {"aweme_id": "x", "desc": "no video"}}}
    assert provider._tiktok_has_playable(bad) is False
    assert wrote == []  # 抑制生效，零写盘


async def test_chain_total_budget_timeout(monkeypatch):
    provider = TikHubProvider()
    monkeypatch.setattr(TikHubProvider, "TIKTOK_TOTAL_BUDGET", 0.05)

    async def slow_call(self, name, path, params, per_timeout):
        await asyncio.sleep(1)
        return load("app_v3_video")

    monkeypatch.setattr(TikHubProvider, "_call_endpoint", slow_call)
    with pytest.raises(ProviderError, match="timed out"):
        await provider.fetch_video_info("tiktok", AID, URL)
