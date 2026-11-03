"""
VideoResolver 终态处理测试（T6 / codex #5）

终态异常（DouyinTerminalError）必须立即停止责任链，不再 fallback 到下一个 provider；
普通失败（ProviderError）仍按原逻辑继续下一个 provider。
"""

import pytest

from app.services.video_resolver import VideoResolver, VideoResolverError
from app.services.providers.base import DouyinTerminalError, ProviderError, TerminalError
from app.services.platforms.base import VideoInfo


class FakeProvider:
    def __init__(self, name, exc=None):
        self.provider_name = name
        self.exc = exc
        self.called = False

    async def fetch_video_info(self, platform, video_id, original_url, **kwargs):
        self.called = True
        if self.exc:
            raise self.exc
        return {"ok": True}


def _video_info():
    return VideoInfo(
        video_id="id", platform="douyin", title="t", description="d",
        author_name="a", author_id="a", video_url="http://x.mp4",
        width=1, height=1,
    )


async def test_terminal_stops_chain_no_fallback():
    r = VideoResolver()
    p1 = FakeProvider("tikhub", DouyinTerminalError("private"))
    p2 = FakeProvider("cobalt")
    r.provider_chains["douyin"] = [p1, p2]
    with pytest.raises(VideoResolverError):
        await r.resolve("douyin", "id", "https://u")
    assert p1.called and not p2.called  # 终态后不试 cobalt


async def test_non_terminal_falls_through(monkeypatch):
    r = VideoResolver()
    p1 = FakeProvider("tikhub", ProviderError("boom"))
    p2 = FakeProvider("cobalt")
    r.provider_chains["douyin"] = [p1, p2]
    monkeypatch.setattr(r, "_adapt_data", lambda **kw: _video_info())
    info, name = await r.resolve("douyin", "id", "https://u")
    assert name == "cobalt"
    assert p1.called and p2.called


# ----------------------------- JustOneAPI：不得终态短路（约束 7） -----------------------------

async def test_justoneapi_failure_never_short_circuits_cobalt(monkeypatch):
    """有 Cobalt 的平台：JOA 失败当 retryable，链走完落 Cobalt。

    用真 JustOneApiProvider（stub 其 _get 抛业务错误）证明其失败不是 TerminalError。
    """
    from app.core.config import settings
    from app.services.providers.justoneapi import JustOneApiProvider

    monkeypatch.setattr(settings, "JUSTONEAPI_TOKEN", "test-token")
    joa_calls: list[str] = []

    async def joa_down(self, path, params):
        joa_calls.append(path)
        raise ProviderError(
            f"JustOneAPI business error code=500 (path={path})"
        )

    monkeypatch.setattr(JustOneApiProvider, "_get", joa_down)

    r = VideoResolver()
    p1 = FakeProvider("tikhub", ProviderError("tikhub down"))
    joa = JustOneApiProvider()
    p3 = FakeProvider("cobalt")
    r.provider_chains["tiktok"] = [p1, joa, p3]
    monkeypatch.setattr(r, "_adapt_data", lambda **kw: _video_info())
    info, name = await r.resolve("tiktok", "id", "https://u")
    assert name == "cobalt"
    assert p1.called and p3.called
    assert joa_calls == ["/api/tiktok/get-post-detail/v1"]


async def test_justoneapi_error_type_is_provider_error_not_terminal(monkeypatch):
    """抖音/小红书本轮无终态样本：JOA 失败一律当 retryable（不是 TerminalError）。"""
    from app.core.config import settings
    from app.services.providers.justoneapi import JustOneApiProvider

    monkeypatch.setattr(settings, "JUSTONEAPI_TOKEN", "test-token")

    async def joa_down(self, path, params):
        raise ProviderError("JustOneAPI business error code=400 (path=x)")

    monkeypatch.setattr(JustOneApiProvider, "_get", joa_down)
    provider = JustOneApiProvider()
    for platform in ("douyin", "xiaohongshu", "tiktok", "twitter"):
        with pytest.raises(ProviderError) as exc_info:
            await provider.fetch_video_info(
                platform=platform, video_id="1", original_url="https://u"
            )
        assert not isinstance(exc_info.value, TerminalError)
