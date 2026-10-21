from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

import pytest

from app.api.stream import _FinalDownloadResponse


async def _send_response(fh: BinaryIO, range_header: str | None = None) -> list[dict]:
    response = _FinalDownloadResponse(
        fh, media_type="audio/mp4", download_name="space-test.m4a"
    )
    messages: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    headers = []
    if range_header is not None:
        headers.append((b"range", range_header.encode("latin-1")))
    await response({"type": "http", "headers": headers}, receive, send)
    return messages


def _assert_one_terminal_body(messages: list[dict]) -> None:
    body_messages = [m for m in messages if m["type"] == "http.response.body"]
    terminal = [m for m in body_messages if m.get("more_body") is False]
    assert len(terminal) == 1
    assert messages[-1] is terminal[0]


def _body_bytes(messages: list[dict]) -> bytes:
    return b"".join(
        m["body"] for m in messages if m["type"] == "http.response.body"
    )


def _start_message(messages: list[dict]) -> dict:
    return next(m for m in messages if m["type"] == "http.response.start")


async def test_full_download_sends_exactly_one_final_body(tmp_path: Path) -> None:
    payload = bytes(range(256)) * 512
    path = tmp_path / "full.m4a"
    path.write_bytes(payload)

    messages = await _send_response(path.open("rb"))

    assert _start_message(messages)["status"] == 200
    assert _body_bytes(messages) == payload
    _assert_one_terminal_body(messages)


async def test_range_download_sends_exactly_one_final_body(tmp_path: Path) -> None:
    payload = bytes(range(256)) * 512
    path = tmp_path / "range.m4a"
    path.write_bytes(payload)

    messages = await _send_response(path.open("rb"), "bytes=100-199")

    start = _start_message(messages)
    assert start["status"] == 206
    assert (
        b"content-range",
        f"bytes 100-199/{len(payload)}".encode(),
    ) in start["headers"]
    assert _body_bytes(messages) == payload[100:200]
    _assert_one_terminal_body(messages)


async def test_empty_file_sends_exactly_one_final_body(tmp_path: Path) -> None:
    path = tmp_path / "empty.m4a"
    path.write_bytes(b"")

    messages = await _send_response(path.open("rb"))

    start = _start_message(messages)
    assert start["status"] == 200
    assert (b"content-length", b"0") in start["headers"]
    assert _body_bytes(messages) == b""
    _assert_one_terminal_body(messages)


class _EarlyEofFile:
    def __init__(self, fh: BinaryIO) -> None:
        self._fh = fh

    def fileno(self) -> int:
        return self._fh.fileno()

    def seek(self, *args):
        return self._fh.seek(*args)

    def read(self, _size: int) -> bytes:
        return b""

    def close(self) -> None:
        self._fh.close()


async def test_early_eof_sends_one_empty_terminal_body(tmp_path: Path) -> None:
    path = tmp_path / "early-eof.m4a"
    path.write_bytes(b"declared bytes")

    messages = await _send_response(_EarlyEofFile(path.open("rb")))

    assert _body_bytes(messages) == b""
    _assert_one_terminal_body(messages)


async def test_unsatisfiable_range_sends_one_final_body(tmp_path: Path) -> None:
    payload = b"bytes"
    path = tmp_path / "unsatisfiable.m4a"
    path.write_bytes(payload)

    messages = await _send_response(path.open("rb"), f"bytes={len(payload)}-")

    start = _start_message(messages)
    assert start["status"] == 416
    assert (
        b"content-range",
        f"bytes */{len(payload)}".encode(),
    ) in start["headers"]
    assert _body_bytes(messages) == b""
    _assert_one_terminal_body(messages)
