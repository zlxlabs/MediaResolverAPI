"""X Space file operations must not block the asyncio event-loop thread."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
import app.api.stream as stream_mod
import app.services.space_cache as space_cache
from app.core.config import settings
from app.services.providers.ytdlp import YtdlpProvider

SPACE_ID = "1AKEmvYqbrOKL"


@pytest.fixture
def isolated_space_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "SPACE_CACHE_DIR", str(tmp_path / "space-cache"))
    space_cache._tasks.clear()
    monkeypatch.setattr(space_cache, "_started", False)
    monkeypatch.setattr(space_cache, "_lock", None)
    monkeypatch.setattr(space_cache, "_sweeper_task", None)
    space_cache._failures.clear()
    yield
    space_cache._tasks.clear()
    space_cache._sweeper_task = None
    space_cache._failures.clear()


class _RecordingFile:
    def __init__(self, fh, thread_ids):
        self._fh = fh
        self._thread_ids = thread_ids

    def fileno(self):
        return self._fh.fileno()

    def seek(self, *args):
        self._thread_ids.append(threading.get_ident())
        return self._fh.seek(*args)

    def read(self, *args):
        self._thread_ids.append(threading.get_ident())
        return self._fh.read(*args)

    def close(self):
        return self._fh.close()


class _FakeProcess:
    returncode = 0

    class _EofStream:
        async def read(self, _size):
            return b""

    stderr = _EofStream()

    def kill(self):
        raise AssertionError("successful materialization must not kill ffmpeg")


class _ChunkStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, _size):
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class _LowWatermarkProcess:
    def __init__(self):
        self.returncode = None
        self.stdout = _ChunkStream([b"written part", b"rejected chunk", b""])
        self.stderr = _ChunkStream([])
        self.kill_count = 0

    def kill(self):
        self.kill_count += 1
        self.returncode = -9


class _RecordingSink:
    def __init__(self, fh, thread_ids):
        self._fh = fh
        self._thread_ids = thread_ids

    def __enter__(self):
        self._fh.__enter__()
        return self

    def __exit__(self, exc_type, exc, traceback):
        return self._fh.__exit__(exc_type, exc, traceback)

    def write(self, chunk):
        self._thread_ids.append(threading.get_ident())
        return self._fh.write(chunk)


def test_final_download_reads_in_worker_and_preserves_range_bytes(
    authed_client, isolated_space_cache, monkeypatch
):
    payload = bytes(range(256)) * 512
    space_cache.ensure_dir()
    space_cache.final_path(SPACE_ID).write_bytes(payload)

    event_loop_threads = []
    file_io_threads = []
    original_open_final = space_cache.open_final
    original_resolve_range = stream_mod._FinalDownloadResponse._resolve_range

    def recording_open_final(space_id):
        fh = original_open_final(space_id)
        if fh is not None:
            return _RecordingFile(fh, file_io_threads)
        return None

    def recording_resolve_range(response, raw):
        event_loop_threads.append(threading.get_ident())
        return original_resolve_range(response, raw)

    monkeypatch.setattr(space_cache, "open_final", recording_open_final)
    monkeypatch.setattr(
        stream_mod._FinalDownloadResponse, "_resolve_range", recording_resolve_range
    )

    full = authed_client.get(f"/api/stream/twitter_space/{SPACE_ID}")
    ranged = authed_client.get(
        f"/api/stream/twitter_space/{SPACE_ID}", headers={"Range": "bytes=100-199"}
    )
    unsatisfiable = authed_client.get(
        f"/api/stream/twitter_space/{SPACE_ID}", headers={"Range": f"bytes={len(payload)}-"}
    )

    assert full.status_code == 200
    assert full.content == payload
    assert full.headers["content-length"] == str(len(payload))
    assert ranged.status_code == 206
    assert ranged.content == payload[100:200]
    assert ranged.headers["content-range"] == f"bytes 100-199/{len(payload)}"
    assert unsatisfiable.status_code == 416
    assert unsatisfiable.headers["content-range"] == f"bytes */{len(payload)}"
    assert event_loop_threads and file_io_threads
    assert all(thread_id != event_loop_threads[0] for thread_id in file_io_threads)


@pytest.mark.asyncio
async def test_materialization_offloads_watermark_and_sink_write(
    isolated_space_cache, monkeypatch
):
    part = space_cache.part_path(SPACE_ID)
    space_cache.ensure_dir()
    payload = b"materialized bytes" * 64
    loop_thread_ids = []
    watermark_threads = []
    write_threads = []

    async def fetch_video_info(self, **_kwargs):
        return {"url": "https://example.invalid/playlist.m3u8"}

    async def fetch_playlist(_url, _headers):
        return "#EXTM3U\n#EXT-X-ENDLIST\n", []

    async def spawn_ffmpeg():
        return _FakeProcess()

    async def pump_segments(*_args):
        return None

    async def forward_output(*_args):
        loop_thread_ids.append(threading.get_ident())
        yield payload

    original_path_open = Path.open

    def recording_path_open(path, mode="r", *args, **kwargs):
        fh = original_path_open(path, mode, *args, **kwargs)
        if path == part and mode == "wb":
            return _RecordingSink(fh, write_threads)
        return fh

    def recording_disk_free_bytes():
        watermark_threads.append(threading.get_ident())
        return 10**12

    monkeypatch.setattr(YtdlpProvider, "fetch_video_info", fetch_video_info)
    monkeypatch.setattr(stream_mod, "_fetch_media_playlist", fetch_playlist)
    monkeypatch.setattr(stream_mod, "_spawn_ffmpeg", spawn_ffmpeg)
    monkeypatch.setattr(stream_mod, "_pump_segments", pump_segments)
    monkeypatch.setattr(stream_mod, "_forward_ffmpeg_output", forward_output)
    monkeypatch.setattr(Path, "open", recording_path_open)
    monkeypatch.setattr(space_cache, "_disk_free_bytes", recording_disk_free_bytes)
    monkeypatch.setattr(settings, "SPACE_CACHE_MIN_FREE_MB", 1)

    await stream_mod._materialize_to_file(SPACE_ID, part)

    assert space_cache.final_path(SPACE_ID).read_bytes() == payload
    assert not part.exists()
    assert loop_thread_ids and watermark_threads and write_threads
    assert all(thread_id != loop_thread_ids[0] for thread_id in watermark_threads)
    assert all(thread_id != loop_thread_ids[0] for thread_id in write_threads)


@pytest.mark.asyncio
async def test_low_watermark_raises_and_discards_part(
    isolated_space_cache, monkeypatch
):
    space_cache.ensure_dir()
    free_bytes = iter([10**12, 10**12, 0])
    process = _LowWatermarkProcess()

    async def fetch_video_info(self, **_kwargs):
        return {"url": "https://example.invalid/playlist.m3u8"}

    async def fetch_playlist(_url, _headers):
        return "#EXTM3U\n#EXT-X-ENDLIST\n", []

    async def spawn_ffmpeg():
        return process

    async def pump_segments(*_args):
        return None

    monkeypatch.setattr(YtdlpProvider, "fetch_video_info", fetch_video_info)
    monkeypatch.setattr(stream_mod, "_fetch_media_playlist", fetch_playlist)
    monkeypatch.setattr(stream_mod, "_spawn_ffmpeg", spawn_ffmpeg)
    monkeypatch.setattr(stream_mod, "_pump_segments", pump_segments)
    monkeypatch.setattr(
        space_cache, "_disk_free_bytes", lambda: next(free_bytes)
    )
    monkeypatch.setattr(settings, "SPACE_CACHE_MIN_FREE_MB", 1)

    with pytest.raises(stream_mod.SegmentFetchError, match="storage below watermark"):
        await stream_mod._materialize_space(SPACE_ID)

    assert process.kill_count == 1
    assert not space_cache.part_path(SPACE_ID).exists()
    assert not space_cache.final_path(SPACE_ID).exists()
