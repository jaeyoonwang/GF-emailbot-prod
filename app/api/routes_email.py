"""
Email API routes.

These endpoints handle:
- Fetching and processing the inbox (filter, prioritize, summarize)
- Fetching a single email's full content
- Marking emails as read
- Sending email replies
"""

import logging

from fastapi import APIRouter, Depends, Response, HTTPException, Query

from app.auth.dependencies import require_auth
from app.auth.session import SessionData
from app.graph.client import GraphClient
from app.agent.engine import AgentEngine
from app.agent.priority import TierConfig
from app.llm.client import LLMClient
from app.config import settings
from app.logging.audit import audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/emails", tags=["emails"])


def _get_graph(session: SessionData) -> GraphClient:
    """Create a Graph client from the session's access token."""
    return GraphClient(access_token=session.access_token)


def _get_engine() -> AgentEngine:
    """Create an agent engine with tier config and LLM client."""
    tiers = TierConfig(settings.tier_config_path)
    llm = LLMClient()
    return AgentEngine(tier_config=tiers, llm_client=llm)


@router.get("/inbox")
async def get_inbox(
    response: Response,
    session: SessionData = Depends(require_auth),
    time_window: str = Query(default="24 hours", description="Time filter: '6 hours', '24 hours', '48 hours', 'All'"),
    unread_only: bool = Query(default=False, description="Only fetch unread emails"),
):
    """
    Fetch, filter, prioritize, and summarize the inbox.

    This is the main endpoint Trevor hits when he clicks "Fetch Emails".
    It:
    1. Fetches emails from Graph API (with time window filter)
    2. Filters out calendar invites and blocked senders
    3. Assigns priority tiers
    4. For Tier 1&2: checks if already responded (shows unresponded only)
    5. For Tier 3&4: shows unread only
    6. Summarizes all remaining emails
    7. Returns the sorted, summarized list
    """
    graph = _get_graph(session)
    engine = _get_engine()

    try:
        # Fetch all emails in the time window
        raw_emails = graph.fetch_inbox(
            time_window=time_window,
            unread_only=False,  # We handle read/unread filtering per-tier below
            max_emails=200,
        )

        # Filter and prioritize
        actionable, filtered = engine.process_inbox(raw_emails)

        # Tier-based response/read filtering
        tier_12 = [e for e in actionable if e.tier and e.tier.value <= 2]
        tier_34 = [e for e in actionable if e.tier and e.tier.value > 2]

        # Tier 1&2: filter out already-responded conversations
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

        # Tier 3&4: filter out already-read emails
        read_filtered = 0
        unread_34 = []
        for email in tier_34:
            if email.is_read:
                read_filtered += 1
            else:
                unread_34.append(email)
        tier_34 = unread_34

        # Combine and sort
        final_emails = tier_12 + tier_34
        final_emails.sort(key=lambda e: e.tier)

        # Build filter summary for the sidebar
        filter_summary = {
            "total_in_window": len(raw_emails),
            "actionable": len(final_emails),
            "calendar_invites": sum(1 for f in filtered if f.reason == "calendar_invite"),
            "blocked_senders": sum(1 for f in filtered if f.reason == "filtered_sender"),
            "already_responded": responded_filtered,
            "already_read": read_filtered,
        }

        # Serialize emails
        email_list = []
        for email in final_emails:
            email_list.append({
                "id": email.id,
                "subject": email.subject,
                "sender_name": email.sender_name,
                "sender_email": email.sender_email,
                "body_preview": email.body_preview,
                "body": email.body,
                "body_html": email.body_html,
                "received_datetime": email.received_datetime,
                "importance": email.importance,
                "has_attachments": email.has_attachments,
                "web_link": email.web_link,
                "conversation_id": email.conversation_id,
                "is_read": email.is_read,
                "tier": email.tier.value if email.tier else 4,
                "tier_name": email.tier.name if email.tier else "DEFAULT",
                "summary": email.summary,
            })

        audit.info(
            "inbox.loaded",
            time_window=time_window,
            total=len(raw_emails),
            shown=len(final_emails),
            filtered_calendar=filter_summary["calendar_invites"],
            filtered_sender=filter_summary["blocked_senders"],
            filtered_responded=responded_filtered,
            filtered_read=read_filtered,
        )

        return {
            "emails": email_list,
            "filter_summary": filter_summary,
            "user_name": session.user_name,
        }

    except Exception as e:
        logger.error(
            "inbox.load_failed",
            extra={"action": "inbox.load_failed", "error": str(e)},
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"Failed to load inbox: {str(e)}")
    finally:
        graph.close()


@router.get("/{email_id}")
async def get_email(
    email_id: str,
    response: Response,
    session: SessionData = Depends(require_auth),
):
    """Fetch a single email's full content by ID."""
    graph = _get_graph(session)
    try:
        # Fetch the specific email from Graph API
        resp = graph._http.get(
            f"{graph._base}/me/messages/{email_id}",
            params={"$select": "id,subject,sender,body,bodyPreview,receivedDateTime,importance,hasAttachments,webLink,conversationId,isRead"},
        )
        resp.raise_for_status()
        msg = resp.json()
        email = graph._parse_inbox_message(msg)

        if email is None:
            raise HTTPException(status_code=404, detail="Email not found or could not be parsed")

        return {
            "id": email.id,
            "subject": email.subject,
            "sender_name": email.sender_name,
            "sender_email": email.sender_email,
            "body": email.body,
            "body_html": email.body_html,
            "body_preview": email.body_preview,
            "received_datetime": email.received_datetime,
            "importance": email.importance,
            "has_attachments": email.has_attachments,
            "web_link": email.web_link,
            "is_read": email.is_read,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("email.fetch_failed", extra={"action": "email.fetch_failed", "email_id": email_id, "error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to fetch email")
    finally:
        graph.close()


@router.post("/{email_id}/read")
async def mark_read(
    email_id: str,
    response: Response,
    session: SessionData = Depends(require_auth),
):
    """Mark an email as read in Outlook."""
    graph = _get_graph(session)
    try:
        success = graph.mark_as_read(email_id)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to mark email as read")
        return {"status": "ok", "email_id": email_id}
    finally:
        graph.close()


@router.post("/{email_id}/send")
async def send_reply(
    email_id: str,
    body: dict,
    response: Response,
    session: SessionData = Depends(require_auth),
):
    """
    Send a reply to an email.

    Request body:
    {
        "to_email": "recipient@example.com",
        "subject": "Re: Original Subject",
        "body_html": "<p>Reply content</p>"
    }
    """
    to_email = body.get("to_email")
    subject = body.get("subject")
    body_html = body.get("body_html")

    if not all([to_email, subject, body_html]):
        raise HTTPException(status_code=400, detail="Missing required fields: to_email, subject, body_html")

    graph = _get_graph(session)
    try:
        success = graph.send_email(
            to_email=to_email,
            subject=subject,
            body_html=body_html,
        )

        if not success:
            raise HTTPException(status_code=500, detail="Failed to send email")

        # Mark original as read after sending
        graph.mark_as_read(email_id)

        return {"status": "sent", "to": to_email, "subject": subject}
    finally:
        graph.close()