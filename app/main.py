"""
FastAPI application entry point.

Run with:
    uvicorn app.main:app --reload --port 8000
"""

import uuid
import logging
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import settings
from app.logging.config import setup_logging, request_id_var, current_user_var
from app.auth.routes import router as auth_router
from app.api.routes_email import router as email_router
from app.api.routes_agent import router as agent_router
from app.api.routes_pages import router as pages_router
from app.auth.session import SESSION_COOKIE_NAME, get_session_from_request

# --- Initialize logging FIRST ---
setup_logging(level=settings.log_level)
logger = logging.getLogger(__name__)

# --- Create the FastAPI app ---
app = FastAPI(
    title=settings.app_name,
    docs_url="/docs" if settings.app_env == "development" else None,
    redoc_url=None,
)


# --- Middleware: Request context + logging ---
@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    """Set up request ID, user context, and request timing."""
    # Generate request ID
    req_id = str(uuid.uuid4())[:8]
    request_id_var.set(req_id)

    # Try to extract user from session
    user = "anonymous"
    session = get_session_from_request(request)
    if session:
        user = session.user_email or session.user_name or "authenticated"
    current_user_var.set(user)

    # Time the request
    start = time.monotonic()
    response = await call_next(request)

    # Add request ID to response headers
    response.headers["X-Request-ID"] = req_id

    # Log the request
    latency_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        "http.request",
        extra={
            "action": "http.request",
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "latency_ms": latency_ms,
        },
    )

    return response


# --- Middleware: Redirect unauthenticated page requests to login ---
@app.middleware("http")
async def auth_redirect_middleware(request: Request, call_next):
    """Redirect unauthenticated browser requests to login. API gets 401."""
    path = request.url.path

    # Always allow: auth routes, health checks, static files, docs
    if (
        path.startswith("/auth/")
        or path.startswith("/health")
        or path.startswith("/ready")
        or path.startswith("/static/")
        or path.startswith("/docs")
        or path.startswith("/openapi")
    ):
        return await call_next(request)

    # Check for session
    session = get_session_from_request(request)
    if session is None:
        if path.startswith("/api/"):
            return JSONResponse(
                status_code=401,
                content={"detail": "Not authenticated"},
            )
        return RedirectResponse(url="/auth/login")

    return await call_next(request)


# --- Register route modules ---
app.include_router(auth_router)
app.include_router(pages_router)  # Pages first (has "/" route)
app.include_router(email_router)
app.include_router(agent_router)


# --- Health check endpoints ---
@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    checks = {
        "config_loaded": True,
        "tier_config_path_set": bool(settings.tier_config_path),
        "anthropic_key_set": bool(settings.anthropic_api_key),
        "azure_client_id_set": bool(settings.azure_client_id),
    }
    all_ok = all(checks.values())
    return {"status": "ready" if all_ok else "not_ready", "checks": checks}