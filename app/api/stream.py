"""微信视频号流式解密代理：边拉 CDN 边 XOR 前 131072 字节，其余透传。"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional, Protocol

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from loguru import logger

from ..core.config import settings
from .deps import verify_api_key
from ..services.providers.base import ProviderError, VideoNotFoundError
from ..services.providers.tikhub import TikHubProvider
from ..services.wechat_channels_crypto import xor_chunk

router = APIRouter(dependencies=[Depends(verify_api_key)])


class UpstreamDisconnected(Exception):
    """CDN ended before the requested byte range was fully forwarded."""


_UPSTREAM_FAIL = (httpx.TransportError, httpx.StreamError, OSError, UpstreamDisconnected)


class CdnResponse(Protocol):
    status_code: int

    def aiter_bytes(self, chunk_size: int = 65536) -> AsyncIterator[bytes]:
        ...

    async def aclose(self) -> None:
        ...


class _HttpxCdnStream:
    def __init__(self, client: httpx.AsyncClient, response: httpx.Response) -> None:
        self._client = client
        self._response = response
        self.status_code = response.status_code

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
    try:
        request = client.build_request("GET", url, headers=headers)
        response = await client.send(request, stream=True)
        return _HttpxCdnStream(client, response)
    except Exception:
        await client.aclose()
        raise


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

    async def try_acquire(self) -> bool:
        async with self._lock_for_loop():
            if self.active >= settings.MAX_CONCURRENT_STREAMS:
                return False
            self.active += 1
            return True

    async def release(self) -> None:
        async with self._lock_for_loop():
            if self.active > 0:
                self.active -= 1

    def reset(self) -> None:
        self.active = 0
        self._lock = None


stream_limiter = StreamLimiter()


def parse_byte_range(
    range_header: Optional[str], file_size: int
) -> tuple[int, int, bool]:
    """Parse a single ``bytes=start-end`` / ``bytes=start-`` range.

    Returns (start, end_inclusive, is_partial). Missing header → full file,
    is_partial=False (HTTP 200). Present header → HTTP 206 even if it covers
    the whole file (e.g. ``bytes=0-``).
    """
    if file_size <= 0:
        raise HTTPException(status_code=502, detail="Invalid upstream file size")
    if not range_header:
        return 0, file_size - 1, False
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
        start = max(file_size - suffix, 0)
        return start, file_size - 1, True
    try:
        start = int(start_s)
    except ValueError as exc:
        raise HTTPException(status_code=416, detail="Malformed Range header") from exc
    if start < 0 or start >= file_size:
        raise HTTPException(
            status_code=416,
            detail="Range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )
    if end_s == "":
        return start, file_size - 1, True
    try:
        end = int(end_s)
    except ValueError as exc:
        raise HTTPException(status_code=416, detail="Malformed Range header") from exc
    if end < start:
        raise HTTPException(status_code=416, detail="Malformed Range header")
    if end >= file_size:
        end = file_size - 1
    return start, end, True


def _range_header_for(start: int, end: int, file_size: int) -> Optional[str]:
    if start == 0 and end == file_size - 1:
        return f"bytes={start}-"
    return f"bytes={start}-{end}"


def _json_http_error(status: int, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail=message)


async def _fetch_media(object_id: str) -> dict:
    provider = TikHubProvider()
    return await provider.fetch_wechat_channels_media(object_id)


async def _iter_decrypted(
    *,
    object_id: str,
    first_media: dict,
    first_stream: CdnResponse,
    start: int,
    end: int,
    file_size: int,
) -> AsyncIterator[bytes]:
    """Yield decrypted bytes from start..end inclusive, resuming on upstream drop.

    ``first_media`` / ``first_stream`` are the pair already opened before
    headers were sent. Subsequent resumes fetch a fresh (url, key) pair.
    """
    offset = start
    retries = 0
    max_retries = settings.STREAM_RESUME_MAX_RETRIES
    chunk_size = settings.STREAM_CHUNK_SIZE
    media = first_media
    stream: Optional[CdnResponse] = first_stream
    try:
        while offset <= end:
            decode_key = media["decode_key"]
            try:
                assert stream is not None
                async for raw in stream.aiter_bytes(chunk_size):
                    if not raw:
                        continue
                    remaining = end - offset + 1
                    if len(raw) > remaining:
                        raw = raw[:remaining]
                    yield xor_chunk(raw, decode_key, offset)
                    offset += len(raw)
                    if offset > end:
                        return
                if offset <= end:
                    raise UpstreamDisconnected("upstream closed before range complete")
            except asyncio.CancelledError:
                logger.warning(
                    "wechat stream cancelled by client object_id={} offset={}",
                    object_id,
                    offset,
                )
                raise
            except GeneratorExit:
                logger.warning(
                    "wechat stream generator closed object_id={} offset={}",
                    object_id,
                    offset,
                )
                raise
            except _UPSTREAM_FAIL as exc:
                if offset > end:
                    return
                if retries >= max_retries:
                    logger.error(
                        "wechat stream resume exhausted object_id={} offset={} "
                        "retries={} max={}: {}",
                        object_id,
                        offset,
                        retries,
                        max_retries,
                        exc,
                    )
                    raise
                retries += 1
                logger.warning(
                    "wechat stream upstream drop, resuming object_id={} "
                    "absolute_offset={} retry={}/{}: {}",
                    object_id,
                    offset,
                    retries,
                    max_retries,
                    exc,
                )
                if stream is not None:
                    try:
                        await stream.aclose()
                    except Exception as close_exc:
                        logger.error(
                            "wechat stream failed to aclose dropped upstream "
                            "object_id={}: {}",
                            object_id,
                            close_exc,
                        )
                    stream = None
                media = await _fetch_media(object_id)
                range_h = _range_header_for(offset, end, file_size)
                stream = await open_cdn_stream(media["full_url"], range_h)
                if stream.status_code not in (200, 206):
                    status = stream.status_code
                    await stream.aclose()
                    stream = None
                    logger.error(
                        "wechat stream resume CDN status={} object_id={}",
                        status,
                        object_id,
                    )
                    raise UpstreamDisconnected(f"CDN status {status} on resume")
                if stream.status_code == 200 and offset > 0:
                    await stream.aclose()
                    stream = None
                    logger.error(
                        "wechat stream resume CDN ignored Range object_id={} offset={}",
                        object_id,
                        offset,
                    )
                    raise UpstreamDisconnected("CDN ignored Range on resume")
    finally:
        if stream is not None:
            try:
                await stream.aclose()
            except Exception as close_exc:
                logger.error(
                    "wechat stream failed to aclose upstream object_id={}: {}",
                    object_id,
                    close_exc,
                )


@router.get("/stream/wechat_channels/{object_id}")
async def stream_wechat_channels(object_id: str, request: Request):
    if not await stream_limiter.try_acquire():
        logger.warning(
            "wechat stream 429 object_id={} active={}",
            object_id,
            stream_limiter.active,
        )
        raise _json_http_error(429, "Too many concurrent streams")

    released = False

    async def _release() -> None:
        nonlocal released
        if not released:
            released = True
            await stream_limiter.release()

    first_stream: Optional[CdnResponse] = None
    try:
        try:
            media = await _fetch_media(object_id)
        except VideoNotFoundError as exc:
            raise _json_http_error(502, str(exc)) from exc
        except ProviderError as exc:
            raise _json_http_error(502, str(exc)) from exc

        file_size = int(media["file_size"])
        start, end, is_partial = parse_byte_range(
            request.headers.get("range"), file_size
        )
        range_h = None if not is_partial else _range_header_for(start, end, file_size)
        first_stream = await open_cdn_stream(media["full_url"], range_h)
        if first_stream.status_code not in (200, 206):
            status = first_stream.status_code
            await first_stream.aclose()
            first_stream = None
            raise _json_http_error(502, f"CDN returned {status}")
        if is_partial and first_stream.status_code == 200:
            await first_stream.aclose()
            first_stream = None
            raise _json_http_error(502, "CDN ignored Range request")

        length = end - start + 1
        headers = {
            "Content-Type": "video/mp4",
            "Content-Length": str(length),
            "Accept-Ranges": "bytes",
        }
        status_code = 206 if is_partial else 200
        if is_partial:
            headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

        opened = first_stream
        first_stream = None  # body iterator owns it

        async def body() -> AsyncIterator[bytes]:
            try:
                async for chunk in _iter_decrypted(
                    object_id=object_id,
                    first_media=media,
                    first_stream=opened,
                    start=start,
                    end=end,
                    file_size=file_size,
                ):
                    yield chunk
            finally:
                await _release()

        return StreamingResponse(
            body(),
            status_code=status_code,
            media_type="video/mp4",
            headers=headers,
        )
    except Exception:
        if first_stream is not None:
            await first_stream.aclose()
        await _release()
        raise
