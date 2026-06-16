"""
TikHub 抖音多级降级链测试

覆盖：终态异常、响应分类器、端点链顺序/短路/全失败、超时预算、HTTPStatusError 终态体。
全部 mock HTTPClient，无真实网络。
"""

import json
from pathlib import Path

import httpx
import pytest

from app.services.providers.base import (
    ProviderError,
    VideoNotFoundError,
    DouyinTerminalError,
)
from app.services.providers.tikhub import TikHubProvider

FIXTURES = Path(__file__).parent / "fixtures" / "douyin"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# ----------------------------- 异常契约 -----------------------------

def test_terminal_error_is_video_not_found_subclass():
    """终态异常须是 VideoNotFoundError 子类，便于现有捕获兼容。"""
    assert issubclass(DouyinTerminalError, VideoNotFoundError)


# ----------------------------- 分类器 -----------------------------

@pytest.mark.parametrize("fixture", ["web_v1", "web_v2", "app_v3", "hybrid"])
def test_classify_ok(fixture):
    assert TikHubProvider._classify_douyin(load(fixture)) == "ok"


@pytest.mark.parametrize("fixture", ["private_reason5", "partial_reason10"])
def test_classify_terminal(fixture):
    assert TikHubProvider._classify_douyin(load(fixture)) == "terminal"


@pytest.mark.parametrize("fixture", ["copyright_reason8", "empty"])
def test_classify_retryable(fixture):
    assert TikHubProvider._classify_douyin(load(fixture)) == "retryable"


def test_classify_scans_whole_filter_list_not_index0():
    """codex #9：终态 reason 可能不在 index 0，需扫整个 filter_list。"""
    data = {
        "data": {
            "filter_list": [
                {"reason": 8, "filter_reason": "版权"},
                {"reason": 5, "filter_reason": "私密"},
            ]
        }
    }
    assert TikHubProvider._classify_douyin(data) == "terminal"
