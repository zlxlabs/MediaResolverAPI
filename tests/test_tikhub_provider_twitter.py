"""TikHub X/Twitter adapter, chain, and API wiring tests."""

import json
from pathlib import Path

import pytest

from app.services.platforms.twitter import TwitterService
from app.services.providers.base import ProviderError, TerminalError, VideoNotFoundError
from app.services.providers.tikhub import TikHubProvider
from app.services.video_resolver import VideoResolver


FIXTURES = Path(__file__).parent / "fixtures" / "twitter"
TWEET_ID = "2098782845183410287"
TWEET_URL = f"https://x.com/0xCodez/status/{TWEET_ID}?s=20"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def test_parser_picks_highest_mp4():
    info = TwitterService("k", "b")._parse_response(load("tweet_video"))
    assert info is not None
    assert info.video_url.endswith("twitter_1080.mp4")
    assert info.video_url.endswith(".mp4")
    assert info.quality == "1080p"
    assert info.width == 1920
    assert info.height == 1080


def test_parser_picks_highest_mp4_across_videos():
    info = TwitterService("k", "b")._parse_response(load("tweet_multi_video"))
    assert info is not None
    assert info.video_url.endswith("multi_high.mp4")
    assert info.quality == "1080p"


def test_parser_skips_hls_only_first_video():
    payload = load("tweet_multi_video")
    payload["data"]["media"]["video"][0]["variants"] = [{
        "content_type": "application/x-mpegURL",
        "url": "https://video.twimg.com/amplify_video/fake/multi_first.m3u8",
    }]
    info = TwitterService("k", "b")._parse_response(payload)
    assert info is not None
    assert info.video_url.endswith("multi_high.mp4")


def test_parser_duration_is_seconds():
    info = TwitterService("k", "b")._parse_response(load("tweet_video"))
    assert info is not None
    assert info.duration == 1595


def test_parser_maps_twitter_metadata():
    info = TwitterService("k", "b")._parse_response(load("tweet_video"))
    assert info is not None
    assert info.platform == "twitter"
    assert info.video_id == TWEET_ID
    assert info.title == "workshop about agent swarms"
    assert info.description == "workshop about agent swarms"
    assert info.view_count == 504166
    assert info.like_count == 1847
    assert info.comment_count == 63
    assert info.share_count == 299
    assert info.collect_count == 4450
    assert info.author_name == "Codez"
    assert info.author_id == "0xCodez"
    assert info.create_time is not None


@pytest.mark.parametrize("fixture", ["empty", "tweet_photo", "tweet_hls_only", "tweet_quoted_only"])
def test_parser_rejects_non_video_posts(fixture):
    assert TwitterService("k", "b")._parse_response(load(fixture)) is None


def test_parser_uses_entities_video_when_primary_media_is_missing():
    payload = load("tweet_video")
    payload["data"].pop("media")
    payload["data"]["entities"] = {
        "media": [{
            "type": "video",
            "video_info": payload["data"]["quoted"]["media"]["video"][0],
        }],
    }
    info = TwitterService("k", "b")._parse_response(payload)
    assert info is not None
    assert info.video_url.endswith("quoted_must_not_win.mp4")


@pytest.mark.parametrize("fixture", ["empty", "tweet_photo", "tweet_video"])
def test_classify_never_terminal(fixture):
    assert TikHubProvider._classify_twitter(load(fixture)) != "terminal"


def test_has_playable_uses_twitter_parser(monkeypatch):
    calls = []
    original = TwitterService._parse_response

    def wrapped(self, data):
        calls.append(data)
        return original(self, data)

    monkeypatch.setattr(TwitterService, "_parse_response", wrapped)
    provider = TikHubProvider()
    assert provider._twitter_has_playable(load("tweet_video")) is True
    assert provider._twitter_has_playable(load("tweet_photo")) is False
    assert len(calls) == 2


def _provider_with(monkeypatch, response):
    provider = TikHubProvider()
    calls = []

    async def fake_call(self, name, path, params, per_timeout):
        calls.append({"name": name, "path": path, "params": params})
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(TikHubProvider, "_call_endpoint", fake_call)
    return provider, calls


async def test_twitter_chain_hits_single_endpoint(monkeypatch):
    provider, calls = _provider_with(monkeypatch, load("tweet_video"))
    data = await provider.fetch_video_info("twitter", TWEET_ID, TWEET_URL)
    assert data == load("tweet_video")
    assert calls == [{
        "name": "web_detail",
        "path": "/api/v1/twitter/web/fetch_tweet_detail",
        "params": {"tweet_id": TWEET_ID},
    }]


@pytest.mark.parametrize("fixture", ["empty", "tweet_photo", "tweet_hls_only", "tweet_quoted_only"])
async def test_twitter_chain_failures_are_not_terminal(monkeypatch, fixture):
    provider, calls = _provider_with(monkeypatch, load(fixture))
    with pytest.raises(VideoNotFoundError) as exc_info:
        await provider.fetch_video_info("twitter", TWEET_ID, TWEET_URL)
    assert not isinstance(exc_info.value, TerminalError)
    assert len(calls) == 1


async def test_twitter_param_construction(monkeypatch):
    provider, calls = _provider_with(monkeypatch, load("empty"))
    with pytest.raises(VideoNotFoundError):
        await provider.fetch_video_info("twitter", TWEET_ID, TWEET_URL)
    assert calls[0]["params"] == {"tweet_id": TWEET_ID}
    assert calls[0]["path"] == "/api/v1/twitter/web/fetch_tweet_detail"


async def test_twitter_http_error_is_not_terminal(monkeypatch):
    provider, calls = _provider_with(monkeypatch, ProviderError("boom"))
    with pytest.raises(VideoNotFoundError) as exc_info:
        await provider.fetch_video_info("twitter", TWEET_ID, TWEET_URL)
    assert not isinstance(exc_info.value, TerminalError)
    assert len(calls) == 1


async def test_resolver_falls_back_to_cobalt(monkeypatch):
    resolver = VideoResolver()

    async def tikhub_failure(*args, **kwargs):
        raise VideoNotFoundError("no playable tweet")

    async def cobalt_success(*args, **kwargs):
        return {
            "status": "redirect",
            "url": "https://cdn.example/twitter_fallback.mp4",
            "filename": "twitter_fallback.mp4",
        }

    monkeypatch.setattr(resolver.tikhub_provider, "fetch_video_info", tikhub_failure)
    monkeypatch.setattr(resolver.cobalt_provider, "fetch_video_info", cobalt_success)
    info, provider = await resolver.resolve("twitter", TWEET_ID, TWEET_URL)

    assert provider == "cobalt"
    assert info.platform == "twitter"
    assert info.video_url.endswith("twitter_fallback.mp4")


def test_resolve_and_platforms_are_wired(authed_client, monkeypatch):
    import app.services.providers.tikhub as tikhub_module

    async def fake_call(self, name, path, params, per_timeout):
        assert path == "/api/v1/twitter/web/fetch_tweet_detail"
        assert params == {"tweet_id": TWEET_ID}
        return load("tweet_video")

    monkeypatch.setattr(tikhub_module.TikHubProvider, "_call_endpoint", fake_call)

    platforms = authed_client.get("/api/platforms")
    assert platforms.status_code == 200
    assert platforms.json()["platforms"]["twitter"] == ["tikhub", "cobalt"]

    response = authed_client.post(
        "/api/resolve",
        json={"url": TWEET_URL, "translate": False},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["platform"] == "twitter"
    assert payload["data"]["video_id"] == TWEET_ID
    assert payload["data"]["video_url"].endswith("twitter_1080.mp4")
    assert payload["data"]["provider"] == "tikhub"


def test_parser_malformed_bitrate_falls_soft_and_prefers_valid():
    payload = load("tweet_video")
    payload["data"]["media"]["video"][0]["variants"] = [{
        "content_type": "video/mp4",
        "url": "https://video.twimg.com/fake_unknown_bitrate.mp4",
        "bitrate": "unknown",
    }]
    info = TwitterService("k", "b")._parse_response(payload)
    assert info is not None
    assert info.video_url == "https://video.twimg.com/fake_unknown_bitrate.mp4"

    payload["data"]["media"]["video"][0]["variants"].append({
        "content_type": "video/mp4",
        "url": "https://video.twimg.com/fake_valid_bitrate.mp4",
        "bitrate": 832000,
    })
    info = TwitterService("k", "b")._parse_response(payload)
    assert info is not None
    assert info.video_url == "https://video.twimg.com/fake_valid_bitrate.mp4"
    assert TwitterService._variant_bitrate({"bitrate": "unknown"}) == 0


def test_parser_malformed_duration_falls_soft():
    payload = load("tweet_video")
    payload["data"]["media"]["video"][0]["duration"] = "unknown"
    payload["data"]["media"]["video"][0].pop("duration_millis", None)
    info = TwitterService("k", "b")._parse_response(payload)
    assert info is not None
    assert info.duration is None
    assert info.video_url.endswith("twitter_1080.mp4")


def test_parser_malformed_created_at_falls_soft():
    payload = load("tweet_video")
    payload["data"]["created_at"] = "unknown"
    info = TwitterService("k", "b")._parse_response(payload)
    assert info is not None
    assert info.create_time is None
    assert info.publish_time == "unknown"
    assert info.video_url.endswith("twitter_1080.mp4")


def test_parser_malformed_metadata_interop_with_tikhub_provider():
    payload = load("tweet_video")
    payload["data"]["created_at"] = "unknown"
    payload["data"]["media"]["video"][0]["duration"] = "unknown"
    payload["data"]["media"]["video"][0].pop("duration_millis", None)
    payload["data"]["media"]["video"][0]["variants"] = [{
        "content_type": "video/mp4",
        "url": "https://video.twimg.com/only_variant.mp4",
        "bitrate": "unknown",
    }]
    provider = TikHubProvider()
    assert provider._twitter_has_playable(payload) is True

    info = TwitterService("k", "b")._parse_response(payload)
    assert info is not None
    assert info.video_url == "https://video.twimg.com/only_variant.mp4"
    assert info.duration is None
    assert info.create_time is None
