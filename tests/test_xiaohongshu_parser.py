"""
小红书 schema 自适应解析器测试

覆盖三种响应结构（app_v2 / web_v3 camelCase / 旧端点）解析到统一 VideoInfo，
以及双名字段兼容（masterUrl/master_url、width/weight）与计数文本解析。
"""

import json
from pathlib import Path

import pytest

from app.services.platforms.xiaohongshu import XiaohongshuService

FIXTURES = Path(__file__).parent / "fixtures" / "xiaohongshu"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture()
def svc():
    return XiaohongshuService("k", "https://api.tikhub.io")


def test_parse_app_v2(svc):
    info = svc._parse_response(load("app_v2_video"))
    assert info is not None
    assert info.platform == "xiaohongshu"
    assert info.video_id == "68a54752000000001d002090"
    assert info.video_url == "http://sns-v10.rednotecdn.com/stream/79/110/258/app_v2_258.mp4"
    assert info.author_name == "fancyfix凡菲"
    assert info.author_id == "645ce2f60000000012035cb1"
    assert info.like_count == 47
    assert info.collect_count == 10
    assert info.comment_count == 5
    assert info.share_count == 3
    assert info.height == 2560


def test_parse_web_v3_camelcase(svc):
    info = svc._parse_response(load("web_v3_video"))
    assert info is not None
    assert info.video_url == "http://sns-v11.rednotecdn.com/stream/79/110/259/web_v3_259.mp4"
    assert info.author_name == "fancyfix凡菲"
    assert info.author_id == "645ce2f60000000012035cb1"
    # camelCase interactInfo（字符串计数）须被解析为数字
    assert info.like_count == 47
    assert info.collect_count == 10
    assert info.width == 1920
    assert info.height == 2560


def test_parse_old_schema_regression(svc):
    """旧端点结构保留兜底解析（含中文计数文本 1.2万）。"""
    info = svc._parse_response(load("old_schema_video"))
    assert info is not None
    assert info.video_url == "http://sns-v11.rednotecdn.com/stream/old_schema.mp4"
    assert info.like_count == 12000  # "1.2万"
    assert info.width == 1080  # 旧结构只有 weight


def test_parse_image_note_returns_none(svc):
    """图文笔记无视频流 → 解析返回 None。"""
    assert svc._parse_response(load("image_note")) is None


def test_parse_ok_no_video_returns_none(svc):
    """video 类型但 h264 为空 → 无可播放直链 → None。"""
    assert svc._parse_response(load("ok_no_video")) is None


def test_parse_garbage_returns_none(svc):
    assert svc._parse_response({"detail": {"code": 400}}) is None
    assert svc._parse_response({}) is None
