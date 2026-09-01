"""
URL解析服务
识别不同平台的URL并提取视频ID
"""

import re
from typing import Optional, Tuple
from urllib.parse import urlparse, parse_qs
from loguru import logger

from ..utils.validators import is_valid_url, extract_domain


class URLParser:
    """
    URL解析器类
    支持多个短视频平台的URL解析
    """

    # 短链域名列表
    SHORT_URL_DOMAINS = {
        'v.douyin.com',      # 抖音短链
        'vm.tiktok.com',     # TikTok短链
        'xhslink.com',       # 小红书短链
        'youtu.be',          # YouTube短链
        'pin.it',            # Pinterest短链
        'fb.watch',          # Facebook短链
    }

    # 平台域名映射
    PLATFORM_DOMAINS = {
        # 抖音
        'douyin.com': 'douyin',
        'v.douyin.com': 'douyin',

        # TikTok
        'tiktok.com': 'tiktok',
        'vm.tiktok.com': 'tiktok',

        # 快手
        'kuaishou.com': 'kuaishou',

        # YouTube
        'youtube.com': 'youtube',
        'youtu.be': 'youtube',
        'm.youtube.com': 'youtube',

        # 小红书
        'xiaohongshu.com': 'xiaohongshu',
        'xhslink.com': 'xiaohongshu',

        # Instagram
        'instagram.com': 'instagram',

        # Pinterest
        'pinterest.com': 'pinterest',
        'pin.it': 'pinterest',

        # Facebook
        'facebook.com': 'facebook',
        'fb.watch': 'facebook',
    }

    def parse_url(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        """
        解析URL并识别平台和视频ID

        Args:
            url: 原始URL

        Returns:
            Tuple[Optional[str], Optional[str]]: (平台名称, 视频ID)
        """
        if not is_valid_url(url):
            logger.warning(f"无效的URL: {url}")
            return None, None

        domain = extract_domain(url)
        if not domain:
            logger.warning(f"无法提取域名: {url}")
            return None, None

        # 识别平台（视频号按路径 /sph/ 判，不能只按 weixin.qq.com 域名）
        platform = self.identify_platform(url)
        if not platform:
            logger.warning(f"不支持的平台域名: {domain}")
            return None, None

        # 提取视频ID
        video_id = self._extract_video_id(url, platform)
        if not video_id:
            # 视频号分享短链里的 sph 码不是 object_id，解析阶段拿不到真实 id 是预期的；
            # 返回平台名让路由层走 URL_FALLBACK_PLATFORMS 用原始 url 兜底。
            if platform == "wechat_channels":
                logger.info(f"视频号分享链无 object_id，走 url 兜底: {url}")
                return platform, None
            logger.warning(f"无法提取视频ID: {url}")
            return None, None

        logger.info(f"成功解析URL - 平台: {platform}, 视频ID: {video_id}")
        return platform, video_id

    def identify_platform(self, url: str) -> Optional[str]:
        """
        识别平台（不要求能提取出视频 ID）。

        现有 8 个平台仍按域名匹配；视频号必须叠加路径前缀 /sph/，
        因为 weixin.qq.com 同时承载公众号等其他业务。

        Args:
            url: 原始 URL

        Returns:
            Optional[str]: 平台名称，无法识别返回 None
        """
        if self._is_wechat_channels_url(url):
            return "wechat_channels"
        domain = extract_domain(url)
        return self._identify_platform(domain) if domain else None

    @staticmethod
    def _is_wechat_channels_url(url: str) -> bool:
        """weixin.qq.com（含子域）且路径前缀为 /sph/ 才是视频号。"""
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host != "weixin.qq.com" and not host.endswith(".weixin.qq.com"):
            return False
        path = parsed.path or ""
        return path == "/sph" or path.startswith("/sph/")

    def is_short_url(self, url: str) -> bool:
        """
        判断是否为短链接

        Args:
            url: URL地址

        Returns:
            bool: 是否为短链接
        """
        domain = extract_domain(url)
        return domain in self.SHORT_URL_DOMAINS if domain else False

    def _identify_platform(self, domain: str) -> Optional[str]:
        """
        根据域名识别平台

        Args:
            domain: 域名

        Returns:
            Optional[str]: 平台名称
        """
        # 直接匹配
        if domain in self.PLATFORM_DOMAINS:
            return self.PLATFORM_DOMAINS[domain]

        # 子域名匹配
        for platform_domain, platform in self.PLATFORM_DOMAINS.items():
            if domain.endswith(f".{platform_domain}"):
                return platform

        return None

    def _extract_video_id(self, url: str, platform: str) -> Optional[str]:
        """
        根据平台提取视频ID

        Args:
            url: 完整URL
            platform: 平台名称

        Returns:
            Optional[str]: 视频ID
        """
        extractors = {
            'douyin': self._extract_douyin_id,
            'tiktok': self._extract_tiktok_id,
            'kuaishou': self._extract_kuaishou_id,
            'youtube': self._extract_youtube_id,
            'xiaohongshu': self._extract_xiaohongshu_id,
            'instagram': self._extract_instagram_id,
            'pinterest': self._extract_pinterest_id,
            'facebook': self._extract_facebook_id,
        }

        extractor = extractors.get(platform)
        if extractor:
            return extractor(url)

        return None

    def _extract_douyin_id(self, url: str) -> Optional[str]:
        """提取抖音视频ID"""
        # 长链接格式: https://www.douyin.com/video/7477059950577978636
        if '/video/' in url:
            match = re.search(r'/video/(\d+)', url)
            if match:
                return match.group(1)

        # 短链接需要先展开，这里提供基础解析
        # v.douyin.com链接需要通过HTTP请求解析跳转链接
        return None

    def _extract_tiktok_id(self, url: str) -> Optional[str]:
        """提取TikTok视频ID"""
        # 长链接格式: https://www.tiktok.com/@username/video/7546575629987253512
        if '/video/' in url:
            match = re.search(r'/video/(\d+)', url)
            if match:
                return match.group(1)

        # 短链接vm.tiktok.com需要先展开
        return None

    def _extract_kuaishou_id(self, url: str) -> Optional[str]:
        """提取快手视频ID"""
        # 格式: https://www.kuaishou.com/short-video/3xgktqzhbkse2fe
        if '/short-video/' in url:
            match = re.search(r'/short-video/([a-zA-Z0-9]+)', url)
            if match:
                return match.group(1)

        return None

    def _extract_youtube_id(self, url: str) -> Optional[str]:
        """提取YouTube视频ID"""
        parsed = urlparse(url)

        # youtu.be短链接
        if parsed.netloc in ['youtu.be']:
            video_id = parsed.path.lstrip('/')
            if video_id:
                return video_id

        # youtube.com长链接
        if parsed.netloc in ['youtube.com', 'www.youtube.com', 'm.youtube.com']:
            # watch?v=参数格式
            if 'v=' in parsed.query:
                query_params = parse_qs(parsed.query)
                video_id = query_params.get('v', [None])[0]
                if video_id:
                    return video_id

            # YouTube Shorts格式: /shorts/video_id
            if '/shorts/' in parsed.path:
                match = re.search(r'/shorts/([a-zA-Z0-9_-]+)', parsed.path)
                if match:
                    return match.group(1)

        return None

    def _extract_xiaohongshu_id(self, url: str) -> Optional[str]:
        """提取小红书笔记ID"""
        # 格式1: https://www.xiaohongshu.com/explore/68aa7790000000001b033641
        if '/explore/' in url:
            match = re.search(r'/explore/([a-fA-F0-9]+)', url)
            if match:
                return match.group(1)

        # 格式2: https://www.xiaohongshu.com/discovery/item/68d24ca9000000001400bb68
        if '/discovery/item/' in url:
            match = re.search(r'/discovery/item/([a-fA-F0-9]+)', url)
            if match:
                return match.group(1)

        return None

    def _extract_instagram_id(self, url: str) -> Optional[str]:
        """提取Instagram帖子ID"""
        # 格式: https://www.instagram.com/reel/DOtvKBZDCAO/
        # 或: https://www.instagram.com/reels/DOtvKBZDCAO/
        # 或: https://www.instagram.com/p/DOtvKBZDCAO/
        match = re.search(r'/(?:reels?|p|tv)/([a-zA-Z0-9_-]+)', url)
        if match:
            return match.group(1)

        return None

    def _extract_pinterest_id(self, url: str) -> Optional[str]:
        """提取Pinterest Pin ID"""
        # 标准格式: https://www.pinterest.com/pin/123456789/
        # 或: https://ca.pinterest.com/pin/904731012654724079/
        if '/pin/' in url:
            match = re.search(r'/pin/(\d+)', url)
            if match:
                return match.group(1)

        # 短链接格式: https://pin.it/abc123
        # 短链接需要先展开
        if 'pin.it' in url:
            parsed = urlparse(url)
            shortcode = parsed.path.lstrip('/')
            if shortcode:
                return shortcode  # 返回短链接代码，后续需要展开

        return None

    def _extract_facebook_id(self, url: str) -> Optional[str]:
        """
        提取Facebook视频ID

        支持的 URL 格式：
        - https://www.facebook.com/reel/622490636870155
        - https://www.facebook.com/watch?v=622490636870155
        - https://www.facebook.com/username/videos/622490636870155
        - https://fb.watch/xxx (短链)
        - https://www.facebook.com/share/v/xxx/ (分享短链)
        - https://www.facebook.com/share/r/xxx/ (Reel分享短链)
        """
        # Reel 格式
        if '/reel/' in url:
            match = re.search(r'/reel/(\d+)', url)
            if match:
                return match.group(1)

        # Watch 格式 (?v=参数)
        if 'watch' in url and 'v=' in url:
            parsed = urlparse(url)
            query_params = parse_qs(parsed.query)
            video_id = query_params.get('v', [None])[0]
            if video_id:
                return video_id

        # Videos 格式
        if '/videos/' in url:
            match = re.search(r'/videos/(\d+)', url)
            if match:
                return match.group(1)

        # 分享短链格式: /share/v/xxx 或 /share/r/xxx
        if '/share/' in url:
            match = re.search(r'/share/[vr]/([a-zA-Z0-9_-]+)', url)
            if match:
                return match.group(1)

        # fb.watch 短链接格式（需要展开）
        if 'fb.watch' in url:
            parsed = urlparse(url)
            shortcode = parsed.path.lstrip('/')
            if shortcode:
                return shortcode

        return None

    async def resolve_short_url(self, url: str) -> Optional[str]:
        """
        解析短链接为长链接
        用于处理v.douyin.com、vm.tiktok.com等短链接

        Args:
            url: 短链接

        Returns:
            Optional[str]: 解析后的长链接
        """
        try:
            import httpx

            # 创建临时的httpx客户端来解析短链
            async with httpx.AsyncClient(
                timeout=30,
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            ) as client:
                response = await client.get(url)
                final_url = str(response.url)
                logger.info(f"短链接解析: {url} -> {final_url}")
                return final_url

        except Exception as e:
            logger.error(f"短链接解析失败: {url}, 错误: {e}")
            return None


# 全局URL解析器实例
url_parser = URLParser()