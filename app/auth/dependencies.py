"""
FastAPI dependencies for authentication.
"""

import logging
import time

from fastapi import Request, Response, HTTPException

from app.auth.session import (
    SessionData, get_session_from_request, update_session, SESSION_COOKIE_NAME,
)
from app.auth.oauth import refresh_access_token
from app.logging.config import current_user_var

logger = logging.getLogger(__name__)


async def require_auth(request: Request, response: Response) -> SessionData:
    """
    FastAPI dependency that ensures the user is authenticated.

    Reads session from cookie → looks up in memory → refreshes token
    if expired → sets logging context → returns SessionData.
    """
    session = get_session_from_request(request)

    if session is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # If token is expired, try to refresh silently
    if session.is_token_expired:
        logger.info(
            "auth.token_expired_refreshing",
            extra={
                "action": "auth.token_expired_refreshing",
                "user": session.user_email,
            },
        )

        if not session.refresh_token:
            raise HTTPException(status_code=401, detail="Session expired, please log in again")

        result = refresh_access_token(session.refresh_token)

        if result is None:
            raise HTTPException(status_code=401, detail="Session expired, please log in again")

        # Update session in memory with new tokens
        session = SessionData(
            access_token=result["access_token"],
            refresh_token=result.get("refresh_token", session.refresh_token),
            token_expires_at=time.time() + result.get("expires_in", 3600),
            user_name=session.user_name,
            user_email=session.user_email,
        )

        # Update the in-memory session store
        cookie = request.cookies.get(SESSION_COOKIE_NAME)
        if cookie:
            update_session(cookie, session)

        logger.info(
            "auth.token_refreshed",
            extra={
                "action": "auth.token_refreshed",
                "user": session.user_email,
            },
        )

    # Set logging context
    current_user_var.set(session.user_email or session.user_name or "authenticated")

    return session