"""resolve quality 请求参数校验契约测试。"""

import pytest

from app.api.resolve import ResolveRequest


YOUTUBE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.mark.parametrize("quality", ["720p", "1080p", "2160p"])
def test_quality_accepts_resolution_cap(quality):
    request = ResolveRequest.model_validate({"url": YOUTUBE_URL, "quality": quality})

    assert request.quality == quality


@pytest.mark.parametrize("quality", ["720", "720P", "p720", "720p60", ""])
def test_quality_rejects_invalid_format(authed_client, quality):
    response = authed_client.post(
        "/api/resolve",
        json={"url": YOUTUBE_URL, "quality": quality},
    )

    assert response.status_code == 422


def test_quality_cannot_be_combined_with_audio(authed_client):
    response = authed_client.post(
        "/api/resolve",
        json={"url": YOUTUBE_URL, "download_mode": "audio", "quality": "720p"},
    )

    assert response.status_code == 422
