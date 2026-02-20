"""
Agent API routes.

These endpoints handle AI-powered operations:
- Generating draft replies (with style context from sent emails)
- Re-summarizing an email (if needed)
"""

import logging

from fastapi import APIRouter, Depends, Response, HTTPException

from app.auth.dependencies import require_auth
from app.auth.session import SessionData
from app.graph.client import GraphClient
from app.agent.engine import AgentEngine
from app.agent.schemas import Email, DraftRequest
from app.agent.priority import TierConfig
from app.llm.client import LLMClient
from app.config import settings
from app.logging.audit import audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _get_graph(session: SessionData) -> GraphClient:
    return GraphClient(access_token=session.access_token)


def _get_engine() -> AgentEngine:
    tiers = TierConfig(settings.tier_config_path)
    llm = LLMClient()
    return AgentEngine(tier_config=tiers, llm_client=llm)


@router.post("/draft")
async def generate_draft(
    request: DraftRequest,
    response: Response,
    session: SessionData = Depends(require_auth),
):
    """
    Generate a draft reply for an email.

    This is the endpoint called when Trevor clicks "Generate Response".
    It:
    1. Fetches the original email from Graph API
    2. Fetches past sent emails to this sender (for specific style)
    3. If none found, fetches recent sent emails (for general style)
    4. Generates a draft with the agent engine
    5. Returns the draft with style metadata

    Request body (DraftRequest):
    {
        "email_id": "AAMk...",
        "key_points": "Agree to the meeting but suggest Thursday",
        "additional_context": "I'm out Monday-Tuesday"
    }
    """
    graph = _get_graph(session)
    engine = _get_engine()

    try:
        # Fetch the original email
        resp = graph._http.get(
            f"{graph._base}/me/messages/{request.email_id}",
            params={"$select": "id,subject,sender,body,bodyPreview,receivedDateTime,importance,conversationId"},
        )
        resp.raise_for_status()
        msg = resp.json()
        email = graph._parse_inbox_message(msg)

        if email is None:
            raise HTTPException(status_code=404, detail="Email not found")

        sender_email = email.sender_email
        if not sender_email:
            raise HTTPException(status_code=400, detail="Cannot determine sender email address")

        # Fetch style context: specific to sender first, then general fallback
        audit.info(
            "draft.fetching_style",
            email_id=request.email_id,
            sender_domain=sender_email.split("@")[-1] if "@" in sender_email else "unknown",
        )

        sent_to_sender = graph.fetch_sent_to_recipient(
            recipient_email=sender_email,
            max_emails=100,
        )

        all_sent = []
        if not sent_to_sender:
            all_sent = graph.fetch_recent_sent(max_emails=100)

        # Generate the draft
        result = engine.draft_reply(
            email=email,
            sent_to_sender=sent_to_sender,
            all_sent=all_sent,
            user_name=session.user_name or "the user",
            key_points=request.key_points,
            additional_context=request.additional_context,
        )

        return {
            "draft": result.draft,
            "style_source": result.style_source,
            "style_email_count": result.style_email_count,
            "tokens_used": result.tokens_used,
            "email_id": request.email_id,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "draft.endpoint_failed",
            extra={
                "action": "draft.endpoint_failed",
                "email_id": request.email_id,
                "error": str(e),
            },
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"Failed to generate draft: {str(e)}")
    finally:
        graph.close()


@router.post("/summarize/{email_id}")
async def summarize_email(
    email_id: str,
    response: Response,
    session: SessionData = Depends(require_auth),
):
    """
    Generate or regenerate a summary for a single email.

    Usually summaries are generated as part of inbox loading,
    but this endpoint allows re-summarizing if needed.
    """
    graph = _get_graph(session)
    engine = _get_engine()

    try:
        resp = graph._http.get(
            f"{graph._base}/me/messages/{email_id}",
            params={"$select": "id,subject,sender,body,bodyPreview,importance"},
        )
        resp.raise_for_status()
        msg = resp.json()
        email = graph._parse_inbox_message(msg)

        if email is None:
            raise HTTPException(status_code=404, detail="Email not found")

        summary = engine.summarize_email(email)

        return {
            "email_id": email_id,
            "summary": summary,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "summarize.endpoint_failed",
            extra={"action": "summarize.endpoint_failed", "email_id": email_id, "error": str(e)},
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to summarize email")
    finally:
        graph.close()