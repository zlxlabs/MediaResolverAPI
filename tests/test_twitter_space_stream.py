"""X Space 流式端点：全部网络与 ffmpeg 子进程 stub。

每次请求现取 playlist（design 不变式 2）、错误映射（400/404/502）、429 并发槽、
ffmpeg argv 形态、瞬时失败 502、客户端断连 kill 进程不留尸。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport

import app.api.stream as stream_mod
from app.core.config import settings
from app.main import app
from app.services.providers.base import ProviderError, VideoNotFoundError
from app.services.providers.ytdlp import SpaceNotReplayError, YtdlpProvider

FIXTURES = Path(__file__).parent / "fixtures" / "twitter_space"
SPACE_ID = "1AKEmvYqbrOKL"
AUTH = {"X-API-Key": "test-key-123"}
PLAYLIST_A = "https://prod-fastly-us-west-1.video.pscp.tv/a/playlist.m3u8?type=replay"
PLAYLIST_B = "https://prod-fastly-us-west-1.video.pscp.tv/b/playlist.m3u8?type=replay"


def _meta() -> dict:
    return json.loads((FIXTURES / "meta.json").read_text(encoding="utf-8"))


class FakeStream:
    """按序吐 chunk，耗尽后挂起直到 EOF 被置位（模拟 StreamReader 管道语义，无 close）。"""

    def __init__(self, chunks: list[bytes], *, hang: bool = False):
        self._chunks = list(chunks)
        self._eof = asyncio.Event()
        if not hang:
            self._eof.set()

    async def read(self, n: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        await self._eof.wait()
        return b""

    def set_eof(self) -> None:
        self._eof.set()


class FakeProcess:
    def __init__(
        self,
        *,
        out_chunks: list[bytes],
        err_chunks: list[bytes] | None = None,
        exit_code: int | None = 0,
        hang: bool = False,
        reap_code: int | None = None,
    ):
        self.stdout = FakeStream(out_chunks, hang=hang)
        self.stderr = FakeStream(err_chunks or [], hang=hang)
        self.returncode = exit_code
        self.reap_code = reap_code
        self.kill_count = 0

    def kill(self) -> None:
        self.kill_count += 1
        self.returncode = -9
        self.stdout.set_eof()
        self.stderr.set_eof()

    async def wait(self, timeout: float | None = None) -> int | None:
        """模拟 child-watcher 收尸：wait 后拿到确定退出码。"""
        if self.returncode is None and self.reap_code is not None:
            self.returncode = self.reap_code
        return self.returncode


def _patch_provider(monkeypatch, payloads: list[dict]) -> list[str]:
    urls = []

    async def fake_fetch(self, *, platform, video_id, original_url, **kwargs):
        urls.append(original_url)
        return payloads[min(len(urls) - 1, len(payloads) - 1)]

    monkeypatch.setattr(YtdlpProvider, "fetch_video_info", fake_fetch)
    return urls


@pytest.fixture
def spawn_record(monkeypatch):
    recorded: list[tuple] = []

    async def fake_exec(*cmd, **kwargs):
        recorded.append(cmd)
        assert kwargs.get("stdout") == asyncio.subprocess.PIPE
        assert kwargs.get("stderr") == asyncio.subprocess.PIPE
        return FakeProcess(out_chunks=[b"frag1", b"frag2"], exit_code=0)

    monkeypatch.setattr(stream_mod.asyncio, "create_subprocess_exec", fake_exec)
    return recorded


def _assert_common_argv(cmd: tuple, playlist_url: str) -> None:
    assert cmd[0] == "ffmpeg"
    assert cmd[1:4] == ("-hide_banner", "-loglevel", "error")
    assert cmd[cmd.index("-user_agent") + 1].startswith("Mozilla/5.0")
    blob = cmd[cmd.index("-headers") + 1]
    assert "Referer: https://twitter.com/\r\n" in blob
    assert "Sec-Fetch-Mode: navigate\r\n" in blob
    assert "User-Agent" not in blob  # UA 走 -user_agent，不进 -headers
    assert cmd[cmd.index("-i") + 1] == playlist_url
    assert cmd[cmd.index("-i") + 2 :] == (
        "-c",
        "copy",
        "-bsf:a",
        "aac_adtstoasc",
        "-f",
        "mp4",
        "-movflags",
        "frag_keyframe+empty_moov",
        "-frag_duration",
        "4000000",
        "pipe:1",
    )


class TestStreamEndpoint:

    def test_happy_path_headers_body_and_argv(
        self, authed_client, spawn_record, monkeypatch
    ):
        _patch_provider(monkeypatch, [_meta()])
        resp = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/mp4"
        assert (
            resp.headers["content-disposition"]
            == f'attachment; filename="space-{SPACE_ID}.m4a"'
        )
        assert "accept-ranges" not in resp.headers
        assert resp.content == b"frag1frag2"
        assert len(spawn_record) == 1
        _assert_common_argv(spawn_record[0], _meta()["url"])  # fixture 原文 playlist

    def test_rejects_bad_space_id(self, authed_client):
        resp = authed_client.get("/api/stream/twitter_space/1AKEmvYqbrOK")
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Invalid X Space id"

    def test_extracts_fresh_playlist_per_request(
        self, authed_client, spawn_record, monkeypatch
    ):
        """design 不变式 2：playlist 短命，每次请求必须现取，第二次不得复用。"""
        payload_a = _meta() | {"url": PLAYLIST_A}
        payload_b = _meta() | {"url": PLAYLIST_B}
        urls = _patch_provider(monkeypatch, [payload_a, payload_b])
        assert authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}").content
        assert authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}").content
        assert urls == [
            f"https://x.com/i/spaces/{SPACE_ID}",
            f"https://x.com/i/spaces/{SPACE_ID}",
        ]
        assert spawn_record[0][spawn_record[0].index("-i") + 1] == PLAYLIST_A
        assert spawn_record[1][spawn_record[1].index("-i") + 1] == PLAYLIST_B

    def test_live_space_maps_400(self, authed_client, monkeypatch):
        async def live(self, **kwargs):
            raise SpaceNotReplayError("twitter space x is not a replay (live_status=is_live)")

        monkeypatch.setattr(YtdlpProvider, "fetch_video_info", live)
        resp = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert resp.status_code == 400
        assert "is_live" in resp.json()["detail"]

    def test_unavailable_space_maps_404(self, authed_client, monkeypatch):
        async def gone(self, **kwargs):
            raise VideoNotFoundError(
                "twitter space replay unavailable: replay is disabled"
            )

        monkeypatch.setattr(YtdlpProvider, "fetch_video_info", gone)
        resp = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert resp.status_code == 404
        assert "pscp.tv" not in resp.text

    def test_extraction_failure_maps_502(self, authed_client, monkeypatch):
        async def boom(self, **kwargs):
            raise ProviderError("yt-dlp extraction failed: [redacted-playlist-url]")

        monkeypatch.setattr(YtdlpProvider, "fetch_video_info", boom)
        resp = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert resp.status_code == 502
        assert resp.json()["detail"].startswith("X Space extraction failed")

    def test_stream_requires_api_key(self, client, spawn_record):
        """router 级 verify_api_key 覆盖本端点：无 key 显式 401，且不触发提取。
        （gate primary security-twitter-space-auth-bypass 前提不成立，此测试钉死。）"""
        original = settings.API_KEY
        settings.API_KEY = "test-key-123"
        try:
            resp = client.get(f"/api/stream/twitter_space/{SPACE_ID}")
            assert resp.status_code == 401
            assert spawn_record == []
        finally:
            settings.API_KEY = original

    def test_429_when_concurrency_full(self, authed_client, spawn_record, monkeypatch):
        monkeypatch.setattr(settings, "MAX_CONCURRENT_STREAMS", 0)
        stream_mod.stream_limiter.reset()
        try:
            resp = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
            assert resp.status_code == 429
            assert resp.json()["detail"] == "Too many concurrent streams"
        finally:
            stream_mod.stream_limiter.reset()

    def test_ffmpeg_instant_failure_maps_502(
        self, authed_client, monkeypatch
    ):
        async def fake_exec(*cmd, **kwargs):
            return FakeProcess(
                out_chunks=[],
                err_chunks=[
                    b"Unable to open " + PLAYLIST_A.encode() + b"\n",
                ],
                exit_code=1,
            )

        monkeypatch.setattr(stream_mod.asyncio, "create_subprocess_exec", fake_exec)
        _patch_provider(monkeypatch, [_meta()])
        resp = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert resp.status_code == 502
        assert "pscp.tv" not in resp.text  # 短命 playlist URL 不回显给客户端

    def test_no_playlist_url_maps_502(self, authed_client, monkeypatch):
        payload = _meta() | {"url": ""}
        _patch_provider(monkeypatch, [payload])
        resp = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert resp.status_code == 502


class TestForwardLoopLifecycle:
    """直接驱动转发生成器，验证断连清理（与视频号 _iter_windows 测试同法）。"""

    async def _drain_task(self, process: FakeProcess, sink: bytearray):
        return asyncio.create_task(stream_mod._drain_stderr(process.stderr, sink))

    @pytest.mark.asyncio
    async def test_disconnect_kills_process_and_closes_pipes(self):
        process = FakeProcess(out_chunks=[], err_chunks=[], exit_code=None, hang=True)
        sink = bytearray()
        drain = await self._drain_task(process, sink)
        agen = stream_mod._forward_ffmpeg_output(
            process, drain, b"first-chunk", sink, SPACE_ID
        )
        chunk = await agen.__anext__()
        assert chunk == b"first-chunk"
        await agen.aclose()
        await asyncio.sleep(0.05)
        assert process.kill_count == 1
        assert drain.cancelled()

    @pytest.mark.asyncio
    async def test_normal_eof_does_not_kill_and_logs_no_error(self):
        process = FakeProcess(
            out_chunks=[b"rest"], err_chunks=[], exit_code=0
        )
        sink = bytearray()
        drain = await self._drain_task(process, sink)
        agen = stream_mod._forward_ffmpeg_output(
            process, drain, b"first", sink, SPACE_ID
        )
        collected = [chunk async for chunk in agen]
        assert collected == [b"first", b"rest"]
        assert process.kill_count == 0
        assert drain.done() and not drain.cancelled()

    @pytest.mark.asyncio
    async def test_abnormal_exit_mid_stream_raises_instead_of_clean_end(self):
        """fail-loud：ffmpeg 中途异常退出时生成器必须抛错中断响应（客户端看到传输中断），
        不许把截断文件当成功流收尾（gate primary finding
correctness-ffmpeg-partial-output-success）。"""
        process = FakeProcess(out_chunks=[b"half"], err_chunks=[], exit_code=1)
        sink = bytearray()
        drain = await self._drain_task(process, sink)
        agen = stream_mod._forward_ffmpeg_output(
            process, drain, b"first", sink, SPACE_ID
        )
        assert await agen.__anext__() == b"first"
        assert await agen.__anext__() == b"half"
        with pytest.raises(RuntimeError, match="before stream completion"):
            await agen.__anext__()

    @pytest.mark.asyncio
    async def test_returncode_none_race_still_fails_loud(self):
        """收尸竞态：stdout EOF 但 child-watcher 未跑完（returncode 仍 None）时，
        必须先等 wait 拿确定退出码再判定，不许把截断流当成功收尾
        （gate primary correctness-ffmpeg-partial-output-success 第二轮）。"""
        process = FakeProcess(out_chunks=[b"half"], exit_code=None, reap_code=1)
        sink = bytearray()
        drain = await self._drain_task(process, sink)
        agen = stream_mod._forward_ffmpeg_output(
            process, drain, b"first", sink, SPACE_ID
        )
        assert await agen.__anext__() == b"first"
        assert await agen.__anext__() == b"half"
        with pytest.raises(RuntimeError, match="before stream completion"):
            await agen.__anext__()
        assert process.returncode == 1

    @pytest.mark.asyncio
    async def test_read_hang_times_out_kills_and_raises(self, monkeypatch):
        """读挂死必须超时释放并发槽并 kill 子进程（gate primary finding
reliability-ffmpeg-read-hang）；超时旋钮复用 HTTP_TIMEOUT_SECONDS。"""
        from app.core.config import settings

        monkeypatch.setattr(settings, "HTTP_TIMEOUT_SECONDS", 0.1)
        process = FakeProcess(out_chunks=[], err_chunks=[], exit_code=None, hang=True)
        sink = bytearray()
        drain = await self._drain_task(process, sink)
        agen = stream_mod._forward_ffmpeg_output(
            process, drain, b"first", sink, SPACE_ID
        )
        assert await agen.__anext__() == b"first"
        with pytest.raises(RuntimeError, match="stalled"):
            await agen.__anext__()
        assert process.kill_count == 1
        await asyncio.sleep(0.05)  # 取消送达需要一拍循环
        assert drain.cancelled()
