"""
Tests for API Key authentication.
"""

import pytest
from app.core.config import settings


# T9: Auth correctly blocks invalid requests
class TestAuthBlocking:

    def test_missing_api_key_returns_401(self, client):
        settings.API_KEY = "test-key-123"
        try:
            resp = client.get("/api/dashboard/stats/overview")
            assert resp.status_code == 401
        finally:
            settings.API_KEY = ""

    def test_wrong_api_key_returns_401(self, client):
        settings.API_KEY = "test-key-123"
        try:
            resp = client.get(
                "/api/dashboard/stats/overview",
                headers={"X-API-Key": "wrong-key"},
            )
            assert resp.status_code == 401
        finally:
            settings.API_KEY = ""

    def test_valid_api_key_passes(self, client):
        settings.API_KEY = "test-key-123"
        try:
            resp = client.get(
                "/api/dashboard/stats/overview",
                headers={"X-API-Key": "test-key-123"},
            )
            assert resp.status_code == 200
        finally:
            settings.API_KEY = ""


# T10: No API Key configured skips auth
class TestAuthSkip:

    def test_no_api_key_configured_allows_all(self, client):
        original = settings.API_KEY
        settings.API_KEY = ""
        try:
            resp = client.get("/api/dashboard/stats/overview")
            assert resp.status_code == 200
        finally:
            settings.API_KEY = original
