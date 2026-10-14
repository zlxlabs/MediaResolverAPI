"""X Space 两阶段交付端点：全部网络与 ffmpeg 子进程 stub。

交付形态 v2（后台获取 + 文件交付）：GET 命中 final → 文件下载（Content-Length + Range）；
未命中 → spawn 后台获取任务并立即 202；轮询至终态；错误映射（400/404/502）在轮询时
呈现且失败态消费一次后重新 spawn；完整性门禁（ENDLIST + 段数对账 + exit 0）缺一
不产 final；排队占槽不 429；ffmpeg argv/段泵语义由后台任务复用。
"""

from __future__ import annotations

import asyncio
import errno
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urljoin

import httpx
import pytest
from fastapi.testclient import TestClient

import app.api.stream as stream_mod
import app.services.space_cache as space_cache
from app.core.config import settings
from app.main import app
from app.services.providers.base import ProviderError, VideoNotFoundError
from app.services.providers.ytdlp import SpaceNotReplayError, YtdlpProvider

FIXTURES = Path(__file__).parent / "fixtures" / "twitter_space"
SPACE_ID = "1AKEmvYqbrOKL"
AUTH = {"X-API-Key": "test-key-123"}
SEG_1 = "https://prod-fastly-us-west-1.video.pscp.tv/a/chunk_1_a.aac"
SEG_2 = "https://prod-fastly-us-west-1.video.pscp.tv/a/chunk_2_a.aac"
PLAYLIST_TEXT = (
    "#EXTM3U\n#EXT-X-TARGETDURATION:4\n"
    f"#EXTINF:3.008,\n{SEG_1}\n#EXTINF:3.0,\n{SEG_2}\n#EXT-X-ENDLIST\n"
)


@pytest.fixture(autouse=True)
def _space_cache_isolated(monkeypatch, tmp_path):
    """物化缓存目录指到本测试 tmp；模块级单飞状态逐测试清零。"""
    monkeypatch.setattr(settings, "SPACE_CACHE_DIR", str(tmp_path / "space-cache"))
    space_cache._tasks.clear()
    monkeypatch.setattr(space_cache, "_started", False)
    monkeypatch.setattr(space_cache, "_lock", None)
    yield
    space_cache._tasks.clear()


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


class FakeStdin:
    """记录写入；hang_on 命中时阻塞写入（模拟背压/stall），close 后 no-op。

    gate_stdout=True 时模拟真实 ffmpeg 的 stdin/stdout 互锁：stdin 收到数据前
    stdout 不产出（write 解锁 stdout 的 EOF）。
    """

    def __init__(self, *, hang_on: bytes | None = None, broken: bool = False, gate_stdout: FakeStream | None = None):
        self.received = bytearray()
        self.closed = False
        self._hang_on = hang_on
        self._broken = broken
        self._gate_stdout = gate_stdout
        self._release = asyncio.Event()

    def write(self, data: bytes) -> None:
        """镜像真实 StreamWriter.write：同步缓冲写，无 await。"""
        if self._broken:
            raise BrokenPipeError("Broken pipe")
        self.received.extend(data)
        if self._gate_stdout is not None and self.received:
            self._gate_stdout.set_eof()

    async def drain(self) -> None:
        # 真实 StreamWriter.drain 的背压语义：缓冲满时挂起
        if self._hang_on is not None and self._hang_on in bytes(self.received):
            await self._release.wait()

    def close(self) -> None:
        self.closed = True
        self._release.set()

    async def wait_closed(self) -> None:
        self.closed = True
        self._release.set()


class FakeProcess:
    def __init__(
        self,
        *,
        out_chunks: list[bytes],
        err_chunks: list[bytes] | None = None,
        exit_code: int | None = 0,
        hang: bool = False,
        reap_code: int | None = None,
        stdin: FakeStdin | None = None,
    ):
        self.stdout = FakeStream(out_chunks, hang=hang)
        self.stderr = FakeStream(err_chunks or [], hang=hang)
        self.stdin = stdin if stdin is not None else FakeStdin()
        self.returncode = exit_code
        self.reap_code = reap_code
        self.kill_count = 0
        self._exited = asyncio.Event()
        if exit_code is not None:
            self._exited.set()

    def kill(self) -> None:
        self.kill_count += 1
        self.returncode = -9
        self._exited.set()
        self.stdout.set_eof()
        self.stderr.set_eof()

    async def wait(self, timeout: float | None = None) -> int | None:
        """模拟真实 wait：未收尸且无确定退出码时阻塞（直到 kill/reap）。"""
        if self.returncode is None:
            if self.reap_code is not None:
                self.returncode = self.reap_code
            else:
                await self._exited.wait()
        return self.returncode


def _patch_provider(monkeypatch, payloads: list[dict]) -> list[str]:
    urls = []

    async def fake_fetch(self, *, platform, video_id, original_url, **kwargs):
        urls.append(original_url)
        return payloads[min(len(urls) - 1, len(payloads) - 1)]

    monkeypatch.setattr(YtdlpProvider, "fetch_video_info", fake_fetch)
    return urls


def _patch_playlist(monkeypatch, text: str, segments: list[str]):
    """后台任务消费的是 _fetch_media_playlist（含 playlist 全文，供 ENDLIST 门禁）。"""

    async def fake_fetch(playlist_url, headers):
        return text, segments

    monkeypatch.setattr(stream_mod, "_fetch_media_playlist", fake_fetch)


def _patch_segment_chunks(monkeypatch, mapping: dict[str, list[bytes]]):
    """按 URL 吐字节；mapping 未覆盖的 URL 视为 HTTP 404。"""

    async def chunks(url, headers, chunk_size, client=None):
        if url not in mapping:
            raise stream_mod.SegmentFetchError("segment HTTP 404")
        for piece in mapping[url]:
            yield piece

    monkeypatch.setattr(stream_mod, "_stream_url_chunks", chunks)


def _poll_until_terminal(client, timeout: float = 5.0):
    """轮询 202 至终态（TestClient portal loop 与后台任务并发推进）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        if resp.status_code != 202:
            return resp
        time.sleep(0.02)
    pytest.fail("materialization did not reach a terminal status in time")


def _make_final(space_id: str, payload: bytes) -> Path:
    space_cache.ensure_dir()
    final = space_cache.final_path(space_id)
    final.write_bytes(payload)
    return final


@pytest.fixture
def spawn_record(monkeypatch):
    recorded: list[tuple] = []

    async def fake_exec(*cmd, **kwargs):
        recorded.append(cmd)
        assert kwargs.get("stdout") == asyncio.subprocess.PIPE
        assert kwargs.get("stderr") == asyncio.subprocess.PIPE
        assert kwargs.get("stdin") == asyncio.subprocess.PIPE
        return FakeProcess(out_chunks=[b"frag1", b"frag2"], exit_code=0)

    monkeypatch.setattr(stream_mod.asyncio, "create_subprocess_exec", fake_exec)
    return recorded


def _assert_common_argv(cmd: tuple) -> None:
    """stdin 形态：ADTS 从 pipe:0 读（ffmpeg 的 ADTS 解复用器名为 aac），http 抓取参数不再出现。"""
    assert cmd[0] == "ffmpeg"
    assert cmd[1:4] == ("-hide_banner", "-loglevel", "error")
    assert cmd[4:8] == ("-f", "aac", "-i", "pipe:0")
    assert "-user_agent" not in cmd
    assert "-headers" not in cmd
    assert cmd[8:] == (
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
    """两阶段交付语义：未命中 202（spawn 后台任务，脱离连接）→ 轮询 → final 文件下载。"""

    def test_happy_path_202_then_200_file(
        self, authed_client, spawn_record, monkeypatch
    ):
        _patch_provider(monkeypatch, [_meta()])
        _patch_playlist(monkeypatch, PLAYLIST_TEXT, [SEG_1, SEG_2])
        _patch_segment_chunks(monkeypatch, {SEG_1: [b"aaa", b"bbb"], SEG_2: [b"ccc"]})
        first = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert first.status_code == 202
        assert first.json() == {"status": "extracting", "retry_after": 5}
        resp = _poll_until_terminal(authed_client)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/mp4"
        assert (
            resp.headers["content-disposition"]
            == f'attachment; filename="space-{SPACE_ID}.m4a"'
        )
        assert resp.headers["content-length"] == str(len(b"frag1frag2"))
        assert resp.content == b"frag1frag2"
        assert len(spawn_record) == 1
        _assert_common_argv(spawn_record[0])
        # 改名即完整态：final 在盘上，.part 已消失
        assert space_cache.final_path(SPACE_ID).is_file()
        assert not space_cache.part_path(SPACE_ID).exists()

    def test_all_segments_reach_ffmpeg_stdin(self, authed_client, monkeypatch):
        _patch_provider(monkeypatch, [_meta()])
        _patch_playlist(monkeypatch, PLAYLIST_TEXT, [SEG_1, SEG_2])
        stdin = FakeStdin()

        async def fake_exec(*cmd, **kwargs):
            return FakeProcess(out_chunks=[b"frag1", b"frag2"], exit_code=0, stdin=stdin)

        monkeypatch.setattr(stream_mod.asyncio, "create_subprocess_exec", fake_exec)
        _patch_segment_chunks(
            monkeypatch, {SEG_1: [b"seg1a", b"seg1b"], SEG_2: [b"seg2a"]}
        )
        resp = _poll_until_terminal(authed_client)
        assert resp.status_code == 200
        assert bytes(stdin.received) == b"seg1aseg1bseg2a"
        assert stdin.closed  # 全部段写完后 stdin 必须关闭让 ffmpeg 收尾

    def test_single_flight_and_cached_repeat_no_refetch(
        self, authed_client, spawn_record, monkeypatch
    ):
        """进行中重复 GET 复用同一任务（单飞）；final 命中后不再提取/抓取（根治重复消耗代理）。"""
        fetch_calls = []

        async def slow_fetch(self, **kwargs):
            fetch_calls.append(kwargs["video_id"])
            await asyncio.sleep(0.15)
            return _meta()

        monkeypatch.setattr(YtdlpProvider, "fetch_video_info", slow_fetch)
        _patch_playlist(monkeypatch, PLAYLIST_TEXT, [SEG_1])
        _patch_segment_chunks(monkeypatch, {SEG_1: [b"seg"]})
        first = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert first.status_code == 202
        second = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert second.status_code == 202
        resp = _poll_until_terminal(authed_client)
        assert resp.status_code == 200
        assert resp.content == b"frag1frag2"
        assert fetch_calls == [SPACE_ID]
        cached = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert cached.status_code == 200
        assert cached.content == b"frag1frag2"
        assert fetch_calls == [SPACE_ID]  # 缓存命中：零重复提取

    def test_final_delivery_supports_range_resume(self, authed_client, monkeypatch):
        """阶段二：对 final 普通文件下载，Range/断点续传由 Starlette FileResponse 原生承担。"""
        payload = bytes(range(256)) * 64
        _make_final(SPACE_ID, payload)
        full = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert full.status_code == 200
        assert full.headers["content-length"] == str(len(payload))
        assert full.headers["accept-ranges"] == "bytes"
        assert full.content == payload
        head = authed_client.get(
            f"/api/stream/twitter_space/{SPACE_ID}", headers={"Range": "bytes=0-9"}
        )
        assert head.status_code == 206
        assert head.content == payload[:10]
        assert head.headers["content-range"] == f"bytes 0-9/{len(payload)}"
        tail = authed_client.get(
            f"/api/stream/twitter_space/{SPACE_ID}", headers={"Range": "bytes=100-"}
        )
        assert tail.status_code == 206
        assert tail.content == payload[100:]

    def test_unsatisfiable_range_returns_416_with_representation_length(
        self, authed_client
    ):
        payload = b"0123456789"
        _make_final(SPACE_ID, payload)
        for value in ("bytes=10-", "bytes=8-2"):
            resp = authed_client.get(
                f"/api/stream/twitter_space/{SPACE_ID}", headers={"Range": value}
            )
            assert resp.status_code == 416
            assert resp.headers["content-range"] == f"bytes */{len(payload)}"
            assert resp.content == b""

    def test_malformed_range_is_ignored_as_full_response(self, authed_client):
        payload = b"0123456789"
        _make_final(SPACE_ID, payload)
        resp = authed_client.get(
            f"/api/stream/twitter_space/{SPACE_ID}", headers={"Range": "bytes=abc"}
        )
        assert resp.status_code == 200
        assert resp.content == payload
        assert "content-range" not in resp.headers

    def test_rejects_bad_space_id(self, authed_client):
        resp = authed_client.get("/api/stream/twitter_space/1AKEmvYqbrOK")
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Invalid X Space id"

    def test_live_space_maps_400_after_poll(self, authed_client, monkeypatch):
        """错误契约保持：直播中仍 400，只是在轮询时呈现（提取在后台任务内）。"""

        async def live(self, **kwargs):
            raise SpaceNotReplayError(
                "twitter space x is not a replay (live_status=is_live)"
            )

        monkeypatch.setattr(YtdlpProvider, "fetch_video_info", live)
        first = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert first.status_code == 202
        resp = _poll_until_terminal(authed_client)
        assert resp.status_code == 400
        assert "is_live" in resp.json()["detail"]
        assert not space_cache.final_path(SPACE_ID).exists()

    def test_unavailable_space_maps_404_after_poll(self, authed_client, monkeypatch):
        async def gone(self, **kwargs):
            raise VideoNotFoundError(
                "twitter space replay unavailable: replay is disabled"
            )

        monkeypatch.setattr(YtdlpProvider, "fetch_video_info", gone)
        first = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert first.status_code == 202
        resp = _poll_until_terminal(authed_client)
        assert resp.status_code == 404
        assert "pscp.tv" not in resp.text

    def test_extraction_failure_maps_502_after_poll(self, authed_client, monkeypatch):
        async def boom(self, **kwargs):
            raise ProviderError("yt-dlp extraction failed: [redacted-playlist-url]")

        monkeypatch.setattr(YtdlpProvider, "fetch_video_info", boom)
        first = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert first.status_code == 202
        resp = _poll_until_terminal(authed_client)
        assert resp.status_code == 502
        assert resp.json()["detail"].startswith("X Space materialization failed")

    def test_failure_is_retryable_next_get_respawns(
        self, authed_client, spawn_record, monkeypatch
    ):
        """失败态不持久（锁定决策 4）：消费一次后，下次 GET 重新 spawn。"""
        calls = {"n": 0}

        async def flaky(self, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ProviderError("yt-dlp extraction failed: transient")
            return _meta()

        monkeypatch.setattr(YtdlpProvider, "fetch_video_info", flaky)
        _patch_playlist(monkeypatch, PLAYLIST_TEXT, [SEG_1])
        _patch_segment_chunks(monkeypatch, {SEG_1: [b"seg"]})
        first = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert first.status_code == 202
        resp = _poll_until_terminal(authed_client)
        assert resp.status_code == 502
        again = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert again.status_code == 202  # 重新 spawn，不再是 502
        resp = _poll_until_terminal(authed_client)
        assert resp.status_code == 200
        assert calls["n"] == 2

    def test_playlist_fetch_failure_maps_502_and_redacts(
        self, authed_client, monkeypatch
    ):
        async def fail(playlist_url, headers):
            raise stream_mod.SegmentFetchError(
                "playlist fetch failed: https://x.video.pscp.tv/a/playlist.m3u8 boom"
            )

        monkeypatch.setattr(stream_mod, "_fetch_media_playlist", fail)
        _patch_provider(monkeypatch, [_meta()])
        first = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert first.status_code == 202
        resp = _poll_until_terminal(authed_client)
        assert resp.status_code == 502
        assert "pscp.tv" not in resp.text

    def test_stream_requires_api_key(self, client, spawn_record):
        """router 级 verify_api_key 覆盖本端点：无 key 显式 401，且不触发提取。"""
        original = settings.API_KEY
        settings.API_KEY = "test-key-123"
        try:
            resp = client.get(f"/api/stream/twitter_space/{SPACE_ID}")
            assert resp.status_code == 401
            assert spawn_record == []
        finally:
            settings.API_KEY = original

    def test_full_slots_queue_instead_of_429(
        self, authed_client, spawn_record, monkeypatch
    ):
        """锁定决策 3/8：后台任务排队等槽（不 429、不翻转 202→502）；
        拿到槽之前不提取（不占上游代理出口）。"""
        monkeypatch.setattr(settings, "MAX_CONCURRENT_STREAMS", 1)
        stream_mod.stream_limiter.reset()
        fetch_calls = []

        async def fetch(self, **kwargs):
            fetch_calls.append(kwargs["video_id"])
            return _meta()

        monkeypatch.setattr(YtdlpProvider, "fetch_video_info", fetch)
        _patch_playlist(monkeypatch, PLAYLIST_TEXT, [SEG_1])
        _patch_segment_chunks(monkeypatch, {SEG_1: [b"seg"]})
        stream_mod.stream_limiter.active = 1  # 槽被占（如视频号流在跑）
        try:
            resp = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
            assert resp.status_code == 202
            assert fetch_calls == []  # 排队中：提取尚未开始
            stream_mod.stream_limiter.active = 0  # 释放槽
            resp = _poll_until_terminal(authed_client)
            assert resp.status_code == 200
            assert fetch_calls == [SPACE_ID]
        finally:
            stream_mod.stream_limiter.reset()

    def test_ffmpeg_instant_failure_maps_502(
        self, authed_client, monkeypatch
    ):
        async def fake_exec(*cmd, **kwargs):
            return FakeProcess(out_chunks=[], err_chunks=[b"failed\n"], exit_code=1)

        monkeypatch.setattr(stream_mod.asyncio, "create_subprocess_exec", fake_exec)
        _patch_provider(monkeypatch, [_meta()])
        _patch_playlist(monkeypatch, PLAYLIST_TEXT, [SEG_1])
        _patch_segment_chunks(monkeypatch, {SEG_1: [b"seg"]})
        first = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert first.status_code == 202
        resp = _poll_until_terminal(authed_client)
        assert resp.status_code == 502
        assert "pscp.tv" not in resp.text  # 短命 playlist URL 不回显给客户端
        assert not space_cache.final_path(SPACE_ID).exists()

    def test_no_playlist_url_maps_502(self, authed_client, monkeypatch):
        payload = _meta() | {"url": ""}
        _patch_provider(monkeypatch, [payload])
        first = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert first.status_code == 202
        resp = _poll_until_terminal(authed_client)
        assert resp.status_code == 502

    def test_empty_playlist_maps_502(self, authed_client, monkeypatch):
        async def fake_text(url, headers):
            return "#EXTM3U\n"

        monkeypatch.setattr(stream_mod, "_fetch_text", fake_text)
        _patch_provider(monkeypatch, [_meta()])
        first = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert first.status_code == 202
        resp = _poll_until_terminal(authed_client)
        assert resp.status_code == 502


class TestMaterializationIntegrity:
    """完整性门禁（锁定决策 5）与故障矩阵（锁定决策 6）：坏列表/坏段/磁盘满
    不发布 final、只留可回收临时文件；交付承诺 = playlist 声明内容的完整交付。"""

    def test_playlist_without_endlist_fails_no_final_no_part(
        self, authed_client, spawn_record, monkeypatch
    ):
        """无 #EXT-X-ENDLIST = 非回放/未完结，fail、不产 final。

        全 stub（含子进程）：若门禁被旁路，伪造的 ffmpeg exit 0 会让物化「成功」
        发布 final，本测试红在 assert not final（比依赖真 ffmpeg 的退出码更准）。
        """
        _patch_provider(monkeypatch, [_meta()])
        _patch_playlist(
            monkeypatch, f"#EXTM3U\n#EXTINF:3.0,\n{SEG_1}\n", [SEG_1]
        )
        _patch_segment_chunks(monkeypatch, {SEG_1: [b"data"]})
        first = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert first.status_code == 202
        resp = _poll_until_terminal(authed_client)
        assert resp.status_code == 502
        assert "ENDLIST" in resp.json()["detail"]
        assert not space_cache.final_path(SPACE_ID).exists()
        assert not space_cache.part_path(SPACE_ID).exists()

    @pytest.mark.asyncio
    async def test_part_open_failure_does_not_spawn_process_or_tasks(
        self, monkeypatch, tmp_path
    ):
        """打开 .part 失败时，尚未 spawn ffmpeg，也没有 drain/pump 残留。"""
        part = tmp_path / "space.part"
        spawned = []
        original_open = Path.open

        async def spawn_process():
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "import time; time.sleep(60)",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            spawned.append(process)
            return process

        def fail_part_open(path, mode="r", *args, **kwargs):
            if path == part and mode == "wb":
                raise PermissionError("cannot open part")
            return original_open(path, mode, *args, **kwargs)

        monkeypatch.setattr(stream_mod, "_spawn_ffmpeg", spawn_process)
        monkeypatch.setattr(Path, "open", fail_part_open)
        _patch_provider(monkeypatch, [_meta()])
        _patch_playlist(monkeypatch, PLAYLIST_TEXT, [SEG_1])
        before = asyncio.all_tasks()
        try:
            with pytest.raises(PermissionError, match="cannot open part"):
                await stream_mod._materialize_to_file(SPACE_ID, part)
            leaked = [
                task
                for task in asyncio.all_tasks() - before
                if task.get_coro().__qualname__.split(".")[-1]
                in {"_drain_stderr", "_pump_segments"}
            ]
            assert (spawned, leaked) == ([], [])
        finally:
            for process in spawned:
                if process.returncode is None:
                    process.kill()
                await process.wait()
            pending = [
                task
                for task in asyncio.all_tasks() - before
                if task.get_coro().__qualname__.split(".")[-1]
                in {"_drain_stderr", "_pump_segments"}
            ]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_cancelled_materialization_reaps_resources_and_discards_part(
        self, monkeypatch
    ):
        """中途取消会清进程、任务、并发槽与 .part，不留磁盘残件。"""
        space_cache.ensure_dir()
        _patch_provider(monkeypatch, [_meta()])
        _patch_playlist(monkeypatch, PLAYLIST_TEXT, [SEG_1])
        _patch_segment_chunks(monkeypatch, {SEG_1: [b"segment"]})
        process = FakeProcess(out_chunks=[], exit_code=None, hang=True)
        reading_stdout = asyncio.Event()
        original_read = process.stdout.read

        async def wait_for_output(n):
            reading_stdout.set()
            return await original_read(n)

        async def fake_spawn():
            return process

        process.stdout.read = wait_for_output
        monkeypatch.setattr(stream_mod, "_spawn_ffmpeg", fake_spawn)
        before = asyncio.all_tasks()
        task = asyncio.create_task(stream_mod._materialize_space(SPACE_ID))
        try:
            await asyncio.wait_for(reading_stdout.wait(), timeout=1)
            part = space_cache.part_path(SPACE_ID)
            assert part.exists()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0)
            leaked = [
                pending
                for pending in asyncio.all_tasks() - before
                if pending.get_coro().__qualname__.split(".")[-1]
                in {"_drain_stderr", "_pump_segments"}
                and not pending.done()
            ]
            assert process.kill_count == 1
            assert leaked == []
            assert stream_mod.stream_limiter.active == 0
            assert not part.exists()
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_sink_write_failure_closes_generator_and_reaps_tasks(self, monkeypatch):
        """落盘中途 ENOSPC 时，已启动的输出生成器关闭进程及后台任务。"""
        space_cache.ensure_dir()
        part = space_cache.part_path(SPACE_ID)
        _patch_provider(monkeypatch, [_meta()])
        _patch_playlist(monkeypatch, PLAYLIST_TEXT, [SEG_1])
        _patch_segment_chunks(monkeypatch, {SEG_1: [b"segment"]})
        process = FakeProcess(out_chunks=[b"fragment"], exit_code=None)

        class FailingSink:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def write(self, data):
                if data:
                    raise OSError(errno.ENOSPC, "No space left on device")

        original_open = Path.open

        def open_sink(path, mode="r", *args, **kwargs):
            if path == part and mode == "wb":
                return FailingSink()
            return original_open(path, mode, *args, **kwargs)

        async def fake_spawn():
            return process

        monkeypatch.setattr(Path, "open", open_sink)
        monkeypatch.setattr(stream_mod, "_spawn_ffmpeg", fake_spawn)
        before = asyncio.all_tasks()
        with pytest.raises(OSError, match="No space left on device"):
            await stream_mod._materialize_to_file(SPACE_ID, part)
        await asyncio.sleep(0)
        leaked = [
            pending
            for pending in asyncio.all_tasks() - before
            if pending.get_coro().__qualname__.split(".")[-1]
            in {"_drain_stderr", "_pump_segments"}
            and not pending.done()
        ]
        assert process.kill_count == 1
        assert leaked == []

    def test_middle_segment_404_no_final_no_part(
        self, authed_client, spawn_record, monkeypatch
    ):
        _patch_provider(monkeypatch, [_meta()])
        _patch_playlist(monkeypatch, PLAYLIST_TEXT, [SEG_1, SEG_2])

        async def chunks(url, headers, chunk_size, client=None):
            if url == SEG_1:
                yield b"ok1"
            else:
                raise stream_mod.SegmentFetchError("segment HTTP 404")

        monkeypatch.setattr(stream_mod, "_stream_url_chunks", chunks)
        first = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert first.status_code == 202
        resp = _poll_until_terminal(authed_client)
        assert resp.status_code == 502
        assert not space_cache.final_path(SPACE_ID).exists()
        assert not space_cache.part_path(SPACE_ID).exists()

    def test_enospc_discards_part_and_stays_retryable(
        self, authed_client, spawn_record, monkeypatch
    ):
        """磁盘满（ENOSPC）：fail-loud、.part 删除、不产 final；下次 GET 重新 spawn。"""
        calls = {"n": 0}

        def fake_forward(process, writer, drain, first, stderr_tail, space_id):
            calls["n"] += 1

            async def gen():
                yield b"half"
                raise OSError(errno.ENOSPC, "No space left on device")

            return gen()

        monkeypatch.setattr(stream_mod, "_forward_ffmpeg_output", fake_forward)
        _patch_provider(monkeypatch, [_meta(), _meta()])
        _patch_playlist(monkeypatch, PLAYLIST_TEXT, [SEG_1])
        _patch_segment_chunks(monkeypatch, {SEG_1: [b"seg"]})
        first = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert first.status_code == 202
        resp = _poll_until_terminal(authed_client)
        assert resp.status_code == 502
        assert not space_cache.final_path(SPACE_ID).exists()
        assert not space_cache.part_path(SPACE_ID).exists()
        again = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert again.status_code == 202
        # R2 修复轮改写：轮询至第二次任务终态再对账（前轮踩坑 3：202 返回时后台任务
        # 可能尚未推进到 ffmpeg 段，即时计数是竞态判据；终态计数才是稳定判据）。
        resp2 = _poll_until_terminal(authed_client)
        assert resp2.status_code == 502
        assert calls["n"] == 2

    def test_segment_count_mismatch_blocks_publish(
        self, authed_client, spawn_record, monkeypatch
    ):
        """段数对账（锁定决策 5）：声明段数 != 实际写入段数 → 不发布 final。
        注入方式：直接对 pump 计数器动手脚（只改判据来源，不全局替换）。"""
        _patch_provider(monkeypatch, [_meta()])
        _patch_playlist(monkeypatch, PLAYLIST_TEXT, [SEG_1, SEG_2])
        _patch_segment_chunks(monkeypatch, {SEG_1: [b"s1"], SEG_2: [b"s2"]})
        original_pump = stream_mod._pump_segments

        async def pump_with_lost_count(segment_urls, headers, process, progress=None):
            result = await original_pump(segment_urls, headers, process, progress)
            if progress is not None:
                # pump 成功后篡改计数：模拟「泵漏记一段」的回归，对账必须拦下发布
                progress["segments_done"] = len(segment_urls) - 1
            return result

        monkeypatch.setattr(stream_mod, "_pump_segments", pump_with_lost_count)
        first = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert first.status_code == 202
        resp = _poll_until_terminal(authed_client)
        assert resp.status_code == 502
        assert "reconciliation" in resp.json()["detail"]
        assert not space_cache.final_path(SPACE_ID).exists()
        assert not space_cache.part_path(SPACE_ID).exists()


class TestCacheFailureSurface:
    """缓存初始化失败 → 502 固定文案（非裸 500，review F3）；502 响应体不泄漏内部路径（F4）。"""

    @staticmethod
    def _client_no_raise() -> TestClient:
        return _no_raise_client()

    def test_cache_init_failure_maps_502_fixed_copy(self, monkeypatch):
        """ensure_started 抛 OSError（如目录不可写）：502 固定文案，不裸抛。"""
        def boom() -> None:
            raise PermissionError(13, "Permission denied", "/data/space-cache")

        monkeypatch.setattr(space_cache, "ensure_started", boom)
        with self._client_no_raise() as client:
            resp = client.get(f"/api/stream/twitter_space/{SPACE_ID}", headers=AUTH)
        assert resp.status_code == 502
        assert resp.json()["detail"] == "X Space cache storage is unavailable"

    def test_cache_start_oserror_maps_502_fixed_copy(self, monkeypatch):
        """start 内 ensure_dir 抛 OSError：502 固定文案，不裸抛。"""
        async def boom(space_id, factory):
            raise OSError(28, "No space left on device", str(space_cache.cache_dir()))

        monkeypatch.setattr(space_cache, "start", boom)
        with self._client_no_raise() as client:
            resp = client.get(f"/api/stream/twitter_space/{SPACE_ID}", headers=AUTH)
        assert resp.status_code == 502
        assert resp.json()["detail"] == "X Space cache storage is unavailable"

    def test_materialization_queue_rejects_when_pending_capacity_is_full(
        self, monkeypatch
    ):
        """后台 pending+running 总数有界；队列满时不承诺 202。"""
        gate = threading.Event()

        async def hold_materialization(space_id):
            await asyncio.to_thread(gate.wait)

        monkeypatch.setattr(space_cache, "_MAX_MATERIALIZATION_TASKS", 1, raising=False)
        monkeypatch.setattr(space_cache, "_disk_free_bytes", lambda: 10**15)
        monkeypatch.setattr(stream_mod, "_materialize_space", hold_materialization)
        with self._client_no_raise() as client:
            try:
                first = client.get(f"/api/stream/twitter_space/{SPACE_ID}", headers=AUTH)
                second_id = "1BKEmvYqbrOKL"
                second = client.get(
                    f"/api/stream/twitter_space/{second_id}", headers=AUTH
                )
                assert first.status_code == 202
                assert second.status_code == 503
                assert second.json()["detail"] == "X Space materialization queue is full"
                assert len(space_cache._tasks) == 1
            finally:
                gate.set()

    def test_low_disk_watermark_rejects_before_starting_materialization(
        self, monkeypatch
    ):
        """spawn 前用当前可用空间做 admission，低于水位返回固定 503。"""
        gate = threading.Event()

        async def hold_materialization(space_id):
            await asyncio.to_thread(gate.wait)

        monkeypatch.setattr(settings, "SPACE_CACHE_MIN_FREE_MB", 1)
        monkeypatch.setattr(space_cache, "_disk_free_bytes", lambda: 1024 * 1024 - 1)
        monkeypatch.setattr(stream_mod, "_materialize_space", hold_materialization)
        with self._client_no_raise() as client:
            try:
                resp = client.get(
                    f"/api/stream/twitter_space/{SPACE_ID}", headers=AUTH
                )
                assert resp.status_code == 503
                assert resp.json()["detail"] == "storage below watermark"
                assert not space_cache.part_path(SPACE_ID).exists()
                assert SPACE_ID not in space_cache._tasks
            finally:
                gate.set()

    def test_502_body_hides_internal_paths(self, authed_client, monkeypatch):
        """任务失败异常 str 含绝对路径（如 ENOSPC）：502 detail 只留 basename（F4）。"""
        part = space_cache.part_path(SPACE_ID)

        def fake_exec(*cmd, **kwargs):
            raise OSError(28, f"No space left on device: '{part}'")

        monkeypatch.setattr(stream_mod.asyncio, "create_subprocess_exec", fake_exec)
        _patch_provider(monkeypatch, [_meta()])
        _patch_playlist(monkeypatch, PLAYLIST_TEXT, [SEG_1])
        resp = _poll_until_terminal(authed_client)
        assert resp.status_code == 502
        detail = resp.json()["detail"]
        assert str(space_cache.cache_dir()) not in detail
        assert f"'{part.name}'" in detail  # basename 保留供诊断


def _no_raise_client() -> TestClient:
    """raise_server_exceptions=False：服务端异常变 500 响应而非直接抛出，
    使「裸抛 PermissionError / FileNotFoundError」类回归表现为可断言的状态码
    （而非测试 error），保证红验红型是断言失败。"""
    return TestClient(app, raise_server_exceptions=False)


class TestDeliveryOpenRace:
    """R2 minor：status=FINAL 与交付打开之间的清理竞态（unlink-while-open 只保已打开读者）。
    修复 = fd 前置打开（space_cache.open_final），打开后交付从 fd 读；
    打开失败（已被清理）是合法状态迁移 → 按未命中 spawn+202，非兜底非 500。"""

    def test_final_unlinked_after_status_maps_202_not_500(self, monkeypatch):
        """status 判定 FINAL 后、打开前被清理 → 202（未命中重拉）。"""
        real_status = space_cache.status

        def status_then_unlink(space_id):
            st = real_status(space_id)
            if st is space_cache.SpaceCacheStatus.FINAL:
                space_cache.final_path(space_id).unlink()  # 模拟判定后被 sweep 清
            return st

        monkeypatch.setattr(space_cache, "status", status_then_unlink)
        _make_final(SPACE_ID, b"to-be-swept-mid-decision")

        async def failing_fetch(self, **kwargs):
            raise ProviderError("no network in test")  # spawn 的重拉任务快速失败，不真跑

        monkeypatch.setattr(YtdlpProvider, "fetch_video_info", failing_fetch)
        with _no_raise_client() as client:
            resp = client.get(f"/api/stream/twitter_space/{SPACE_ID}", headers=AUTH)
        assert resp.status_code == 202
        assert resp.json() == {"status": "extracting", "retry_after": 5}

    def test_fd_survives_unlink_after_open(self, authed_client, monkeypatch):
        """fd 前置打开后文件被清理 → unlink-while-open 保读者，交付 200 完整内容。"""
        payload = b"unlink-while-open-still-readable"
        _make_final(SPACE_ID, payload)
        real_open_final = space_cache.open_final

        def open_then_unlink(space_id):
            fh = real_open_final(space_id)
            if fh is not None:
                space_cache.final_path(space_id).unlink()  # 模拟交付中 sweep unlink
            return fh

        monkeypatch.setattr(space_cache, "open_final", open_then_unlink)
        resp = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert resp.status_code == 200
        assert resp.content == payload
        assert resp.headers["content-length"] == str(len(payload))


class TestEmptySegmentGuard:
    """直测真实 _stream_url_chunks 的空体守卫（round-6 major）：200 + 0 字节 = 段失败。

    用 httpx.MockTransport 驱动真实现，不经 monkeypatch 替身。
    """

    def _client(self, body: bytes):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
        )

    @pytest.mark.asyncio
    async def test_zero_byte_200_rejected(self):
        async with self._client(b"") as client:
            gen = stream_mod._stream_url_chunks("https://s/1.aac", {}, 65536, client)
            with pytest.raises(stream_mod.SegmentFetchError, match="empty body"):
                async for _ in gen:
                    pass

    @pytest.mark.asyncio
    async def test_nonempty_200_passes(self):
        async with self._client(b"adtsdata") as client:
            got = []
            async for chunk in stream_mod._stream_url_chunks(
                "https://s/1.aac", {}, 65536, client
            ):
                got.append(chunk)
            assert b"".join(got) == b"adtsdata"


class TestProxyWiring:
    """YTDLP_PROXY 接线（x-space-egress-proxy）：X 链路三处 httpx.AsyncClient
    （_fetch_text / _stream_url_chunks 自建分支 / _pump_segments）设置时带
    proxy=，空时不传该参（空值路径零行为变化）。假占位代理，禁止真实凭据入仓。
    """

    FAKE_PROXY = "http://user:pass@proxy.example:7890"

    @staticmethod
    def _spy_async_client(monkeypatch):
        """包装 httpx.AsyncClient 记录构造 kwargs（断言点），再注入 MockTransport。

        proxy= 必须从测试替身里剥掉：httpx 0.28 的代理挂载优先于自定义
        transport，带着假代理会真拨号 DNS。代理能否眥通是 httpx 自身契约，
        本卡只断言「生产代码把 proxy= 传进了构造器」。
        """
        recorded = []
        real = httpx.AsyncClient

        def factory(*args, **kwargs):
            recorded.append(dict(kwargs))  # 副本：随后会从 kwargs 剥 proxy，不能共用引用
            kwargs.pop("proxy", None)
            return real(
                *args,
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, content=b"adtsdata")
                ),
                **kwargs,
            )

        monkeypatch.setattr(httpx, "AsyncClient", factory)
        return recorded

    async def test_fetch_text_passes_proxy_when_set(self, monkeypatch):
        recorded = self._spy_async_client(monkeypatch)
        monkeypatch.setattr(settings, "YTDLP_PROXY", self.FAKE_PROXY)
        await stream_mod._fetch_text("https://playlist.example/live.m3u8", {})
        assert recorded[0].get("proxy") == self.FAKE_PROXY

    async def test_fetch_text_omits_proxy_when_unset(self, monkeypatch):
        recorded = self._spy_async_client(monkeypatch)
        monkeypatch.setattr(settings, "YTDLP_PROXY", "")
        await stream_mod._fetch_text("https://playlist.example/live.m3u8", {})
        assert "proxy" not in recorded[0]

    async def test_stream_url_chunks_passes_proxy_when_set(self, monkeypatch):
        recorded = self._spy_async_client(monkeypatch)
        monkeypatch.setattr(settings, "YTDLP_PROXY", self.FAKE_PROXY)
        got = [chunk async for chunk in stream_mod._stream_url_chunks(
            "https://s/1.aac", {}, 65536
        )]
        assert b"".join(got) == b"adtsdata"
        assert recorded[0].get("proxy") == self.FAKE_PROXY

    async def test_stream_url_chunks_omits_proxy_when_unset(self, monkeypatch):
        recorded = self._spy_async_client(monkeypatch)
        monkeypatch.setattr(settings, "YTDLP_PROXY", "")
        _ = [chunk async for chunk in stream_mod._stream_url_chunks(
            "https://s/1.aac", {}, 65536
        )]
        assert "proxy" not in recorded[0]

    async def test_pump_segments_passes_proxy_when_set(self, monkeypatch):
        recorded = self._spy_async_client(monkeypatch)
        monkeypatch.setattr(settings, "YTDLP_PROXY", self.FAKE_PROXY)
        process = FakeProcess(out_chunks=[], err_chunks=[], exit_code=0)
        await stream_mod._pump_segments(["https://s/1.aac"], {}, process)
        assert recorded[0].get("proxy") == self.FAKE_PROXY

    async def test_pump_segments_omits_proxy_when_unset(self, monkeypatch):
        recorded = self._spy_async_client(monkeypatch)
        monkeypatch.setattr(settings, "YTDLP_PROXY", "")
        process = FakeProcess(out_chunks=[], err_chunks=[], exit_code=0)
        await stream_mod._pump_segments(["https://s/1.aac"], {}, process)
        assert "proxy" not in recorded[0]


class TestForwardLoopLifecycle:
    """直接驱动转发生成器，验证断连清理与双向 stall（与视频号 _iter_windows 测试同法）。"""

    async def _tasks(self, monkeypatch, process: FakeProcess, sink: bytearray):
        drain = asyncio.create_task(stream_mod._drain_stderr(process.stderr, sink))

        async def chunks(url, headers, chunk_size, client=None):
            yield b"seg"

        monkeypatch.setattr(stream_mod, "_stream_url_chunks", chunks)
        writer = asyncio.create_task(
            stream_mod._pump_segments([SEG_1], {}, process)
        )
        return drain, writer

    @pytest.mark.asyncio
    async def test_disconnect_kills_process_and_cancels_tasks(self, monkeypatch):
        process = FakeProcess(
            out_chunks=[], err_chunks=[], exit_code=None, hang=True
        )
        sink = bytearray()
        drain, writer = await self._tasks(monkeypatch, process, sink)
        agen = stream_mod._forward_ffmpeg_output(
            process, writer, drain, b"first-chunk", sink, SPACE_ID
        )
        chunk = await agen.__anext__()
        assert chunk == b"first-chunk"
        await agen.aclose()
        await asyncio.sleep(0.05)
        assert process.kill_count == 1
        assert drain.cancelled()
        assert writer.cancelled()

    @pytest.mark.asyncio
    async def test_normal_eof_clean_finish(self, monkeypatch):
        process = FakeProcess(out_chunks=[b"rest"], err_chunks=[], exit_code=0)
        sink = bytearray()
        drain, writer = await self._tasks(monkeypatch, process, sink)
        agen = stream_mod._forward_ffmpeg_output(
            process, writer, drain, b"first", sink, SPACE_ID
        )
        collected = [chunk async for chunk in agen]
        assert collected == [b"first", b"rest"]
        assert process.kill_count == 0
        assert drain.done() and not drain.cancelled()
        await writer
        assert process.stdin.closed

    @pytest.mark.asyncio
    async def test_segment_failure_aborts_no_fake_success(self):
        """段失败（writer 上抛）→ 生成器必须抛错中断，不能当干净 EOF 收尾。"""
        process = FakeProcess(out_chunks=[], err_chunks=[], exit_code=0)
        sink = bytearray()
        drain = asyncio.create_task(stream_mod._drain_stderr(process.stderr, sink))

        async def failing_pump():
            await asyncio.sleep(0.01)
            process.kill()
            raise stream_mod.SegmentFetchError("segment HTTP 404")

        writer = asyncio.create_task(failing_pump())
        agen = stream_mod._forward_ffmpeg_output(
            process, writer, drain, b"first", sink, SPACE_ID
        )
        with pytest.raises(stream_mod.SegmentFetchError, match="404"):
            _ = [chunk async for chunk in agen]

    @pytest.mark.asyncio
    async def test_abnormal_exit_mid_stream_raises_instead_of_clean_end(
        self, monkeypatch
    ):
        process = FakeProcess(out_chunks=[b"half"], err_chunks=[], exit_code=1)
        sink = bytearray()
        drain, writer = await self._tasks(monkeypatch, process, sink)
        agen = stream_mod._forward_ffmpeg_output(
            process, writer, drain, b"first", sink, SPACE_ID
        )
        assert await agen.__anext__() == b"first"
        assert await agen.__anext__() == b"half"
        with pytest.raises(RuntimeError, match="before stream completion"):
            await agen.__anext__()

    @pytest.mark.asyncio
    async def test_returncode_none_race_still_fails_loud(self, monkeypatch):
        process = FakeProcess(out_chunks=[b"half"], exit_code=None, reap_code=1)
        sink = bytearray()
        drain, writer = await self._tasks(monkeypatch, process, sink)
        agen = stream_mod._forward_ffmpeg_output(
            process, writer, drain, b"first", sink, SPACE_ID
        )
        assert await agen.__anext__() == b"first"
        assert await agen.__anext__() == b"half"
        with pytest.raises(RuntimeError, match="before stream completion"):
            await agen.__anext__()
        assert process.returncode == 1

    @pytest.mark.asyncio
    async def test_reap_timeout_unknown_state_fails_loud(self, monkeypatch):
        """收尸不确定（returncode 仍 None）绝不放行为成功：超时即 kill + 上抛
        （round-6 major correctness-ffmpeg-partial-output-success）。"""
        monkeypatch.setattr(stream_mod, "_REAP_TIMEOUT_SECONDS", 0.1)
        process = FakeProcess(out_chunks=[b"half"], exit_code=None, reap_code=None)
        sink = bytearray()
        drain, writer = await self._tasks(monkeypatch, process, sink)
        agen = stream_mod._forward_ffmpeg_output(
            process, writer, drain, b"first", sink, SPACE_ID
        )
        assert await agen.__anext__() == b"first"
        assert await agen.__anext__() == b"half"
        with pytest.raises(RuntimeError, match="did not exit"):
            await agen.__anext__()
        assert process.kill_count == 1

    @pytest.mark.asyncio
    async def test_post_eof_drain_hang_fails_loud(self, monkeypatch):
        """EOF 后 stderr 不排干 = 未知状态：有界等待超时即 kill + 上抛
        （round-7 minor reliability-ffmpeg-post-eof-cleanup-hang）。"""
        monkeypatch.setattr(stream_mod, "_REAP_TIMEOUT_SECONDS", 0.1)
        process = FakeProcess(out_chunks=[b"half"], err_chunks=[], exit_code=None)
        process.stderr = FakeStream([], hang=True)  # stderr 永不 EOF（stdout 正常 EOF）
        sink = bytearray()
        drain, writer = await self._tasks(monkeypatch, process, sink)
        agen = stream_mod._forward_ffmpeg_output(
            process, writer, drain, b"first", sink, SPACE_ID
        )
        assert await agen.__anext__() == b"first"
        assert await agen.__anext__() == b"half"
        with pytest.raises(RuntimeError, match="stderr drain did not finish"):
            await agen.__anext__()
        assert process.kill_count == 1

    @pytest.mark.asyncio
    async def test_post_eof_pump_hang_fails_loud(self, monkeypatch):
        """EOF 后段泵不收尾 = 未知状态：有界等待超时即 kill + 上抛。"""
        from app.core.config import settings

        monkeypatch.setattr(settings, "HTTP_TIMEOUT_SECONDS", 0.2)
        stdin = FakeStdin(hang_on=b"seg")
        process = FakeProcess(out_chunks=[b"half"], exit_code=None, stdin=stdin)
        sink = bytearray()
        drain, writer = await self._tasks(monkeypatch, process, sink)
        agen = stream_mod._forward_ffmpeg_output(
            process, writer, drain, b"first", sink, SPACE_ID
        )
        assert await agen.__anext__() == b"first"
        assert await agen.__anext__() == b"half"
        with pytest.raises(RuntimeError, match="segment pump did not finish"):
            await agen.__anext__()
        assert process.kill_count == 1

    @pytest.mark.asyncio
    async def test_read_hang_times_out_kills_and_raises(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "HTTP_TIMEOUT_SECONDS", 0.1)
        process = FakeProcess(out_chunks=[], err_chunks=[], exit_code=None, hang=True)
        sink = bytearray()
        drain, writer = await self._tasks(monkeypatch, process, sink)
        agen = stream_mod._forward_ffmpeg_output(
            process, writer, drain, b"first", sink, SPACE_ID
        )
        assert await agen.__anext__() == b"first"
        with pytest.raises(RuntimeError, match="stalled"):
            await agen.__anext__()
        assert process.kill_count == 1
        await asyncio.sleep(0.05)  # 取消送达需要一拍循环
        assert drain.cancelled()
        assert writer.done()  # 假 stdin 即写即收，泵任务早已自然完成

    @pytest.mark.asyncio
    async def test_stdin_write_stall_bounded_by_reader_timeout(self, monkeypatch):
        """stdin 写入侧 stall：ffmpeg 不消费时，读侧超时兜底杀进程并取消泵任务。"""
        from app.core.config import settings

        monkeypatch.setattr(settings, "HTTP_TIMEOUT_SECONDS", 0.1)
        stdin = FakeStdin(hang_on=b"seg")  # 写入即挂死
        process = FakeProcess(
            out_chunks=[], err_chunks=[], exit_code=None, hang=True, stdin=stdin
        )
        sink = bytearray()
        drain, writer = await self._tasks(monkeypatch, process, sink)
        agen = stream_mod._forward_ffmpeg_output(
            process, writer, drain, b"first", sink, SPACE_ID
        )
        assert await agen.__anext__() == b"first"
        with pytest.raises(RuntimeError, match="stalled"):
            await agen.__anext__()
        assert process.kill_count == 1
        await asyncio.sleep(0.05)
        assert writer.cancelled()


class TestRealSubprocessCompletion:
    """真子进程回归（round-5 major： StreamWriter 无 wait()）：

    假进程接口若与真实 StreamWriter 不一致，单元测试挡不住——必须有一条
    真 ffmpeg 走完 pump→close→wait_closed→exit 0 的完整收尾序列。
    CI 环境无 ffmpeg 时跳过（runtime 镜像内必装）。
    """

    @pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
    @pytest.mark.asyncio
    async def test_full_completion_sequence_real_subprocess(self, tmp_path):
        adts = tmp_path / "sample.aac"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
             "-c:a", "aac", "-f", "adts", str(adts), "-y"],
            check=True, capture_output=True,
        )
        assert adts.stat().st_size > 0

        process = await stream_mod._spawn_ffmpeg()
        sink: bytearray = bytearray()
        drain = asyncio.create_task(stream_mod._drain_stderr(process.stderr, sink))

        async def chunks(url, headers, chunk_size, client=None):
            yield adts.read_bytes()

        monkey = pytest.MonkeyPatch()
        try:
            monkey.setattr(stream_mod, "_stream_url_chunks", chunks)
            pump = asyncio.create_task(
                stream_mod._pump_segments(["local://sample.aac"], {}, process)
            )
            collected = b""
            while True:
                chunk = await process.stdout.read(65536)
                if not chunk:
                    break
                collected += chunk
            await drain
            await pump
            rc = await process.wait()
        finally:
            monkey.undo()

        assert rc == 0
        assert len(collected) > 1000  # fMP4 头 + 分片实际产出


class TestM3u8Parsing:

    MEDIA = "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:3.008,\nchunk_1_a.aac\n#EXTINF:3.0,\nchunk_2_a.aac\n"
    MASTER = (
        "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=96000\naudio_96k.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=64000\naudio_64k.m3u8\n"
    )

    def test_media_playlist_relative_urls_resolved(self):
        base = "https://x.video.pscp.tv/hls/audio-space/playlist.m3u8"
        got = stream_mod._playlist_uris(self.MEDIA)
        assert got == ["chunk_1_a.aac", "chunk_2_a.aac"]
        assert [urljoin(base, u) for u in got] == [
            "https://x.video.pscp.tv/hls/audio-space/chunk_1_a.aac",
            "https://x.video.pscp.tv/hls/audio-space/chunk_2_a.aac",
        ]

    def test_master_playlist_detected_and_follows_first_variant(self, monkeypatch):
        fetched: list[str] = []
        pages = iter([self.MASTER, self.MEDIA])

        async def fake_text(url, headers):
            fetched.append(url)
            return next(pages)

        monkeypatch.setattr(stream_mod, "_fetch_text", fake_text)
        base = "https://x.video.pscp.tv/hls/master.m3u8"

        async def result():
            return await stream_mod._fetch_media_segments(base, {})

        import asyncio

        segments = asyncio.run(result())
        assert fetched == [
            base,
            "https://x.video.pscp.tv/hls/audio_96k.m3u8",
        ]  # 跟随第一个 variant，相对基址换成 variant playlist
        assert segments == [
            "https://x.video.pscp.tv/hls/chunk_1_a.aac",
            "https://x.video.pscp.tv/hls/chunk_2_a.aac",
        ]

    def test_absolute_segment_urls_untouched(self):
        text = "#EXTM3U\n#EXTINF:3.0,\nhttps://cdn.example.com/a/seg.aac\n"
        assert stream_mod._playlist_uris(text) == [
            "https://cdn.example.com/a/seg.aac"
        ]

    def test_empty_playlist_raises(self, monkeypatch):
        import asyncio

        async def fake_text(url, headers):
            return "#EXTM3U\n"

        monkeypatch.setattr(stream_mod, "_fetch_text", fake_text)

        async def result():
            return await stream_mod._fetch_media_segments(
                "https://x.video.pscp.tv/a/p.m3u8", {}
            )

        with pytest.raises(stream_mod.SegmentFetchError, match="no segments"):
            asyncio.run(result())
