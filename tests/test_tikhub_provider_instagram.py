"""
TikHub Instagram 多级降级链测试

覆盖：cobalt 兜底平台不出终态（评审 Issue 6/8，非视频/轮播 → retryable 而非短路）、
v1/v2 schema 自适应、端点链命中/降级/全失败、逐端点 param 构造（code_or_url vs
post_url，防静默 422）、整链总预算超时。全部 mock _call_endpoint。
"""

import asyncio
import json
from pathlib import Path

import pytest

from app.services.providers.base import ProviderError, VideoNotFoundError, TerminalError
from app.services.providers.tikhub import TikHubProvider
from app.services.platforms.instagram import InstagramService

FIXTURES = Path(__file__).parent / "fixtures" / "instagram"

CODE = "DZJj5MMsWxA"
URL = "https://www.instagram.com/reels/DZJj5MMsWxA/"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# ----------------------------- 分类器 -----------------------------

@pytest.mark.parametrize("fixture", ["v2_video", "v1_by_url_video", "image_post"])
def test_classify_ok_for_any_envelope(fixture):
    """有 envelope 数据就 ok（含图文帖，交 has_playable 判定，不在分类阶段短路）。"""
    assert TikHubProvider._classify_instagram(load(fixture)) == "ok"


def test_classify_empty_retryable():
    assert TikHubProvider._classify_instagram(load("empty")) == "retryable"


def test_classify_never_terminal_for_image():
    """
    回归铁律（Issue 8）：IG 非视频/轮播绝不判终态——top-level 非视频不等于不可用，
    且 IG 有 cobalt 兜底（Issue 6）。图文帖应 ok→has_playable False→retryable 落 cobalt。
    """
    assert TikHubProvider._classify_instagram(load("image_post")) == "ok"
    assert InstagramService("k", "b")._parse_response(load("image_post")) is None


# --------------------------- v1/v2 schema 自适应 ---------------------------

def test_parser_v2_schema():
    info = InstagramService("k", "b")._parse_response(load("v2_video"))
    assert info is not None and info.video_url.endswith("test_ig_v2.mp4")


def test_parser_v1_schema():
    info = InstagramService("k", "b")._parse_response(load("v1_by_url_video"))
    assert info is not None and info.video_url.endswith("test_ig_v1.mp4")


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


async def test_chain_v2_hit_short_circuits(monkeypatch):
    provider, calls = _provider_with(monkeypatch, {"v2": load("v2_video")})
    data = await provider.fetch_video_info("instagram", CODE, URL)
    assert data == load("v2_video")
    assert calls == ["v2"]


async def test_chain_falls_through_to_v1(monkeypatch):
    provider, calls = _provider_with(
        monkeypatch, {"v2": load("empty"), "v1_by_url": load("v1_by_url_video")}
    )
    data = await provider.fetch_video_info("instagram", CODE, URL)
    assert data == load("v1_by_url_video")
    assert calls == ["v2", "v1_by_url"]


async def test_chain_image_post_falls_through_not_terminal(monkeypatch):
    """图文帖（非视频）应 parse_failed → retryable，继续 v1，绝不短路（Issue 8）。"""
    provider, calls = _provider_with(
        monkeypatch, {"v2": load("image_post"), "v1_by_url": load("v1_by_url_video")}
    )
    data = await provider.fetch_video_info("instagram", CODE, URL)
    assert data == load("v1_by_url_video")
    assert calls == ["v2", "v1_by_url"]


async def test_chain_all_fail_raises_not_found_not_terminal(monkeypatch):
    """全失败（含图文）抛 VideoNotFoundError 而非终态，让 resolver 继续试 cobalt。"""
    provider, calls = _provider_with(
        monkeypatch, {"v2": load("image_post"), "v1_by_url": load("empty")}
    )
    with pytest.raises(VideoNotFoundError) as ei:
        await provider.fetch_video_info("instagram", CODE, URL)
    assert not isinstance(ei.value, TerminalError)
    assert calls == ["v2", "v1_by_url"]


async def test_chain_http_error_continues(monkeypatch):
    provider, calls = _provider_with(
        monkeypatch,
        {"v2": ProviderError("boom"), "v1_by_url": load("v1_by_url_video")},
    )
    data = await provider.fetch_video_info("instagram", CODE, URL)
    assert data == load("v1_by_url_video")
    assert calls == ["v2", "v1_by_url"]


# ----------------- 逐端点 param 构造（硬要求 T11） -----------------

async def test_param_construction_per_endpoint(monkeypatch):
    """v2 必须收到 {code_or_url}，v1_by_url 必须收到 {post_url}（防静默 422）。"""
    provider = TikHubProvider()
    seen = {}

    async def capture(self, name, path, params, per_timeout):
        seen[name] = params
        return load("empty")

    monkeypatch.setattr(TikHubProvider, "_call_endpoint", capture)
    with pytest.raises(VideoNotFoundError):
        await provider.fetch_video_info("instagram", CODE, URL)

    assert seen["v2"] == {"code_or_url": URL}
    assert seen["v1_by_url"] == {"post_url": URL}


async def test_chain_total_budget_timeout(monkeypatch):
    provider = TikHubProvider()
    monkeypatch.setattr(TikHubProvider, "INSTAGRAM_TOTAL_BUDGET", 0.05)

    async def slow_call(self, name, path, params, per_timeout):
        await asyncio.sleep(1)
        return load("v2_video")

    monkeypatch.setattr(TikHubProvider, "_call_endpoint", slow_call)
    with pytest.raises(ProviderError, match="timed out"):
        await provider.fetch_video_info("instagram", CODE, URL)
