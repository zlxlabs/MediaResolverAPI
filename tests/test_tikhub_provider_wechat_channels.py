"""
TikHub 微信视频号解析与单端点链测试。

覆盖：extract_data / _parse_response 字段映射、classify 两态、has_playable 与
_parse_response 同源、build_params 逐字段硬断言（含 raw: false）、空响应/缺 media
视为 retryable、全失败抛型、总预算超时。全部 mock _call_endpoint，无真实网络。
"""

import asyncio
import json
from pathlib import Path

import pytest

from app.api.resolve import _build_response
from app.core.config import settings
from app.services.platforms.wechat_channels import WechatChannelsService
from app.services.providers.base import ProviderError, TerminalError, VideoNotFoundError
from app.services.providers.tikhub import TikHubProvider

FIXTURES = Path(__file__).parent / "fixtures" / "wechat_channels"

OBJECT_ID = "14998022876670594427"
SHARE_URL = "https://weixin.qq.com/sph/AOzokRxWHz"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# ----------------------------- 解析器 -----------------------------

def test_extract_data_ok_for_video():
    node = WechatChannelsService.extract_data(load("detail"))
    assert isinstance(node, dict)
    assert node["id"] == OBJECT_ID
    assert node["object_type"] == 0


def test_extract_data_none_for_empty_and_image():
    assert WechatChannelsService.extract_data(load("empty")) is None
    assert WechatChannelsService.extract_data(load("image_note")) is None
    assert WechatChannelsService.extract_data({}) is None
    assert WechatChannelsService.extract_data({"data": None}) is None


def test_parse_response_maps_sample_fields():
    info = WechatChannelsService("k", "b")._parse_response(load("detail"))
    assert info is not None
    assert info.platform == "wechat_channels"
    assert info.video_id == OBJECT_ID
    assert isinstance(info.video_id, str)
    assert info.title == "对谈张笑宇：AI重塑组织与生活方式"
    assert info.description == "对谈张笑宇：AI重塑组织与生活方式"
    assert info.author_name == "晓辉博士"
    assert info.author_id.startswith("v2_060000231003b20f")
    assert isinstance(info.author_id, str)
    assert info.like_count == 42
    assert info.collect_count == 61
    assert info.share_count == 171
    assert info.comment_count == 8
    assert info.view_count is None
    assert info.duration == 7352
    assert info.width == 912
    assert info.height == 1920
    assert info.create_time is not None
    assert int(info.create_time.timestamp()) == 1787903651
    expected_url = (
        f"{settings.PUBLIC_BASE_URL.rstrip('/')}/api/stream/wechat_channels/{OBJECT_ID}"
    )
    assert info.video_url == expected_url
    # 同一 object_id 两次解析必须得到完全相同的 video_url（缓存安全）
    again = WechatChannelsService("k", "b")._parse_response(load("detail"))
    assert again is not None and again.video_url == info.video_url


def test_parse_response_redacts_credentials_in_raw_data():
    info = WechatChannelsService("k", "b")._parse_response(load("detail"))
    assert info is not None and info.raw_data is not None
    media = info.raw_data["data"]["media"]
    assert media["url"] == "REDACTED"
    assert media["url_token"] == "REDACTED"
    assert media["full_url"] == "REDACTED"
    assert media["decode_key"] == "REDACTED"
    dumped = json.dumps(info.to_dict())
    assert "decode_key" in dumped  # 键还在，值必须是占位
    assert media["decode_key"] == "REDACTED"


def test_build_response_excludes_credential_fields():
    info = WechatChannelsService("k", "b")._parse_response(load("detail"))
    resp = _build_response(info)
    payload = resp.model_dump()
    blob = json.dumps(payload)
    assert "decode_key" not in blob
    assert "url_token" not in blob
    assert "full_url" not in blob
    assert "raw_data" not in payload
    assert resp.video_id == OBJECT_ID
    assert resp.author_id == info.author_id
    assert resp.view_count is None


def test_parse_response_none_without_media_or_non_video():
    assert WechatChannelsService("k", "b")._parse_response(load("empty")) is None
    assert WechatChannelsService("k", "b")._parse_response(load("image_note")) is None
    missing_media = {"data": {"id": OBJECT_ID, "object_type": 0, "title": "x"}}
    assert WechatChannelsService("k", "b")._parse_response(missing_media) is None
