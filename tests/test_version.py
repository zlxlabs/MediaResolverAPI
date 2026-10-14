"""版本单一来源契约：pyproject.toml 是版本唯一字面量，/health 透出版本与构建 SHA。"""

import importlib.metadata

from app.core.config import settings


def test_app_version_comes_from_installed_distribution_metadata():
    assert settings.APP_VERSION == importlib.metadata.version("media-resolver-api")


def test_health_exposes_version_and_git_sha(client):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == settings.APP_VERSION
    assert isinstance(body["git_sha"], str) and body["git_sha"]
