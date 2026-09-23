"""Regression locks for WeChat Channels streaming endpoint boundaries.

Locks:
1. First window ConnectError retries and returns 502 JSON (#11-1).
2. Suffix range with wildcard complete-length probe fails with 502 before streaming (#13-P2-2).
"""

from __future__ import annotations

import httpx
import pytest

import app.api.stream as stream_mod
from tests.test_stream_wechat_channels import FakeCdn, _get, _stream_harness, client


def test_first_window_connect_error_retries_and_returns_502_json(
    client, _stream_harness, monkeypatch
):
    open_calls = 0

    async def connect_error_open(url: str, requested_range: str | None):
        nonlocal open_calls
        open_calls += 1
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(stream_mod, "open_cdn_stream", connect_error_open)
    resp = _get(client)

    assert resp.status_code == 502
    assert resp.status_code != 500
    assert resp.headers["content-type"].startswith("application/json")
    assert "detail" in resp.json()
    assert open_calls >= 2


def test_suffix_range_wildcard_complete_length_fails_before_streaming(
    client, _stream_harness, monkeypatch
):
    real_open = stream_mod.open_cdn_stream
    body_stream = FakeCdn(b"media_bytes_must_not_be_consumed", status_code=206)

    async def return_wildcard_probe(url: str, requested_range: str | None):
        if requested_range == "bytes=0-0":
            response = await real_open(url, requested_range)
            response.content_range = "bytes 0-0/*"
            return response
        return body_stream

    monkeypatch.setattr(stream_mod, "open_cdn_stream", return_wildcard_probe)
    resp = _get(client, "bytes=-100000")

    assert resp.status_code == 502
    assert resp.status_code != 200
    assert resp.status_code != 500
    assert resp.headers["content-type"].startswith("application/json")
    assert "detail" in resp.json()
    assert body_stream.aiter_calls == 0
    assert len(_stream_harness["opens"]) == 1
    assert _stream_harness["opens"][0]["stream"].aclose_called is True
