"""微信视频号流式解密代理：分窗全读 CDN，再 XOR 前 131072 字节转发。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import re
from typing import AsyncIterator, Optional, Protocol

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from loguru import logger

from ..core.config import settings
from .deps import verify_api_key
from ..services.providers.base import ProviderError, VideoNotFoundError
from ..services.providers.tikhub import TikHubProvider
from ..services.wechat_channels_crypto import generate_keystream, xor_chunk

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
    client = httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        },
    )
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

    async def release(self) -> None:
        async with self._lock_for_loop():
            if self.active > 0:
                self.active -= 1

    def reset(self) -> None:
        self.active = 0
        self._lock = None


stream_limiter = StreamLimiter()

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
        expected_len = declared_end - declared_start + 1
        raw = await _consume_cdn_body(stream)
        if len(raw) != expected_len:
            raise UpstreamDisconnected("upstream closed before range complete")
        return raw, declared_end, complete_length
    finally:
        if stream is not None:
            try:
                await stream.aclose()
            except Exception as close_exc:
                logger.error(
                    "wechat stream failed to aclose upstream url={} start={}: {}",
                    url,
                    start,
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
                state["media"] = await _fetch_media(sph_code)
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


async def _stream_slot(sph_code: str) -> AsyncIterator[None]:
    async with stream_limiter.slot() as acquired:
        if not acquired:
            logger.warning(
                "wechat stream 429 sph_code={} active={}",
                sph_code,
                stream_limiter.active,
            )
            raise _json_http_error(429, "Too many concurrent streams")
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
    except (asyncio.CancelledError, Exception):
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
