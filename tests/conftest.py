"""
Test fixtures for MediaResolverAPI.

Uses an in-memory SQLite database for isolation.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import database
from app.core.database import Base, get_db
from app.core.config import settings

# Import all models so they register with Base.metadata
from app.models.video_cache import VideoCache  # noqa: F401
from app.models.usage_log import UsageLog  # noqa: F401

from app.main import app

# In-memory SQLite with shared cache so all sessions see the same data
_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@pytest.fixture(autouse=True)
def setup_db(monkeypatch):
    """Create tables before each test, drop after."""
    Base.metadata.create_all(bind=_engine)
    # 服务层自开的 SessionLocal 必须落在测试库上：生产里 resolve 写缓存与 stream
    # 读缓存是同一个库，测试里 get_db 被 override 了，不对齐就读不到缓存行。
    monkeypatch.setattr(
        database,
        "SessionLocal",
        sessionmaker(autocommit=False, autoflush=False, bind=_engine),
    )
    yield
    Base.metadata.drop_all(bind=_engine)


@pytest.fixture(autouse=True)
def test_tikhub_api_key():
    """Keep mocked provider-chain tests independent of local credentials."""
    original_key = settings.TIKHUB_API_KEY
    settings.TIKHUB_API_KEY = "test-tikhub-api-key"
    yield
    settings.TIKHUB_API_KEY = original_key


@pytest.fixture(autouse=True)
def test_justoneapi_token_empty():
    """worktree 会软链主仓 .env：必须把 JOA token 置空，防止链上意外打真上游计费。

    JOA 相关测试自行在用例内设置非空 token 并负责还原。
    """
    original = settings.JUSTONEAPI_TOKEN
    settings.JUSTONEAPI_TOKEN = ""
    yield
    settings.JUSTONEAPI_TOKEN = original


JOA_TEST_TOKEN = "test-joa-token-abcdef"


@pytest.fixture()
def justoneapi_token():
    """用例内启用非空 JOA token 并保证还原。"""
    original = settings.JUSTONEAPI_TOKEN
    settings.JUSTONEAPI_TOKEN = JOA_TEST_TOKEN
    yield JOA_TEST_TOKEN
    settings.JUSTONEAPI_TOKEN = original


@pytest.fixture(autouse=True)
def disable_translation_startup_probe():
    """测试默认不让 TestClient 触发真实翻译上游探测。"""
    original_probe = settings.TRANSLATION_STARTUP_PROBE
    settings.TRANSLATION_STARTUP_PROBE = False
    yield
    settings.TRANSLATION_STARTUP_PROBE = original_probe


@pytest.fixture(autouse=True)
def reset_translation_service():
    """每个测试隔离翻译服务实例及其熔断状态。"""
    import app.api.resolve as resolve_module

    resolve_module._translation_service = None
    yield
    resolve_module._translation_service = None


@pytest.fixture()
def db(setup_db):
    """Provide a test database session."""
    session = sessionmaker(autocommit=False, autoflush=False, bind=_engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db):
    """Provide a FastAPI TestClient with overridden DB dependency."""

    def _override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def authed_client(client):
    """TestClient that includes a valid API key header."""
    original_key = settings.API_KEY
    settings.API_KEY = "test-key-123"

    class _AuthClient:
        def __init__(self, c):
            self._client = c
            self.headers = {"X-API-Key": "test-key-123"}

        def get(self, url, **kw):
            kw.setdefault("headers", {}).update(self.headers)
            return self._client.get(url, **kw)

        def post(self, url, **kw):
            kw.setdefault("headers", {}).update(self.headers)
            return self._client.post(url, **kw)

        def delete(self, url, **kw):
            kw.setdefault("headers", {}).update(self.headers)
            return self._client.delete(url, **kw)

    yield _AuthClient(client)
    settings.API_KEY = original_key
