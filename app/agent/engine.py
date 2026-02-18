"""
Agent engine — the orchestrator for all email processing.

This module ties together priority assignment, filtering, summarization,
and draft generation. It replaces the old ai_service.py.

The engine does NOT fetch emails from Graph API directly. It receives
email data and a graph client interface, keeping it testable and
decoupled from the API layer.

Usage:
    from app.agent.engine import AgentEngine

    engine = AgentEngine(tier_config=tiers, llm_client=llm)
    
    # Process inbox
    results = engine.process_inbox(emails)
    
    # Summarize a single email
    summary = engine.summarize_email(email)
    
    # Draft a reply (with style context from sent emails)
    draft = engine.draft_reply(email, sent_emails_to_sender, all_sent_emails, ...)
"""

import logging
from typing import Optional, Protocol

from app.agent.schemas import Email, FilterResult, Tier, DraftResponse, SentEmail
from app.agent.priority import TierConfig
from app.agent.filters import check_filters
from app.agent.prompts import (
    SUMMARIZE_SYSTEM,
    SUMMARIZE_USER,
    DRAFT_SYSTEM,
    DRAFT_USER,
    parse_summary,
    build_style_block,
    format_style_context,
    ensure_disclaimer,
)
from app.llm.client import LLMClient, LLMError
from app.logging.audit import audit
from app.config import settings

logger = logging.getLogger(__name__)


class ProcessedEmail:
    """
    Result of processing a single email through the pipeline.

    Either the email passed filters (email is set, filter_result.filtered is False),
    or it was filtered out (email may be set for reference, filter_result.filtered is True).
    """

    def __init__(self, email: Email, filter_result: FilterResult):
        self.email = email
        self.filter_result = filter_result

    @property
    def filtered(self) -> bool:
        return self.filter_result.filtered

    @property
    def reason(self) -> Optional[str]:
        return self.filter_result.reason

    @property
    def detail(self) -> Optional[str]:
        return self.filter_result.detail


class AgentEngine:
    """
    Orchestrates all email processing: filtering, prioritization,
    summarization, and draft generation.
    """

    def __init__(self, tier_config: TierConfig, llm_client: LLMClient):
        self._tiers = tier_config
        self._llm = llm_client

        logger.info(
            "agent_engine.initialized",
            extra={"action": "agent_engine.initialized"},
        )

    # =========================================================================
    # INBOX PROCESSING — Filter and prioritize a batch of emails
    # =========================================================================

    def process_inbox(self, emails: list[Email]) -> tuple[list[Email], list[ProcessedEmail]]:
        """
        Process a batch of emails: filter, assign tiers, and summarize.

        Summarization happens for all actionable emails so Trevor can scan
        the inbox immediately. Draft generation remains on-demand.

        Args:
            emails: Raw emails from Graph API.

        Returns:
            Tuple of (actionable_emails, filtered_emails).
            actionable_emails: Emails with tier + summary, sorted by tier (highest priority first).
            filtered_emails: ProcessedEmail objects for emails that were filtered out.
        """
        actionable = []
        filtered = []

        for email in emails:
            # Run filters
            filter_result = check_filters(email, self._tiers)

            if filter_result.filtered:
                filtered.append(ProcessedEmail(email, filter_result))
                audit.info(
                    "email.filtered",
                    extra={
                        "email_id": email.id,
                        "reason": filter_result.reason,
                    },
                )
                continue

            # Assign tier
            email.tier = self._tiers.get_tier(email.sender_email)

            actionable.append(email)
            audit.info(
                "email.classified",
                extra={
                    "email_id": email.id,
                    "tier": email.tier.value,
                    "tier_name": email.tier.name,
                },
            )

        # Sort by tier (VVIP first)
        actionable.sort(key=lambda e: e.tier)

        # Summarize all actionable emails
        for email in actionable:
            self.summarize_email(email)

        audit.info(
            "inbox.processed",
            extra={
                "total_emails": len(emails),
                "actionable_count": len(actionable),
                "filtered_count": len(filtered),
                "summarized_count": len(actionable),
            },
        )

        return actionable, filtered

    # =========================================================================
    # SUMMARIZATION — On-demand, one email at a time
    # =========================================================================

    def summarize_email(self, email: Email) -> str:
        """
        Generate a concise summary for a single email.

        Called on-demand when the user views an email or when the frontend
        loads a summary via HTMX. NOT called in batch to avoid long waits.

        Args:
            email: The email to summarize.

        Returns:
            Summary string. Returns a fallback if the LLM call fails.
        """
        try:
            user_prompt = SUMMARIZE_USER.format(
                subject=email.subject,
                sender_name=email.sender_name,
                importance=email.importance,
                body_preview=email.body_preview[:500],
            )

            result = self._llm.complete(
                system=SUMMARIZE_SYSTEM,
                user=user_prompt,
                max_tokens=settings.anthropic_max_tokens_summary,
                purpose="summarize",
            )

            summary = parse_summary(result.text)
            email.summary = summary

            audit.info(
                "email.summarized",
                extra={
                    "email_id": email.id,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "cost_usd": round(result.cost, 6),
                    "latency_ms": result.latency_ms,
                },
            )

            return summary

        except LLMError as e:
            logger.error(
                "email.summarize_failed",
                extra={
                    "action": "email.summarize_failed",
                    "email_id": email.id,
                    "error": str(e),
                },
            )
            fallback = f"Email from {email.sender_name} regarding {email.subject}"
            email.summary = fallback
            return fallback

    # =========================================================================
    # DRAFT GENERATION — With style context fallback
    # =========================================================================

    def draft_reply(
        self,
        email: Email,
        sent_to_sender: list[dict],
        all_sent: list[dict],
        user_name: str,
        key_points: str = "",
        additional_context: str = "",
    ) -> DraftResponse:
        """
        Generate a draft reply for an email.

        Style context logic:
        1. If sent_to_sender is not empty → use "specific" style
           (past emails to this exact sender)
        2. Else if all_sent is not empty → use "general" style
           (recent sent emails to anyone)
        3. Else → no style context

        Args:
            email: The email to reply to.
            sent_to_sender: Past sent emails to this specific sender.
                            Pass an empty list if none found.
            all_sent: Recent sent emails to anyone (fallback).
                      Pass an empty list if none found.
            user_name: The authenticated user's display name.
            key_points: User-provided key points (from guidance form).
            additional_context: User-provided additional context.

        Returns:
            DraftResponse with the draft text, style info, and token usage.
        """
        # Determine style source and format context
        if sent_to_sender:
            style_source = "specific"
            style_context = format_style_context(sent_to_sender)
            style_email_count = len(sent_to_sender)
        elif all_sent:
            style_source = "general"
            style_context = format_style_context(all_sent)
            style_email_count = len(all_sent)
        else:
            style_source = "none"
            style_context = ""
            style_email_count = 0

        # Build the style block for insertion into the prompt
        style_block = build_style_block(style_source, style_context, user_name)

        # Format prompts
        system_prompt = DRAFT_SYSTEM.format(user_name=user_name)
        user_prompt = DRAFT_USER.format(
            subject=email.subject,
            sender_name=email.sender_name,
            body=email.body or email.body_preview,
            key_points=key_points if key_points else "None specified - use your judgment",
            additional_context=additional_context if additional_context else "None specified",
            style_block=style_block,
            user_name=user_name,
        )

        try:
            result = self._llm.complete(
                system=system_prompt,
                user=user_prompt,
                max_tokens=settings.anthropic_max_tokens_draft,
                purpose="draft",
            )

            # Ensure disclaimer is present
            draft_text = ensure_disclaimer(result.text, user_name)

            # Update email object
            email.draft = draft_text
            email.style_source = style_source
            email.style_email_count = style_email_count

            audit.info(
                "draft.generated",
                extra={
                    "email_id": email.id,
                    "style_source": style_source,
                    "style_email_count": style_email_count,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "cost_usd": round(result.cost, 6),
                    "latency_ms": result.latency_ms,
                },
            )

            return DraftResponse(
                draft=draft_text,
                style_source=style_source,
                style_email_count=style_email_count,
                tokens_used=result.total_tokens,
            )

        except LLMError as e:
            logger.error(
                "draft.failed",
                extra={
                    "action": "draft.failed",
                    "email_id": email.id,
                    "style_source": style_source,
                    "error": str(e),
                },
            )
            # Return a minimal fallback draft
            fallback = f"Thank you for your email regarding {email.subject}. I will review and respond accordingly."
            fallback = ensure_disclaimer(fallback, user_name)
            email.draft = fallback

            return DraftResponse(
                draft=fallback,
                style_source="none",
                style_email_count=0,
                tokens_used=0,
            )