"""版本单一来源契约：pyproject.toml 是版本唯一字面量，/health 透出版本与构建 SHA。"""

import tomllib
from pathlib import Path

from app.core.config import Settings, settings


def test_app_version_matches_project_version():
    with (Path(__file__).parents[1] / "pyproject.toml").open("rb") as project_file:
        project_version = tomllib.load(project_file)["project"]["version"]

    assert settings.APP_VERSION == project_version


def test_app_version_ignores_environment_override(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "0.0.0-bogus")
    new_settings = Settings()
    with (Path(__file__).parents[1] / "pyproject.toml").open("rb") as project_file:
        project_version = tomllib.load(project_file)["project"]["version"]

    assert new_settings.APP_VERSION == project_version


def test_health_exposes_version_and_git_sha(client):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == settings.APP_VERSION
    assert isinstance(body["git_sha"], str) and body["git_sha"]


def test_git_sha_environment_reaches_new_settings_and_health(client, monkeypatch):
    import app.main as main_module

    monkeypatch.setenv("GIT_SHA", "test-build-sha-123")
    new_settings = Settings()
    monkeypatch.setattr(main_module, "settings", new_settings)

    response = client.get("/health")

    assert new_settings.GIT_SHA == "test-build-sha-123"
    assert response.status_code == 200
    assert response.json()["git_sha"] == "test-build-sha-123"


def test_dockerfile_passes_git_sha_build_argument_to_runtime_environment():
    dockerfile = (Path(__file__).parents[1] / "docker/Dockerfile").read_text()

    assert "ARG GIT_SHA" in dockerfile
    assert "ENV GIT_SHA=$GIT_SHA" in dockerfile
