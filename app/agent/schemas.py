"""
Data models for the email agent.

These Pydantic models define the shape of all data flowing through the app.
They replace the raw Dict types from the prototype, giving us type safety,
validation, and documentation.
"""

from pydantic import BaseModel, Field
from typing import Optional
from enum import IntEnum


class Tier(IntEnum):
    """
    Email priority tiers. Lower number = higher priority.

    Tier 1 (VVIP): Response expected within 30 minutes.
    Tier 2 (Important): Response expected within the day.
    Tier 3 (Standard): Response expected within the week.
    Tier 4 (Default): Anyone not in tiers 1-3.
    """
    VVIP = 1
    IMPORTANT = 2
    STANDARD = 3
    DEFAULT = 4


class FilterResult(BaseModel):
    """Result of running an email through the filter pipeline."""
    filtered: bool
    reason: Optional[str] = None
    detail: Optional[str] = None


class Email(BaseModel):
    """An email as received from Microsoft Graph API and enriched by the agent."""

    # --- From Graph API ---
    id: str = Field(description="Graph API message ID")
    subject: str = Field(default="No Subject")
    sender_name: str = Field(default="Unknown")
    sender_email: str = Field(default="")
    body_preview: str = Field(default="")
    body: str = Field(default="")
    body_html: str = Field(default="")
    received_datetime: str = Field(default="")
    importance: str = Field(default="normal")
    has_attachments: bool = Field(default=False)
    web_link: str = Field(default="")
    conversation_id: str = Field(default="")
    is_read: bool = Field(default=False)
    meeting_message_type: str = Field(default="")

    # --- Enriched by agent pipeline ---
    tier: Optional[Tier] = Field(default=None)
    summary: Optional[str] = Field(default=None)
    draft: Optional[str] = Field(default=None)
    style_email_count: Optional[int] = Field(default=None)
    style_source: Optional[str] = Field(default=None)


class DraftRequest(BaseModel):
    """Request to generate a draft reply."""
    email_id: str
    key_points: str = Field(default="")
    additional_context: str = Field(default="")


class DraftResponse(BaseModel):
    """Response from draft generation."""
    draft: str
    style_source: str
    style_email_count: int
    tokens_used: int = Field(default=0)


class SentEmail(BaseModel):
    """A sent email fetched for style context."""
    id: Optional[str] = Field(default=None)
    subject: str = Field(default="")
    body: str = Field(default="")
    body_preview: str = Field(default="")
    sent_datetime: str = Field(default="")