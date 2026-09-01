"""WeChat Channels streaming decrypt endpoint — all network stubbed.

Range × decrypt boundary (7), upstream failure, Content-Range reconciliation,
client disconnect, concurrency 429, memory bound, auth, TikHub 5xx JSON.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport
from loguru import logger as loguru_logger

import app.api.stream as stream_mod
from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.services.providers.base import ProviderError
from app.services.wechat_channels_crypto import KEYSTREAM_SIZE, generate_keystream

SPH_CODE = "AOzokRxWHz"
FILE_SIZE = 600_000
TIKHUB_FILE_SIZE = 2_450_521_066
KEY_A = 55516695
KEY_B = 12345678
PLAIN = bytes.fromhex("000000206674797069736f6d") + bytes(
    (i * 31 + 7) & 0xFF for i in range(FILE_SIZE - 12)
)
AUTH = {"X-API-Key": "test-key-123"}


def _cipher(key) -> bytes:
    """Encrypt the leading 128KiB by indexing the keystream, never via xor_chunk.

    xor_chunk is the unit under test on the decrypt path; using it here would
    cancel an off-by-one in n_xor (ciphertext bit == plaintext bit).
    generate_keystream is the sample-verified oracle.
    """
    ks = generate_keystream(key)
    head = bytes(PLAIN[i] ^ ks[i] for i in range(KEYSTREAM_SIZE))
    return head + PLAIN[KEYSTREAM_SIZE:]


def _slice_plain(range_header: str | None) -> bytes:
    start, end, _ = stream_mod.parse_byte_range(range_header)
    assert start is not None
    if end is None:
        end = FILE_SIZE - 1
    return PLAIN[start : end + 1]


class FakeCdn:
    def __init__(
        self,
        payload: bytes,
        *,
        status_code: int,
        content_range: str | None = None,
        content_length: str | None = None,
        disconnect_after: int | None = None,
        hold: asyncio.Event | None = None,
    ) -> None:
        self.payload = payload
        self.status_code = status_code
        self.content_range = content_range
        self.content_length = content_length
        self.disconnect_after = disconnect_after
        self.hold = hold
        self.aclose_called = False
        self.aclose_count = 0
        self.aiter_calls = 0

    async def aiter_bytes(self, chunk_size: int = 65536):
        self.aiter_calls += 1
        if self.hold is not None:
            await self.hold.wait()
        sent = 0
        data = self.payload
        limit = self.disconnect_after
        while sent < len(data):
            if limit is not None and sent >= limit:
                raise httpx.ReadError("injected upstream disconnect")
            n = min(chunk_size, len(data) - sent)
            if limit is not None:
                n = min(n, limit - sent)
                if n <= 0:
                    raise httpx.ReadError("injected upstream disconnect")
            yield data[sent : sent + n]
            sent += n
            if limit is not None and sent >= limit:
                raise httpx.ReadError("injected upstream disconnect")

    async def aclose(self) -> None:
        self.aclose_called = True
        self.aclose_count += 1


@pytest.fixture(autouse=True)
def _stream_harness(monkeypatch):
    original_key = settings.API_KEY
    original_chunk = settings.STREAM_CHUNK_SIZE
    original_conc = settings.MAX_CONCURRENT_STREAMS
    settings.API_KEY = "test-key-123"
    settings.STREAM_CHUNK_SIZE = 65536
    settings.MAX_CONCURRENT_STREAMS = 4
    stream_mod.stream_limiter.reset()

    opens: list[dict] = []
    media_calls: list[str] = []
    keys = [KEY_A, KEY_B]
    disconnect_abs: list[int | None] = []
    hold_event: dict[str, asyncio.Event | None] = {"e": None}

    async def fake_fetch(sph_code: str) -> dict:
        media_calls.append(sph_code)
        idx = len(media_calls) - 1
        key = keys[idx] if idx < len(keys) else keys[-1]
        return {
            "full_url": f"https://cdn.test/v{len(media_calls)}",
            "decode_key": key,
            "file_size": TIKHUB_FILE_SIZE,
        }

    async def fake_open(url: str, range_header: str | None) -> FakeCdn:
        nth = int(url.rsplit("v", 1)[-1])
        key = keys[nth - 1] if nth - 1 < len(keys) else keys[-1]
        cipher = _cipher(key)
        if range_header:
            requested_start, requested_end, _ = stream_mod.parse_byte_range(range_header)
            if requested_start is None:
                suffix = int(range_header.split("=", 1)[1].split("-", 1)[1])
                start = max(FILE_SIZE - suffix, 0)
            else:
                start = requested_start
            if start >= FILE_SIZE:
                return FakeCdn(
                    b"",
                    status_code=416,
                    content_range=f"bytes */{FILE_SIZE}",
                    content_length="0",
                )
            end = (
                FILE_SIZE - 1
                if requested_end is None
                else min(requested_end, FILE_SIZE - 1)
            )
            status = 206
        else:
            start, end = 0, FILE_SIZE - 1
            status = 200
        payload = cipher[start : end + 1]
        abs_cut = disconnect_abs[len(opens)] if len(opens) < len(disconnect_abs) else None
        rel = None if abs_cut is None else max(abs_cut - start, 0)
        cdn = FakeCdn(
            payload,
            status_code=status,
            content_range=(
                f"bytes {start}-{end}/{FILE_SIZE}" if status == 206 else None
            ),
            content_length=str(len(payload)),
            disconnect_after=rel,
            hold=hold_event["e"],
        )
        opens.append({"url": url, "range": range_header, "stream": cdn, "start": start})
        return cdn

    monkeypatch.setattr(stream_mod, "_fetch_media", fake_fetch)
    monkeypatch.setattr(stream_mod, "open_cdn_stream", fake_open)
    harness = {
        "opens": opens,
        "media_calls": media_calls,
        "disconnect_abs": disconnect_abs,
        "hold_event": hold_event,
        "keys": keys,
    }
    yield harness
    settings.API_KEY = original_key
    settings.STREAM_CHUNK_SIZE = original_chunk
    settings.MAX_CONCURRENT_STREAMS = original_conc
    stream_mod.stream_limiter.reset()


@pytest.fixture()
def client(db):
    def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


def _get(client: TestClient, range_header: str | None = None, sph_code: str = SPH_CODE):
    headers = dict(AUTH)
    if range_header is not None:
        headers["Range"] = range_header
    return client.get(f"/api/stream/wechat_channels/{sph_code}", headers=headers)


RANGE_CASES = [
    ("no_range", None),
    ("bytes_0_open", "bytes=0-"),
    ("bytes_encrypted_exact", "bytes=0-131071"),
    ("bytes_cross_boundary", "bytes=0-131072"),
    ("bytes_straddle", "bytes=131071-131073"),
    ("bytes_plain_from_boundary", "bytes=131072-"),
    ("bytes_plain_window", "bytes=200000-300000"),
    ("bytes_window_past_cdn_end", "bytes=200000-700000"),
]


@pytest.mark.parametrize("name,range_header", RANGE_CASES, ids=[c[0] for c in RANGE_CASES])
def test_range_matches_full_download_slice(client, _stream_harness, name, range_header):
    expected = _slice_plain(range_header)
    resp = _get(client, range_header)
    assert resp.status_code in (200, 206)
    assert resp.headers["content-type"].startswith("video/mp4")
    assert resp.content == expected
    if range_header is None:
        assert resp.content[4:8] == b"ftyp"
        assert resp.status_code == 200
        assert resp.headers["content-length"] == str(FILE_SIZE)
        assert resp.headers["content-length"] != str(TIKHUB_FILE_SIZE)
    else:
        assert resp.status_code == 206
        assert _stream_harness["opens"][0]["range"] == range_header
        start, end, _ = stream_mod.parse_byte_range(range_header)
        assert start is not None
        expected_end = FILE_SIZE - 1 if end is None else min(end, FILE_SIZE - 1)
        assert resp.headers["content-range"] == (
            f"bytes {start}-{expected_end}/{FILE_SIZE}"
        )
        assert resp.headers["content-length"] == str(expected_end - start + 1)


MATRIX_CLIENTS = {
    "R0": (None, 0, None),
    "R1": ("bytes=0-", 0, None),
    "R2": ("bytes=200000-", 200000, None),
    "R3": ("bytes=0-99999", 0, 99999),
    "R4": ("bytes=200000-299999", 200000, 299999),
    "R5": ("bytes=-100000", None, None),
}


MATRIX_RESPONSE_FORMS = ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"]


def _matrix_cdn_range(range_id: str, response_id: str) -> tuple[int, int] | None:
    starts = {
        "R0": 0,
        "R1": 0,
        "R2": 200000,
        "R3": 0,
        "R4": 200000,
        "R5": FILE_SIZE - 100000,
    }
    if response_id == "C6":
        return None
    start = starts[range_id]
    if response_id == "C5":
        start += 1
    if response_id == "C4":
        end = {
            "R0": FILE_SIZE - 2,
            "R1": FILE_SIZE - 2,
            "R2": FILE_SIZE - 2,
            "R3": 99999,
            "R4": 299999,
            "R5": FILE_SIZE - 2,
        }[range_id]
    else:
        end = FILE_SIZE - 1
    return start, end


def _matrix_expected(range_id: str, response_id: str) -> dict:
    if response_id == "C7":
        return {"status": 416, "content_length": None, "content_range": "bytes */600000"}
    if response_id in {"C5", "C6", "C8"}:
        return {"status": 502, "content_length": None, "content_range": None}
    if response_id == "C4" and range_id == "R0":
        return {"status": 502, "content_length": None, "content_range": None}
    if response_id == "C2" and range_id in {"R1", "R2", "R4", "R5"}:
        return {"status": 502, "content_length": None, "content_range": None}
    if response_id == "C1" and range_id in {"R2", "R4", "R5"}:
        return {"status": 502, "content_length": None, "content_range": None}
    if response_id == "C2" and range_id == "R0":
        return {"status": 200, "content_length": None, "content_range": None, "start": 0, "end": FILE_SIZE - 1}
    if response_id == "C2" and range_id == "R3":
        return {"status": 206, "content_length": None, "content_range": "bytes 0-99999/*", "start": 0, "end": 99999}
    if response_id == "C1":
        _, requested_start, requested_end = MATRIX_CLIENTS[range_id]
        start = 0
        end = FILE_SIZE - 1 if requested_end is None else min(requested_end, FILE_SIZE - 1)
        return {
            "status": 200 if range_id == "R0" else 206,
            "content_length": FILE_SIZE if range_id == "R0" else end - start + 1,
            "content_range": None if range_id == "R0" else f"bytes {start}-{end}/{FILE_SIZE}",
            "start": start,
            "end": end,
        }
    start, end = _matrix_cdn_range(range_id, response_id)
    return {
        "status": 200 if range_id == "R0" else 206,
        "content_length": end - start + 1 if range_id != "R0" else FILE_SIZE,
        "content_range": None if range_id == "R0" else f"bytes {start}-{end}/{FILE_SIZE}",
        "start": start,
        "end": end,
    }


@pytest.mark.parametrize(
    "range_id, response_id",
    [(range_id, response_id) for range_id in MATRIX_CLIENTS for response_id in MATRIX_RESPONSE_FORMS],
    ids=[f"{range_id}x{response_id}" for range_id in MATRIX_CLIENTS for response_id in MATRIX_RESPONSE_FORMS],
)
def test_client_range_cdn_response_matrix(
    client, _stream_harness, monkeypatch, range_id, response_id
):
    range_header = MATRIX_CLIENTS[range_id][0]
    expected = _matrix_expected(range_id, response_id)
    cipher = _cipher(KEY_A)

    async def matrix_open(url: str, requested_range: str | None):
        assert requested_range == range_header
        if response_id == "C1":
            cdn = FakeCdn(
                cipher,
                status_code=200,
                content_length=str(FILE_SIZE),
            )
        elif response_id == "C2":
            cdn = FakeCdn(cipher, status_code=200)
        elif response_id in {"C3", "C4", "C5"}:
            start, end = _matrix_cdn_range(range_id, response_id)
            cdn = FakeCdn(
                cipher[start : end + 1],
                status_code=206,
                content_range=f"bytes {start}-{end}/{FILE_SIZE}",
                content_length=str(end - start + 1),
            )
        elif response_id == "C6":
            cdn = FakeCdn(
                b"malformed response must not be consumed",
                status_code=206,
                content_range="bytes malformed",
            )
        elif response_id == "C7":
            cdn = FakeCdn(
                b"416 response must not be consumed",
                status_code=416,
                content_range=f"bytes */{FILE_SIZE}",
                content_length="0",
            )
        else:
            cdn = FakeCdn(b"upstream error must not be consumed", status_code=500)
        _stream_harness["opens"].append({"url": url, "range": requested_range, "stream": cdn})
        return cdn

    monkeypatch.setattr(stream_mod, "open_cdn_stream", matrix_open)
    response = _get(client, range_header)

    assert response.status_code == expected["status"]
    if expected["status"] in {200, 206}:
        assert response.headers["content-type"].startswith("video/mp4")
        assert response.content == PLAIN[expected["start"] : expected["end"] + 1]
        assert len(response.content) == expected["end"] - expected["start"] + 1
        if expected["content_length"] is None:
            assert "content-length" not in response.headers
        else:
            assert response.headers["content-length"] == str(expected["content_length"])
        if expected["content_range"] is None:
            assert "content-range" not in response.headers
        else:
            assert response.headers["content-range"] == expected["content_range"]
    else:
        assert response.headers["content-type"].startswith("application/json")
        assert "detail" in response.json()
        assert int(response.headers["content-length"]) == len(response.content)
        if expected["content_range"] is None:
            assert "content-range" not in response.headers
        else:
            assert response.headers["content-range"] == expected["content_range"]
        assert _stream_harness["opens"][0]["stream"].aiter_calls == 0


@pytest.mark.parametrize(
    "range_header",
    ["items=0-1", "bytes=0-1,2-3", "bytes=bad-1"],
    ids=["non-bytes", "multiple", "malformed"],
)
def test_malformed_range_rejected_before_cdn_request(
    client, _stream_harness, range_header
):
    response = _get(client, range_header)

    assert response.status_code == 416
    assert response.headers["content-type"].startswith("application/json")
    assert int(response.headers["content-length"]) == len(response.content)
    assert _stream_harness["opens"] == []


@pytest.mark.parametrize(
    "range_header, expected_end",
    [("bytes=0-", FILE_SIZE - 1), ("bytes=0-131071", 131071)],
    ids=["open-ended", "bounded"],
)
def test_range_start_zero_accepts_cdn_full_response(
    client, _stream_harness, monkeypatch, range_header, expected_end
):
    real_open = stream_mod.open_cdn_stream

    async def return_full_response(url: str, requested_range: str | None):
        response = await real_open(url, requested_range)
        response.status_code = 200
        response.content_range = None
        response.content_length = str(FILE_SIZE)
        response.payload = _cipher(KEY_A)
        return response

    monkeypatch.setattr(stream_mod, "open_cdn_stream", return_full_response)
    resp = _get(client, range_header)

    expected = PLAIN[: expected_end + 1]
    assert resp.status_code == 206
    assert resp.content == expected
    assert resp.headers["content-length"] == str(len(expected))
    assert resp.headers["content-range"] == (
        f"bytes 0-{expected_end}/{FILE_SIZE}"
    )
    assert _stream_harness["opens"][0]["range"] == range_header


def test_range_nonzero_start_rejects_cdn_full_response(
    client, _stream_harness, monkeypatch
):
    real_open = stream_mod.open_cdn_stream

    async def return_full_response(url: str, requested_range: str | None):
        response = await real_open(url, requested_range)
        response.status_code = 200
        response.content_range = None
        response.content_length = str(FILE_SIZE)
        response.payload = _cipher(KEY_A)
        return response

    monkeypatch.setattr(stream_mod, "open_cdn_stream", return_full_response)
    resp = _get(client, "bytes=200000-")

    assert resp.status_code == 502
    assert resp.headers["content-type"].startswith("application/json")
    assert "detail" in resp.json()
    assert _stream_harness["opens"][0]["stream"].aiter_calls == 0


def test_missing_api_key_401(client):
    resp = client.get(f"/api/stream/wechat_channels/{SPH_CODE}")
    assert resp.status_code == 401
    assert "application/json" in resp.headers.get("content-type", "")


def test_wrong_api_key_401(client):
    resp = client.get(
        f"/api/stream/wechat_channels/{SPH_CODE}",
        headers={"X-API-Key": "wrong-key"},
    )
    assert resp.status_code == 401
    assert "application/json" in resp.headers.get("content-type", "")


@pytest.mark.parametrize("invalid_sph_code", ["bad-code", "x" * 65])
def test_invalid_sph_code_rejected_without_external_request(
    client, _stream_harness, invalid_sph_code
):
    response = _get(client, sph_code=invalid_sph_code)

    assert 400 <= response.status_code < 500
    assert _stream_harness["media_calls"] == []
    assert _stream_harness["opens"] == []


def test_empty_sph_code_rejected_without_external_request(client, _stream_harness):
    response = client.get("/api/stream/wechat_channels/", headers=AUTH)

    assert 400 <= response.status_code < 500
    assert _stream_harness["media_calls"] == []
    assert _stream_harness["opens"] == []


def test_tikhub_failure_returns_5xx_json(client, monkeypatch):
    async def boom(sph_code: str):
        raise ProviderError("tikhub down")

    monkeypatch.setattr(stream_mod, "_fetch_media", boom)
    resp = _get(client)
    assert resp.status_code == 502
    body = resp.json()
    assert "detail" in body
    assert resp.headers["content-type"].startswith("application/json")


def test_upstream_disconnect_terminates_response_with_error(
    client, _stream_harness
):
    _stream_harness["disconnect_abs"].append(50_000)
    errors: list[str] = []
    with TestClient(app) as raising_client:
        hid = loguru_logger.add(lambda m: errors.append(str(m)), level="ERROR")
        try:
            with pytest.raises(httpx.ReadError):
                _get(raising_client)
        finally:
            loguru_logger.remove(hid)

    assert len(_stream_harness["opens"]) == 1
    assert _stream_harness["media_calls"] == [SPH_CODE]
    assert _stream_harness["opens"][0]["stream"].aclose_called is True
    assert any("wechat stream upstream failed" in msg for msg in errors)


def test_initial_range_mismatch_fails_before_streaming(
    client, _stream_harness, monkeypatch
):
    range_header = "bytes=100-200"
    real_open = stream_mod.open_cdn_stream
    open_calls = 0

    async def mismatch_once(url: str, requested_range: str | None):
        nonlocal open_calls
        open_calls += 1
        response = await real_open(url, requested_range)
        if open_calls == 1:
            response.content_range = "bytes 0-100/600000"
            response.payload = b"wrong bytes must not be consumed"
        return response

    monkeypatch.setattr(stream_mod, "open_cdn_stream", mismatch_once)
    resp = _get(client, range_header)

    assert resp.status_code == 502
    assert resp.headers["content-type"].startswith("application/json")
    assert "detail" in resp.json()
    assert open_calls == 1
    assert _stream_harness["opens"][0]["stream"].aiter_calls == 0


def test_initial_range_missing_content_range_fails_before_streaming(
    client, _stream_harness, monkeypatch
):
    range_header = "bytes=100-200"
    real_open = stream_mod.open_cdn_stream
    open_calls = 0

    async def missing_once(url: str, requested_range: str | None):
        nonlocal open_calls
        open_calls += 1
        response = await real_open(url, requested_range)
        if open_calls == 1:
            response.content_range = None
            response.payload = b"missing range must not be consumed"
        return response

    monkeypatch.setattr(stream_mod, "open_cdn_stream", missing_once)
    resp = _get(client, range_header)

    assert resp.status_code == 502
    assert resp.headers["content-type"].startswith("application/json")
    assert "detail" in resp.json()
    assert open_calls == 1
    assert _stream_harness["opens"][0]["stream"].aiter_calls == 0


def test_initial_range_malformed_content_range_fails_before_streaming(
    client, _stream_harness, monkeypatch
):
    range_header = "bytes=100-200"
    real_open = stream_mod.open_cdn_stream
    open_calls = 0

    async def malformed_once(url: str, requested_range: str | None):
        nonlocal open_calls
        open_calls += 1
        response = await real_open(url, requested_range)
        if open_calls == 1:
            response.content_range = "bytes not-a-range"
            response.payload = b"malformed range must not be consumed"
        return response

    monkeypatch.setattr(stream_mod, "open_cdn_stream", malformed_once)
    resp = _get(client, range_header)

    assert resp.status_code == 502
    assert resp.headers["content-type"].startswith("application/json")
    assert "detail" in resp.json()
    assert open_calls == 1
    assert _stream_harness["opens"][0]["stream"].aiter_calls == 0


def test_initial_range_inverted_content_range_fails_before_streaming(
    client, _stream_harness, monkeypatch
):
    real_open = stream_mod.open_cdn_stream

    async def return_inverted(url: str, requested_range: str | None):
        response = await real_open(url, requested_range)
        response.content_range = "bytes 201-100/600000"
        response.payload = b"inverted range must not be consumed"
        return response

    monkeypatch.setattr(stream_mod, "open_cdn_stream", return_inverted)
    response = _get(client, "bytes=100-200")

    assert response.status_code == 502
    assert response.headers["content-type"].startswith("application/json")
    assert isinstance(response.json(), dict)
    assert _stream_harness["opens"][0]["stream"].aiter_calls == 0


def test_initial_range_end_mismatch_uses_cdn_range(
    client, _stream_harness, monkeypatch
):
    real_open = stream_mod.open_cdn_stream

    async def return_short_range(url: str, requested_range: str | None):
        response = await real_open(url, requested_range)
        response.content_range = "bytes 100-199/600000"
        response.payload = response.payload[:100]
        response.content_length = "100"
        return response

    monkeypatch.setattr(stream_mod, "open_cdn_stream", return_short_range)
    response = _get(client, "bytes=100-200")

    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 100-199/600000"
    assert response.headers["content-length"] == "100"
    assert response.content == PLAIN[100:200]


def test_initial_range_complete_length_mismatch_is_allowed(
    client, _stream_harness, monkeypatch
):
    real_open = stream_mod.open_cdn_stream

    async def return_wrong_total(url: str, requested_range: str | None):
        response = await real_open(url, requested_range)
        response.content_range = "bytes 100-200/600001"
        return response

    monkeypatch.setattr(stream_mod, "open_cdn_stream", return_wrong_total)
    response = _get(client, "bytes=100-200")

    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 100-200/600001"
    assert response.content == _slice_plain("bytes=100-200")


def test_initial_range_content_length_mismatch_fails_before_streaming(
    client, _stream_harness, monkeypatch
):
    real_open = stream_mod.open_cdn_stream

    async def return_truncated_payload(url: str, requested_range: str | None):
        response = await real_open(url, requested_range)
        response.payload = b"x"
        response.content_length = "1"
        return response

    monkeypatch.setattr(stream_mod, "open_cdn_stream", return_truncated_payload)
    response = _get(client, "bytes=100-200")

    assert response.status_code == 502
    assert response.headers["content-type"].startswith("application/json")
    assert isinstance(response.json(), dict)
    assert _stream_harness["opens"][0]["stream"].aiter_calls == 0


def test_initial_range_invalid_content_length_fails_before_streaming(
    client, _stream_harness, monkeypatch
):
    real_open = stream_mod.open_cdn_stream

    async def return_invalid_length(url: str, requested_range: str | None):
        response = await real_open(url, requested_range)
        response.content_length = "not-a-length"
        return response

    monkeypatch.setattr(stream_mod, "open_cdn_stream", return_invalid_length)
    response = _get(client, "bytes=100-200")

    assert response.status_code == 502
    assert response.headers["content-type"].startswith("application/json")
    assert isinstance(response.json(), dict)
    assert _stream_harness["opens"][0]["stream"].aiter_calls == 0


def test_initial_range_missing_content_length_is_allowed(
    client, _stream_harness, monkeypatch
):
    real_open = stream_mod.open_cdn_stream

    async def return_without_length(url: str, requested_range: str | None):
        response = await real_open(url, requested_range)
        response.content_length = None
        return response

    monkeypatch.setattr(stream_mod, "open_cdn_stream", return_without_length)
    response = _get(client, "bytes=100-200")

    assert response.status_code == 206
    assert "content-length" not in response.headers
    assert response.content == _slice_plain("bytes=100-200")


def test_initial_range_wildcard_complete_length_is_allowed(
    client, _stream_harness, monkeypatch
):
    real_open = stream_mod.open_cdn_stream

    async def return_unknown_total(url: str, requested_range: str | None):
        response = await real_open(url, requested_range)
        response.content_range = "bytes 100-200/*"
        return response

    monkeypatch.setattr(stream_mod, "open_cdn_stream", return_unknown_total)
    response = _get(client, "bytes=100-200")

    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 100-200/*"
    assert response.content == _slice_plain("bytes=100-200")


def test_no_range_206_start_zero_reconciles_before_streaming(
    client, _stream_harness, monkeypatch
):
    real_open = stream_mod.open_cdn_stream

    async def return_206(url: str, requested_range: str | None):
        response = await real_open(url, requested_range)
        response.status_code = 206
        response.content_range = f"bytes 0-{FILE_SIZE - 1}/{FILE_SIZE}"
        return response

    monkeypatch.setattr(stream_mod, "open_cdn_stream", return_206)
    response = _get(client)

    assert response.status_code == 200
    assert response.content == PLAIN


def test_no_range_without_cdn_content_length_uses_chunked_streaming(
    client, _stream_harness, monkeypatch
):
    real_open = stream_mod.open_cdn_stream

    async def return_without_length(url: str, requested_range: str | None):
        response = await real_open(url, requested_range)
        response.content_length = None
        return response

    monkeypatch.setattr(stream_mod, "open_cdn_stream", return_without_length)
    response = _get(client)

    assert response.status_code == 200
    assert "content-length" not in response.headers
    assert response.content == PLAIN


def test_no_range_206_nonzero_start_fails_before_streaming(
    client, _stream_harness, monkeypatch
):
    real_open = stream_mod.open_cdn_stream

    async def return_206(url: str, requested_range: str | None):
        response = await real_open(url, requested_range)
        response.status_code = 206
        response.content_range = f"bytes 1-{FILE_SIZE - 1}/{FILE_SIZE}"
        return response

    monkeypatch.setattr(stream_mod, "open_cdn_stream", return_206)
    response = _get(client)

    assert response.status_code == 502
    assert response.headers["content-type"].startswith("application/json")
    assert "detail" in response.json()
    assert _stream_harness["opens"][0]["stream"].aiter_calls == 0


def test_invalid_decode_key_returns_502_json_before_streaming(
    client, _stream_harness, monkeypatch
):
    async def invalid_media(sph_code: str) -> dict:
        return {
            "full_url": "https://cdn.test/v1",
            "decode_key": "not-a-decimal-key",
            "file_size": FILE_SIZE,
        }

    monkeypatch.setattr(stream_mod, "_fetch_media", invalid_media)
    response = _get(client)

    assert response.status_code == 502
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["detail"] == "Invalid decode_key"
    assert _stream_harness["opens"] == []


@pytest.mark.asyncio
async def test_client_disconnect_acloses_upstream(_stream_harness):
    media = {
        "full_url": "https://cdn.test/v1",
        "decode_key": KEY_A,
        "file_size": FILE_SIZE,
    }
    cdn = FakeCdn(_cipher(KEY_A), status_code=200)
    agen = stream_mod._iter_decrypted(
        sph_code=SPH_CODE,
        first_media=media,
        first_stream=cdn,
        start=0,
        end=FILE_SIZE - 1,
    )
    first = await agen.__anext__()
    assert len(first) > 0
    await agen.aclose()
    assert cdn.aclose_called is True
    assert cdn.aclose_count >= 1


@pytest.mark.asyncio
async def test_pre_response_cancel_during_media_releases_slot(db, _stream_harness, monkeypatch):
    settings.MAX_CONCURRENT_STREAMS = 1
    stream_mod.stream_limiter.reset()
    started = asyncio.Event()

    async def blocked_fetch(sph_code: str) -> dict:
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(stream_mod, "_fetch_media", blocked_fetch)

    def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            task = asyncio.create_task(
                ac.get(f"/api/stream/wechat_channels/{SPH_CODE}", headers=AUTH)
            )
            await asyncio.wait_for(started.wait(), timeout=1)
            assert stream_mod.stream_limiter.active == 1
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert stream_mod.stream_limiter.active == 0
    finally:
        app.dependency_overrides.clear()
        stream_mod.stream_limiter.reset()


@pytest.mark.asyncio
async def test_pre_response_cancel_during_cdn_open_closes_client(
    db, _stream_harness, monkeypatch
):
    settings.MAX_CONCURRENT_STREAMS = 1
    stream_mod.stream_limiter.reset()
    started = asyncio.Event()
    clients: list[object] = []
    real_async_client = httpx.AsyncClient

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.closed = False
            self.send_called = False
            clients.append(self)

        def build_request(self, method, url, headers):
            return object()

        async def send(self, request, stream):
            self.send_called = True
            started.set()
            await asyncio.Event().wait()

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr(stream_mod.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(
        stream_mod, "open_cdn_stream", stream_mod._open_cdn_stream_httpx
    )

    def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    try:
        async with real_async_client(transport=transport, base_url="http://test") as ac:
            task = asyncio.create_task(
                ac.get(f"/api/stream/wechat_channels/{SPH_CODE}", headers=AUTH)
            )
            await asyncio.wait_for(started.wait(), timeout=1)
            assert stream_mod.stream_limiter.active == 1
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert stream_mod.stream_limiter.active == 0
            opened = [client for client in clients if client.send_called]
            assert len(opened) == 1
            assert opened[0].closed is True
    finally:
        app.dependency_overrides.clear()
        stream_mod.stream_limiter.reset()


@pytest.mark.asyncio
async def test_repeated_pre_response_cancels_do_not_exhaust_slots(
    db, _stream_harness, monkeypatch
):
    settings.MAX_CONCURRENT_STREAMS = 4
    stream_mod.stream_limiter.reset()
    cancellations = settings.MAX_CONCURRENT_STREAMS + 1
    started = asyncio.Event()
    calls = 0

    async def cancel_then_return(sph_code: str) -> dict:
        nonlocal calls
        calls += 1
        if calls <= cancellations:
            started.set()
            await asyncio.Event().wait()
        return {
            "full_url": "https://cdn.test/v1",
            "decode_key": KEY_A,
            "file_size": FILE_SIZE,
        }

    monkeypatch.setattr(stream_mod, "_fetch_media", cancel_then_return)

    def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            for _ in range(cancellations):
                started.clear()
                task = asyncio.create_task(
                    ac.get(f"/api/stream/wechat_channels/{SPH_CODE}", headers=AUTH)
                )
                await asyncio.wait_for(started.wait(), timeout=1)
                assert stream_mod.stream_limiter.active == 1
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert stream_mod.stream_limiter.active == 0

            response = await ac.get(
                f"/api/stream/wechat_channels/{SPH_CODE}", headers=AUTH
            )
            assert response.status_code == 200
            assert response.content == PLAIN
    finally:
        app.dependency_overrides.clear()
        stream_mod.stream_limiter.reset()


@pytest.mark.asyncio
async def test_concurrency_limit_429_and_release(db, _stream_harness):
    settings.MAX_CONCURRENT_STREAMS = 2
    stream_mod.stream_limiter.reset()
    gate = asyncio.Event()
    _stream_harness["hold_event"]["e"] = gate

    def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:

            async def _download():
                return await ac.get(
                    f"/api/stream/wechat_channels/{SPH_CODE}", headers=AUTH
                )

            t1 = asyncio.create_task(_download())
            t2 = asyncio.create_task(_download())
            for _ in range(50):
                if stream_mod.stream_limiter.active >= 2:
                    break
                await asyncio.sleep(0.01)
            assert stream_mod.stream_limiter.active == 2
            overflow = await ac.get(
                f"/api/stream/wechat_channels/{SPH_CODE}", headers=AUTH
            )
            assert overflow.status_code == 429
            assert overflow.headers["content-type"].startswith("application/json")
            gate.set()
            r1, r2 = await asyncio.gather(t1, t2)
            assert r1.status_code == 200
            assert r2.status_code == 200
            assert r1.content == PLAIN
            assert r2.content == PLAIN
            after = await ac.get(
                f"/api/stream/wechat_channels/{SPH_CODE}", headers=AUTH
            )
            assert after.status_code == 200
            assert after.content == PLAIN
    finally:
        gate.set()
        app.dependency_overrides.clear()
        stream_mod.stream_limiter.reset()


def test_memory_constant_no_full_body_read(client, monkeypatch, _stream_harness):
    src = Path(stream_mod.__file__).read_text(encoding="utf-8")
    assert "aread(" not in src
    assert "response.content" not in src
    assert "readall" not in src
    assert "aiter_bytes" in src

    settings.STREAM_CHUNK_SIZE = 1024
    seen: list[int] = []
    real_xor = stream_mod.xor_chunk

    def tracking_xor(chunk, decode_key, absolute_offset):
        seen.append(len(chunk))
        return real_xor(chunk, decode_key, absolute_offset)

    monkeypatch.setattr(stream_mod, "xor_chunk", tracking_xor)
    resp = _get(client)
    assert resp.status_code == 200
    assert resp.content == PLAIN
    assert seen
    assert max(seen) <= settings.STREAM_CHUNK_SIZE
    assert max(seen) < FILE_SIZE
    assert sum(seen) == FILE_SIZE


def test_each_request_fetches_media_fresh(client, _stream_harness):
    _get(client)
    _get(client)
    assert _stream_harness["media_calls"] == [SPH_CODE, SPH_CODE]
    assert len(_stream_harness["opens"]) == 2
    assert _stream_harness["opens"][0]["url"] != _stream_harness["opens"][1]["url"]


@pytest.mark.asyncio
async def test_fetch_wechat_channels_media_reads_fixture_and_is_uncached(monkeypatch):
    import json
    from app.services.providers.tikhub import TikHubProvider

    payload = json.loads(
        (Path(__file__).parent / "fixtures" / "wechat_channels" / "detail.json").read_text(
            encoding="utf-8"
        )
    )
    calls: list[str] = []

    async def fake_chain(self, video_id, original_url):
        calls.append((video_id, original_url))
        return payload

    monkeypatch.setattr(TikHubProvider, "_fetch_wechat_channels", fake_chain)
    provider = TikHubProvider()
    a = await provider.fetch_wechat_channels_media(SPH_CODE)
    b = await provider.fetch_wechat_channels_media(SPH_CODE)
    assert calls == [("", "https://weixin.qq.com/sph/AOzokRxWHz")] * 2
    assert a["full_url"] == "REDACTED"
    assert a["decode_key"] == "REDACTED"
    assert a["file_size"] == 2450521066
    assert a == b


@pytest.mark.asyncio
async def test_fetch_wechat_channels_media_retries_retryable_then_hits(monkeypatch):
    import json
    from app.services.providers.tikhub import TikHubProvider
    from app.services.providers.base import VideoNotFoundError

    payload = json.loads(
        (Path(__file__).parent / "fixtures" / "wechat_channels" / "detail.json").read_text(
            encoding="utf-8"
        )
    )
    n = {"i": 0}

    async def flaky(self, video_id, original_url):
        n["i"] += 1
        if n["i"] < 3:
            raise VideoNotFoundError("retryable envelope")
        return payload

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(TikHubProvider, "_fetch_wechat_channels", flaky)
    monkeypatch.setattr("app.services.providers.tikhub.asyncio.sleep", no_sleep)
    out = await TikHubProvider().fetch_wechat_channels_media(SPH_CODE)
    assert n["i"] == 3
    assert out["file_size"] == 2450521066


@pytest.mark.asyncio
async def test_fetch_wechat_channels_media_retry_exhausted_raises(monkeypatch):
    from app.services.providers.tikhub import TikHubProvider
    from app.services.providers.base import VideoNotFoundError

    n = {"i": 0}

    async def always_miss(self, video_id, original_url):
        n["i"] += 1
        raise VideoNotFoundError("still missing")

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(TikHubProvider, "_fetch_wechat_channels", always_miss)
    monkeypatch.setattr("app.services.providers.tikhub.asyncio.sleep", no_sleep)
    with pytest.raises(VideoNotFoundError):
        await TikHubProvider().fetch_wechat_channels_media(SPH_CODE)
    assert n["i"] == 3
