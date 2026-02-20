"""
Microsoft Entra ID (Azure AD) OAuth 2.0 authorization code flow.

This replaces the old device code flow with a proper browser-based flow:
1. User visits the app → redirected to Microsoft login
2. Microsoft authenticates user (SSO — usually no password needed)
3. Microsoft redirects back to /auth/callback with an authorization code
4. We exchange the code for access_token + refresh_token
5. Tokens stored in an encrypted session cookie
6. On subsequent requests, middleware silently refreshes expired tokens

The user never types a device code. If SSO is active, they never type a password.

Usage:
    from app.auth.oauth import get_msal_app, build_auth_url, exchange_code, refresh_access_token
"""

import logging
from typing import Optional

from msal import ConfidentialClientApplication

from app.config import settings

logger = logging.getLogger(__name__)

# Cache the MSAL app instance (thread-safe, stateless)
_msal_app: Optional[ConfidentialClientApplication] = None


def get_msal_app() -> ConfidentialClientApplication:
    """
    Get or create the MSAL confidential client application.

    Uses a confidential client (with client_secret) instead of the old
    public client. This is more secure and supports the auth code flow.

    Note: Your Azure app registration must be updated:
    - Add a client secret under "Certificates & secrets"
    - Add a redirect URI under "Authentication" → "Web" platform
      (e.g., http://localhost:8000/auth/callback for dev)
    - Keep "Allow public client flows" disabled
    """
    global _msal_app
    if _msal_app is None:
        _msal_app = ConfidentialClientApplication(
            client_id=settings.azure_client_id,
            client_credential=settings.azure_client_secret,
            authority=f"https://login.microsoftonline.com/{settings.azure_tenant_id}",
        )
    return _msal_app


def build_auth_url(state: str = "") -> str:
    """
    Build the Microsoft login URL to redirect the user to.

    Args:
        state: Opaque value to prevent CSRF. Will be returned in the callback.

    Returns:
        Full URL to redirect the user's browser to.
    """
    app = get_msal_app()
    auth_url = app.get_authorization_request_url(
        scopes=settings.graph_scopes,
        redirect_uri=settings.azure_redirect_uri,
        state=state,
    )
    logger.info("oauth.auth_url_built", extra={"action": "oauth.auth_url_built"})
    return auth_url


def exchange_code(code: str) -> Optional[dict]:
    """
    Exchange an authorization code for tokens.

    Called in the /auth/callback endpoint after Microsoft redirects back.

    Args:
        code: The authorization code from the callback URL.

    Returns:
        Dict with 'access_token', 'refresh_token', 'expires_in', etc.
        Returns None if the exchange fails.
    """
    app = get_msal_app()
    result = app.acquire_token_by_authorization_code(
        code=code,
        scopes=settings.graph_scopes,
        redirect_uri=settings.azure_redirect_uri,
    )

    if "access_token" in result:
        logger.info(
            "oauth.token_acquired",
            extra={
                "action": "oauth.token_acquired",
                "has_refresh_token": "refresh_token" in result,
                "expires_in": result.get("expires_in"),
            },
        )
        return result
    else:
        error = result.get("error_description", result.get("error", "Unknown error"))
        logger.error(
            "oauth.token_exchange_failed",
            extra={
                "action": "oauth.token_exchange_failed",
                "error": error,
            },
        )
        return None


def refresh_access_token(refresh_token: str) -> Optional[dict]:
    """
    Use a refresh token to get a new access token.

    Called by middleware when the access token has expired.
    Refresh tokens typically last 90 days in enterprise tenants.

    Args:
        refresh_token: The refresh token from the previous authentication.

    Returns:
        Dict with new 'access_token', 'refresh_token', 'expires_in', etc.
        Returns None if the refresh fails (e.g., token revoked).
    """
    app = get_msal_app()
    result = app.acquire_token_by_refresh_token(
        refresh_token=refresh_token,
        scopes=settings.graph_scopes,
    )

    if "access_token" in result:
        logger.info(
            "oauth.token_refreshed",
            extra={
                "action": "oauth.token_refreshed",
                "has_new_refresh_token": "refresh_token" in result,
                "expires_in": result.get("expires_in"),
            },
        )
        return result
    else:
        error = result.get("error_description", result.get("error", "Unknown error"))
        logger.warning(
            "oauth.token_refresh_failed",
            extra={
                "action": "oauth.token_refresh_failed",
                "error": error,
            },
        )
        return None