"""
TikHub 小红书多级降级链测试

覆盖：终态异常契约、响应分类器三态、端点链命中/降级/短路/全失败、
token 缺失只走 app_v2、单端 HTTPStatusError 终态体、整链总预算超时。
全部 mock HTTPClient/_call_xhs_endpoint，无真实网络。
"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.services.providers.base import (
    ProviderError,
    VideoNotFoundError,
    TerminalError,
    XhsTerminalError,
)
from app.services.providers.tikhub import TikHubProvider

FIXTURES = Path(__file__).parent / "fixtures" / "xiaohongshu"

URL = (
    "https://www.xiaohongshu.com/explore/68a54752000000001d002090"
    "?xsec_token=ABywGfdwDOIcHhHJSIIHxj8Glq_YEqNSiHgxJ8jJrxRNA=&xsec_source=pc_user"
)
URL_NO_TOKEN = "https://www.xiaohongshu.com/explore/68a54752000000001d002090"
NOTE_ID = "68a54752000000001d002090"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# ----------------------------- 异常契约 -----------------------------

def test_terminal_error_is_video_not_found_subclass():
    """XHS 终态异常须是 VideoNotFoundError 子类，兼容现有捕获。"""
    assert issubclass(XhsTerminalError, VideoNotFoundError)


def test_terminal_errors_share_base():
    """Douyin 与 Xhs 终态异常共享 TerminalError 基类（DRY，统一捕获）。"""
    from app.services.providers.base import DouyinTerminalError

    assert issubclass(XhsTerminalError, TerminalError)
    assert issubclass(DouyinTerminalError, TerminalError)


# ----------------------------- token 提取 -----------------------------

def test_extract_xsec_token():
    p = TikHubProvider()
    assert p._extract_xsec_token(URL) == "ABywGfdwDOIcHhHJSIIHxj8Glq_YEqNSiHgxJ8jJrxRNA="


def test_extract_xsec_token_urlencoded():
    p = TikHubProvider()
    url = "https://www.xiaohongshu.com/explore/x?xsec_token=AB%3D%3D&xsec_source=pc"
    assert p._extract_xsec_token(url) == "AB=="


def test_extract_xsec_token_absent():
    p = TikHubProvider()
    assert p._extract_xsec_token(URL_NO_TOKEN) is None


# ----------------------------- 分类器 -----------------------------

@pytest.mark.parametrize("fixture", ["app_v2_video", "web_v3_video", "ok_no_video"])
def test_classify_ok(fixture):
    assert TikHubProvider._classify_xhs(load(fixture)) == "ok"


def test_classify_terminal_image_note():
    assert TikHubProvider._classify_xhs(load("image_note")) == "terminal"


@pytest.mark.parametrize("fixture", ["empty", "detail_400"])
def test_classify_retryable(fixture):
    assert TikHubProvider._classify_xhs(load(fixture)) == "retryable"


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


async def test_chain_app_v2_hit_short_circuits(monkeypatch):
    provider, calls = _provider_with(monkeypatch, {"app_v2": load("app_v2_video")})
    data = await provider.fetch_video_info("xiaohongshu", NOTE_ID, URL)
    assert data == load("app_v2_video")
    assert calls == ["app_v2"]  # 命中即停，不调 web_v3


async def test_chain_falls_through_to_web_v3(monkeypatch):
    provider, calls = _provider_with(
        monkeypatch, {"app_v2": load("empty"), "web_v3": load("web_v3_video")}
    )
    data = await provider.fetch_video_info("xiaohongshu", NOTE_ID, URL)
    assert data == load("web_v3_video")
    assert calls == ["app_v2", "web_v3"]


async def test_chain_parse_failure_is_retryable(monkeypatch):
    """video 类型但无可播放流，应视为可重试继续 web_v3。"""
    provider, calls = _provider_with(
        monkeypatch, {"app_v2": load("ok_no_video"), "web_v3": load("web_v3_video")}
    )
    data = await provider.fetch_video_info("xiaohongshu", NOTE_ID, URL)
    assert data == load("web_v3_video")
    assert calls == ["app_v2", "web_v3"]


async def test_chain_terminal_short_circuits(monkeypatch):
    """图文笔记为终态，立即短路，绝不试 web_v3。"""
    provider, calls = _provider_with(
        monkeypatch,
        {"app_v2": load("image_note"), "web_v3": load("web_v3_video")},
    )
    with pytest.raises(XhsTerminalError):
        await provider.fetch_video_info("xiaohongshu", NOTE_ID, URL)
    assert calls == ["app_v2"]


async def test_chain_no_token_skips_web_v3(monkeypatch):
    """无 xsec_token 时 web_v3 跳过，只走 app_v2。"""
    provider, calls = _provider_with(monkeypatch, {"app_v2": load("empty")})
    with pytest.raises(VideoNotFoundError):
        await provider.fetch_video_info("xiaohongshu", NOTE_ID, URL_NO_TOKEN)
    assert calls == ["app_v2"]  # web_v3 因缺 token 被跳过


async def test_chain_all_fail_raises_not_found(monkeypatch):
    provider, calls = _provider_with(
        monkeypatch, {"app_v2": load("empty"), "web_v3": load("empty")}
    )
    with pytest.raises(VideoNotFoundError) as ei:
        await provider.fetch_video_info("xiaohongshu", NOTE_ID, URL)
    assert not isinstance(ei.value, XhsTerminalError)
    assert calls == ["app_v2", "web_v3"]


async def test_chain_http_error_on_one_endpoint_continues(monkeypatch):
    provider, calls = _provider_with(
        monkeypatch,
        {"app_v2": ProviderError("boom"), "web_v3": load("web_v3_video")},
    )
    data = await provider.fetch_video_info("xiaohongshu", NOTE_ID, URL)
    assert data == load("web_v3_video")
    assert calls == ["app_v2", "web_v3"]


# ------------------- 单端调用：HTTPStatusError 体 -------------------

async def test_call_endpoint_returns_body_on_http_status_error(monkeypatch):
    """4xx 错误体应取出来交给分类器，而非吞掉。"""
    provider = TikHubProvider()
    err_body = load("detail_400")

    class _Resp:
        status_code = 400

        def json(self):
            return err_body

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            raise httpx.HTTPStatusError(
                "400", request=httpx.Request("GET", "http://x"), response=_Resp()
            )

    monkeypatch.setattr(
        "app.services.providers.tikhub.HTTPClient", lambda *a, **k: _FakeClient()
    )
    body = await provider._call_endpoint("app_v2", "/p", {"note_id": NOTE_ID}, 25)
    assert body == err_body
    assert TikHubProvider._classify_xhs(body) == "retryable"


async def test_chain_total_budget_timeout(monkeypatch):
    """整链总预算超时应抛 ProviderError，而非无限串行放大。"""
    provider = TikHubProvider()
    monkeypatch.setattr(TikHubProvider, "XHS_TOTAL_BUDGET", 0.05)

    async def slow_call(self, name, path, params, per_timeout):
        await asyncio.sleep(1)
        return load("app_v2_video")

    monkeypatch.setattr(TikHubProvider, "_call_endpoint", slow_call)
    with pytest.raises(ProviderError, match="timed out"):
        await provider.fetch_video_info("xiaohongshu", NOTE_ID, URL)
