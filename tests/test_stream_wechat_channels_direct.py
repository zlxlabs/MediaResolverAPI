"""GET /api/stream/wechat_channels/{sph_code}/direct — all network stubbed.

Normal path, short file, expired-URL refresh, missing complete length,
bad sph_code, TikHub failure.
"""

from __future__ import annotations

import base64

import pytest

import app.api.stream as stream_mod
from app.core.config import settings
from app.services.providers.base import ProviderError
from app.services.wechat_channels_crypto import KEYSTREAM_SIZE, generate_keystream

SPH_CODE = "AOzokRxWHz"
FILE_SIZE = 435_768_323
KEY_A = 55516695
KEY_B = 12345678
URL_A = "https://cdn.test/a"
URL_B = "https://cdn.test/b"
PLAIN_HEAD = bytes.fromhex("000000206674797069736f6d") + bytes(
    (i * 31 + 7) & 0xFF for i in range(KEYSTREAM_SIZE - 12)
)
AUTH = {"X-API-Key": "test-key-123"}


def _cipher_head(key) -> bytes:
    """Encrypt by indexing the keystream, never via xor_chunk.

    xor_chunk is the unit under test on the decrypt path; using it here would
    cancel an off-by-one. generate_keystream is the sample-verified oracle.
    """
    ks = generate_keystream(key)
    return bytes(PLAIN_HEAD[i] ^ ks[i] for i in range(KEYSTREAM_SIZE))


class FakeCdn:
    def __init__(
        self,
        payload: bytes,
        *,
        status_code: int,
        content_range: str | None = None,
        content_length: str | None = None,
    ) -> None:
        self.payload = payload
        self.status_code = status_code
        self.content_range = content_range
        self.content_length = content_length
        self.aclose_called = False

    async def aiter_bytes(self, chunk_size: int = 65536):
        for i in range(0, len(self.payload), chunk_size):
            yield self.payload[i : i + chunk_size]

    async def aclose(self) -> None:
        self.aclose_called = True


@pytest.fixture()
def harness(monkeypatch):
    original_key = settings.API_KEY
    settings.API_KEY = "test-key-123"

    opens: list[dict] = []
    media_calls: list[str] = []
    control = {
        "file_size": FILE_SIZE,
        "first_status": None,  # e.g. 403 to simulate an expired first URL
        "star_length": False,  # Content-Range complete length is "*"
        "fetch_error": None,  # ProviderError instance to raise from _fetch_media
    }

    medias = [
        {"full_url": URL_A, "decode_key": KEY_A, "file_size": FILE_SIZE},
        {"full_url": URL_B, "decode_key": KEY_B, "file_size": FILE_SIZE},
    ]

    async def fake_fetch(sph_code: str) -> dict:
        media_calls.append(sph_code)
        if control["fetch_error"] is not None:
            raise control["fetch_error"]
        idx = min(len(media_calls) - 1, len(medias) - 1)
        return medias[idx]

    async def fake_open(url: str, range_header: str | None) -> FakeCdn:
        opens.append({"url": url, "range": range_header})
        if control["first_status"] is not None and len(opens) == 1:
            cdn = FakeCdn(b"", status_code=control["first_status"])
            opens[-1]["stream"] = cdn
            return cdn
        key = KEY_A if url == URL_A else KEY_B
        cipher = _cipher_head(key)
        start, req_end, _ = stream_mod.parse_byte_range(range_header)
        file_size = control["file_size"]
        end = min(req_end, file_size - 1)
        payload = cipher[start : end + 1]
        complete = "*" if control["star_length"] else str(file_size)
        cdn = FakeCdn(
            payload,
            status_code=206,
            content_range=f"bytes {start}-{end}/{complete}",
            content_length=str(len(payload)),
        )
        opens[-1]["stream"] = cdn
        return cdn

    monkeypatch.setattr(stream_mod, "_fetch_media", fake_fetch)
    monkeypatch.setattr(stream_mod, "open_cdn_stream", fake_open)
    harness = {
        "opens": opens,
        "media_calls": media_calls,
        "control": control,
        "medias": medias,
    }
    yield harness
    settings.API_KEY = original_key


def _get(client, sph_code: str = SPH_CODE, auth: dict | None = None):
    return client.get(
        f"/api/stream/wechat_channels/{sph_code}/direct",
        headers=AUTH if auth is None else auth,
    )


def test_direct_happy_path(client, harness):
    resp = _get(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["sph_code"] == SPH_CODE
    assert body["cdn_url"] == URL_A
    assert body["content_length"] == FILE_SIZE
    assert body["encrypted_head_bytes"] == KEYSTREAM_SIZE
    assert body["content_type"] == "video/mp4"
    head = base64.b64decode(body["head_b64"])
    assert head == PLAIN_HEAD
    assert len(harness["opens"]) == 1
    assert harness["opens"][0]["range"] == f"bytes=0-{KEYSTREAM_SIZE - 1}"
    assert harness["opens"][0]["stream"].aclose_called is True


def test_direct_short_file(client, harness):
    harness["control"]["file_size"] = 1000
    resp = _get(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["content_length"] == 1000
    assert body["encrypted_head_bytes"] == 1000
    head = base64.b64decode(body["head_b64"])
    assert len(head) == 1000
    assert head == PLAIN_HEAD[:1000]


def test_direct_expired_url_refreshes_media(client, harness):
    harness["control"]["first_status"] = 403
    resp = _get(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["cdn_url"] == URL_B
    assert body["content_length"] == FILE_SIZE
    head = base64.b64decode(body["head_b64"])
    assert head == PLAIN_HEAD  # decrypted with the second pair's KEY_B
    assert harness["media_calls"] == [SPH_CODE, SPH_CODE]
    assert len(harness["opens"]) == 2
    assert harness["opens"][0]["url"] == URL_A
    assert harness["opens"][1]["url"] == URL_B
    assert all(
        rec["range"] == f"bytes=0-{KEYSTREAM_SIZE - 1}" for rec in harness["opens"]
    )
    assert harness["opens"][0]["stream"].aclose_called is True
    assert harness["opens"][1]["stream"].aclose_called is True


def test_direct_refresh_invalid_second_decode_key_is_502(client, harness):
    harness["control"]["first_status"] = 403
    harness["medias"][1]["decode_key"] = "not-a-key"
    resp = _get(client)
    assert resp.status_code == 502
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["detail"] == "Invalid decode_key"
    body = resp.text
    assert URL_A not in body
    assert URL_B not in body
    assert "http://" not in body
    assert "https://" not in body
    assert len(harness["opens"]) == 1
    assert harness["opens"][0]["url"] == URL_A
    assert harness["opens"][0]["stream"].aclose_called is True


def test_direct_missing_complete_length_is_502(client, harness):
    harness["control"]["star_length"] = True
    resp = _get(client)
    assert resp.status_code == 502


def test_direct_invalid_sph_code_is_400(client, harness):
    resp = _get(client, sph_code="bad code!")
    assert resp.status_code == 400
    assert harness["media_calls"] == []
    assert harness["opens"] == []


def test_direct_tikhub_failure_is_502(client, harness):
    harness["control"]["fetch_error"] = ProviderError("tikhub down")
    resp = _get(client)
    assert resp.status_code == 502
    assert harness["opens"] == []


def test_direct_requires_api_key(client, harness):
    resp = _get(client, auth={})
    assert resp.status_code == 401
    assert harness["media_calls"] == []
