"""
Microsoft Graph API client for email operations.

Replaces the old email_service.py with:
- Proper token management (auto-refresh, no file-based cache)
- Structured logging on every API call
- Typed return values (Email and SentEmail models)
- Pagination support for large mailboxes
- Async-ready httpx client (sync for now, easy to make async later)

This client is used by the API route handlers, NOT by the agent engine.
The engine receives parsed Email objects; this client handles the raw
Graph API communication.

Usage:
    from app.graph.client import GraphClient

    graph = GraphClient(access_token="eyJ...")
    emails = graph.fetch_inbox(time_window="24 hours")
    sent = graph.fetch_sent_to_recipient("mark@org.com", max_emails=100)
"""

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from app.agent.schemas import Email, SentEmail
from app.config import settings
from app.logging.audit import audit

logger = logging.getLogger(__name__)

# Fields we request from the Graph API for inbox emails.
# Requesting only what we need reduces response size and latency.
INBOX_SELECT_FIELDS = (
    "id,subject,sender,from,body,bodyPreview,receivedDateTime,"
    "importance,hasAttachments,webLink,conversationId,isRead"
)

# Fields for sent emails (lighter — we only need body for style context)
SENT_SELECT_FIELDS = "id,subject,body,bodyPreview,sentDateTime,toRecipients"


class GraphClient:
    """
    Microsoft Graph API client for email operations.

    Expects a valid access token. Token acquisition and refresh are handled
    by the auth layer (Step 8), not by this client. This keeps the client
    simple and testable.
    """

    def __init__(self, access_token: str):
        self._token = access_token
        self._base = settings.graph_base_url
        self._http = httpx.Client(
            timeout=30.0,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )

    def close(self):
        """Close the HTTP client. Call when done."""
        self._http.close()

    # =========================================================================
    # CURRENT USER
    # =========================================================================

    def get_current_user(self) -> Optional[dict]:
        """
        Get the authenticated user's profile (name and email).

        Returns None if User.Read permission is not granted.
        """
        try:
            resp = self._http.get(
                f"{self._base}/me",
                params={"$select": "displayName,mail,userPrincipalName"},
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "name": data.get("displayName", "Unknown"),
                "email": data.get("mail") or data.get("userPrincipalName", "Unknown"),
            }
        except httpx.HTTPError:
            logger.warning("graph.get_user.failed", extra={"action": "graph.get_user.failed"})
            return None

    # =========================================================================
    # INBOX FETCHING — With pagination and time window
    # =========================================================================

    def fetch_inbox(
        self,
        time_window: str = "24 hours",
        unread_only: bool = False,
        max_emails: int = 200,
    ) -> list[Email]:
        """
        Fetch emails from the inbox with optional time and read-status filters.

        Paginates automatically until max_emails is reached or no more pages.

        Args:
            time_window: How far back to look. Examples: "6 hours", "24 hours",
                         "48 hours", "7 days", "All".
            unread_only: If True, only fetch unread emails.
            max_emails: Maximum total emails to fetch (across all pages).

        Returns:
            List of Email objects parsed from Graph API responses.
        """
        start = time.monotonic()

        # Build filter
        filter_parts = []
        if unread_only:
            filter_parts.append("isRead eq false")

        cutoff = self._parse_time_window(time_window)
        if cutoff:
            cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
            filter_parts.append(f"receivedDateTime ge {cutoff_str}")

        params = {
            "$orderby": "receivedDateTime desc",
            "$top": min(50, max_emails),
            "$select": INBOX_SELECT_FIELDS,
        }
        if filter_parts:
            params["$filter"] = " and ".join(filter_parts)

        all_emails: list[Email] = []
        url: Optional[str] = f"{self._base}/me/messages"
        page_count = 0

        while url and len(all_emails) < max_emails:
            try:
                if page_count == 0:
                    resp = self._http.get(url, params=params)
                else:
                    # Subsequent pages use @odata.nextLink which includes params
                    resp = self._http.get(url)

                resp.raise_for_status()
                data = resp.json()
                messages = data.get("value", [])
                page_count += 1

                for msg in messages:
                    if len(all_emails) >= max_emails:
                        break
                    email = self._parse_inbox_message(msg)
                    if email:
                        all_emails.append(email)

                url = data.get("@odata.nextLink")

            except httpx.HTTPStatusError as e:
                logger.error(
                    "graph.fetch_inbox.error",
                    extra={
                        "action": "graph.fetch_inbox.error",
                        "page": page_count,
                        "emails_so_far": len(all_emails),
                        "error": str(e),
                        "status_code": e.response.status_code,
                        "response_body": e.response.text[:500],
                    },
                )
                break
            except httpx.HTTPError as e:
                logger.error(
                    "graph.fetch_inbox.error",
                    extra={
                        "action": "graph.fetch_inbox.error",
                        "page": page_count,
                        "emails_so_far": len(all_emails),
                        "error": str(e),
                    },
                )
                break

        latency_ms = int((time.monotonic() - start) * 1000)
        audit.info(
            "graph.inbox.fetched",
            time_window=time_window,
            unread_only=unread_only,
            emails_fetched=len(all_emails),
            pages=page_count,
            latency_ms=latency_ms,
        )

        return all_emails

    # =========================================================================
    # SENT EMAILS — For style context
    # =========================================================================

    def fetch_sent_to_recipient(
        self, recipient_email: str, max_emails: int = 100
    ) -> list[dict]:
        """
        Fetch sent emails to a specific recipient for style context.

        Graph API doesn't support filtering on toRecipients, so we fetch
        pages of sent emails and filter client-side.

        Args:
            recipient_email: Email address of the recipient.
            max_emails: Maximum matching emails to return.

        Returns:
            List of dicts with 'subject', 'body', 'body_preview', 'sent_datetime'.
        """
        start = time.monotonic()
        recipient_lower = recipient_email.lower().strip()
        matched: list[dict] = []

        params = {
            "$orderby": "sentDateTime desc",
            "$top": 50,
            "$select": SENT_SELECT_FIELDS,
        }

        url: Optional[str] = f"{self._base}/me/mailFolders/sentItems/messages"
        pages_fetched = 0
        max_pages = 5  # Safety limit: 5 pages × 50 = 250 emails scanned

        while url and len(matched) < max_emails and pages_fetched < max_pages:
            try:
                if pages_fetched == 0:
                    resp = self._http.get(url, params=params)
                else:
                    resp = self._http.get(url)

                resp.raise_for_status()
                data = resp.json()
                messages = data.get("value", [])
                pages_fetched += 1

                for msg in messages:
                    recipients = [
                        r.get("emailAddress", {}).get("address", "").lower()
                        for r in msg.get("toRecipients", [])
                    ]
                    if recipient_lower in recipients:
                        body = msg.get("body", {})
                        matched.append({
                            "subject": msg.get("subject", ""),
                            "body": body.get("content", "") or "",
                            "body_preview": msg.get("bodyPreview", ""),
                            "sent_datetime": msg.get("sentDateTime", ""),
                        })
                        if len(matched) >= max_emails:
                            break

                url = data.get("@odata.nextLink")

            except httpx.HTTPError as e:
                logger.error(
                    "graph.fetch_sent_to.error",
                    extra={
                        "action": "graph.fetch_sent_to.error",
                        "recipient": recipient_lower,
                        "page": pages_fetched,
                        "matched_so_far": len(matched),
                        "error": str(e),
                    },
                )
                break

        latency_ms = int((time.monotonic() - start) * 1000)
        audit.info(
            "graph.sent_to_recipient.fetched",
            recipient_domain=recipient_lower.split("@")[-1] if "@" in recipient_lower else "unknown",
            matched=len(matched),
            pages=pages_fetched,
            latency_ms=latency_ms,
        )

        return matched

    def fetch_recent_sent(self, max_emails: int = 100) -> list[dict]:
        """
        Fetch recent sent emails (to anyone) for general style context.
        Used as fallback when no emails to a specific recipient are found.

        Returns:
            List of dicts with 'subject', 'body', 'body_preview', 'sent_datetime'.
        """
        start = time.monotonic()
        emails: list[dict] = []

        params = {
            "$orderby": "sentDateTime desc",
            "$top": 50,
            "$select": SENT_SELECT_FIELDS,
        }

        url: Optional[str] = f"{self._base}/me/mailFolders/sentItems/messages"
        pages_fetched = 0
        max_pages = 2  # 2 pages × 50 = 100 emails max

        while url and len(emails) < max_emails and pages_fetched < max_pages:
            try:
                if pages_fetched == 0:
                    resp = self._http.get(url, params=params)
                else:
                    resp = self._http.get(url)

                resp.raise_for_status()
                data = resp.json()
                messages = data.get("value", [])
                pages_fetched += 1

                for msg in messages:
                    body = msg.get("body", {})
                    emails.append({
                        "subject": msg.get("subject", ""),
                        "body": body.get("content", "") or "",
                        "body_preview": msg.get("bodyPreview", ""),
                        "sent_datetime": msg.get("sentDateTime", ""),
                    })
                    if len(emails) >= max_emails:
                        break

                url = data.get("@odata.nextLink")

            except httpx.HTTPError as e:
                logger.error(
                    "graph.fetch_recent_sent.error",
                    extra={
                        "action": "graph.fetch_recent_sent.error",
                        "page": pages_fetched,
                        "error": str(e),
                    },
                )
                break

        latency_ms = int((time.monotonic() - start) * 1000)
        audit.info(
            "graph.recent_sent.fetched",
            count=len(emails),
            pages=pages_fetched,
            latency_ms=latency_ms,
        )

        return emails

    # =========================================================================
    # CONVERSATION RESPONSE CHECK — Has the user replied in this thread?
    # =========================================================================

    def check_conversations_responded(
        self, conversation_ids: list[str]
    ) -> dict[str, bool]:
        """
        Check which conversations have been responded to (have sent items
        in the same thread).

        Args:
            conversation_ids: List of Graph API conversation IDs.

        Returns:
            Dict mapping conversation_id → True if responded, False otherwise.
        """
        responded = {cid: False for cid in conversation_ids if cid}

        for conv_id in responded:
            try:
                resp = self._http.get(
                    f"{self._base}/me/mailFolders/sentItems/messages",
                    params={
                        "$filter": f"conversationId eq '{conv_id}'",
                        "$top": 1,
                        "$select": "id",
                    },
                )
                resp.raise_for_status()
                messages = resp.json().get("value", [])
                if messages:
                    responded[conv_id] = True

            except httpx.HTTPError:
                # On error, assume not responded (safer to show the email)
                pass

        responded_count = sum(1 for v in responded.values() if v)
        audit.info(
            "graph.conversations.checked",
            total=len(responded),
            responded=responded_count,
        )

        return responded

    # =========================================================================
    # EMAIL ACTIONS — Mark as read, send
    # =========================================================================

    def mark_as_read(self, message_id: str) -> bool:
        """Mark an email as read in Outlook."""
        try:
            resp = self._http.patch(
                f"{self._base}/me/messages/{message_id}",
                json={"isRead": True},
            )
            resp.raise_for_status()
            audit.info("graph.email.marked_read", email_id=message_id)
            return True
        except httpx.HTTPError as e:
            logger.error(
                "graph.mark_read.failed",
                extra={
                    "action": "graph.mark_read.failed",
                    "email_id": message_id,
                    "error": str(e),
                },
            )
            return False

    def send_email(
        self, to_email: str, subject: str, body_html: str
    ) -> bool:
        """
        Send an email through Microsoft Graph.

        Args:
            to_email: Recipient email address.
            subject: Email subject line.
            body_html: HTML body content.

        Returns:
            True if sent successfully, False otherwise.
        """
        message = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "html",
                    "content": body_html,
                },
                "toRecipients": [
                    {"emailAddress": {"address": to_email}}
                ],
            }
        }

        try:
            resp = self._http.post(
                f"{self._base}/me/sendMail",
                json=message,
            )
            resp.raise_for_status()
            audit.info(
                "graph.email.sent",
                recipient_domain=to_email.split("@")[-1] if "@" in to_email else "unknown",
            )
            return True
        except httpx.HTTPError as e:
            logger.error(
                "graph.send.failed",
                extra={
                    "action": "graph.send.failed",
                    "error": str(e),
                    "status_code": getattr(e, "response", None)
                    and e.response.status_code,
                },
            )
            return False

    # =========================================================================
    # PRIVATE HELPERS
    # =========================================================================

    @staticmethod
    def _parse_time_window(time_window: str) -> Optional[datetime]:
        """Convert a time window string like '24 hours' to a UTC cutoff datetime."""
        if not time_window or time_window.lower() == "all":
            return None

        now = datetime.now(timezone.utc)
        parts = time_window.strip().split()
        if len(parts) != 2:
            return None

        try:
            value = int(parts[0])
        except ValueError:
            return None

        unit = parts[1].lower()
        if "hour" in unit:
            return now - timedelta(hours=value)
        elif "day" in unit:
            return now - timedelta(days=value)

        return None

    @staticmethod
    def _parse_inbox_message(msg: dict) -> Optional[Email]:
        """
        Parse a raw Graph API message dict into an Email model.

        Defensive parsing: if any field is malformed, use defaults rather
        than crashing. This prevents one bad email from breaking the
        entire inbox fetch.
        """
        try:
            # Sender info — triple defensive
            sender_obj = msg.get("sender") or {}
            email_addr = sender_obj.get("emailAddress") or {} if isinstance(sender_obj, dict) else {}
            sender_name = str((email_addr.get("name") or "Unknown")) if isinstance(email_addr, dict) else "Unknown"
            sender_email = str((email_addr.get("address") or "")) if isinstance(email_addr, dict) else ""

            # Body content
            body_obj = msg.get("body") or {}
            content = str(body_obj.get("content") or "") if isinstance(body_obj, dict) else ""
            content_type = str(body_obj.get("contentType") or "").lower() if isinstance(body_obj, dict) else ""
            body_html = content if content_type == "html" else ""
            body_text = content if content_type == "text" else str(msg.get("bodyPreview") or "")

            return Email(
                id=str(msg.get("id") or f"unknown_{id(msg)}"),
                subject=str(msg.get("subject") or "No Subject"),
                sender_name=sender_name,
                sender_email=sender_email,
                body_preview=str(msg.get("bodyPreview") or ""),
                body=body_text,
                body_html=body_html,
                received_datetime=str(msg.get("receivedDateTime") or ""),
                importance=str(msg.get("importance") or "normal"),
                has_attachments=bool(msg.get("hasAttachments", False)),
                web_link=str(msg.get("webLink") or ""),
                conversation_id=str(msg.get("conversationId") or ""),
                is_read=bool(msg.get("isRead", False)),
                meeting_message_type=str(msg.get("meetingMessageType") or ""),
            )
        except Exception as e:
            logger.error(
                "graph.parse_message.failed",
                extra={
                    "action": "graph.parse_message.failed",
                    "error": str(e),
                    "msg_id": msg.get("id", "unknown"),
                },
            )
            return None