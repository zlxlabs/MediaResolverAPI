"""
TikHub 抖音多级降级链测试

覆盖：终态异常、响应分类器、端点链顺序/短路/全失败、超时预算、HTTPStatusError 终态体。
全部 mock HTTPClient，无真实网络。
"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.services.providers.base import (
    ProviderError,
    VideoNotFoundError,
    DouyinTerminalError,
)
from app.services.providers.tikhub import TikHubProvider

FIXTURES = Path(__file__).parent / "fixtures" / "douyin"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# ----------------------------- 异常契约 -----------------------------

def test_terminal_error_is_video_not_found_subclass():
    """终态异常须是 VideoNotFoundError 子类，便于现有捕获兼容。"""
    assert issubclass(DouyinTerminalError, VideoNotFoundError)


# ----------------------------- 分类器 -----------------------------

@pytest.mark.parametrize("fixture", ["web_v1", "web_v2", "app_v3", "hybrid"])
def test_classify_ok(fixture):
    assert TikHubProvider._classify_douyin(load(fixture)) == "ok"


@pytest.mark.parametrize("fixture", ["private_reason5", "partial_reason10"])
def test_classify_terminal(fixture):
    assert TikHubProvider._classify_douyin(load(fixture)) == "terminal"


@pytest.mark.parametrize("fixture", ["copyright_reason8", "empty"])
def test_classify_retryable(fixture):
    assert TikHubProvider._classify_douyin(load(fixture)) == "retryable"


def test_classify_scans_whole_filter_list_not_index0():
    """codex #9：终态 reason 可能不在 index 0，需扫整个 filter_list。"""
    data = {
        "data": {
            "filter_list": [
                {"reason": 8, "filter_reason": "版权"},
                {"reason": 5, "filter_reason": "私密"},
            ]
        }
    }
    assert TikHubProvider._classify_douyin(data) == "terminal"


# ----------------------------- 端点降级链 -----------------------------

def _provider_with(monkeypatch, mapping):
    """构造 provider，并把通用 _call_endpoint 替换为按端点名返回 mapping 的桩。"""
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


async def test_chain_t1_hit_short_circuits(monkeypatch):
    provider, calls = _provider_with(monkeypatch, {"web_v1": load("web_v1")})
    data = await provider.fetch_video_info("douyin", "7592102779420115355", "https://u")
    assert data == load("web_v1")
    assert calls == ["web_v1"]  # 命中即停，不调 T2/T3


async def test_chain_falls_through_to_web_v2(monkeypatch):
    provider, calls = _provider_with(
        monkeypatch, {"web_v1": load("empty"), "web_v2": load("web_v2")}
    )
    data = await provider.fetch_video_info("douyin", "id", "https://u")
    assert data == load("web_v2")
    assert calls == ["web_v1", "web_v2"]


async def test_chain_falls_through_to_app_v3_on_copyright(monkeypatch):
    """reason=8 版权受限可重试，最终由 app v3(无版权限制) 命中。"""
    provider, calls = _provider_with(
        monkeypatch,
        {
            "web_v1": load("copyright_reason8"),
            "web_v2": load("copyright_reason8"),
            "app_v3": load("app_v3"),
        },
    )
    data = await provider.fetch_video_info("douyin", "id", "https://u")
    assert data == load("app_v3")
    assert calls == ["web_v1", "web_v2", "app_v3"]


async def test_chain_terminal_short_circuits(monkeypatch):
    """回归铁律：私密/部分可见为终态，立即短路，绝不试后续端点。"""
    provider, calls = _provider_with(
        monkeypatch,
        {
            "web_v1": load("private_reason5"),
            "web_v2": load("web_v2"),  # 不应被调用
            "app_v3": load("app_v3"),
        },
    )
    with pytest.raises(DouyinTerminalError):
        await provider.fetch_video_info("douyin", "id", "https://u")
    assert calls == ["web_v1"]


async def test_chain_parse_failure_is_retryable(monkeypatch):
    """codex #10：envelope 合法但无可播放直链，应视为可重试继续下一端点。"""
    provider, calls = _provider_with(
        monkeypatch, {"web_v1": load("ok_no_video"), "web_v2": load("web_v2")}
    )
    data = await provider.fetch_video_info("douyin", "id", "https://u")
    assert data == load("web_v2")
    assert calls == ["web_v1", "web_v2"]


async def test_chain_all_fail_raises_not_found(monkeypatch):
    provider, calls = _provider_with(
        monkeypatch,
        {"web_v1": load("empty"), "web_v2": load("empty"), "app_v3": load("empty")},
    )
    with pytest.raises(VideoNotFoundError) as ei:
        await provider.fetch_video_info("douyin", "id", "https://u")
    assert not isinstance(ei.value, DouyinTerminalError)
    assert calls == ["web_v1", "web_v2", "app_v3"]


async def test_use_hybrid_only_calls_hybrid(monkeypatch):
    provider, calls = _provider_with(monkeypatch, {"hybrid": load("hybrid")})
    data = await provider.fetch_video_info(
        "douyin", "", "https://v.douyin.com/abc/", use_hybrid=True
    )
    assert data == load("hybrid")
    assert calls == ["hybrid"]


async def test_chain_http_error_on_one_endpoint_continues(monkeypatch):
    provider, calls = _provider_with(
        monkeypatch,
        {"web_v1": ProviderError("boom"), "web_v2": load("web_v2")},
    )
    data = await provider.fetch_video_info("douyin", "id", "https://u")
    assert data == load("web_v2")
    assert calls == ["web_v1", "web_v2"]


# ------------------- 单端调用：HTTPStatusError 终态体（codex #4） -------------------

async def test_call_endpoint_returns_body_on_http_status_error(monkeypatch):
    """4xx 错误体里若含 filter_list，应取出来交给分类器，而非吞掉。"""
    provider = TikHubProvider()
    err_body = load("private_reason5")

    class _Resp:
        status_code = 404

        def json(self):
            return err_body

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            raise httpx.HTTPStatusError("404", request=httpx.Request("GET", "http://x"), response=_Resp())

    monkeypatch.setattr(
        "app.services.providers.tikhub.HTTPClient", lambda *a, **k: _FakeClient()
    )
    body = await provider._call_endpoint("web_v1", "/p", {"aweme_id": "id"}, 25)
    assert body == err_body
    assert TikHubProvider._classify_douyin(body) == "terminal"


async def test_chain_total_budget_timeout(monkeypatch):
    """T4/codex #3：整链总预算超时应抛 ProviderError，而非无限串行放大。"""
    provider = TikHubProvider()
    monkeypatch.setattr(TikHubProvider, "DOUYIN_TOTAL_BUDGET", 0.05)

    async def slow_call(self, name, path, params, per_timeout):
        await asyncio.sleep(1)
        return load("web_v1")

    monkeypatch.setattr(TikHubProvider, "_call_endpoint", slow_call)
    with pytest.raises(ProviderError, match="timed out"):
        await provider.fetch_video_info("douyin", "id", "https://u")
