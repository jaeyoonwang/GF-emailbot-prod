"""
Authentication routes: login, callback, logout.
"""

import time
import secrets
import logging

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, HTMLResponse

from app.auth.oauth import build_auth_url, exchange_code
from app.auth.session import (
    SessionData, create_session, delete_session, SESSION_COOKIE_NAME,
)
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
async def login():
    """Redirect the user to Microsoft's login page."""
    state = secrets.token_urlsafe(32)
    auth_url = build_auth_url(state=state)
    return RedirectResponse(url=auth_url)


@router.get("/callback")
async def callback(request: Request):
    """Handle the OAuth callback from Microsoft."""
    code = request.query_params.get("code")
    error = request.query_params.get("error")

    if error:
        error_desc = request.query_params.get("error_description", "Unknown error")
        logger.error(
            "auth.callback.error",
            extra={
                "action": "auth.callback.error",
                "error": error,
                "error_description": error_desc,
            },
        )
        return HTMLResponse(
            content=f"<h2>Authentication Failed</h2><p>{error_desc}</p>"
            '<p><a href="/auth/login">Try again</a></p>',
            status_code=400,
        )

    if not code:
        logger.error("auth.callback.no_code", extra={"action": "auth.callback.no_code"})
        return HTMLResponse(
            content="<h2>Authentication Failed</h2><p>No authorization code received.</p>"
            '<p><a href="/auth/login">Try again</a></p>',
            status_code=400,
        )

    # Exchange authorization code for tokens
    result = exchange_code(code)
    if result is None:
        return HTMLResponse(
            content="<h2>Authentication Failed</h2>"
            "<p>Could not exchange authorization code for tokens. "
            "This may be a configuration issue (client secret, redirect URI, or permissions).</p>"
            '<p><a href="/auth/login">Try again</a></p>',
            status_code=500,
        )

    # Extract user info from the ID token claims (provided by openid + profile scopes)
    id_token_claims = result.get("id_token_claims", {})
    user_name = id_token_claims.get("name", "")
    user_email = (
        id_token_claims.get("preferred_username", "")
        or id_token_claims.get("email", "")
        or id_token_claims.get("upn", "")
    )

    # Create session (tokens stored in server memory, session ID in cookie)
    session = SessionData(
        access_token=result["access_token"],
        refresh_token=result.get("refresh_token", ""),
        token_expires_at=time.time() + result.get("expires_in", 3600),
        user_name=user_name,
        user_email=user_email,
    )
    cookie_value = create_session(session)

    # Set small cookie containing only the encrypted session ID
    redirect = RedirectResponse(url="/", status_code=302)
    redirect.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=cookie_value,
        max_age=7 * 24 * 3600,  # 7 days
        httponly=True,
        secure=settings.app_env != "development",
        samesite="lax",
    )

    logger.info(
        "auth.login.success",
        extra={
            "action": "auth.login.success",
            "user": user_email or user_name or "unknown",
            "cookie_size": len(cookie_value),
        },
    )

    return redirect


@router.get("/logout")
async def logout(request: Request):
    """Clear session cookie and server-side session, redirect to Microsoft logout."""
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie:
        delete_session(cookie)

    # Redirect to Microsoft's logout endpoint to clear the SSO session.
    # Without this, Microsoft silently re-authenticates the user on the next
    # visit to /auth/login (because their browser still holds an MS session cookie).
    post_logout_uri = f"{settings.app_base_url}/auth/login"
    ms_logout_url = (
        f"https://login.microsoftonline.com/{settings.azure_tenant_id}"
        f"/oauth2/v2.0/logout?post_logout_redirect_uri={post_logout_uri}"
    )

    redirect = RedirectResponse(url=ms_logout_url, status_code=302)
    redirect.delete_cookie(SESSION_COOKIE_NAME)
    logger.info("auth.logout", extra={"action": "auth.logout"})
    return redirect