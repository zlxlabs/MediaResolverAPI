"""
Tests for URL parser — platform identification and video ID extraction.
"""

import pytest
from app.services.url_parser import URLParser


@pytest.fixture
def parser():
    return URLParser()


# T1: Video ID extraction for each platform
class TestVideoIdExtraction:

    def test_douyin_long_url(self, parser):
        platform, vid = parser.parse_url(
            "https://www.douyin.com/video/7477059950577978636"
        )
        assert platform == "douyin"
        assert vid == "7477059950577978636"

    def test_tiktok_long_url(self, parser):
        platform, vid = parser.parse_url(
            "https://www.tiktok.com/@user/video/7546575629987253512"
        )
        assert platform == "tiktok"
        assert vid == "7546575629987253512"

    def test_kuaishou_url(self, parser):
        platform, vid = parser.parse_url(
            "https://www.kuaishou.com/short-video/3xgktqzhbkse2fe"
        )
        assert platform == "kuaishou"
        assert vid == "3xgktqzhbkse2fe"

    def test_youtube_watch_url(self, parser):
        platform, vid = parser.parse_url(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        assert platform == "youtube"
        assert vid == "dQw4w9WgXcQ"

    def test_youtube_short_url(self, parser):
        platform, vid = parser.parse_url("https://youtu.be/dQw4w9WgXcQ")
        assert platform == "youtube"
        assert vid == "dQw4w9WgXcQ"

    def test_youtube_shorts_url(self, parser):
        platform, vid = parser.parse_url(
            "https://www.youtube.com/shorts/abc123_-X"
        )
        assert platform == "youtube"
        assert vid == "abc123_-X"

    def test_xiaohongshu_explore(self, parser):
        platform, vid = parser.parse_url(
            "https://www.xiaohongshu.com/explore/68aa7790000000001b033641"
        )
        assert platform == "xiaohongshu"
        assert vid == "68aa7790000000001b033641"

    def test_instagram_reel(self, parser):
        platform, vid = parser.parse_url(
            "https://www.instagram.com/reel/DOtvKBZDCAO/"
        )
        assert platform == "instagram"
        assert vid == "DOtvKBZDCAO"

    def test_pinterest_pin(self, parser):
        platform, vid = parser.parse_url(
            "https://www.pinterest.com/pin/904731012654724079/"
        )
        assert platform == "pinterest"
        assert vid == "904731012654724079"

    def test_facebook_reel(self, parser):
        platform, vid = parser.parse_url(
            "https://www.facebook.com/reel/622490636870155"
        )
        assert platform == "facebook"
        assert vid == "622490636870155"

    def test_invalid_url_returns_none(self, parser):
        platform, vid = parser.parse_url("not-a-url")
        assert platform is None
        assert vid is None

    def test_unsupported_domain_returns_none(self, parser):
        platform, vid = parser.parse_url("https://example.com/video/123")
        assert platform is None
        assert vid is None


# T2: Short URL detection
class TestShortUrlDetection:

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://v.douyin.com/abc123/", True),
            ("https://vm.tiktok.com/ZMxyz/", True),
            ("https://youtu.be/abc", True),
            ("https://pin.it/abc", True),
            ("https://xhslink.com/abc", True),
            ("https://www.youtube.com/watch?v=abc", False),
            ("https://www.tiktok.com/@user/video/123", False),
        ],
    )
    def test_short_url_detection(self, parser, url, expected):
        assert parser.is_short_url(url) == expected
