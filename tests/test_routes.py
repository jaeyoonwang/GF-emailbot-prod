"""
Tests for API routes.

Verifies that routes exist, require authentication, and return
correct status codes. Uses mocked auth to test authenticated flows.
"""

import pytest
import time
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.auth.session import SessionData, create_session, SESSION_COOKIE_NAME
from app.logging.config import setup_logging


@pytest.fixture(autouse=True)
def init_logging():
    setup_logging("debug")


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_cookie() -> dict:
    """Create a valid session cookie for testing."""
    session = SessionData(
        access_token="test-access-token",
        refresh_token="test-refresh-token",
        token_expires_at=time.time() + 3600,
        user_name="Test User",
        user_email="test@example.com",
    )
    return {SESSION_COOKIE_NAME: create_session(session)}


class TestUnauthenticatedAccess:
    """All API endpoints should return 401 without authentication."""

    def test_inbox_requires_auth(self, client):
        resp = client.get("/api/emails/inbox")
        assert resp.status_code == 401

    def test_email_detail_requires_auth(self, client):
        resp = client.get("/api/emails/some-id")
        assert resp.status_code == 401

    def test_mark_read_requires_auth(self, client):
        resp = client.post("/api/emails/some-id/read")
        assert resp.status_code == 401

    def test_send_requires_auth(self, client):
        resp = client.post("/api/emails/some-id/send", json={})
        assert resp.status_code == 401

    def test_draft_requires_auth(self, client):
        resp = client.post("/api/agent/draft", json={"email_id": "test"})
        assert resp.status_code == 401

    def test_summarize_requires_auth(self, client):
        resp = client.post("/api/agent/summarize/some-id")
        assert resp.status_code == 401


class TestHealthEndpoints:
    """Health endpoints should always be accessible."""

    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_ready(self, client):
        resp = client.get("/ready")
        assert resp.status_code == 200
        assert "status" in resp.json()


class TestAuthenticatedRoutes:
    """Test that authenticated routes accept valid cookies and reach the handler."""

    @patch("app.api.routes_email.GraphClient")
    @patch("app.api.routes_email.TierConfig")
    @patch("app.api.routes_email.LLMClient")
    def test_inbox_with_auth(self, mock_llm_cls, mock_tier_cls, mock_graph_cls, client, auth_cookie):
        """Inbox endpoint should accept auth cookie and attempt to fetch emails."""
        # Mock the Graph client to return an empty inbox
        mock_graph = MagicMock()
        mock_graph.fetch_inbox.return_value = []
        mock_graph.close.return_value = None
        mock_graph_cls.return_value = mock_graph

        # Mock tier config
        mock_tiers = MagicMock()
        mock_tier_cls.return_value = mock_tiers

        # Mock LLM client
        mock_llm = MagicMock()
        mock_llm_cls.return_value = mock_llm

        resp = client.get("/api/emails/inbox", cookies=auth_cookie)

        assert resp.status_code == 200
        data = resp.json()
        assert "emails" in data
        assert "filter_summary" in data
        assert data["emails"] == []

    def test_mark_read_with_auth(self, client, auth_cookie):
        """Mark-read endpoint should accept auth and call Graph API."""
        with patch("app.api.routes_email.GraphClient") as mock_cls:
            mock_graph = MagicMock()
            mock_graph.mark_as_read.return_value = True
            mock_graph.close.return_value = None
            mock_cls.return_value = mock_graph

            resp = client.post("/api/emails/test-id/read", cookies=auth_cookie)

            assert resp.status_code == 200
            mock_graph.mark_as_read.assert_called_once_with("test-id")

    def test_send_validates_required_fields(self, client, auth_cookie):
        """Send endpoint should validate that required fields are present."""
        with patch("app.api.routes_email.GraphClient") as mock_cls:
            mock_graph = MagicMock()
            mock_graph.close.return_value = None
            mock_cls.return_value = mock_graph

            # Missing required fields
            resp = client.post(
                "/api/emails/test-id/send",
                json={"to_email": "someone@example.com"},  # missing subject and body_html
                cookies=auth_cookie,
            )

            assert resp.status_code == 400

    def test_send_with_valid_body(self, client, auth_cookie):
        """Send endpoint should work with all required fields."""
        with patch("app.api.routes_email.GraphClient") as mock_cls:
            mock_graph = MagicMock()
            mock_graph.send_email.return_value = True
            mock_graph.mark_as_read.return_value = True
            mock_graph.close.return_value = None
            mock_cls.return_value = mock_graph

            resp = client.post(
                "/api/emails/test-id/send",
                json={
                    "to_email": "recipient@example.com",
                    "subject": "Re: Test",
                    "body_html": "<p>Thanks!</p>",
                },
                cookies=auth_cookie,
            )

            assert resp.status_code == 200
            mock_graph.send_email.assert_called_once()
            # Should also mark original as read
            mock_graph.mark_as_read.assert_called_once_with("test-id")