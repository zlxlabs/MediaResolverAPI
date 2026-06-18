"""
TikHub YouTube 多级降级链测试

覆盖：cobalt 兜底平台不出终态（评审 Issue 6）、解析器自适应两套 schema
（data.videos.items / data.streamingData.formats，codex Issue 10）、端点链命中/
降级/全失败、逐端点 param 构造、整链总预算超时。全部 mock _call_endpoint。
"""

import asyncio
import json
from pathlib import Path

import pytest

from app.services.providers.base import ProviderError, VideoNotFoundError, TerminalError
from app.services.providers.tikhub import TikHubProvider
from app.services.platforms.youtube import YouTubeService

FIXTURES = Path(__file__).parent / "fixtures" / "youtube"

VID = "dQw4w9WgXcQ"
URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# ----------------------------- 分类器 -----------------------------

@pytest.mark.parametrize("fixture", ["web_video", "web_v2_video"])
def test_classify_ok(fixture):
    assert TikHubProvider._classify_youtube(load(fixture)) == "ok"


def test_classify_empty_retryable():
    assert TikHubProvider._classify_youtube(load("empty")) == "retryable"


def test_classify_never_terminal():
    """YouTube 有 cobalt 兜底 → 永不出终态（Issue 6）。"""
    assert TikHubProvider._classify_youtube({"data": None}) == "retryable"


# --------------------- 解析器自适应两套 schema（codex Issue 10） ---------------------

def test_parser_web_schema():
    """current 端点：data.videos.items，选 1080p。"""
    info = YouTubeService("k", "b")._parse_response(load("web_video"))
    assert info is not None
    assert info.video_url.endswith("yt_web_1080.mp4")
    assert info.quality == "1080p"


def test_parser_v2_streamingdata_schema():
    """v2 端点：data.streamingData.formats(muxed)，取合流 360p（跳过纯视频 adaptiveFormats）。"""
    info = YouTubeService("k", "b")._parse_response(load("web_v2_video"))
    assert info is not None
    assert info.video_url.endswith("yt_v2_360.mp4")
    assert info.title == "youtube test (v2)"


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


async def test_chain_web_hit_short_circuits(monkeypatch):
    provider, calls = _provider_with(monkeypatch, {"web": load("web_video")})
    data = await provider.fetch_video_info("youtube", VID, URL)
    assert data == load("web_video")
    assert calls == ["web"]


async def test_chain_falls_through_to_web_v2(monkeypatch):
    provider, calls = _provider_with(
        monkeypatch, {"web": load("empty"), "web_v2": load("web_v2_video")}
    )
    data = await provider.fetch_video_info("youtube", VID, URL)
    assert data == load("web_v2_video")
    assert calls == ["web", "web_v2"]


async def test_chain_all_fail_raises_not_found_not_terminal(monkeypatch):
    provider, calls = _provider_with(
        monkeypatch, {"web": load("empty"), "web_v2": load("empty")}
    )
    with pytest.raises(VideoNotFoundError) as ei:
        await provider.fetch_video_info("youtube", VID, URL)
    assert not isinstance(ei.value, TerminalError)
    assert calls == ["web", "web_v2"]


async def test_chain_http_error_continues(monkeypatch):
    provider, calls = _provider_with(
        monkeypatch, {"web": ProviderError("boom"), "web_v2": load("web_v2_video")}
    )
    data = await provider.fetch_video_info("youtube", VID, URL)
    assert data == load("web_v2_video")
    assert calls == ["web", "web_v2"]


# ----------------- 逐端点 param 构造（硬要求 T11） -----------------

async def test_param_construction_per_endpoint(monkeypatch):
    """两端点都必须收到 {video_id}。"""
    provider = TikHubProvider()
    seen = {}

    async def capture(self, name, path, params, per_timeout):
        seen[name] = params
        return load("empty")

    monkeypatch.setattr(TikHubProvider, "_call_endpoint", capture)
    with pytest.raises(VideoNotFoundError):
        await provider.fetch_video_info("youtube", VID, URL)

    assert seen["web"] == {"video_id": VID}
    assert seen["web_v2"] == {"video_id": VID}


async def test_chain_total_budget_timeout(monkeypatch):
    provider = TikHubProvider()
    monkeypatch.setattr(TikHubProvider, "YOUTUBE_TOTAL_BUDGET", 0.05)

    async def slow_call(self, name, path, params, per_timeout):
        await asyncio.sleep(1)
        return load("web_video")

    monkeypatch.setattr(TikHubProvider, "_call_endpoint", slow_call)
    with pytest.raises(ProviderError, match="timed out"):
        await provider.fetch_video_info("youtube", VID, URL)
