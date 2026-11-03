"""
JustOneApiProvider 单元测试

覆盖（对应卡面约束 1/3/8/9 的 provider 侧）：
- 空 token：httpx 调用次数为 0、责任链上不出现 justoneapi、SKIP 标记日志；
- 每平台一次成功只打锁定的一枪：路径/入参名硬断言，禁止 share-url-transfer；
- 视频号：有 objectId 只打 download-url（不含 get-video-basic-info）；
  sph_code 先 basic-info 再 download（stream 层允许的两枪）；
- 业务码语义：code==0 成功；301/100/302/303/400/500/600/601/602 不计费且单次
  调用内不叠打；
- 日志与异常字符串不含 token；
- 媒体方法显式 decrypt=False 标记。

全部 mock httpx.AsyncClient（transport 边界），无真实网络。
"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from loguru import logger

import app.api.resolve as resolve_mod
from app.core.config import settings
from app.services.providers.base import ProviderError, VideoNotFoundError
from app.services.providers.justoneapi import JustOneApiProvider
from app.services.video_resolver import VideoResolver

FIXTURES = Path(__file__).parent / "fixtures" / "justoneapi"
TOKEN = "test-joa-token-abcdef"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture()
def capture_loguru():
    """loguru 不走 stdlib logging，caplog 抓不到；用自带 sink 收集。"""
    messages: list[str] = []
    sink_id = logger.add(messages.append, level="DEBUG")
    yield messages
    logger.remove(sink_id)


class HttpxStub:
    """替换 httpx.AsyncClient：按注册表回包并记录每次请求的 method/URL/params。"""

    def __init__(self, responses, calls):
        self._responses = responses
        self._calls = calls
        self._client = None

    def __call__(self, *, timeout=None, **kwargs):
        self._client = _StubClient(self._responses, self._calls)
        return self._client

    @property
    def calls(self):
        return self._calls


class _StubClient:
    def __init__(self, responses, calls):
        self._responses = responses
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, **kwargs):
        self._calls.append({"url": url, "params": dict(params or {})})
        for matcher, response in self._responses:
            if matcher(url, dict(params or {})):
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"unexpected request: {url} params={params}")


def _response(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", "https://api.example.test/stub"),
    )


@pytest.fixture()
def stub_http(monkeypatch):
    """安装 httpx.AsyncClient 桩，返回 (calls, install) 回调。"""
    calls: list[dict] = []

    def install(*responses):
        stub = HttpxStub(list(responses), calls)
        monkeypatch.setattr(httpx, "AsyncClient", stub)
        return stub

    return calls, install


# ----------------------------- 空 token（约束 1） -----------------------------

def test_empty_token_no_httpx_calls(stub_http):
    calls, install = stub_http
    install()
    provider = JustOneApiProvider()
    async def _run():
        for platform in provider.SUPPORTED_PLATFORMS:
            with pytest.raises(ProviderError):
                await provider.fetch_video_info(
                    platform=platform,
                    video_id="someid",
                    original_url="https://weixin.qq.com/sph/AOzokRxWHz",
                )
        with pytest.raises(ProviderError):
            await provider.fetch_wechat_channels_media(sph_code="AOzokRxWHz")

    asyncio.run(_run())
    assert calls == []  # 空 token：httpx 调用次数为 0


def test_empty_token_chains_exclude_justoneapi():
    """空 token 时该源不存在：默认链（含抖音/视频号）不含 justoneapi。"""
    resolver = VideoResolver()
    for platform in (
        "douyin", "kuaishou", "xiaohongshu", "wechat_channels",
        "tiktok", "instagram", "youtube", "twitter",
    ):
        assert "justoneapi" not in resolver.get_providers_for_platform(platform)


def test_empty_token_logs_skip_marker(capture_loguru):
    """失败可见性标记：空 token 时输出 JUSTONEAPI-SKIP-EMPTY-TOKEN（fail-open 初始化）。"""
    JustOneApiProvider()
    assert any("JUSTONEAPI-SKIP-EMPTY-TOKEN" in m for m in capture_loguru)


def test_priority_override_drops_justoneapi_when_token_empty(monkeypatch):
    """用户覆盖链含 justoneapi 但 token 为空：该源被忽略，其余 provider 保留。"""
    monkeypatch.setattr(settings, "PROVIDER_PRIORITY_DOUYIN", "justoneapi,tikhub")
    try:
        resolver = VideoResolver()
        assert resolver.get_providers_for_platform("douyin") == ["tikhub"]
    finally:
        monkeypatch.setattr(settings, "PROVIDER_PRIORITY_DOUYIN", "")


# ----------------------------- 端点单枪表（约束 3） -----------------------------

EXPECTED_ENDPOINTS = {
    "douyin": ("/api/douyin/get-video-detail/v2", "videoId", "7477059950577978636"),
    "kuaishou": ("/api/kuaishou/get-video-detail/v2", "videoId", "3xfdpkqyzabcdef"),
    "xiaohongshu": ("/api/xiaohongshu/get-note-detail/v6", "noteId", "65f2d1a3000000002201cb3b"),
    "tiktok": ("/api/tiktok/get-post-detail/v1", "postId", "7267439653153003782"),
    "youtube": ("/api/youtube/get-video-detail/v1", "videoId", "dQw4w9WgXcQ"),
    "instagram": ("/api/instagram/get-post-detail/v1", "code", "C0abcdefghIj"),
    "twitter": ("/api/twitter/get-post-detail/v1", "tweetId", "1700000000000000000"),
}


@pytest.mark.parametrize("platform", sorted(EXPECTED_ENDPOINTS))
def test_platform_single_shot_endpoint(justoneapi_token, stub_http, platform):
    """有平台 ID：只打锁定的一枪，路径/入参硬断言，禁止 share-url-transfer。"""
    calls, install = stub_http
    path, param, video_id = EXPECTED_ENDPOINTS[platform]
    install((lambda u, p: True, _response({"code": 0, "data": {"ok": True}})))

    provider = JustOneApiProvider()
    payload = asyncio.run(
        provider.fetch_video_info(
            platform=platform, video_id=video_id, original_url="https://upstream.test/x"
        )
    )
    assert payload["code"] == 0
    assert len(calls) == 1
    assert path in calls[0]["url"]
    assert "share-url-transfer" not in calls[0]["url"]
    assert calls[0]["params"].get(param) == video_id
    assert calls[0]["params"].get("token") == justoneapi_token


def test_missing_platform_id_skips_share_url_transfer(justoneapi_token, stub_http):
    """无平台 ID：不打 share-url-transfer（短链展开本仓已做），直接失败。"""
    calls, install = stub_http
    install()
    provider = JustOneApiProvider()
    with pytest.raises(VideoNotFoundError):
        asyncio.run(
            provider.fetch_video_info(
                platform="douyin", video_id="", original_url="https://v.douyin.com/x/"
            )
        )
    assert calls == []


# ----------------------------- 视频号 -----------------------------

SPH_CODE = "AOzokRxWHz"
OBJECT_ID = "14998022876670594427"


def _wechat_responses(basic_info=True, download=True):
    responses = []
    if basic_info:
        responses.append((
            lambda u, p: "get-video-basic-info" in u,
            _response(load("wechat_basic_info")),
        ))
    if download:
        responses.append((
            lambda u, p: "get-video-download-url" in u,
            _response(load("wechat_download_url")),
        ))
    return responses


def test_wechat_media_with_object_id_single_shot(justoneapi_token, stub_http):
    """已有 objectId：只打 download-url，不含 get-video-basic-info。"""
    calls, install = stub_http
    install(*_wechat_responses(basic_info=False))
    provider = JustOneApiProvider()
    media = asyncio.run(provider.fetch_wechat_channels_media(object_id=OBJECT_ID))
    assert [c["params"].get("objectId") for c in calls] == [OBJECT_ID]
    assert all("get-video-basic-info" not in c["url"] for c in calls)
    assert media["decrypt"] is False
    assert media["full_url"] == load("wechat_download_url")["data"]["download_url"]
    assert media["file_size"] == 456301568


def test_wechat_media_with_sph_code_two_shots(justoneapi_token, stub_http):
    """只有 sph_code：先 basic-info 拿 objectId 再 download（stream 层允许的两枪）。"""
    calls, install = stub_http
    install(*_wechat_responses())
    provider = JustOneApiProvider()
    media = asyncio.run(provider.fetch_wechat_channels_media(sph_code=SPH_CODE))
    assert len(calls) == 2
    assert "get-video-basic-info" in calls[0]["url"]
    assert calls[0]["params"]["shareUrl"] == f"https://weixin.qq.com/sph/{SPH_CODE}"
    assert "get-video-download-url" in calls[1]["url"]
    assert calls[1]["params"]["objectId"] == OBJECT_ID
    assert media["decrypt"] is False


def test_wechat_media_requires_object_id_from_basic_info(justoneapi_token, stub_http):
    """basic-info 不回 objectId：ProviderError，不打 download 一枪。"""
    calls, install = stub_http
    basic = load("wechat_basic_info")
    basic["data"].pop("objectId")
    install((lambda u, p: "get-video-basic-info" in u, _response(basic)))
    provider = JustOneApiProvider()
    with pytest.raises(ProviderError):
        asyncio.run(provider.fetch_wechat_channels_media(sph_code=SPH_CODE))
    assert all("get-video-download-url" not in c["url"] for c in calls)


def test_wechat_media_missing_download_url(justoneapi_token, stub_http):
    calls, install = stub_http
    download = load("wechat_download_url")
    download["data"].pop("download_url")
    install(
        (lambda u, p: "get-video-basic-info" in u, _response(load("wechat_basic_info"))),
        (lambda u, p: "get-video-download-url" in u, _response(download)),
    )
    provider = JustOneApiProvider()
    with pytest.raises(ProviderError):
        asyncio.run(provider.fetch_wechat_channels_media(sph_code=SPH_CODE))


def test_wechat_detail_uses_basic_info_only(justoneapi_token, stub_http):
    """resolve 级只要元数据一枪 basic-info（video_url 恒为本服务流端点，
    无需 download-url，不重复付费）。"""
    calls, install = stub_http
    install(*_wechat_responses(basic_info=True, download=False))
    provider = JustOneApiProvider()
    payload = asyncio.run(
        provider.fetch_video_info(
            platform="wechat_channels",
            video_id="",
            original_url=f"https://weixin.qq.com/sph/{SPH_CODE}",
        )
    )
    assert len(calls) == 1
    assert "get-video-basic-info" in calls[0]["url"]
    assert calls[0]["params"]["shareUrl"] == f"https://weixin.qq.com/sph/{SPH_CODE}"
    assert payload["data"]["objectId"] == OBJECT_ID


def test_wechat_object_id_only_never_hits_basic_info(justoneapi_token, stub_http):
    """只有 objectId（纯数字）、没有 sph 链接：不打 get-video-basic-info（约束 3）。"""
    calls, install = stub_http
    install(*_wechat_responses(basic_info=False))
    provider = JustOneApiProvider()
    with pytest.raises(VideoNotFoundError):
        asyncio.run(
            provider.fetch_video_info(
                platform="wechat_channels", video_id=OBJECT_ID, original_url=""
            )
        )
    assert calls == []


# ----------------------------- 业务码语义 -----------------------------


def _one_call_stub(stub_http, payload, status_code=200):
    calls, install = stub_http
    install((lambda u, p: True, _response(payload, status_code)))
    return calls


@pytest.mark.parametrize("code", [0])
def test_business_code_zero_success(justoneapi_token, stub_http, code):
    calls = _one_call_stub(stub_http, {"code": code, "data": {"ok": True}})
    provider = JustOneApiProvider()

    payload = asyncio.run(
        provider.fetch_video_info(platform="douyin", video_id="1", original_url="")
    )
    assert payload["data"] == {"ok": True}
    assert len(calls) == 1


@pytest.mark.parametrize(
    "code", [301, 100, 302, 303, 400, 500, 600, 601, 602, 999]
)
def test_business_codes_nonzero_fail_without_retry(justoneapi_token, stub_http, code):
    """非 0 业务码：不计费失败不得叠打（单次调用只打一枪）。999 为表外码，同样不重试。"""
    calls = _one_call_stub(stub_http, {"code": code, "message": "boom"})
    provider = JustOneApiProvider()

    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(
            provider.fetch_video_info(platform="douyin", video_id="1", original_url="")
        )
    assert f"code={code}" in str(exc_info.value)
    assert len(calls) == 1


def test_http_500_raises_provider_error(justoneapi_token, stub_http):
    calls = _one_call_stub(stub_http, {"error": "server"}, status_code=500)
    provider = JustOneApiProvider()

    with pytest.raises(ProviderError):
        asyncio.run(
            provider.fetch_video_info(platform="douyin", video_id="1", original_url="")
        )
    assert len(calls) == 1


def test_empty_data_raises(justoneapi_token, stub_http):
    calls = _one_call_stub(stub_http, {"code": 0, "data": {}})
    provider = JustOneApiProvider()

    with pytest.raises(ProviderError):
        asyncio.run(
            provider.fetch_video_info(platform="douyin", video_id="1", original_url="")
        )
    assert len(calls) == 1


# ----------------------------- token 不进日志/异常（约束 8） -----------------------------

def test_no_token_in_logs_or_errors(justoneapi_token, stub_http, capture_loguru):
    """网络异常：上抛的 ProviderError 只含异常类型名与 path（不含上游原始文本），
    日志禁止出现带 token 的完整 URL。"""
    calls, install = stub_http
    install(
        (lambda u, p: True, httpx.ConnectError("boom for https://api.example.test/api/douyin/get-video-detail/v2?token=" + TOKEN)),
    )
    provider = JustOneApiProvider()

    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(
            provider.fetch_video_info(platform="douyin", video_id="1", original_url="")
        )

    message = str(exc_info.value)
    assert TOKEN not in message
    assert "ConnectError" in message
    assert "boom" not in message  # 上游异常原文不上抛，只记类型名
    for log in capture_loguru:
        assert TOKEN not in log


def test_no_token_in_http_status_error(justoneapi_token, stub_http, capture_loguru):
    """非 200：错误信息只含状态码与 path，不含 query/token。"""
    calls, install = stub_http
    install((lambda u, p: True, _response({"bad": True}, status_code=403)))
    provider = JustOneApiProvider()

    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(
            provider.fetch_video_info(platform="douyin", video_id="1", original_url="")
        )

    assert TOKEN not in str(exc_info.value)
    assert "path=" in str(exc_info.value)
    for message in capture_loguru:
        assert TOKEN not in message
