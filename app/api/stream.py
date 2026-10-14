"""微信视频号流式解密代理：分窗全读 CDN，再 XOR 前 131072 字节转发。"""

from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from pathlib import Path
import os
import re
from typing import AsyncIterator, BinaryIO, Optional, Protocol
from urllib.parse import urljoin

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel
from starlette.responses import Response

from ..core.config import settings
from .deps import verify_api_key
from ..services import space_cache
from ..services.providers.base import ProviderError, VideoNotFoundError
from ..services.providers.tikhub import TikHubProvider
from ..services.providers.ytdlp import (
    SpaceNotReplayError,
    YtdlpProvider,
    redact_playlist_url,
)
from ..services.wechat_channels_crypto import (
    KEYSTREAM_SIZE,
    generate_keystream,
    xor_chunk,
)

router = APIRouter(dependencies=[Depends(verify_api_key)])


class UpstreamDisconnected(Exception):
    """CDN ended before the requested byte range was fully forwarded."""


class CdnHttpError(Exception):
    """CDN returned a non-success status that the caller must interpret."""

    def __init__(self, status_code: int, content_range: Optional[str] = None) -> None:
        self.status_code = status_code
        self.content_range = content_range
        super().__init__(f"CDN returned {status_code}")


_UPSTREAM_FAIL = (
    httpx.TransportError,
    httpx.StreamError,
    OSError,
    ProviderError,
    UpstreamDisconnected,
)


class CdnResponse(Protocol):
    status_code: int
    content_range: Optional[str]
    content_length: Optional[str]

    def aiter_bytes(self, chunk_size: int = 65536) -> AsyncIterator[bytes]:
        ...

    async def aclose(self) -> None:
        ...


class _HttpxCdnStream:
    def __init__(self, client: httpx.AsyncClient, response: httpx.Response) -> None:
        self._client = client
        self._response = response
        self.status_code = response.status_code
        self.content_range = response.headers.get("Content-Range")
        self.content_length = response.headers.get("Content-Length")

    def aiter_bytes(self, chunk_size: int = 65536) -> AsyncIterator[bytes]:
        return self._response.aiter_bytes(chunk_size)

    async def aclose(self) -> None:
        try:
            await self._response.aclose()
        finally:
            await self._client.aclose()


async def _open_cdn_stream_httpx(
    url: str, range_header: Optional[str]
) -> CdnResponse:
    timeout = httpx.Timeout(connect=15.0, read=60.0, write=15.0, pool=15.0)
    headers = {}
    if range_header:
        headers["Range"] = range_header
    client: Optional[httpx.AsyncClient] = httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        },
    )
    try:
        request = client.build_request("GET", url, headers=headers)
        response = await client.send(request, stream=True)
        stream = _HttpxCdnStream(client, response)
        client = None
        return stream
    finally:
        if client is not None:
            await client.aclose()


# Tests monkeypatch this name.
open_cdn_stream = _open_cdn_stream_httpx


class StreamLimiter:
    """Non-blocking concurrency gate. Overflow returns 429 instead of queueing."""

    def __init__(self) -> None:
        self._lock: Optional[asyncio.Lock] = None
        self.active = 0

    def _lock_for_loop(self) -> asyncio.Lock:
        lock = self._lock
        if lock is None:
            lock = asyncio.Lock()
            self._lock = lock
        return lock

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[bool]:
        acquired = False
        try:
            async with self._lock_for_loop():
                if self.active < settings.MAX_CONCURRENT_STREAMS:
                    self.active += 1
                    acquired = True
            if not acquired:
                yield False
                return
            yield True
        finally:
            if acquired:
                await self.release()

    @asynccontextmanager
    async def blocking_slot(self, label: str) -> AsyncIterator[None]:
        """后台物化任务专用：占不到槽时排队等待而非 429。

        任务脱离客户端连接，等得起；不排队会把暂时性饱和变成 202→502 翻转并
        诱发客户端立即重试风暴。同一计数器与视频号/流式共享 MAX_CONCURRENT_STREAMS。
        """
        if settings.MAX_CONCURRENT_STREAMS <= 0:
            raise RuntimeError("MAX_CONCURRENT_STREAMS must be positive")
        while True:
            async with self._lock_for_loop():
                if self.active < settings.MAX_CONCURRENT_STREAMS:
                    self.active += 1
                    break
            await asyncio.sleep(_SLOT_POLL_SECONDS)
        try:
            yield
        finally:
            await self.release()

    async def release(self) -> None:
        async with self._lock_for_loop():
            if self.active > 0:
                self.active -= 1

    def reset(self) -> None:
        self.active = 0
        self._lock = None


stream_limiter = StreamLimiter()

_SLOT_POLL_SECONDS = 0.2  # blocking_slot 排队轮询间隔
_SPACE_RETRY_AFTER_SECONDS = 5  # 202 等待协议建议轮询间隔（锁定决策 4）

_SPH_CODE_PATTERN = re.compile(r"^[A-Za-z0-9]{1,64}$")


def parse_byte_range(
    range_header: Optional[str],
) -> tuple[Optional[int], Optional[int], bool]:
    """Parse a single ``bytes=start-end`` / ``bytes=start-`` range.

    Returns (requested_start, requested_end, is_partial). The end is only the
    endpoint supplied by the client; an open-ended range has no local endpoint.
    A suffix range has no local start because its start depends on the CDN's
    actual file size. Missing header → full file, is_partial=False (HTTP 200).
    Present header → HTTP 206 even if it covers the whole file (e.g.
    ``bytes=0-``).
    """
    if not range_header:
        return 0, None, False
    text = range_header.strip()
    if not text.lower().startswith("bytes="):
        raise HTTPException(status_code=416, detail="Unsupported Range unit")
    spec = text.split("=", 1)[1].strip()
    if "," in spec:
        raise HTTPException(status_code=416, detail="Multiple ranges not supported")
    if "-" not in spec:
        raise HTTPException(status_code=416, detail="Malformed Range header")
    start_s, end_s = spec.split("-", 1)
    if start_s == "" and end_s == "":
        raise HTTPException(status_code=416, detail="Malformed Range header")
    if start_s == "":
        # suffix-byte-range: last N bytes
        try:
            suffix = int(end_s)
        except ValueError as exc:
            raise HTTPException(status_code=416, detail="Malformed Range header") from exc
        if suffix <= 0:
            raise HTTPException(status_code=416, detail="Range not satisfiable")
        return None, None, True
    try:
        start = int(start_s)
    except ValueError as exc:
        raise HTTPException(status_code=416, detail="Malformed Range header") from exc
    if start < 0:
        raise HTTPException(status_code=416, detail="Range not satisfiable")
    if end_s == "":
        return start, None, True
    try:
        end = int(end_s)
    except ValueError as exc:
        raise HTTPException(status_code=416, detail="Malformed Range header") from exc
    if end < start:
        raise HTTPException(status_code=416, detail="Malformed Range header")
    return start, end, True


def _json_http_error(status: int, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail=message)


_CONTENT_RANGE_PATTERN = re.compile(
    r"^bytes\s+(?P<start>\d+)-(?P<end>\d+)/(?P<complete_length>\d+|\*)$",
    re.IGNORECASE,
)
_UNSATISFIABLE_RANGE_PATTERN = re.compile(r"^bytes\s+\*/\d+$", re.IGNORECASE)


def _reconcile_cdn_offset(
    stream: CdnResponse,
    *,
    expected_offset: Optional[int],
) -> tuple[int, int, Optional[int]]:
    """Validate CDN 206 metadata and return its authoritative range."""
    content_range = stream.content_range
    if not content_range:
        raise UpstreamDisconnected("CDN 206 response missing Content-Range")
    match = _CONTENT_RANGE_PATTERN.fullmatch(content_range.strip())
    if match is None:
        raise UpstreamDisconnected("CDN 206 response has malformed Content-Range")
    declared_start = int(match.group("start"))
    declared_end = int(match.group("end"))
    if declared_end < declared_start:
        raise UpstreamDisconnected("CDN 206 response has invalid Content-Range")
    complete_length = match.group("complete_length")
    if complete_length != "*" and int(complete_length) <= declared_end:
        raise UpstreamDisconnected("CDN 206 response has invalid complete length")
    if expected_offset is not None and declared_start != expected_offset:
        raise UpstreamDisconnected(
            "CDN 206 response starts at "
            f"{declared_start}, expected {expected_offset}"
        )

    content_length = stream.content_length
    if content_length is not None:
        if re.fullmatch(r"\d+", content_length.strip()) is None:
            raise UpstreamDisconnected("CDN 206 response has invalid Content-Length")
        declared_length = int(content_length)
        expected_length = declared_end - declared_start + 1
        if declared_length != expected_length:
            raise UpstreamDisconnected(
                "CDN 206 response has Content-Length "
                f"{declared_length}, expected {expected_length}"
            )
    return (
        declared_start,
        declared_end,
        None if complete_length == "*" else int(complete_length),
    )


def _cdn_content_length(stream: CdnResponse) -> Optional[int]:
    content_length = stream.content_length
    if content_length is None:
        return None
    if re.fullmatch(r"\d+", content_length.strip()) is None:
        raise UpstreamDisconnected("CDN response has invalid Content-Length")
    return int(content_length)


_EXPIRED_STATUSES = frozenset({401, 403, 404, 410})
_WINDOW_MAX_ATTEMPTS = 3
_REQUEST_RETRY_BUDGET = 20
_URL_REFRESH_MIN_OFFSET = 131072


async def _consume_cdn_body(stream: CdnResponse) -> bytes:
    chunks: list[bytes] = []
    try:
        async for chunk in stream.aiter_bytes(settings.STREAM_CHUNK_SIZE):
            if chunk:
                chunks.append(chunk)
    except _UPSTREAM_FAIL as exc:
        raise UpstreamDisconnected("upstream closed before range complete") from exc
    return b"".join(chunks)


async def _read_window(url: str, start: int, end: int) -> tuple[bytes, int, Optional[int]]:
    """Open Range: bytes=start-end, validate 206, read the whole body, aclose.

    Returns ``(raw, declared_end, complete_length)``. Body length must equal
    ``declared_end - start + 1`` or ``UpstreamDisconnected`` is raised.
    This is the only function that opens a CDN stream.
    """
    if end < start:
        raise UpstreamDisconnected("invalid window bounds")
    stream: Optional[CdnResponse] = None
    try:
        try:
            stream = await open_cdn_stream(url, f"bytes={start}-{end}")
            status = stream.status_code
            if status == 416:
                raise CdnHttpError(416, stream.content_range)
            if status in _EXPIRED_STATUSES:
                raise CdnHttpError(status, stream.content_range)
            if status == 200:
                content_length = _cdn_content_length(stream)
                window_size = end - start + 1
                if start == 0 and content_length is not None and content_length <= window_size:
                    raw = await _consume_cdn_body(stream)
                    if len(raw) != content_length:
                        raise UpstreamDisconnected("upstream closed before range complete")
                    return raw, content_length - 1, content_length
                raise UpstreamDisconnected("CDN ignored Range request")
            if status != 206:
                raise UpstreamDisconnected(f"CDN returned {status}")
            declared_start, declared_end, complete_length = _reconcile_cdn_offset(
                stream,
                expected_offset=start,
            )
            if declared_end < end and (
                complete_length is None or declared_end < complete_length - 1
            ):
                raise UpstreamDisconnected("upstream closed before range complete")
            if declared_end > end:
                raise UpstreamDisconnected("CDN 206 response exceeds requested window")
            expected_len = declared_end - declared_start + 1
            raw = await _consume_cdn_body(stream)
            if len(raw) != expected_len:
                raise UpstreamDisconnected("upstream closed before range complete")
            return raw, declared_end, complete_length
        except CdnHttpError:
            raise
        except _UPSTREAM_FAIL as exc:
            if isinstance(exc, UpstreamDisconnected):
                raise
            raise UpstreamDisconnected("upstream closed before range complete") from exc
    finally:
        if stream is not None:
            try:
                await stream.aclose()
            except Exception as close_exc:
                logger.error(
                    "wechat stream failed to aclose upstream window start={} end={}: {}",
                    start,
                    end,
                    close_exc,
                )


async def _read_window_retry(
    url: str,
    start: int,
    end: int,
    *,
    sph_code: str,
    state: dict,
) -> tuple[bytes, int, Optional[int]]:
    """Read one window with per-window and per-request retry limits."""
    attempts = 0
    while True:
        attempts += 1
        try:
            return await _read_window(url, start, end)
        except CdnHttpError as exc:
            if exc.status_code == 416:
                raise
            expired = exc.status_code in _EXPIRED_STATUSES
            can_refresh = (
                expired
                and start >= _URL_REFRESH_MIN_OFFSET
                and not state["refreshed"]
            )
            state["budget"] -= 1
            if can_refresh:
                if attempts >= _WINDOW_MAX_ATTEMPTS or state["budget"] <= 0:
                    raise UpstreamDisconnected(str(exc)) from exc
                state["refreshed"] = True
                try:
                    state["media"] = await _fetch_media(sph_code)
                except ProviderError as fetch_exc:
                    raise UpstreamDisconnected(str(fetch_exc)) from fetch_exc
                url = state["media"]["full_url"]
                continue
            raise UpstreamDisconnected(str(exc)) from exc
        except UpstreamDisconnected:
            state["budget"] -= 1
            if attempts >= _WINDOW_MAX_ATTEMPTS or state["budget"] <= 0:
                raise
            continue


async def _fetch_media(sph_code: str) -> dict:
    provider = TikHubProvider()
    return await provider.fetch_wechat_channels_media(sph_code)


@asynccontextmanager
async def _stream_slot_cm(label: str) -> AsyncIterator[None]:
    """共享并发槽：同一 stream_limiter（MAX_CONCURRENT_STREAMS 信号量）服务两个流式端点。"""
    async with stream_limiter.slot() as acquired:
        if not acquired:
            logger.warning(
                "stream 429 slot={} active={}",
                label,
                stream_limiter.active,
            )
            raise _json_http_error(429, "Too many concurrent streams")
        yield


async def _stream_slot(sph_code: str) -> AsyncIterator[None]:
    async with _stream_slot_cm(f"wechat_channels:{sph_code}"):
        yield


def _416_from_cdn(content_range: Optional[str]) -> HTTPException:
    headers = {}
    if content_range and _UNSATISFIABLE_RANGE_PATTERN.fullmatch(content_range.strip()):
        headers["Content-Range"] = content_range.strip()
    return HTTPException(
        status_code=416,
        detail="CDN returned 416",
        headers=headers or None,
    )


async def _cancel_task(task: Optional[asyncio.Task]) -> None:
    if task is None:
        return
    if not task.done():
        task.cancel()
    try:
        await task
    except BaseException:
        pass


async def _iter_windows(
    *,
    sph_code: str,
    media: dict,
    first_raw: bytes,
    start: int,
    end: int,
    state: dict,
) -> AsyncIterator[bytes]:
    """Yield decrypted bytes from fully-read windows. ``first_raw`` is already in memory."""
    window = settings.STREAM_WINDOW_BYTES
    chunk_size = settings.STREAM_CHUNK_SIZE
    key = media["decode_key"]
    offset = start
    current = first_raw
    prefetch: Optional[asyncio.Task] = None
    try:
        while True:
            next_start = offset + len(current)
            if next_start <= end:
                wend = min(next_start + window - 1, end)
                url = state["media"]["full_url"]
                prefetch = asyncio.create_task(
                    _read_window_retry(
                        url, next_start, wend, sph_code=sph_code, state=state
                    )
                )
            else:
                prefetch = None
            decrypted = xor_chunk(current, key, offset)
            for i in range(0, len(decrypted), chunk_size):
                yield decrypted[i : i + chunk_size]
            if prefetch is None:
                break
            raw, declared_end, _total = await prefetch
            prefetch = None
            current = raw
            offset = next_start
    except asyncio.CancelledError:
        logger.warning(
            "wechat stream cancelled by client sph_code={} offset={}",
            sph_code,
            offset,
        )
        raise
    except GeneratorExit:
        logger.warning(
            "wechat stream generator closed sph_code={} offset={}",
            sph_code,
            offset,
        )
        raise
    except _UPSTREAM_FAIL as exc:
        logger.error(
            "wechat stream upstream failed sph_code={} offset={}: {}",
            sph_code,
            offset,
            exc,
        )
        raise
    finally:
        await _cancel_task(prefetch)


@router.get(
    "/stream/wechat_channels/{sph_code}",
    dependencies=[Depends(_stream_slot)],
)
async def stream_wechat_channels(sph_code: str, request: Request):
    if _SPH_CODE_PATTERN.fullmatch(sph_code) is None:
        raise _json_http_error(400, "Invalid WeChat Channels share code")
    try:
        media = await _fetch_media(sph_code)
    except VideoNotFoundError as exc:
        raise _json_http_error(502, str(exc)) from exc
    except ProviderError as exc:
        raise _json_http_error(502, str(exc)) from exc

    try:
        generate_keystream(media["decode_key"])
    except (TypeError, ValueError) as exc:
        raise _json_http_error(502, "Invalid decode_key") from exc

    range_h = request.headers.get("range")
    requested_start, requested_end, is_partial = parse_byte_range(range_h)
    requested_suffix = None
    if is_partial and requested_start is None and range_h is not None:
        requested_suffix = int(range_h.split("=", 1)[1].strip().split("-", 1)[1])

    window = settings.STREAM_WINDOW_BYTES
    url = media["full_url"]
    state = {
        "budget": _REQUEST_RETRY_BUDGET,
        "refreshed": False,
        "media": media,
    }
    try:
        if requested_suffix is not None:
            _probe, _probe_end, total = await _read_window_retry(
                url, 0, 0, sph_code=sph_code, state=state
            )
            url = state["media"]["full_url"]
            if total is None:
                raise UpstreamDisconnected("CDN 206 response missing complete length")
            start = max(total - requested_suffix, 0)
            if start >= total:
                raise _416_from_cdn(f"bytes */{total}")
            end = total - 1
            first_end = min(start + window - 1, end)
            first_raw, _declared_end, total = await _read_window_retry(
                url, start, first_end, sph_code=sph_code, state=state
            )
        else:
            start = 0 if requested_start is None else requested_start
            first_end = start + window - 1
            if requested_end is not None:
                first_end = min(first_end, requested_end)
            first_raw, _declared_end, total = await _read_window_retry(
                url, start, first_end, sph_code=sph_code, state=state
            )
            if total is None:
                raise UpstreamDisconnected("CDN 206 response missing complete length")
            end = total - 1 if requested_end is None else min(requested_end, total - 1)
        needed = end - start + 1
        if len(first_raw) > needed:
            first_raw = first_raw[:needed]
    except CdnHttpError as exc:
        if exc.status_code == 416:
            raise _416_from_cdn(exc.content_range) from exc
        raise _json_http_error(502, str(exc)) from exc
    except UpstreamDisconnected as exc:
        raise _json_http_error(502, str(exc)) from exc

    media = state["media"]
    headers = {
        "Content-Type": "video/mp4",
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
    }
    if is_partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{total}"
    status_code = 206 if is_partial else 200

    async def body() -> AsyncIterator[bytes]:
        async for chunk in _iter_windows(
            sph_code=sph_code,
            media=media,
            first_raw=first_raw,
            start=start,
            end=end,
            state=state,
        ):
            yield chunk

    return StreamingResponse(
        body(),
        status_code=status_code,
        media_type="video/mp4",
        headers=headers,
    )


_SPACE_ID_PATTERN = re.compile(r"^[0-9A-Za-z]{13}$")


class SegmentFetchError(RuntimeError):
    """段/播放列表抓取失败（message 已脱敏）。"""


# 收尸等待上界：EOF 后进程应早已退出，5s 仍无确定退出码即按失败处理。
_REAP_TIMEOUT_SECONDS = 5


def _http_timeout() -> httpx.Timeout:
    return httpx.Timeout(
        connect=settings.HTTP_TIMEOUT_SECONDS,
        read=settings.HTTP_TIMEOUT_SECONDS,
        write=settings.HTTP_TIMEOUT_SECONDS,
        pool=settings.HTTP_TIMEOUT_SECONDS,
    )


def _proxy_client_kwargs() -> dict:
    """YTDLP_PROXY 接线（X 链路三处 AsyncClient 共用）：设置时传 proxy=，

    空时返回空 dict 不传该键——现网空值路径零行为变化。
    """
    return {"proxy": settings.YTDLP_PROXY} if settings.YTDLP_PROXY else {}


def _playlist_uris(text: str) -> list[str]:
    """播放列表中非注释行（variant URI 或分段子行）。"""
    return [
        stripped
        for line in text.splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    ]


async def _fetch_text(url: str, headers: dict) -> str:
    try:
        async with httpx.AsyncClient(
            timeout=_http_timeout(),
            follow_redirects=True,
            headers=headers,
            **_proxy_client_kwargs(),
        ) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        raise SegmentFetchError(
            f"playlist fetch failed: {redact_playlist_url(str(exc))}"
        ) from exc
    if resp.status_code >= 400:
        raise SegmentFetchError(f"playlist HTTP {resp.status_code}")
    return resp.text


async def _fetch_media_playlist(
    playlist_url: str, headers: dict
) -> tuple[str, list[str]]:
    """现取最终 media playlist：返回（playlist 全文，绝对 URL 段列表）。

    master playlist 跟随第一个 variant；相对 URL 按所在播放列表的基址解析
    （urljoin）。ENDLIST 门禁由消费方（物化任务）基于返回的全文判断。
    """
    text, base = await _fetch_text(playlist_url, headers), playlist_url
    if "#EXT-X-STREAM-INF" in text:
        variants = _playlist_uris(text)
        if not variants:
            raise SegmentFetchError("master playlist has no variant URI")
        base = urljoin(playlist_url, variants[0])
        text = await _fetch_text(base, headers)
    segments = [urljoin(base, uri) for uri in _playlist_uris(text)]
    if not segments:
        raise SegmentFetchError("playlist has no segments")
    return text, segments


async def _fetch_media_segments(playlist_url: str, headers: dict) -> list[str]:
    """现取播放列表并解析分段子行（保留给非物化路径与解析测试的旧入口）。"""
    _text, segments = await _fetch_media_playlist(playlist_url, headers)
    return segments


async def _stream_url_chunks(
    url: str, headers: dict, chunk_size: int,
    client: Optional[httpx.AsyncClient] = None,
):
    """逐段流式抓取；读超时复用 HTTP_TIMEOUT_SECONDS，非 2xx 视为段失败。

    client 传入时复用（整场回放共用一个连接池），不传则自建（兼容单段调用）。
    """
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(
            timeout=_http_timeout(),
            follow_redirects=True,
            headers=headers,
            **_proxy_client_kwargs(),
        )
    try:
        async with client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                raise SegmentFetchError(f"segment HTTP {resp.status_code}")
            sent = 0
            async for chunk in resp.aiter_bytes(chunk_size):
                sent += len(chunk)
                yield chunk
    finally:
        if owns_client:
            await client.aclose()
    if sent == 0:
        # ADTS 段零字节不可能是合法内容：200 + 空体按段失败处理（round-6 major），
        # 与 404 同路径（pump 杀 ffmpeg 上抛），绝不静默跳过。
        raise SegmentFetchError("segment HTTP 200 with empty body")


async def _pump_segments(
    segment_urls: list[str],
    headers: dict,
    process: asyncio.subprocess.Process,
    progress: Optional[dict] = None,
) -> None:
    """逐段抓取写入 ffmpeg stdin；任何段失败：杀 ffmpeg 并上抛（无假成功）。

    stdin 背压天然限流：ffmpeg 停止消费时 write 阻塞，上游抓取随之暂停。
    progress 传入时逐段回写 ``segments_done``（段数对账用，锁定决策 5）。
    """
    stdin = process.stdin
    try:
        async with httpx.AsyncClient(
            timeout=_http_timeout(),
            follow_redirects=True,
            headers=headers,
            **_proxy_client_kwargs(),
        ) as client:
            for index, url in enumerate(segment_urls):
                async for chunk in _stream_url_chunks(
                    url, headers, settings.STREAM_CHUNK_SIZE, client
                ):
                    stdin.write(chunk)      # 同步缓冲写（StreamWriter.write 非协程）
                    await stdin.drain()     # 背压：缓冲满时阻塞，上游抓取随之暂停
                if progress is not None:
                    progress["segments_done"] = index + 1
        stdin.close()
        await stdin.wait_closed()   # 注意是 wait_closed：StreamWriter 没有 wait()
    except Exception as exc:
        if process.returncode is None:
            process.kill()
        detail = redact_playlist_url(str(exc)) or type(exc).__name__
        logger.error(
            "twitter_space segment pump aborted segments={} first={} err={}",
            len(segment_urls),
            redact_playlist_url(segment_urls[0]) if segment_urls else "-",
            detail,
        )
        raise SegmentFetchError(f"segment fetch failed: {detail}") from exc


async def _spawn_ffmpeg() -> asyncio.subprocess.Process:
    """ADTS(stdin) → fragmented MP4 remux 到 stdout；-c copy 不重编码（remux 非转码）。

    分段 HTTP 抓取由服务端完成（round-4 major 裁定：ffmpeg 自取分段在中间段
    404 时 exit 0 空 stderr 静默截断，实测见 design.md 测量记录），故输入是
    pipe:0，不再需要 -user_agent/-headers。
    """
    return await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "aac",
        "-i",
        "pipe:0",
        "-c",
        "copy",
        "-bsf:a",
        "aac_adtstoasc",
        "-f",
        "mp4",
        "-movflags",
        "frag_keyframe+empty_moov",
        # audio-only 下 frag_keyframe 永不触发（无视频关键帧），不显式给切片参数
        # 会把整场音频堆在 muxer 缓冲里不吐字节（实测卡 32B moov，2026-10-14）。
        "-frag_duration",
        "4000000",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def _drain_stderr(stderr, sink: bytearray) -> None:
    """持续排干 ffmpeg stderr，防 64KB 管道缓冲卡死子进程；尾部 4KB 留作诊断。"""
    while True:
        chunk = await stderr.read(settings.STREAM_CHUNK_SIZE)
        if not chunk:
            return
        sink.extend(chunk)
        del sink[:-4096]


def _cleanup_ffmpeg(
    process: asyncio.subprocess.Process,
    drain: asyncio.Task,
    writer: Optional[asyncio.Task] = None,
) -> None:
    """同步清理：kill 未退出的 ffmpeg、取消 stderr 排干与段泵任务（writer 未建则跳过）。"""
    if process.returncode is None:
        process.kill()
    drain.cancel()
    if writer is not None:
        writer.cancel()


async def _forward_ffmpeg_output(
    process: asyncio.subprocess.Process,
    writer: asyncio.Task,
    drain: asyncio.Task,
    first: bytes,
    stderr_tail: bytearray,
    space_id: str,
) -> AsyncIterator[bytes]:
    """把 ffmpeg stdout 转发给客户端；EOF/断连后同步清理子进程（不留尸）。

    fail-loud 语义：段抓取失败、中途异常退出或读挂死超时都抛错中断响应
    （客户端看到传输中断），不把截断文件当成功流收尾。完整性不变式：
    响应正常收尾 ⇔ 所有段抓取成功且 ffmpeg exit 0。
    """
    sent = len(first)
    try:
        yield first
        while True:
            try:
                chunk = await asyncio.wait_for(
                    process.stdout.read(settings.STREAM_CHUNK_SIZE),
                    timeout=settings.HTTP_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "twitter_space stream ffmpeg stdout stalled space_id={} sent={}",
                    space_id,
                    sent,
                )
                raise RuntimeError(
                    f"ffmpeg stdout stalled over {settings.HTTP_TIMEOUT_SECONDS}s"
                )
            if not chunk:
                break
            sent += len(chunk)
            yield chunk
        # 正常 EOF：等 stderr 排干与段泵收尾——均有界，未知状态不当成功
        try:
            await asyncio.wait_for(drain, timeout=_REAP_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            if process.returncode is None:
                process.kill()
            logger.error(
                "twitter_space stream stderr drain hung space_id={} sent={}",
                space_id,
                sent,
            )
            raise RuntimeError("ffmpeg stderr drain did not finish after stream end")
        try:
            await asyncio.wait_for(writer, timeout=settings.HTTP_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            if process.returncode is None:
                process.kill()
            logger.error(
                "twitter_space stream segment pump hung space_id={} sent={}",
                space_id,
                sent,
            )
            raise RuntimeError("segment pump did not finish after stream end")
        if process.returncode is None:
            # stdout EOF 即进程应已退出；显式等收尸拿确定退出码。
            # 收尸超时即 kill + 上抛：未知状态绝不当成功（round-6 major）。
            try:
                await asyncio.wait_for(process.wait(), timeout=_REAP_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                if process.returncode is None:
                    process.kill()
                logger.error(
                    "twitter_space stream ffmpeg reap timeout space_id={} sent={}",
                    space_id,
                    sent,
                )
                raise RuntimeError(
                    "ffmpeg did not exit after stream completion; "
                    "output is incomplete"
                )
        if process.returncode not in (0, None):
            logger.error(
                "twitter_space stream ffmpeg exited code={} space_id={} sent={} tail={}",
                process.returncode,
                space_id,
                sent,
                redact_playlist_url(bytes(stderr_tail).decode("utf-8", "replace")),
            )
            raise RuntimeError(
                f"ffmpeg exited {process.returncode} before stream completion"
            )
    finally:
        _cleanup_ffmpeg(process, drain, writer)


class _FinalDownloadResponse(Response):
    """从已打开 fd 交付 final（R2 minor）：打开动作前置到端点内，「status 判定 →
    打开」之间被清理的竞态窗口由此消除——fd 一经打开，unlink 不影响本读者。
    Starlette FileResponse 在响应阶段先发状态码再 open，无法在打开失败时改发
    未命中响应，故自写；Range 支持单区间（断点续传/拖动进度条均为单区间），
    多区间与畸形 Range 回 200 全量（RFC 9110 允许忽略 Range）。
    """

    _CHUNK = 64 * 1024
    _RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")

    def __init__(self, fh: BinaryIO, *, media_type: str, download_name: str) -> None:
        self._fh = fh
        self._size = os.fstat(fh.fileno()).st_size
        self._disposition = f'attachment; filename="{download_name}"'
        super().__init__(content=b"", media_type=media_type)

    def _resolve_range(self, raw: Optional[str]) -> tuple[int, int]:
        """解析请求 Range 为闭区间 [start, end]；无/畸形/多区间/空文件 → 全量。"""
        start, end = 0, self._size - 1
        if raw and self._size:
            m = self._RANGE_RE.fullmatch(raw.strip())
            if m and (m[1] or m[2]):
                if m[1]:
                    start = int(m[1])
                    if m[2]:
                        end = min(int(m[2]), self._size - 1)
                else:
                    start = max(0, self._size - int(m[2]))
        return start, end

    async def __call__(self, scope, receive, send) -> None:
        try:
            range_header = None
            if scope["type"] == "http":
                range_header = next(
                    (v.decode("latin-1") for k, v in scope["headers"] if k == b"range"),
                    None,
                )
            start, end = self._resolve_range(range_header)
            if self._size and start >= self._size:
                await send({
                    "type": "http.response.start",
                    "status": 416,
                    "headers": [(b"content-range", f"bytes */{self._size}".encode("latin-1"))],
                })
                await send({"type": "http.response.body", "body": b"", "more_body": False})
                return
            length = max(0, end - start + 1)
            partial = self._size > 0 and (start > 0 or end != self._size - 1)
            headers = [
                (b"content-type", (self.media_type or "application/octet-stream").encode("latin-1")),
                (b"content-disposition", self._disposition.encode("latin-1")),
                (b"accept-ranges", b"bytes"),
                (b"content-length", str(length).encode("latin-1")),
            ]
            if partial:
                headers.append(
                    (b"content-range", f"bytes {start}-{end}/{self._size}".encode("latin-1"))
                )
            await send({
                "type": "http.response.start",
                "status": 206 if partial else 200,
                "headers": headers,
            })
            self._fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = self._fh.read(min(self._CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                await send({"type": "http.response.body", "body": chunk, "more_body": remaining > 0})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            self._fh.close()


@router.get("/stream/twitter_space/{space_id}")
async def stream_twitter_space(space_id: str):
    """X Space 回放两阶段交付（交付形态 v2，锁定决策 3/4/8）。

    命中 final → 普通文件下载（Content-Length + Range 断点续传，fd 前置打开）；
    未命中 → spawn 后台获取任务并立即 202（任务脱离客户端连接、持有上游并发槽）；
    任务进行中 → 202；任务失败 → 400/404/502 一次，下次 GET 重新 spawn（失败态不持久）。
    playlist URL 短命，绝不进缓存、绝不进响应，日志中脱敏。
    """
    if _SPACE_ID_PATTERN.fullmatch(space_id) is None:
        raise _json_http_error(400, "Invalid X Space id")
    # 缓存目录初始化失败（权限/磁盘等 OSError）→ 502 固定文案（review F3）：
    # README 承诺的可识别错误（fail-loud 拒绝新任务），不裸抛成 500。
    try:
        space_cache.ensure_started()
    except OSError as exc:
        logger.error("twitter_space cache init failed err={}", type(exc).__name__)
        raise _json_http_error(502, _CACHE_UNAVAILABLE_DETAIL) from exc

    st = space_cache.status(space_id)
    if st is space_cache.SpaceCacheStatus.FINAL:
        fh = space_cache.open_final(space_id)
        if fh is not None:
            return _FinalDownloadResponse(
                fh, media_type="audio/mp4", download_name=f"space-{space_id}.m4a"
            )
        # 交付打开竞态（R2 minor）：status 判定后被 sweep 清理是合法状态迁移，
        # 按未命中重拉（spawn + 202），非兜底；已打开的读者由 unlink-while-open 保护。
        st = space_cache.SpaceCacheStatus.ABSENT
    if st is space_cache.SpaceCacheStatus.FAILED:
        exc = space_cache.consume_failure(space_id)
        if exc is not None:
            raise _materialize_failure_to_http(exc)
        st = space_cache.SpaceCacheStatus.ABSENT  # 被并发请求抢先消费 → 按未命中重拉
    if st is space_cache.SpaceCacheStatus.ABSENT:
        try:
            await space_cache.start(space_id, lambda: _materialize_space(space_id))
        except OSError as exc:
            logger.error("twitter_space cache start failed err={}", type(exc).__name__)
            raise _json_http_error(502, _CACHE_UNAVAILABLE_DETAIL) from exc
    return _202_extracting()


def _202_extracting() -> JSONResponse:
    return JSONResponse(
        status_code=202,
        content={"status": "extracting", "retry_after": _SPACE_RETRY_AFTER_SECONDS},
    )


_CACHE_UNAVAILABLE_DETAIL = "X Space cache storage is unavailable"
_ABSOLUTE_PATH_RE = re.compile(r"/[^\s'\"]+")


def _strip_internal_paths(text: str) -> str:
    """502 响应体脱敏（review F4）：内部文件系统绝对路径只留 basename，不暴露部署目录结构。"""
    return _ABSOLUTE_PATH_RE.sub(lambda m: m.group(0).rsplit("/", 1)[-1], text)


def _materialize_failure_to_http(exc: BaseException) -> HTTPException:
    """任务失败 → 客户端状态码：沿用既有错误契约（直播 400/不可用 404/其余 502 脱敏）。"""
    if isinstance(exc, SpaceNotReplayError):
        return _json_http_error(400, f"X Space is not a downloadable replay: {exc}")
    if isinstance(exc, VideoNotFoundError):
        return _json_http_error(404, f"X Space replay unavailable: {exc}")
    detail = redact_playlist_url(str(exc)) or type(exc).__name__
    return _json_http_error(
        502, f"X Space materialization failed: {_strip_internal_paths(detail)}"
    )


async def _materialize_space(space_id: str) -> None:
    """后台获取任务（锁定决策 3）：脱离客户端连接，全程持有上游并发槽。

    失败收尾：删 .part（故障矩阵：磁盘满/段失败只留可回收临时文件），异常存进
    任务由 GET 消费一次；成功路径 .part 已被 publish_final 原子改名为 final。
    """
    part = space_cache.part_path(space_id)
    try:
        async with stream_limiter.blocking_slot(f"twitter_space_download:{space_id}"):
            await _materialize_to_file(space_id, part)
    except Exception as exc:
        space_cache.discard_part(space_id)
        logger.error(
            "twitter_space materialization failed space_id={} err={}",
            space_id,
            redact_playlist_url(str(exc)) or type(exc).__name__,
        )
        raise


async def _materialize_to_file(space_id: str, part: Path) -> None:
    """完整获取管线：提取 → ENDLIST 门禁 → 段抓取喂 ffmpeg → stdout 写 .part。

    完整性门禁（锁定决策 5）：ENDLIST 在场 + 段数对账 + ffmpeg exit 0
    （_forward_ffmpeg_output 内置校验）三者齐备才 publish_final（os.replace 原子改名）。
    交付承诺 = playlist 声明内容的完整交付，非「源内容完整」。
    """
    provider = YtdlpProvider()
    info = await provider.fetch_video_info(
        platform="twitter_space",
        video_id=space_id,
        original_url=f"https://x.com/i/spaces/{space_id}",
    )
    playlist_url = str(info.get("url") or "")
    if not playlist_url:
        raise SegmentFetchError("X Space extraction returned no playlist URL")
    headers = info.get("http_headers") or {}
    playlist_text, segment_urls = await _fetch_media_playlist(playlist_url, headers)
    if "#EXT-X-ENDLIST" not in playlist_text:
        raise SegmentFetchError(
            "playlist has no #EXT-X-ENDLIST (not a completed replay)"
        )
    progress: dict = {"segments_done": 0}
    # Open the destination before spawning: if filesystem admission fails (for
    # example ENOSPC or permissions), there is no child process or task to reap.
    with part.open("wb") as sink:
        process = await _spawn_ffmpeg()
        stderr_tail: bytearray = bytearray()
        drain = asyncio.create_task(_drain_stderr(process.stderr, stderr_tail))
        writer = asyncio.create_task(
            _pump_segments(segment_urls, headers, process, progress)
        )
        agen = _forward_ffmpeg_output(process, writer, drain, b"", stderr_tail, space_id)
        try:
            async for chunk in agen:
                sink.write(chunk)
        finally:
            # 异常路径由此触发生成器 finally：kill ffmpeg、取消 drain/writer（不留尸）
            await agen.aclose()
    if progress["segments_done"] != len(segment_urls):
        # 段数对账（锁定决策 5）：pump 内逐段 fail-fast 之下的常态化 tripwire，
        # 锁住未来「跳段静默成功」类回归（声明段数 == 实际写入段数）。
        raise SegmentFetchError(
            f"segment reconciliation failed: declared {len(segment_urls)}, "
            f"pumped {progress['segments_done']}"
        )
    space_cache.publish_final(space_id)
    logger.info(
        "twitter_space materialized space_id={} segments={} part->final",
        space_id,
        len(segment_urls),
    )


class WechatChannelsDirectInfo(BaseModel):
    """直连信息：解密后的文件头 + CDN 直链 + 可靠总长度，客户端自行拉身子拼接。"""

    sph_code: str
    cdn_url: str
    content_length: int
    encrypted_head_bytes: int
    head_b64: str
    content_type: str = "video/mp4"


@router.get("/stream/wechat_channels/{sph_code}/direct")
async def stream_wechat_channels_direct(sph_code: str) -> WechatChannelsDirectInfo:
    """向 CDN 发有界请求 ``bytes=0-131071`` 读文件头（非过期异常沿用同窗最多 3 次重试），解密后与直链一起回给客户端。

    不占流式并发槽；文件头请求遇到 URL 过期状态时重新取一次 media 再重读，
    ``cdn_url`` 回传最终成功那一对的 ``full_url``。
    """
    if _SPH_CODE_PATTERN.fullmatch(sph_code) is None:
        raise _json_http_error(400, "Invalid WeChat Channels share code")
    try:
        media = await _fetch_media(sph_code)
    except ProviderError as exc:
        raise _json_http_error(502, str(exc)) from exc
    try:
        generate_keystream(media["decode_key"])
    except (TypeError, ValueError) as exc:
        raise _json_http_error(502, "Invalid decode_key") from exc

    state = {"budget": _REQUEST_RETRY_BUDGET, "refreshed": False, "media": media}

    async def read_head() -> tuple[bytes, Optional[int]]:
        raw, _declared_end, total = await _read_window_retry(
            state["media"]["full_url"],
            0,
            KEYSTREAM_SIZE - 1,
            sph_code=sph_code,
            state=state,
        )
        return raw, total

    try:
        raw, total = await read_head()
    except UpstreamDisconnected as exc:
        cause = exc.__cause__
        if (
            isinstance(cause, CdnHttpError)
            and cause.status_code in _EXPIRED_STATUSES
            and not state["refreshed"]
        ):
            state["refreshed"] = True
            try:
                state["media"] = await _fetch_media(sph_code)
            except ProviderError as fetch_exc:
                raise _json_http_error(502, str(fetch_exc)) from fetch_exc
            try:
                generate_keystream(state["media"]["decode_key"])
            except (TypeError, ValueError) as exc:
                raise _json_http_error(502, "Invalid decode_key") from exc
            try:
                raw, total = await read_head()
            except (CdnHttpError, UpstreamDisconnected) as retry_exc:
                raise _json_http_error(502, str(retry_exc)) from retry_exc
        else:
            raise _json_http_error(502, str(exc)) from exc
    except CdnHttpError as exc:
        raise _json_http_error(502, str(exc)) from exc

    if total is None:
        raise _json_http_error(502, "CDN response missing complete length")
    media = state["media"]
    head = xor_chunk(raw, media["decode_key"], 0)
    return WechatChannelsDirectInfo(
        sph_code=sph_code,
        cdn_url=media["full_url"],
        content_length=total,
        encrypted_head_bytes=len(head),
        head_b64=base64.b64encode(head).decode("ascii"),
    )
