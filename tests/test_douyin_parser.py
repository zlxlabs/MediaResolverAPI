"""
抖音解析器测试

验证 DouyinService._parse_response 的 schema 自适应：
- web v1 / web v2 / app v3 的根是 data.aweme_detail
- hybrid 的根是 data 本身
一个解析器需通吃这三种结构（codex #11 schema-detecting parsing）。
"""

import json
from pathlib import Path

import pytest

from app.services.platforms.douyin import DouyinService

FIXTURES = Path(__file__).parent / "fixtures" / "douyin"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture()
def service():
    return DouyinService(api_key="test", api_base="https://api.tikhub.io")


# 三种真实响应结构都应解析出无水印直链
@pytest.mark.parametrize("fixture", ["web_v1", "web_v2", "app_v3", "hybrid"])
def test_parse_real_response_yields_video_url(service, fixture):
    info = service._parse_response(load(fixture))
    assert info is not None, f"{fixture} 应能解析"
    assert info.platform == "douyin"
    assert info.video_id == "7592102779420115355"
    assert info.video_url.startswith("http"), f"{fixture} 应有无水印直链"
    assert info.width > 0 and info.height > 0
    assert info.like_count == 120  # digg_count，跨端点一致


def test_parse_hybrid_root_is_data_itself(service):
    """hybrid 的 detail 直接铺在 data 上（无 aweme_detail 包裹），仍应解析成功。"""
    raw = load("hybrid")
    assert "aweme_detail" not in raw["data"]  # 确认结构假设
    assert "aweme_id" in raw["data"]
    info = service._parse_response(raw)
    assert info is not None and info.video_url.startswith("http")


@pytest.mark.parametrize("fixture", ["private_reason5", "partial_reason10", "empty"])
def test_parse_unusable_response_returns_none(service, fixture):
    """无 detail（私密/部分可见/空）应返回 None，由上层判定降级/终态。"""
    assert service._parse_response(load(fixture)) is None
