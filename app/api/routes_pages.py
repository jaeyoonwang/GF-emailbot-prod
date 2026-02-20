"""
Page routes — serves HTML pages and HTMX fragments.

These routes render Jinja2 templates and return HTML (not JSON).
The HTMX library in the browser makes requests to these endpoints
and swaps the returned HTML into the page without full reloads.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request, Response, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.auth.dependencies import require_auth
from app.auth.session import SessionData
from app.graph.client import GraphClient
from app.agent.engine import AgentEngine
from app.agent.schemas import DraftRequest
from app.agent.priority import TierConfig
from app.llm.client import LLMClient
from app.config import settings
from app.logging.audit import audit

logger = logging.getLogger(__name__)

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="app/templates")


def _get_graph(session: SessionData) -> GraphClient:
    return GraphClient(access_token=session.access_token)


def _get_engine() -> AgentEngine:
    tiers = TierConfig(settings.tier_config_path)
    llm = LLMClient()
    return AgentEngine(tier_config=tiers, llm_client=llm)


def _get_greeting() -> str:
    """Get time-appropriate greeting in Seattle/Pacific time."""
    try:
        hour = datetime.now(ZoneInfo("America/Los_Angeles")).hour
        if 5 <= hour < 12:
            return "Good morning"
        elif 12 <= hour < 17:
            return "Good afternoon"
        elif 17 <= hour < 21:
            return "Good evening"
        return "Hello"
    except Exception:
        return "Hello"


# =========================================================================
# FULL PAGES
# =========================================================================

@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    response: Response,
    session: SessionData = Depends(require_auth),
    time_window: str = Query(default="24 hours"),
):
    """Render the main dashboard page."""
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user_name": session.user_name,
        "user_email": session.user_email,
        "greeting": _get_greeting(),
        "time_window": time_window,
    })


@router.get("/pages/email/{email_id}", response_class=HTMLResponse)
async def email_detail_page(
    email_id: str,
    request: Request,
    response: Response,
    session: SessionData = Depends(require_auth),
):
    """Render the full email detail page."""
    graph = _get_graph(session)
    try:
        resp = graph._http.get(
            f"{graph._base}/me/messages/{email_id}",
            params={
                "$select": "id,subject,sender,body,bodyPreview,receivedDateTime,"
                "importance,hasAttachments,webLink,conversationId,isRead"
            },
        )
        resp.raise_for_status()
        email = graph._parse_inbox_message(resp.json())

        if email is None:
            raise HTTPException(status_code=404, detail="Email not found")

        return templates.TemplateResponse("email_detail.html", {
            "request": request,
            "user_name": session.user_name,
            "user_email": session.user_email,
            "email": email,
        })
    finally:
        graph.close()


# =========================================================================
# HTMX FRAGMENTS — Partial HTML returned for dynamic updates
# =========================================================================

@router.get("/pages/inbox-content", response_class=HTMLResponse)
async def inbox_content(
    request: Request,
    response: Response,
    session: SessionData = Depends(require_auth),
    time_window: str = Query(default="24 hours"),
):
    """
    Fetch, filter, prioritize, summarize emails and return as HTML fragment.

    Flow (optimized to avoid wasting API calls):
    1. Fetch emails from Graph API
    2. Filter + classify (no LLM calls — instant)
    3. Tier-based filtering (responded/read checks)
    4. Summarize ONLY the final list (LLM calls — parallel)
    5. Return HTML
    """
    graph = _get_graph(session)
    engine = _get_engine()

    try:
        # Step 1: Fetch from Graph API
        raw_emails = graph.fetch_inbox(
            time_window=time_window,
            unread_only=False,
            max_emails=200,
        )

        # Step 2: Filter + classify (instant — no LLM)
        actionable, filtered = engine.process_inbox(raw_emails)

        # Step 3: Tier-based filtering
        tier_12 = [e for e in actionable if e.tier and e.tier.value <= 2]
        tier_34 = [e for e in actionable if e.tier and e.tier.value > 2]

        responded_filtered = 0
        if tier_12:
            conv_ids = [e.conversation_id for e in tier_12 if e.conversation_id]
            if conv_ids:
                responded = graph.check_conversations_responded(conv_ids)
                unresponded = []
                for email in tier_12:
                    if responded.get(email.conversation_id, False):
                        responded_filtered += 1
                    else:
                        unresponded.append(email)
                tier_12 = unresponded

        read_filtered = 0
        unread_34 = []
        for email in tier_34:
            if email.is_read:
                read_filtered += 1
            else:
                unread_34.append(email)
        tier_34 = unread_34

        final_emails = tier_12 + tier_34
        final_emails.sort(key=lambda e: e.tier)

        # Step 4: Summarize ONLY the final list (parallel LLM calls)
        engine.summarize_batch(final_emails)

        filter_summary = {
            "total_in_window": len(raw_emails),
            "actionable": len(final_emails),
            "calendar_invites": sum(1 for f in filtered if f.reason == "calendar_invite"),
            "blocked_senders": sum(1 for f in filtered if f.reason == "filtered_sender"),
            "already_responded": responded_filtered,
            "already_read": read_filtered,
        }

        email_dicts = []
        for email in final_emails:
            email_dicts.append({
                "id": email.id,
                "subject": email.subject,
                "sender_name": email.sender_name,
                "sender_email": email.sender_email,
                "body_preview": email.body_preview,
                "summary": email.summary,
                "received_datetime": email.received_datetime,
                "importance": email.importance,
                "has_attachments": email.has_attachments,
                "tier": email.tier.value if email.tier else 4,
            })

        audit.info(
            "inbox.page_loaded",
            time_window=time_window,
            total=len(raw_emails),
            shown=len(final_emails),
        )

        return templates.TemplateResponse("components/email_list.html", {
            "request": request,
            "emails": email_dicts,
            "filter_summary": filter_summary,
        })

    except Exception as e:
        logger.error("inbox.page_error", extra={"action": "inbox.page_error", "error": str(e)}, exc_info=True)
        return HTMLResponse(
            content=f'<div class="bg-red-50 border border-red-200 rounded-lg p-4 text-red-700">Error loading inbox: {str(e)}</div>',
            status_code=500,
        )
    finally:
        graph.close()


@router.get("/pages/email-inline/{email_id}", response_class=HTMLResponse)
async def email_inline_fragment(
    email_id: str,
    request: Request,
    response: Response,
    session: SessionData = Depends(require_auth),
):
    """Return the full email body as an inline HTML fragment (expands in place)."""
    graph = _get_graph(session)
    try:
        resp = graph._http.get(
            f"{graph._base}/me/messages/{email_id}",
            params={
                "$select": "id,subject,sender,body,bodyPreview,receivedDateTime,"
                "importance,hasAttachments,webLink,conversationId,isRead"
            },
        )
        resp.raise_for_status()
        email = graph._parse_inbox_message(resp.json())

        if email is None:
            return HTMLResponse('<div class="text-red-600 text-sm mt-2">Could not load email.</div>')

        return templates.TemplateResponse("components/email_inline.html", {
            "request": request,
            "email": email,
        })
    except Exception as e:
        return HTMLResponse(f'<div class="text-red-600 text-sm mt-2">Error: {str(e)}</div>')
    finally:
        graph.close()


@router.post("/pages/draft/{email_id}", response_class=HTMLResponse)
async def generate_draft_fragment(
    email_id: str,
    request: Request,
    response: Response,
    session: SessionData = Depends(require_auth),
):
    """
    Generate a draft reply and return as HTML fragment.
    Called by HTMX when "Generate Response" is clicked.
    """
    graph = _get_graph(session)
    engine = _get_engine()

    try:
        # Fetch the original email
        resp = graph._http.get(
            f"{graph._base}/me/messages/{email_id}",
            params={
                "$select": "id,subject,sender,body,bodyPreview,receivedDateTime,"
                "importance,conversationId"
            },
        )
        resp.raise_for_status()
        email = graph._parse_inbox_message(resp.json())

        if email is None:
            return HTMLResponse('<div class="text-red-600 text-sm mt-2">Could not load email.</div>')

        sender_email = email.sender_email
        if not sender_email:
            return HTMLResponse('<div class="text-red-600 text-sm mt-2">Cannot determine sender.</div>')

        # Fetch style context
        sent_to_sender = graph.fetch_sent_to_recipient(sender_email, max_emails=100)
        all_sent = []
        if not sent_to_sender:
            all_sent = graph.fetch_recent_sent(max_emails=100)

        # Generate draft
        result = engine.draft_reply(
            email=email,
            sent_to_sender=sent_to_sender,
            all_sent=all_sent,
            user_name=session.user_name or "the user",
        )

        # Escape subject for JavaScript
        safe_subject = email.subject.replace("'", "\\'").replace('"', '\\"')

        return templates.TemplateResponse("components/draft_panel.html", {
            "request": request,
            "draft": result.draft,
            "style_source": result.style_source,
            "style_email_count": result.style_email_count,
            "tokens_used": result.tokens_used,
            "email_id": email_id,
            "sender_email": email.sender_email,
            "subject": safe_subject,
        })

    except Exception as e:
        logger.error("draft.page_error", extra={"action": "draft.page_error", "email_id": email_id, "error": str(e)}, exc_info=True)
        return HTMLResponse(
            f'<div class="text-red-600 text-sm mt-2">Error generating draft: {str(e)}</div>',
            status_code=500,
        )
    finally:
        graph.close()


@router.post("/pages/mark-read/{email_id}", response_class=HTMLResponse)
async def mark_read_fragment(
    email_id: str,
    request: Request,
    response: Response,
    session: SessionData = Depends(require_auth),
):
    """Mark an email as read and return empty HTML (removes the card via HTMX swap)."""
    graph = _get_graph(session)
    try:
        success = graph.mark_as_read(email_id)
        if success:
            # Return empty string — HTMX outerHTML swap removes the card
            return HTMLResponse("")
        return HTMLResponse('<div class="text-red-600 text-sm">Failed to mark as read.</div>')
    finally:
        graph.close()