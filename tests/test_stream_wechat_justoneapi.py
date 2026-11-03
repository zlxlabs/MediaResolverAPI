"""
视频号 Just One API 明文流测试

覆盖（对应卡面约束 6 + E2E-Assertion）：
- 媒体模块：TikHub 命中标记 decrypt=True；TikHub 失败且 token 非空降级 JOA
  （decrypt=False）；token 为空时零 JOA 调用直接上抛；
- 流端点：TikHub 取媒体失败、JOA 返回明文 mp4（片头 ftypisom）时，
  GET /api/stream/wechat_channels/{sph} 带 key、Range: bytes=0-15 得到 ftyp
  且未调用 XOR；TikHub 命中路径既有 XOR 用例保持绿（见
  test_stream_wechat_channels.py，media 无 decrypt 键时默认走 XOR）；
- E2E：POST /api/resolve（TikHub 全败、JOA basic-info 命中）→ video_url 仍指
  本服务流端点 → GET 该地址（带 key、Range 0-15）得到 ftyp 且未走 XOR。
  同一 sph 的 resolve+stream 只打 basic-info + download 两枪（objectId 从
  VideoCache 复用，不重复付 basic-info）。
  只打桩最外层 HTTP（TikHub _call_endpoint / JOA _get / CDN），流式层真实走
  wechat_channels_media 降级模块。
"""

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.api.resolve as resolve_mod
import app.api.stream as stream_mod
from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.video_cache import VideoCache
from app.services.providers.base import ProviderError
from app.services.providers.justoneapi import JustOneApiProvider
from app.services.providers.tikhub import TikHubProvider
from app.services.wechat_channels_media import fetch_wechat_channels_media
from tests.conftest import JOA_TEST_TOKEN

FIXTURES = Path(__file__).parent / "fixtures" / "justoneapi"

SPH_CODE = "AOzokRxWHz"
SHARE_URL = f"https://weixin.qq.com/sph/{SPH_CODE}"
JOA_DOWNLOAD_URL = "https://media.example.test/weixin-channels/plaintext.mp4"
PLAIN_TOTAL = 262_144  # 明文样本总长（≥ 片头 + 一段身子）

# 明文 mp4 片头：size(32) + "ftyp" + "isom"
PLAIN_HEAD = bytes.fromhex("000000206674797069736f6d") + bytes(
    (i * 31 + 7) & 0xFF for i in range(PLAIN_TOTAL - 12)
)
AUTH = {"X-API-Key": "test-key-123"}

JOA_PATH_FIXTURES = {
    "/api/weixin-channels/get-video-basic-info/v1": "wechat_basic_info",
    "/api/weixin-channels/get-video-download-url/v1": "wechat_download_url",
}


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class PlainCdn:
    """明文 mp4 假 CDN：严格按请求 Range 回 206。"""

    def __init__(self) -> None:
        self.opens: list[str] = []

    async def __call__(self, url: str, range_header: str | None):
        self.opens.append(range_header or "")
        start, end, _ = stream_mod.parse_byte_range(range_header)
        if start is None:  # suffix range：先探测 0-0 拿总长
            suffix = int(range_header.split("=", 1)[1].split("-", 1)[1])
            start = max(PLAIN_TOTAL - suffix, 0)
            end = PLAIN_TOTAL - 1
        else:
            end = PLAIN_TOTAL - 1 if end is None else min(end, PLAIN_TOTAL - 1)
        payload = PLAIN_HEAD[start : end + 1]
        return _FakeResponse(
            payload,
            status_code=206,
            content_range=f"bytes {start}-{end}/{PLAIN_TOTAL}",
            content_length=str(len(payload)),
        )


class _FakeResponse:
    def __init__(self, payload, *, status_code, content_range, content_length):
        self.payload = payload
        self.status_code = status_code
        self.content_range = content_range
        self.content_length = content_length
        self.aclose_called = False

    async def aiter_bytes(self, chunk_size: int = 65536):
        for i in range(0, len(self.payload), chunk_size):
            yield self.payload[i : i + chunk_size]

    async def aclose(self):
        self.aclose_called = True


@pytest.fixture()
def xor_recorder(monkeypatch):
    """记录 XOR 调用（明文流必须为零次）。"""
    calls: list[int] = []

    def spy_xor(chunk, decode_key, absolute_offset):
        calls.append(absolute_offset)
        raise AssertionError("plaintext media must not be XORed")

    monkeypatch.setattr(stream_mod, "xor_chunk", spy_xor)
    return calls


@pytest.fixture()
def joa_http(monkeypatch):
    """JOA HTTP 桩（provider 最外层 _get）：按路径回 fixture 并记录。"""
    calls: list[dict] = []

    async def stub_get(self, path, params):
        calls.append({"path": path, "params": dict(params)})
        fixture = JOA_PATH_FIXTURES.get(path)
        assert fixture, f"unexpected JOA path {path}"
        return load(fixture)

    monkeypatch.setattr(JustOneApiProvider, "_get", stub_get)
    return calls


# ----------------------------- 媒体模块单测 -----------------------------

async def test_media_tikhub_hit_marks_decrypt_true(monkeypatch, joa_http):
    async def tikhub_ok(self, sph_code):
        return {"full_url": "https://cdn.test/enc", "decode_key": 55516695, "file_size": 10}

    monkeypatch.setattr(TikHubProvider, "fetch_wechat_channels_media", tikhub_ok)
    media = await fetch_wechat_channels_media(SPH_CODE)
    assert media["decrypt"] is True
    assert media["decode_key"] == 55516695
    assert joa_http == []  # TikHub 命中不问 JOA


async def test_media_tikhub_fail_falls_back_to_joa(monkeypatch, joa_http, joa_down):
    async def tikhub_down(self, sph_code):
        raise ProviderError("stub: tikhub media down")

    monkeypatch.setattr(TikHubProvider, "fetch_wechat_channels_media", tikhub_down)
    media = await fetch_wechat_channels_media(SPH_CODE)
    assert media["decrypt"] is False
    assert media["full_url"] == JOA_DOWNLOAD_URL
    assert media["file_size"] == 456301568
    assert [c["path"] for c in joa_http] == [
        "/api/weixin-channels/get-video-basic-info/v1",
        "/api/weixin-channels/get-video-download-url/v1",
    ]
    assert joa_http[0]["params"]["shareUrl"] == SHARE_URL
    assert joa_http[1]["params"]["objectId"] == "14998022876670594427"


async def test_media_joa_reuses_cached_object_id(monkeypatch, joa_http, joa_down, db):
    """resolve 已落库的 objectId 必须被流层复用：只补 download 一枪。

    同一 sph 先 resolve（basic-info）再取流（download）= ¥0.60；重复打 basic-info
    就是 ¥0.90。
    """

    async def tikhub_down(self, sph_code):
        raise ProviderError("stub: tikhub media down")

    monkeypatch.setattr(TikHubProvider, "fetch_wechat_channels_media", tikhub_down)
    db.add(
        VideoCache(
            platform="wechat_channels",
            video_id="14998022876670594427",
            download_mode="video",
            quality="",
            video_data={
                "video_id": "14998022876670594427",
                "video_url": f"/api/stream/wechat_channels/{SPH_CODE}",
            },
            provider="justoneapi",
        )
    )
    db.commit()

    media = await fetch_wechat_channels_media(SPH_CODE)
    assert media["decrypt"] is False
    assert media["full_url"] == JOA_DOWNLOAD_URL
    assert [c["path"] for c in joa_http] == [
        "/api/weixin-channels/get-video-download-url/v1",
    ]
    assert joa_http[0]["params"]["objectId"] == "14998022876670594427"


async def test_media_empty_token_never_calls_joa(monkeypatch, joa_http):
    """空 token：TikHub 失败后不降级 JOA，直接上抛（约束 1）。"""
    assert settings.JUSTONEAPI_TOKEN == ""

    async def tikhub_down(self, sph_code):
        raise ProviderError("stub: tikhub media down")

    monkeypatch.setattr(TikHubProvider, "fetch_wechat_channels_media", tikhub_down)
    with pytest.raises(ProviderError):
        await fetch_wechat_channels_media(SPH_CODE)
    assert joa_http == []


@pytest.fixture()
def joa_down(monkeypatch):
    """用例内启用非空 JOA token。"""
    original = settings.JUSTONEAPI_TOKEN
    settings.JUSTONEAPI_TOKEN = JOA_TEST_TOKEN
    yield JOA_TEST_TOKEN
    settings.JUSTONEAPI_TOKEN = original


# ----------------------------- 流端点：明文直出、跳过 XOR -----------------------------

@pytest.fixture()
def joa_stream_harness(monkeypatch):
    """stream 层 harness：媒体来自 JOA（decrypt=False），CDN 回明文。"""
    original = {
        "key": settings.API_KEY,
        "chunk": settings.STREAM_CHUNK_SIZE,
        "conc": settings.MAX_CONCURRENT_STREAMS,
        "window": settings.STREAM_WINDOW_BYTES,
        "token": settings.JUSTONEAPI_TOKEN,
    }
    settings.API_KEY = "test-key-123"
    settings.STREAM_CHUNK_SIZE = 65536
    settings.MAX_CONCURRENT_STREAMS = 4
    settings.STREAM_WINDOW_BYTES = 4194304
    settings.JUSTONEAPI_TOKEN = JOA_TEST_TOKEN
    stream_mod.stream_limiter.reset()

    media_calls: list[str] = []

    async def fake_fetch(sph_code: str) -> dict:
        media_calls.append(sph_code)
        return {"full_url": JOA_DOWNLOAD_URL, "decrypt": False, "file_size": PLAIN_TOTAL}

    cdn = PlainCdn()
    monkeypatch.setattr(stream_mod, "_fetch_media", fake_fetch)
    monkeypatch.setattr(stream_mod, "open_cdn_stream", cdn)

    yield {"media_calls": media_calls, "cdn": cdn}

    settings.API_KEY = original["key"]
    settings.STREAM_CHUNK_SIZE = original["chunk"]
    settings.MAX_CONCURRENT_STREAMS = original["conc"]
    settings.STREAM_WINDOW_BYTES = original["window"]
    settings.JUSTONEAPI_TOKEN = original["token"]
    stream_mod.stream_limiter.reset()


@pytest.fixture()
def stream_client(db):
    def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


def test_stream_joa_plaintext_head_is_ftyp_without_xor(
    stream_client, joa_stream_harness, xor_recorder
):
    resp = stream_client.get(
        f"/api/stream/wechat_channels/{SPH_CODE}",
        headers={**AUTH, "Range": "bytes=0-15"},
    )
    assert resp.status_code == 206
    assert resp.headers["content-type"].startswith("video/mp4")
    assert resp.headers["content-range"] == f"bytes 0-15/{PLAIN_TOTAL}"
    assert resp.content == PLAIN_HEAD[:16]
    assert resp.content[4:8] == b"ftyp"
    assert joa_stream_harness["media_calls"] == [SPH_CODE]
    assert xor_recorder == []  # 明文媒体零 XOR
    assert all(o == "bytes=0-15" for o in joa_stream_harness["cdn"].opens)


def test_stream_joa_plaintext_full_body(stream_client, joa_stream_harness, xor_recorder):
    resp = stream_client.get(
        f"/api/stream/wechat_channels/{SPH_CODE}", headers=AUTH
    )
    assert resp.status_code == 200
    assert resp.headers["content-length"] == str(PLAIN_TOTAL)
    assert resp.content == PLAIN_HEAD
    assert resp.content[4:8] == b"ftyp"
    assert xor_recorder == []


def test_stream_joa_plaintext_suffix_range(
    stream_client, joa_stream_harness, xor_recorder
):
    resp = stream_client.get(
        f"/api/stream/wechat_channels/{SPH_CODE}",
        headers={**AUTH, "Range": "bytes=-16"},
    )
    assert resp.status_code == 206
    assert resp.headers["content-range"] == f"bytes {PLAIN_TOTAL - 16}-{PLAIN_TOTAL - 1}/{PLAIN_TOTAL}"
    assert resp.content == PLAIN_HEAD[-16:]
    assert xor_recorder == []


def test_direct_joa_plaintext_returns_service_url(
    stream_client, joa_stream_harness, xor_recorder
):
    """/direct 对 Just One API 明文媒体：cdn_url 是本服务流地址，不回传对方直链。

    客户端门口仍是本服务：head 仍是明文（零 XOR），但 cdn_url 不能是 JOA 的
    download_url（对方 IP:port 直链）。
    """
    resp = stream_client.get(
        f"/api/stream/wechat_channels/{SPH_CODE}/direct", headers=AUTH
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["cdn_url"] == f"/api/stream/wechat_channels/{SPH_CODE}"
    assert "media.example.test" not in resp.text
    assert "justoneapi" not in resp.text
    assert "weixin-channels" not in resp.text
    head = base64.b64decode(body["head_b64"])
    assert head[:16] == PLAIN_HEAD[:16]
    assert head[4:8] == b"ftyp"
    assert xor_recorder == []  # 明文媒体零 XOR


# ----------------------------- E2E：resolve → stream（E2E-Assertion） -----------------------------

def test_resolve_then_stream_plaintext_e2e(
    authed_client, monkeypatch, joa_http, xor_recorder
):
    """只打桩最外层 HTTP（TikHub/JOA/CDN）：resolve 命中 JOA 后，流端点对同一
    sph 仍能取到明文媒体且不走 XOR。"""
    original = settings.JUSTONEAPI_TOKEN
    settings.JUSTONEAPI_TOKEN = JOA_TEST_TOKEN
    monkeypatch.setattr(resolve_mod, "_video_resolver", None)

    async def tikhub_down(self, name, path, params, per_timeout):
        raise ProviderError("stub: tikhub down")

    monkeypatch.setattr(TikHubProvider, "_call_endpoint", tikhub_down)
    monkeypatch.setattr(stream_mod, "open_cdn_stream", PlainCdn())

    try:
        resp = authed_client.post(
            "/api/resolve",
            json={"url": SHARE_URL, "translate": False, "force_refresh": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True, body
        assert body["data"]["provider"] == "justoneapi"
        video_url = body["data"]["video_url"]
        assert video_url.endswith(f"/api/stream/wechat_channels/{SPH_CODE}")

        stream_resp = authed_client.get(
            video_url.replace("http://testserver", ""),
            headers={**AUTH, "Range": "bytes=0-15"},
        )
        assert stream_resp.status_code == 206
        assert stream_resp.content[:16] == PLAIN_HEAD[:16]
        assert stream_resp.content[4:8] == b"ftyp"
        assert xor_recorder == []  # JOA 命中：明文直出，零 XOR
    finally:
        resolve_mod._video_resolver = None
        settings.JUSTONEAPI_TOKEN = original

    # resolve 1 枪 basic-info；流层复用已落库的 objectId，只补 1 枪 download。
    assert [c["path"] for c in joa_http] == [
        "/api/weixin-channels/get-video-basic-info/v1",
        "/api/weixin-channels/get-video-download-url/v1",
    ]
