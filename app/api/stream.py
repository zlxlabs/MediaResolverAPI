"""微信视频号流式解密代理：边拉 CDN 边 XOR 前 131072 字节，其余透传。"""

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


async def _iter_decrypted(
    *,
    sph_code: str,
    first_media: dict,
    first_stream: CdnResponse,
    start: int,
    end: Optional[int],
) -> AsyncIterator[bytes]:
    """Yield decrypted bytes from the CDN range, or until an unbounded stream ends."""
    offset = start
    chunk_size = settings.STREAM_CHUNK_SIZE
    try:
        if end is not None and end < start:
            return
        async for raw in first_stream.aiter_bytes(chunk_size):
            if not raw:
                continue
            if end is not None:
                remaining = end - offset + 1
                if len(raw) > remaining:
                    raw = raw[:remaining]
            yield xor_chunk(raw, first_media["decode_key"], offset)
            offset += len(raw)
            if end is not None and offset > end:
                return
        if end is not None and offset <= end:
            raise UpstreamDisconnected("upstream closed before range complete")
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
        try:
            await first_stream.aclose()
        except Exception as close_exc:
            logger.error(
                "wechat stream failed to aclose upstream sph_code={}: {}",
                sph_code,
                close_exc,
            )


@router.get(
    "/stream/wechat_channels/{sph_code}",
    dependencies=[Depends(_stream_slot)],
)
async def stream_wechat_channels(sph_code: str, request: Request):
    first_stream: Optional[CdnResponse] = None
    try:
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
        first_stream = await open_cdn_stream(
            media["full_url"], range_h if is_partial else None
        )
        if first_stream.status_code == 416:
            headers = {}
            content_range = first_stream.content_range
            if content_range and _UNSATISFIABLE_RANGE_PATTERN.fullmatch(
                content_range.strip()
            ):
                headers["Content-Range"] = content_range.strip()
            raise HTTPException(
                status_code=416,
                detail="CDN returned 416",
                headers=headers or None,
            )
        if first_stream.status_code not in (200, 206):
            status = first_stream.status_code
            raise _json_http_error(502, f"CDN returned {status}")
        if is_partial and first_stream.status_code == 200 and requested_start != 0:
            raise _json_http_error(502, "CDN ignored Range request")
        try:
            cdn_content_length = _cdn_content_length(first_stream)
            content_range = None
            if first_stream.status_code == 206:
                start, end, complete_length = _reconcile_cdn_offset(
                    first_stream,
                    expected_offset=requested_start,
                )
                content_range = first_stream.content_range.strip()
                if requested_end is not None and end > requested_end:
                    end = requested_end
                    total = "*" if complete_length is None else str(complete_length)
                    content_range = f"bytes {start}-{end}/{total}"
                if requested_suffix is not None and complete_length is not None:
                    expected_suffix_start = max(complete_length - requested_suffix, 0)
                    if start != expected_suffix_start:
                        raise UpstreamDisconnected(
                            "CDN 206 response starts at "
                            f"{start}, expected {expected_suffix_start}"
                        )
                if not is_partial:
                    if complete_length is None or end != complete_length - 1:
                        raise UpstreamDisconnected(
                            "CDN 206 response does not contain the complete file"
                        )
                    cdn_content_length = complete_length
            else:
                start = 0
                end = requested_end
                if cdn_content_length is not None:
                    end = (
                        cdn_content_length - 1
                        if end is None
                        else min(end, cdn_content_length - 1)
                    )
                elif not is_partial and end is None:
                    raise UpstreamDisconnected(
                        "CDN 200 response missing Content-Length for complete file"
                    )
            if is_partial and first_stream.status_code == 200:
                if requested_start != 0:
                    raise UpstreamDisconnected("CDN ignored Range request")
                if cdn_content_length is None and end is None:
                    raise UpstreamDisconnected(
                        "CDN 200 response missing Content-Length for Range request"
                    )
                content_length = (
                    None
                    if cdn_content_length is None
                    else end - start + 1
                )
            elif first_stream.status_code == 206:
                content_length = (
                    None
                    if cdn_content_length is None
                    else end - start + 1
                )
            else:
                content_length = cdn_content_length
        except UpstreamDisconnected as exc:
            raise _json_http_error(502, str(exc)) from exc
        headers = {
            "Content-Type": "video/mp4",
            "Accept-Ranges": "bytes",
        }
        if content_length is not None:
            headers["Content-Length"] = str(content_length)
        if is_partial:
            if first_stream.status_code == 206:
                headers["Content-Range"] = content_range
            else:
                complete_length = (
                    "*" if cdn_content_length is None else str(cdn_content_length)
                )
                headers["Content-Range"] = (
                    f"bytes {start}-{end}/{complete_length}"
                )
        status_code = 206 if is_partial else 200

        opened = first_stream
        first_stream = None  # body iterator owns it

        async def body() -> AsyncIterator[bytes]:
            async for chunk in _iter_decrypted(
                sph_code=sph_code,
                first_media=media,
                first_stream=opened,
                start=start,
                end=end,
            ):
                yield chunk

        return StreamingResponse(
            body(),
            status_code=status_code,
            media_type="video/mp4",
            headers=headers,
        )
    finally:
        if first_stream is not None:
            await first_stream.aclose()
