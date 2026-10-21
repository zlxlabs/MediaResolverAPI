"""
Tests for URL parser — platform identification and video ID extraction.
"""

import pytest
from app.services.url_parser import URLParser


@pytest.fixture
def parser():
    return URLParser()


# T1: Video ID extraction for each platform
PLATFORM_URLS_WITHOUT_ID = {
    "douyin": "https://www.douyin.com/user/MS4wLjABAAAAxxxx",
    "tiktok": "https://www.tiktok.com/@user",
    "kuaishou": "https://www.kuaishou.com/f/X9aBcDeF",
    "youtube": "https://www.youtube.com/channel/UCabc",
    "xiaohongshu": "https://www.xiaohongshu.com/user/profile/abc",
    "instagram": "https://www.instagram.com/share/BAcDeFgHi",
    "pinterest": "https://www.pinterest.com/user/name/",
    "facebook": "https://www.facebook.com/profile.php?id=123",
    "twitter": "https://x.com/0xCodez",
}


class TestVideoIdExtraction:

    @pytest.mark.parametrize("platform,url", PLATFORM_URLS_WITHOUT_ID.items())
    def test_known_platform_without_video_id_preserves_platform(self, parser, platform, url):
        assert parser.parse_url(url) == (platform, None)

    def test_every_domain_platform_has_an_idless_contract_case(self):
        assert set(URLParser.PLATFORM_DOMAINS.values()) == set(PLATFORM_URLS_WITHOUT_ID)

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

    @pytest.mark.parametrize(
        "url",
        [
            "https://x.com/0xCodez/status/2098782845183410287?s=20",
            "https://twitter.com/0xCodez/status/2098782845183410287",
            "https://mobile.twitter.com/0xCodez/status/2098782845183410287",
            "https://x.com/i/status/2098782845183410287",
        ],
    )
    def test_twitter_status_url(self, parser, url):
        assert parser.parse_url(url) == ("twitter", "2098782845183410287")

    def test_twitter_profile_url_is_platform_without_video_id(self, parser):
        assert parser.parse_url("https://x.com/0xCodez") == ("twitter", None)

    def test_twitter_status_id_not_taken_from_query(self, parser):
        assert parser.parse_url("https://x.com/home?next=/someone/status/2098782845183410287") == ("twitter", None)
        assert parser.parse_url("https://x.com/home#status/2098782845183410287") == ("twitter", None)


# T2: Short URL detection
class TestShortUrlDetection:

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://v.douyin.com/abc123/", True),
            ("https://vm.tiktok.com/ZMxyz/", True),
            ("https://vt.tiktok.com/ZSVc3fkd2/", True),
            ("https://youtu.be/abc", True),
            ("https://pin.it/abc", True),
            ("https://xhslink.com/abc", True),
            ("https://xhslink.cn/o/8Gx4uVk2CR1", True),
            ("https://t.co/abc", True),
            ("https://www.youtube.com/watch?v=abc", False),
            ("https://www.tiktok.com/@user/video/123", False),
        ],
    )
    def test_short_url_detection(self, parser, url, expected):
        assert parser.is_short_url(url) == expected

    def test_wechat_channels_sph_is_not_short_url(self, parser):
        """视频号分享链由 TikHub 直接吃 share_url，不得进 SHORT_URL_DOMAINS。"""
        assert parser.is_short_url("https://weixin.qq.com/sph/AOzokRxWHz") is False

    @pytest.mark.parametrize(
        "url,expected_platform",
        [
            ("https://xhslink.cn/o/8Gx4uVk2CR1", "xiaohongshu"),
            ("https://vt.tiktok.com/ZSVc3fkd2/", "tiktok"),
        ],
    )
    def test_new_short_domains_identify_platform(self, parser, url, expected_platform):
        assert parser.is_short_url(url) is True
        assert parser.identify_platform(url) == expected_platform


# 视频号：按路径识别，禁止把公众号域名误判进来
class TestWechatChannelsIdentification:

    def test_sph_share_url_returns_platform_without_video_id(self, parser):
        platform, vid = parser.parse_url("https://weixin.qq.com/sph/AOzokRxWHz")
        assert platform == "wechat_channels"
        assert vid is None

    def test_sph_share_url_www_subdomain(self, parser):
        platform, vid = parser.parse_url("https://www.weixin.qq.com/sph/AOzokRxWHz")
        assert platform == "wechat_channels"
        assert vid is None

    def test_identify_platform_sph(self, parser):
        assert parser.identify_platform("https://weixin.qq.com/sph/AOzokRxWHz") == "wechat_channels"

    def test_mp_weixin_is_not_wechat_channels(self, parser):
        """公众号文章域名不得被识别成视频号。"""
        url = "https://mp.weixin.qq.com/s/xxxx"
        assert parser.identify_platform(url) is None
        platform, vid = parser.parse_url(url)
        assert platform is None
        assert vid is None

    def test_weixin_qq_without_sph_path_is_not_wechat_channels(self, parser):
        url = "https://weixin.qq.com/s/xxxx"
        assert parser.identify_platform(url) is None
        platform, vid = parser.parse_url(url)
        assert platform is None
        assert vid is None


# X Space：/i/spaces/<13位字母数字> 路径形态，与推文 /status/<数字> 双向互斥（design 不变式 6）
class TestTwitterSpaceIdentification:

    SPACE_ID = "1AKEmvYqbrOKL"

    @pytest.mark.parametrize(
        "url",
        [
            "https://x.com/i/spaces/1AKEmvYqbrOKL",
            "https://www.x.com/i/spaces/1AKEmvYqbrOKL",
            "https://twitter.com/i/spaces/1AKEmvYqbrOKL",
            "https://mobile.twitter.com/i/spaces/1AKEmvYqbrOKL",
        ],
    )
    def test_space_url_extracts_13_char_id(self, parser, url):
        assert parser.parse_url(url) == ("twitter_space", self.SPACE_ID)

    @pytest.mark.parametrize(
        "url",
        [
            "https://x.com/i/spaces/1AKEmvYqbrOKL/peek",
            "https://x.com/i/spaces/1AKEmvYqbrOKL/",
            "https://x.com/i/spaces/1AKEmvYqbrOKL?s=20",
        ],
    )
    def test_space_url_tail_and_query_are_ignored(self, parser, url):
        assert parser.parse_url(url) == ("twitter_space", self.SPACE_ID)

    def test_space_url_is_not_parsed_as_tweet(self, parser):
        """Space URL 不得落进 /status/ 提取器变成 twitter 平台。"""
        platform, vid = parser.parse_url("https://x.com/i/spaces/1AKEmvYqbrOKL")
        assert platform == "twitter_space"
        assert platform != "twitter"

    def test_tweet_url_is_not_parsed_as_space(self, parser):
        """推文 URL 不得被 Space 提取器吃掉（双向互斥的另一侧）。"""
        assert parser.parse_url("https://x.com/0xCodez/status/2098782845183410287") == (
            "twitter",
            "2098782845183410287",
        )

    @pytest.mark.parametrize(
        "space_id",
        ["1AKEmvYqbrOK", "1AKEmvYqbrOKLL", "1AKEmvYqbrOK_"],
    )
    def test_space_id_wrong_length_keeps_known_domain(self, parser, space_id):
        """非 13 位 ID 不得落进 status 提取器，但 X 域名仍已识别。"""
        assert parser.parse_url(f"https://x.com/i/spaces/{space_id}") == ("twitter", None)

    def test_space_url_without_id_keeps_known_domain(self, parser):
        assert parser.parse_url("https://x.com/i/spaces/") == ("twitter", None)

    def test_space_path_on_other_domain_is_rejected(self, parser):
        """/i/spaces/ 路径形态只在 x.com/twitter.com（含子域）生效。"""
        url = "https://evil.example.com/i/spaces/1AKEmvYqbrOKL"
        assert parser.parse_url(url) == (None, None)
        assert parser.identify_platform(url) is None

    def test_space_host_lookalike_is_rejected(self, parser):
        """evil-x.com 这类形近域名不得靠后缀匹配混进来。"""
        url = "https://evil-x.com/i/spaces/1AKEmvYqbrOKL"
        assert parser.parse_url(url) == (None, None)
