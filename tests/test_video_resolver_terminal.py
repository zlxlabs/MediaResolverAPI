"""
VideoResolver 终态处理测试（T6 / codex #5）

终态异常（DouyinTerminalError）必须立即停止责任链，不再 fallback 到下一个 provider；
普通失败（ProviderError）仍按原逻辑继续下一个 provider。
"""

import pytest

from app.services.video_resolver import VideoResolver, VideoResolverError
from app.services.providers.base import DouyinTerminalError, ProviderError
from app.services.platforms.base import VideoInfo


class FakeProvider:
    def __init__(self, name, exc=None):
        self.provider_name = name
        self.exc = exc
        self.called = False

    async def fetch_video_info(self, platform, video_id, original_url):
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
