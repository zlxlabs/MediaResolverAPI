"""
Test fixtures for MediaResolverAPI.

Uses an in-memory SQLite database for isolation.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

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
def setup_db():
    """Create tables before each test, drop after."""
    Base.metadata.create_all(bind=_engine)
    yield
    Base.metadata.drop_all(bind=_engine)


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
