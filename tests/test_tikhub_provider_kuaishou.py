"""
TikHub 快手多级降级链测试

覆盖：响应分类器两态（快手单源但无终态样本，只 ok/retryable）、端点链命中/降级/
全失败、逐端点 param 构造（photo_id vs share_text，防静默 422）、解析器多 schema
自适应（data.photo / data[0]）、整链总预算超时。全部 mock _call_endpoint，无真实网络。
"""

import asyncio
import json
from pathlib import Path

import pytest

from app.services.providers.base import (
    ProviderError,
    VideoNotFoundError,
    TerminalError,
    KuaishouTerminalError,
)
from app.services.providers.tikhub import TikHubProvider
from app.services.platforms.kuaishou import KuaishouService

FIXTURES = Path(__file__).parent / "fixtures" / "kuaishou"

PHOTO_ID = "3xh683uyhqbzan4"
URL = "https://www.kuaishou.com/short-video/3xh683uyhqbzan4?authorId=3x78tdkxcz6zkk2"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# ----------------------------- 异常契约 -----------------------------

def test_terminal_error_is_terminal_subclass():
    """快手终态异常须是 TerminalError 子类（单源平台预留，统一捕获）。"""
    assert issubclass(KuaishouTerminalError, TerminalError)
    assert issubclass(KuaishouTerminalError, VideoNotFoundError)


# ----------------------------- 分类器 -----------------------------

@pytest.mark.parametrize("fixture", ["web_v2_video", "web_share_video", "ok_no_video"])
def test_classify_ok(fixture):
    """定位到视频节点即 ok（含 data.photo 与 data[0] 两种 schema）。"""
    assert TikHubProvider._classify_kuaishou(load(fixture)) == "ok"


@pytest.mark.parametrize("fixture", ["empty"])
def test_classify_retryable(fixture):
    assert TikHubProvider._classify_kuaishou(load(fixture)) == "retryable"


def test_classify_never_terminal():
    """快手不臆造终态：即便像私密/删除的空响应也只 retryable（评审 Issue 2）。"""
    assert TikHubProvider._classify_kuaishou({"data": None}) == "retryable"
    assert TikHubProvider._classify_kuaishou({}) == "retryable"


# --------------------------- 解析器多 schema 自适应 ---------------------------

def test_parser_adaptive_photo_singular():
    """web_v2 的 data.photo（单数 dict）能解析出直链。"""
    info = KuaishouService("k", "b")._parse_response(load("web_v2_video"))
    assert info is not None and info.video_url.endswith("test_web_v2.mp4")


def test_parser_adaptive_data_list():
    """web_share 的 data[0]（list）同样能解析出直链。"""
    info = KuaishouService("k", "b")._parse_response(load("web_share_video"))
    assert info is not None and info.video_url.endswith("test_web_share.mp4")


# ----------------------------- 端点降级链 -----------------------------

def _provider_with(monkeypatch, mapping):
    """构造 provider，把通用 _call_endpoint 替换为按端点名返回 mapping 的桩。"""
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


async def test_chain_web_v2_hit_short_circuits(monkeypatch):
    provider, calls = _provider_with(monkeypatch, {"web_v2": load("web_v2_video")})
    data = await provider.fetch_video_info("kuaishou", PHOTO_ID, URL)
    assert data == load("web_v2_video")
    assert calls == ["web_v2"]  # 命中即停，不调 web_share


async def test_chain_falls_through_to_web_share(monkeypatch):
    provider, calls = _provider_with(
        monkeypatch, {"web_v2": load("empty"), "web_share": load("web_share_video")}
    )
    data = await provider.fetch_video_info("kuaishou", PHOTO_ID, URL)
    assert data == load("web_share_video")
    assert calls == ["web_v2", "web_share"]


async def test_chain_parse_failure_is_retryable(monkeypatch):
    """有视频节点但无可播放流，应视为可重试继续 web_share。"""
    provider, calls = _provider_with(
        monkeypatch, {"web_v2": load("ok_no_video"), "web_share": load("web_share_video")}
    )
    data = await provider.fetch_video_info("kuaishou", PHOTO_ID, URL)
    assert data == load("web_share_video")
    assert calls == ["web_v2", "web_share"]


async def test_chain_all_fail_raises_not_found_not_terminal(monkeypatch):
    """全失败抛 VideoNotFoundError，且绝不是终态（快手不臆造终态）。"""
    provider, calls = _provider_with(
        monkeypatch, {"web_v2": load("empty"), "web_share": load("empty")}
    )
    with pytest.raises(VideoNotFoundError) as ei:
        await provider.fetch_video_info("kuaishou", PHOTO_ID, URL)
    assert not isinstance(ei.value, KuaishouTerminalError)
    assert calls == ["web_v2", "web_share"]


async def test_chain_http_error_on_one_endpoint_continues(monkeypatch):
    provider, calls = _provider_with(
        monkeypatch,
        {"web_v2": ProviderError("boom"), "web_share": load("web_share_video")},
    )
    data = await provider.fetch_video_info("kuaishou", PHOTO_ID, URL)
    assert data == load("web_share_video")
    assert calls == ["web_v2", "web_share"]


# ----------------- 逐端点 param 构造（硬要求 T11，防静默 422） -----------------

async def test_param_construction_per_endpoint(monkeypatch):
    """
    web_v2 必须收到 {photo_id}，web_share 必须收到 {share_text=原始url}。
    映错参数名会让 TikHub 静默 422 然后误降级，看起来像「端点挂了」。
    """
    provider = TikHubProvider()
    seen = {}

    async def capture(self, name, path, params, per_timeout):
        seen[name] = params
        return load("empty")  # 全 retryable，强制走完整条链

    monkeypatch.setattr(TikHubProvider, "_call_endpoint", capture)
    with pytest.raises(VideoNotFoundError):
        await provider.fetch_video_info("kuaishou", PHOTO_ID, URL)

    assert seen["web_v2"] == {"photo_id": PHOTO_ID}
    assert seen["web_share"] == {"share_text": URL}


async def test_chain_total_budget_timeout(monkeypatch):
    """整链总预算超时应抛 ProviderError，而非无限串行放大。"""
    provider = TikHubProvider()
    monkeypatch.setattr(TikHubProvider, "KUAISHOU_TOTAL_BUDGET", 0.05)

    async def slow_call(self, name, path, params, per_timeout):
        await asyncio.sleep(1)
        return load("web_v2_video")

    monkeypatch.setattr(TikHubProvider, "_call_endpoint", slow_call)
    with pytest.raises(ProviderError, match="timed out"):
        await provider.fetch_video_info("kuaishou", PHOTO_ID, URL)
