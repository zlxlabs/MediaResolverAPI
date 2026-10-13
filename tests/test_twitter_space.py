"""X Space 回放：yt-dlp 提供者提取、回放映射、resolve API 面。

流式端点行为在 tests/test_twitter_space_stream.py。
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

from app.api.resolve import URL_FALLBACK_PLATFORMS
from app.services.platforms.twitter_space import TwitterSpaceService
from app.services.providers.base import (
    ProviderError,
    TerminalError,
    VideoNotFoundError,
)
from app.services.providers.ytdlp import SpaceNotReplayError, YtdlpProvider

FIXTURES = Path(__file__).parent / "fixtures" / "twitter_space"
SPACE_ID = "1AKEmvYqbrOKL"
SPACE_URL = f"https://x.com/i/spaces/{SPACE_ID}"


def _meta() -> dict:
    return json.loads((FIXTURES / "meta.json").read_text(encoding="utf-8"))


@pytest.fixture
def extract_calls(monkeypatch):
    """替换同步提取入口，记录调用并回放 fixture；返回调用记录列表。"""
    calls: list[str] = []

    def _extract(self, url: str) -> dict:
        calls.append(url)
        return _meta()

    monkeypatch.setattr(YtdlpProvider, "_extract_sync", _extract)
    return calls


def _patch_extract_error(monkeypatch, error: Exception):
    monkeypatch.setattr(
        YtdlpProvider, "_extract_sync", lambda self, url: (_ for _ in ()).throw(error)
    )


async def _fetch(provider: YtdlpProvider, **kwargs) -> dict:
    return await provider.fetch_video_info(
        platform="twitter_space",
        video_id=SPACE_ID,
        original_url=kwargs.pop("original_url", SPACE_URL),
        **kwargs,
    )


class TestYtdlpProvider:

    async def test_extracts_via_library_and_passes_original_url(
        self, extract_calls
    ):
        info = await _fetch(YtdlpProvider())
        assert extract_calls == [SPACE_URL]
        assert info["id"] == SPACE_ID
        assert info["url"].startswith("https://")

    async def test_rejects_unsupported_platform(self):
        with pytest.raises(ProviderError, match="does not support platform"):
            await YtdlpProvider().fetch_video_info(
                platform="twitter", video_id="1", original_url="https://x.com/x/status/1"
            )

    async def test_live_space_is_terminal_not_replay(self, monkeypatch):
        payload = _meta() | {"live_status": "is_live"}
        monkeypatch.setattr(YtdlpProvider, "_extract_sync", lambda self, url: payload)
        with pytest.raises(SpaceNotReplayError, match="is_live"):
            await _fetch(YtdlpProvider())

    async def test_post_live_space_is_rejected_per_locked_design(self, monkeypatch):
        """yt-dlp 的 timedout 态（post_live）按 design 字面判据同样拒绝；已上报主脑裁决。"""
        payload = _meta() | {"live_status": "post_live"}
        monkeypatch.setattr(YtdlpProvider, "_extract_sync", lambda self, url: payload)
        with pytest.raises(SpaceNotReplayError):
            await _fetch(YtdlpProvider())

    async def test_missing_live_status_is_rejected(self, monkeypatch):
        payload = _meta()
        payload.pop("live_status")
        monkeypatch.setattr(YtdlpProvider, "_extract_sync", lambda self, url: payload)
        with pytest.raises(SpaceNotReplayError, match="missing"):
            await _fetch(YtdlpProvider())

    async def test_space_not_found_maps_video_not_found(self, monkeypatch):
        _patch_extract_error(
            monkeypatch,
            DownloadError("ERROR: [TwitterSpaces] 1AKEmvYqbrOKL: Twitter Space not found"),
        )
        with pytest.raises(VideoNotFoundError):
            await _fetch(YtdlpProvider())

    @pytest.mark.parametrize(
        "message",
        [
            "Twitter Space ended and replay is disabled",
            "Twitter Space not started yet",
            "Twitter Space ended but not downloadable yet",
        ],
    )
    async def test_unavailable_family_maps_video_not_found(
        self, monkeypatch, message
    ):
        _patch_extract_error(
            monkeypatch,
            DownloadError(f"ERROR: [TwitterSpaces] {SPACE_ID}: {message}"),
        )
        with pytest.raises(VideoNotFoundError):
            await _fetch(YtdlpProvider())

    async def test_login_error_keeps_login_required_semantics(self, monkeypatch):
        _patch_extract_error(
            monkeypatch,
            DownloadError("ERROR: not authorized to access this Space, login required"),
        )
        with pytest.raises(ProviderError, match="login_required"):
            await _fetch(YtdlpProvider())

    async def test_generic_error_is_provider_error_with_playlist_redacted(
        self, monkeypatch
    ):
        _patch_extract_error(
            monkeypatch,
            DownloadError(
                "boom: https://prod-fastly-us-west-1.video.pscp.tv/x/y/playlist.m3u8?type=replay failed"
            ),
        )
        with pytest.raises(ProviderError) as exc_info:
            await _fetch(YtdlpProvider())
        assert "pscp.tv" not in str(exc_info.value)
        assert "redacted-playlist-url" in str(exc_info.value)

    async def test_extract_timeout_maps_provider_error(self, monkeypatch):
        import time

        from app.core.config import settings

        monkeypatch.setattr(settings, "YTDLP_EXTRACT_TIMEOUT_SECONDS", 0.05)

        def _slow(self, url: str) -> dict:
            time.sleep(0.5)
            return _meta()

        monkeypatch.setattr(YtdlpProvider, "_extract_sync", _slow)
        with pytest.raises(ProviderError, match="timed out"):
            await _fetch(YtdlpProvider())

    def test_space_not_replay_is_terminal(self):
        """终态：单 provider 链上命中即短路，不再重试。"""
        assert issubclass(SpaceNotReplayError, TerminalError)


class TestTwitterSpaceServiceMapping:

    def _parse(self, payload=None):
        service = TwitterSpaceService(api_key="", api_base="")
        return service._parse_response(payload if payload is not None else _meta())

    def test_maps_core_fields(self):
        info = self._parse()
        assert info.video_id == SPACE_ID
        assert info.platform == "twitter_space"
        assert info.media_type == "audio"
        assert info.video_url == f"/api/stream/twitter_space/{SPACE_ID}"
        assert info.width == 0 and info.height == 0
        assert info.quality is None
        assert info.variants is None
        assert info.title == "噪音之后：为什么做IP一定要有自己的“理论”"
        assert info.author_name == "Adrian Punk"
        assert info.author_id == "AdrianPunk115"

    def test_contract_tolerates_null_duration_and_missing_like_count(self):
        """design 不变式 4：duration 缺失或为 null、like_count 键不存在时映射 None 不报错。

        实际 fixture（probe meta.json 原文）顶层无 duration/like_count 键；
        探针报告 D4 写 duration 键存在值为 null 与文件不符，两种形态都锁死。
        """
        meta = _meta()
        assert "like_count" not in meta and "duration" not in meta
        info = self._parse()
        assert info.duration is None
        assert info.like_count is None
        # 键存在但值为 null 的形态同样归 None
        info_null = self._parse(meta | {"duration": None})
        assert info_null.duration is None

    def test_present_counts_are_mapped(self):
        info = self._parse(_meta() | {"like_count": 42, "view_count": 7})
        assert info.like_count == 42
        assert info.view_count == 7

    def test_create_time_from_timestamp_utc(self):
        info = self._parse()
        assert info.create_time == datetime.fromtimestamp(1791810443, tz=timezone.utc)

    def test_video_url_never_carries_playlist(self):
        """失败可见性锚点：fixture 原始数据带 pscp.tv，映射产出绝不允许带出。"""
        raw = _meta()
        assert "pscp.tv" in raw["url"]  # fixture 有牙
        info = self._parse(raw)
        assert "pscp.tv" not in info.video_url

    def test_quality_kwarg_is_noop(self):
        service = TwitterSpaceService(api_key="", api_base="")
        info = service._parse_response(_meta(), quality="720p")
        assert info.quality is None

    def test_non_dict_returns_none(self):
        assert self._parse("not-a-dict") is None

    def test_missing_playlist_url_returns_none(self):
        assert self._parse({"id": SPACE_ID}) is None

    def test_missing_space_id_returns_none(self):
        payload = _meta()
        payload.pop("id")
        assert self._parse(payload) is None


class TestResolveApiSurface:

    def test_resolve_returns_service_stream_url(
        self, authed_client, extract_calls
    ):
        resp = authed_client.post(
            "/api/resolve", json={"url": SPACE_URL, "translate": False}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["platform"] == "twitter_space"
        assert data["media_type"] == "audio"
        assert data["video_id"] == SPACE_ID
        assert data["video_url"].endswith(f"/api/stream/twitter_space/{SPACE_ID}")
        assert data["provider"] == "ytdlp"
        assert data["duration"] is None
        assert data["quality"] is None
        assert data["variants"] is None

    def test_resolve_response_never_leaks_playlist(self, authed_client, extract_calls):
        """提取结果里的 pscp.tv playlist URL 不得出现在任何 API 响应（design 不变式 2）。"""
        resp = authed_client.post(
            "/api/resolve", json={"url": SPACE_URL, "translate": False}
        )
        assert "pscp.tv" not in resp.text

    def test_resolve_second_call_hits_cache(self, authed_client, extract_calls):
        for _ in range(2):
            resp = authed_client.post(
                "/api/resolve", json={"url": SPACE_URL, "translate": False}
            )
            assert resp.json()["success"] is True
        assert len(extract_calls) == 1

    def test_resolve_unavailable_space_is_clear_error(self, authed_client, monkeypatch):
        _patch_extract_error(
            monkeypatch,
            DownloadError(
                f"ERROR: [TwitterSpaces] {SPACE_ID}: Twitter Space ended and replay is disabled"
            ),
        )
        resp = authed_client.post(
            "/api/resolve", json={"url": SPACE_URL, "translate": False}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "replay is disabled" in body["error"]
        assert "pscp.tv" not in resp.text

    def test_resolve_live_space_is_terminal_error(self, authed_client, monkeypatch):
        payload = _meta() | {"live_status": "is_live"}
        monkeypatch.setattr(YtdlpProvider, "_extract_sync", lambda self, url: payload)
        resp = authed_client.post(
            "/api/resolve", json={"url": SPACE_URL, "translate": False}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "is_live" in body["error"]

    def test_resolve_rejects_bad_space_id(self, authed_client):
        resp = authed_client.post(
            "/api/resolve", json={"url": "https://x.com/i/spaces/short", "translate": False}
        )
        assert resp.status_code == 400

    def test_twitter_space_not_in_url_fallback_platforms(self):
        """ID 提取确定性，resolve.py 的 by_url 兜底不纳入 twitter_space（锁定决策 8）。"""
        assert "twitter_space" not in URL_FALLBACK_PLATFORMS
