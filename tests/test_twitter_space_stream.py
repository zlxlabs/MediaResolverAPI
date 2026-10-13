"""X Space 流式端点：全部网络与 ffmpeg 子进程 stub。

服务端逐段抓取喂 ffmpeg stdin（gate primary round-4 major 裁定后的传输形态）：
每次请求现取 playlist（design 不变式 2）、m3u8 解析、错误映射（400/404/502）、
429 并发槽、ffmpeg argv 形态、段失败中断（无 200 假成功）、断连清理、双向 stall。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import urljoin

import pytest

import app.api.stream as stream_mod
from app.core.config import settings
from app.services.providers.base import ProviderError, VideoNotFoundError
from app.services.providers.ytdlp import SpaceNotReplayError, YtdlpProvider

FIXTURES = Path(__file__).parent / "fixtures" / "twitter_space"
SPACE_ID = "1AKEmvYqbrOKL"
AUTH = {"X-API-Key": "test-key-123"}
PLAYLIST_A = "https://prod-fastly-us-west-1.video.pscp.tv/a/playlist.m3u8?type=replay"
PLAYLIST_B = "https://prod-fastly-us-west-1.video.pscp.tv/b/playlist.m3u8?type=replay"
SEG_1 = "https://prod-fastly-us-west-1.video.pscp.tv/a/chunk_1_a.aac"
SEG_2 = "https://prod-fastly-us-west-1.video.pscp.tv/a/chunk_2_a.aac"


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

    async def wait(self) -> None:
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


def _patch_segments(monkeypatch, segments: list[str]):
    async def fake_fetch(playlist_url, headers):
        return segments

    monkeypatch.setattr(stream_mod, "_fetch_media_segments", fake_fetch)


def _patch_segment_chunks(monkeypatch, mapping: dict[str, list[bytes]]):
    """按 URL 吐字节；mapping 未覆盖的 URL 视为 HTTP 404。"""

    async def chunks(url, headers, chunk_size):
        if url not in mapping:
            raise stream_mod.SegmentFetchError("segment HTTP 404")
        for piece in mapping[url]:
            yield piece

    monkeypatch.setattr(stream_mod, "_stream_url_chunks", chunks)


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

    def test_happy_path_headers_body_stdin_and_argv(
        self, authed_client, spawn_record, monkeypatch
    ):
        _patch_provider(monkeypatch, [_meta()])
        _patch_segments(monkeypatch, [SEG_1, SEG_2])
        _patch_segment_chunks(monkeypatch, {SEG_1: [b"aaa", b"bbb"], SEG_2: [b"ccc"]})
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
        _assert_common_argv(spawn_record[0])

    def test_all_segments_reach_ffmpeg_stdin(self, authed_client, monkeypatch):
        _patch_provider(monkeypatch, [_meta()])
        _patch_segments(monkeypatch, [SEG_1, SEG_2])
        stdin = FakeStdin()

        async def fake_exec(*cmd, **kwargs):
            return FakeProcess(out_chunks=[b"frag1", b"frag2"], exit_code=0, stdin=stdin)

        monkeypatch.setattr(stream_mod.asyncio, "create_subprocess_exec", fake_exec)
        _patch_segment_chunks(
            monkeypatch, {SEG_1: [b"seg1a", b"seg1b"], SEG_2: [b"seg2a"]}
        )
        resp = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert resp.status_code == 200
        assert bytes(stdin.received) == b"seg1aseg1bseg2a"
        assert stdin.closed  # 全部段写完后 stdin 必须关闭让 ffmpeg 收尾

    def test_stdin_gated_stdout_still_streams(self, authed_client, monkeypatch):
        """真实 ffmpeg 互锁建模：stdin 收到段数据前 stdout 零产出。

        泵任务若先于预读启动（修复后顺序）则正常出流；旧顺序（预读先行）
        会在这里白等 HTTP_TIMEOUT_SECONDS 后 502——本测试锁住修复。
        """
        from app.core.config import settings

        monkeypatch.setattr(settings, "HTTP_TIMEOUT_SECONDS", 1.0)
        gated_out = FakeStream([b"frag1"], hang=True)  # stdout 等 stdin 喂数据才吐 frag1、随后 EOF
        stdin = FakeStdin(gate_stdout=gated_out)

        async def fake_exec(*cmd, **kwargs):
            p = FakeProcess(out_chunks=[], exit_code=0, stdin=stdin)
            p.stdout = gated_out
            return p

        monkeypatch.setattr(stream_mod.asyncio, "create_subprocess_exec", fake_exec)
        _patch_provider(monkeypatch, [_meta()])
        _patch_segments(monkeypatch, [SEG_1])
        _patch_segment_chunks(monkeypatch, {SEG_1: [b"seg"]})
        resp = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert resp.status_code == 200
        assert resp.content == b"frag1"

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
        fetched_playlists: list[str] = []

        async def fake_fetch(playlist_url, headers):
            fetched_playlists.append(playlist_url)
            return [SEG_1]

        monkeypatch.setattr(stream_mod, "_fetch_media_segments", fake_fetch)

        async def chunks(url, headers, chunk_size):
            yield b"x"

        monkeypatch.setattr(stream_mod, "_stream_url_chunks", chunks)
        assert authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}").content
        assert authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}").content
        assert urls == [
            f"https://x.com/i/spaces/{SPACE_ID}",
            f"https://x.com/i/spaces/{SPACE_ID}",
        ]
        assert fetched_playlists == [PLAYLIST_A, PLAYLIST_B]

    def test_live_space_maps_400(self, authed_client, monkeypatch):
        async def live(self, **kwargs):
            raise SpaceNotReplayError(
                "twitter space x is not a replay (live_status=is_live)"
            )

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

    def test_playlist_fetch_failure_maps_502_and_redacts(
        self, authed_client, monkeypatch
    ):
        async def fail(playlist_url, headers):
            raise stream_mod.SegmentFetchError(
                "segment fetch failed: https://x.video.pscp.tv/a/playlist.m3u8 boom"
            )

        monkeypatch.setattr(stream_mod, "_fetch_media_segments", fail)
        _patch_provider(monkeypatch, [_meta()])
        resp = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
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

    def test_429_when_concurrency_full(self, authed_client, spawn_record, monkeypatch):
        monkeypatch.setattr(settings, "MAX_CONCURRENT_STREAMS", 0)
        stream_mod.stream_limiter.reset()
        try:
            resp = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
            assert resp.status_code == 429
            assert resp.json()["detail"] == "Too many concurrent streams"
        finally:
            stream_mod.stream_limiter.reset()

    def test_ffmpeg_instant_failure_maps_502(self, authed_client, monkeypatch):
        async def fake_exec(*cmd, **kwargs):
            return FakeProcess(
                out_chunks=[],
                err_chunks=[b"failed\n"],
                exit_code=1,
            )

        monkeypatch.setattr(stream_mod.asyncio, "create_subprocess_exec", fake_exec)
        _patch_provider(monkeypatch, [_meta()])
        _patch_segments(monkeypatch, [SEG_1])
        resp = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert resp.status_code == 502
        assert "pscp.tv" not in resp.text  # 短命 playlist URL 不回显给客户端

    def test_no_playlist_url_maps_502(self, authed_client, monkeypatch):
        payload = _meta() | {"url": ""}
        _patch_provider(monkeypatch, [payload])
        resp = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert resp.status_code == 502

    def test_empty_playlist_maps_502(self, authed_client, monkeypatch):
        _patch_provider(monkeypatch, [_meta()])
        _patch_segments(monkeypatch, [])
        resp = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert resp.status_code == 502


class TestSegmentFailureAborts:
    """完整性不变式：响应正常收尾 ⇔ 所有段抓取成功且 ffmpeg exit 0。
    任何段失败必须传输中断，禁止 200 假成功（round-4 major）。"""

    def _spawn(self, monkeypatch, spawn_record):
        async def fake_exec(*cmd, **kwargs):
            spawn_record.append(cmd)
            return FakeProcess(out_chunks=[b"f1", b"f2"], exit_code=0)

        monkeypatch.setattr(stream_mod.asyncio, "create_subprocess_exec", fake_exec)

    def test_middle_segment_404_aborts_response(
        self, authed_client, spawn_record, monkeypatch
    ):
        _patch_provider(monkeypatch, [_meta()])
        _patch_segments(monkeypatch, [SEG_1, SEG_2])

        async def fake_exec(*cmd, **kwargs):
            spawn_record.append(cmd)
            return FakeProcess(out_chunks=[b"f1", b"f2"], exit_code=0)

        monkeypatch.setattr(stream_mod.asyncio, "create_subprocess_exec", fake_exec)

        async def chunks(url, headers, chunk_size):
            if url == SEG_1:
                yield b"ok1"
            else:
                raise stream_mod.SegmentFetchError("segment HTTP 404")

        monkeypatch.setattr(stream_mod, "_stream_url_chunks", chunks)
        try:
            resp = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        except Exception as exc:
            # 传输中断形态：客户端拿到异常而非干净响应
            assert "404" in str(exc) or "segment" in str(exc).lower()
            return
        # 若返回了响应，绝不允许是「200 + 完整体」的假成功形态
        assert resp.status_code != 200 or resp.content != b"f1f2"

    def test_segment_error_redacts_url(self, authed_client, spawn_record, monkeypatch):
        _patch_provider(monkeypatch, [_meta()])
        _patch_segments(monkeypatch, [SEG_1])
        _patch_segment_chunks(monkeypatch, {})  # 全部 404
        resp = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
        assert "pscp.tv" not in resp.text


class TestForwardLoopLifecycle:
    """直接驱动转发生成器，验证断连清理与双向 stall（与视频号 _iter_windows 测试同法）。"""

    async def _tasks(self, monkeypatch, process: FakeProcess, sink: bytearray):
        drain = asyncio.create_task(stream_mod._drain_stderr(process.stderr, sink))

        async def chunks(url, headers, chunk_size):
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
