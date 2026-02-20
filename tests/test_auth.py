"""
Tests for authentication: session encryption, token expiry, middleware.
"""

import time
import pytest
from app.auth.session import SessionData, create_session, get_session
from app.logging.config import setup_logging


@pytest.fixture(autouse=True)
def init_logging():
    setup_logging("debug")


class TestSessionData:
    def test_token_not_expired(self):
        session = SessionData(
            access_token="token",
            refresh_token="refresh",
            token_expires_at=time.time() + 3600,  # 1 hour from now
        )
        assert session.is_token_expired is False

    def test_token_expired(self):
        session = SessionData(
            access_token="token",
            refresh_token="refresh",
            token_expires_at=time.time() - 60,  # 1 minute ago
        )
        assert session.is_token_expired is True

    def test_token_expired_within_buffer(self):
        """Token expiring within 5 minutes should be considered expired."""
        session = SessionData(
            access_token="token",
            refresh_token="refresh",
            token_expires_at=time.time() + 120,  # 2 minutes from now (within 5-min buffer)
        )
        assert session.is_token_expired is True


class TestSessionEncryption:
    def test_create_and_get_roundtrip(self):
        """Creating a session then getting it should return the original data."""
        original = SessionData(
            access_token="access-token-123",
            refresh_token="refresh-token-456",
            token_expires_at=1708300000.0,
            user_name="Trevor",
            user_email="trevor@gatesfoundation.org",
        )

        cookie_value = create_session(original)
        retrieved = get_session(cookie_value)

        assert retrieved is not None
        assert retrieved.access_token == "access-token-123"
        assert retrieved.refresh_token == "refresh-token-456"
        assert retrieved.token_expires_at == 1708300000.0
        assert retrieved.user_name == "Trevor"
        assert retrieved.user_email == "trevor@gatesfoundation.org"

    def test_cookie_value_is_not_plaintext(self):
        """The cookie value should not contain plaintext tokens."""
        session = SessionData(
            access_token="super-secret-token",
            refresh_token="super-secret-refresh",
            token_expires_at=time.time() + 3600,
        )
        cookie_value = create_session(session)

        assert "super-secret-token" not in cookie_value
        assert "super-secret-refresh" not in cookie_value

    def test_tampered_cookie_returns_none(self):
        """A modified cookie value should fail decryption."""
        session = SessionData(
            access_token="token",
            refresh_token="refresh",
            token_expires_at=time.time() + 3600,
        )
        cookie_value = create_session(session)

        tampered = cookie_value[:-5] + "XXXXX"
        result = get_session(tampered)

        assert result is None

    def test_garbage_input_returns_none(self):
        assert get_session("not-a-valid-cookie") is None

    def test_empty_input_returns_none(self):
        assert get_session("") is None


class TestFastAPIApp:
    """Tests for the FastAPI app endpoints."""

    def test_health_endpoint(self):
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        resp = client.get("/health")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_ready_endpoint(self):
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        resp = client.get("/ready")

        assert resp.status_code == 200
        assert "status" in resp.json()

    def test_unauthenticated_root_redirects(self):
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app, follow_redirects=False)
        resp = client.get("/")

        assert resp.status_code == 307  # Redirect to /auth/login

    def test_unauthenticated_api_returns_401(self):
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        resp = client.get("/api/emails/inbox")

        # Should get 401, not a redirect
        assert resp.status_code == 401

    def test_auth_login_redirects_to_microsoft(self):
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app, follow_redirects=False)
        resp = client.get("/auth/login")

        assert resp.status_code == 307
        location = resp.headers.get("location", "")
        assert "login.microsoftonline.com" in location

    def test_request_id_in_response_headers(self):
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        resp = client.get("/health")

        assert "x-request-id" in resp.headers
        assert len(resp.headers["x-request-id"]) == 8