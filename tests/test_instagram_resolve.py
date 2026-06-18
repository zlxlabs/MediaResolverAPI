"""
Instagram 解析修复测试

背景：TikHub 下线了旧端点 /api/v1/instagram/web_app/fetch_post_media_by_url（404），
provider 仍指着它 → Instagram 解析全挂。改用 v2 /fetch_post_info（code_or_url）。
现有 InstagramService._parse_v2_response 已匹配 v2 结构，无需改解析器。
"""

import json
from pathlib import Path

import pytest

from app.services.platforms.instagram import InstagramService
from app.services.providers.tikhub import TikHubProvider

FIXTURES = Path(__file__).parent / "fixtures" / "instagram"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture()
def service():
    return InstagramService(api_key="test", api_base="https://api.tikhub.io")


def test_parse_v2_real_response(service):
    """现有解析器应能解析真实 v2 响应，产出无水印直链与元数据。"""
    info = service._parse_response(load("v2_post_info"))
    assert info is not None
    assert info.platform == "instagram"
    assert info.video_url.startswith("http")
    assert info.video_id == "3782201593618504233"
    assert info.width == 1080 and info.height == 1920
    assert info.author_name  # 非空
    assert info.like_count == 972


def test_instagram_chain_uses_v2_not_dead_endpoint():
    """
    回归防护：instagram 多级链首端必须是 v2/fetch_post_info（code_or_url），
    绝不能再指向已下线的 web_app 端点。原 _validate_response/PLATFORM_ENDPOINTS
    校验已随通用 GET 路径删除，有效性现由 classify + has_playable 判定（评审 Issue 3）。
    """
    chain = TikHubProvider.INSTAGRAM_CHAIN
    names = {n for n, _p, _param in chain}
    paths = {p for _n, p, _param in chain}
    assert ("v2", "/api/v1/instagram/v2/fetch_post_info", "code_or_url") in chain
    assert not any("web_app/fetch_post_media_by_url" in p for p in paths)
    assert "v1_by_url" in names  # 多级降级：v2 之外还有 v1 兜底
