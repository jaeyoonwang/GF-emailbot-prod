"""
Session management with in-memory token store.

Tokens are too large for cookies (~6KB encrypted vs 4KB browser limit).
Instead, we store tokens in server memory and put only a small session ID
in the cookie.

Trade-off: sessions are lost on server restart (Trevor logs in again via
SSO — takes ~2 seconds, no typing required).

Session data flow:
1. Auth callback stores tokens in _sessions dict, keyed by random session ID
2. Session ID is encrypted and stored in a small cookie (~200 bytes)
3. On each request, middleware reads session ID from cookie, looks up tokens
4. If server restarted → _sessions is empty → redirect to login → SSO auto-signs in
"""

import json
import logging
import secrets
import time
from typing import Optional
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
import base64
import hashlib

from app.config import settings

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "email_agent_session"

# In-memory session store. Keys are session IDs, values are SessionData.
# Intentionally not persisted — zero data at rest.
_sessions: dict[str, "SessionData"] = {}


@dataclass
class SessionData:
    """Data stored per session (in server memory, not in cookie)."""
    access_token: str
    refresh_token: str
    token_expires_at: float  # UTC timestamp
    user_name: str = ""
    user_email: str = ""

    @property
    def is_token_expired(self) -> bool:
        """Check if the access token has expired (with 5-min buffer)."""
        return time.time() >= (self.token_expires_at - 300)


def _get_fernet() -> Fernet:
    """Create a Fernet encryption instance from the session secret key."""
    key_bytes = hashlib.sha256(settings.session_secret_key.encode()).digest()
    key_b64 = base64.urlsafe_b64encode(key_bytes)
    return Fernet(key_b64)


def create_session(data: SessionData) -> str:
    """
    Store session data in memory and return an encrypted cookie value
    containing only the session ID.

    Returns:
        Encrypted cookie value (~200 bytes — well under 4KB limit).
    """
    session_id = secrets.token_urlsafe(32)
    _sessions[session_id] = data

    f = _get_fernet()
    encrypted = f.encrypt(session_id.encode())

    logger.info(
        "session.created",
        extra={
            "action": "session.created",
            "active_sessions": len(_sessions),
        },
    )

    return encrypted.decode()


def get_session(cookie_value: str) -> Optional[SessionData]:
    """
    Decrypt the cookie to get the session ID, then look up session data.

    Returns None if cookie is invalid or session not found in memory.
    """
    try:
        f = _get_fernet()
        session_id = f.decrypt(cookie_value.encode()).decode()
        return _sessions.get(session_id)
    except (InvalidToken, Exception) as e:
        logger.warning(
            "session.decode_failed",
            extra={"action": "session.decode_failed", "error_type": type(e).__name__},
        )
        return None


def update_session(cookie_value: str, data: SessionData) -> bool:
    """
    Update an existing session's data (e.g., after token refresh).

    Returns True if the session was found and updated.
    """
    try:
        f = _get_fernet()
        session_id = f.decrypt(cookie_value.encode()).decode()
        if session_id in _sessions:
            _sessions[session_id] = data
            return True
        return False
    except (InvalidToken, Exception):
        return False


def delete_session(cookie_value: str) -> None:
    """Remove a session from memory (on logout)."""
    try:
        f = _get_fernet()
        session_id = f.decrypt(cookie_value.encode()).decode()
        _sessions.pop(session_id, None)
    except (InvalidToken, Exception):
        pass


def get_session_from_request(request) -> Optional[SessionData]:
    """Convenience: extract session from a FastAPI/Starlette request."""
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if not cookie:
        return None
    return get_session(cookie)