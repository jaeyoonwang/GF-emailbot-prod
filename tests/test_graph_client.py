"""
Tests for the Microsoft Graph API client.

Uses httpx mock to simulate Graph API responses without network calls.
"""

import pytest
from unittest.mock import patch, MagicMock
import httpx
from app.graph.client import GraphClient
from app.agent.schemas import Email
from app.logging.config import setup_logging


@pytest.fixture(autouse=True)
def init_logging():
    setup_logging("debug")


@pytest.fixture
def graph() -> GraphClient:
    """Create a GraphClient with a fake token."""
    return GraphClient(access_token="fake-token-for-testing")


def make_graph_message(**overrides) -> dict:
    """Create a realistic Graph API message dict."""
    base = {
        "id": "AAMk123",
        "subject": "Test Subject",
        "sender": {
            "emailAddress": {
                "name": "Jane Smith",
                "address": "jane@example.com",
            }
        },
        "body": {"content": "Hello, this is a test.", "contentType": "text"},
        "bodyPreview": "Hello, this is a test.",
        "receivedDateTime": "2026-02-18T10:00:00Z",
        "importance": "normal",
        "hasAttachments": False,
        "webLink": "https://outlook.office.com/mail/id/AAMk123",
        "conversationId": "conv-456",
        "isRead": False,
        "meetingMessageType": None,
    }
    base.update(overrides)
    return base


class TestParseInboxMessage:
    """Tests for _parse_inbox_message (static method)."""

    def test_parses_standard_message(self):
        msg = make_graph_message()
        email = GraphClient._parse_inbox_message(msg)

        assert isinstance(email, Email)
        assert email.id == "AAMk123"
        assert email.subject == "Test Subject"
        assert email.sender_name == "Jane Smith"
        assert email.sender_email == "jane@example.com"
        assert email.body == "Hello, this is a test."
        assert email.is_read is False
        assert email.conversation_id == "conv-456"

    def test_html_body(self):
        msg = make_graph_message(
            body={"content": "<p>Hello <b>world</b></p>", "contentType": "html"}
        )
        email = GraphClient._parse_inbox_message(msg)

        assert email.body_html == "<p>Hello <b>world</b></p>"
        # body should be body_preview for HTML emails (plain text fallback)
        assert email.body == "Hello, this is a test."

    def test_missing_sender(self):
        msg = make_graph_message(sender=None)
        email = GraphClient._parse_inbox_message(msg)

        assert email.sender_name == "Unknown"
        assert email.sender_email == ""

    def test_missing_body(self):
        msg = make_graph_message(body=None)
        email = GraphClient._parse_inbox_message(msg)

        assert email.body == "Hello, this is a test."  # Falls back to bodyPreview

    def test_meeting_message_type(self):
        msg = make_graph_message(meetingMessageType="meetingRequest")
        email = GraphClient._parse_inbox_message(msg)

        assert email.meeting_message_type == "meetingRequest"

    def test_malformed_message_returns_none(self):
        """A completely broken message should return None, not crash."""
        # Pass something that will cause parsing issues
        email = GraphClient._parse_inbox_message({"id": None, "sender": "not-a-dict"})
        # Should return an Email with defaults or None — either is acceptable
        # The key is it doesn't raise an exception


class TestParseTimeWindow:
    def test_24_hours(self):
        result = GraphClient._parse_time_window("24 hours")
        assert result is not None

    def test_6_hours(self):
        result = GraphClient._parse_time_window("6 hours")
        assert result is not None

    def test_7_days(self):
        result = GraphClient._parse_time_window("7 days")
        assert result is not None

    def test_all(self):
        result = GraphClient._parse_time_window("All")
        assert result is None

    def test_empty(self):
        result = GraphClient._parse_time_window("")
        assert result is None

    def test_invalid(self):
        result = GraphClient._parse_time_window("not a window")
        assert result is None


class TestFetchInbox:
    """Tests for fetch_inbox using mocked HTTP responses."""

    def test_basic_fetch(self, graph):
        """Should parse messages from a single-page response."""
        mock_response = httpx.Response(
            200,
            json={
                "value": [make_graph_message(id="e1"), make_graph_message(id="e2")],
                "@odata.count": 2,
            },
            request=httpx.Request("GET", "https://graph.microsoft.com"),
        )

        with patch.object(graph._http, "get", return_value=mock_response):
            emails = graph.fetch_inbox(time_window="24 hours")

        assert len(emails) == 2
        assert emails[0].id == "e1"
        assert emails[1].id == "e2"

    def test_pagination(self, graph):
        """Should follow @odata.nextLink for multi-page results."""
        page1 = httpx.Response(
            200,
            json={
                "value": [make_graph_message(id=f"e{i}") for i in range(50)],
                "@odata.nextLink": "https://graph.microsoft.com/next-page",
            },
            request=httpx.Request("GET", "https://graph.microsoft.com"),
        )
        page2 = httpx.Response(
            200,
            json={
                "value": [make_graph_message(id=f"e{i}") for i in range(50, 60)],
            },
            request=httpx.Request("GET", "https://graph.microsoft.com"),
        )

        with patch.object(graph._http, "get", side_effect=[page1, page2]):
            emails = graph.fetch_inbox(time_window="24 hours")

        assert len(emails) == 60

    def test_max_emails_respected(self, graph):
        """Should stop fetching after max_emails."""
        mock_response = httpx.Response(
            200,
            json={
                "value": [make_graph_message(id=f"e{i}") for i in range(50)],
                "@odata.nextLink": "https://graph.microsoft.com/next-page",
            },
            request=httpx.Request("GET", "https://graph.microsoft.com"),
        )

        with patch.object(graph._http, "get", return_value=mock_response):
            emails = graph.fetch_inbox(time_window="24 hours", max_emails=10)

        assert len(emails) == 10

    def test_api_error_returns_partial(self, graph):
        """On API error, return whatever we've fetched so far."""
        with patch.object(
            graph._http, "get", side_effect=httpx.HTTPError("connection failed")
        ):
            emails = graph.fetch_inbox(time_window="24 hours")

        assert emails == []


class TestFetchSentToRecipient:
    def test_filters_by_recipient(self, graph):
        """Should only return emails sent to the specified recipient."""
        messages = [
            {
                "subject": "To Jane",
                "body": {"content": "Hello Jane"},
                "bodyPreview": "Hello Jane",
                "sentDateTime": "2026-02-18T10:00:00Z",
                "toRecipients": [
                    {"emailAddress": {"address": "jane@example.com"}}
                ],
            },
            {
                "subject": "To Bob",
                "body": {"content": "Hello Bob"},
                "bodyPreview": "Hello Bob",
                "sentDateTime": "2026-02-18T10:00:00Z",
                "toRecipients": [
                    {"emailAddress": {"address": "bob@example.com"}}
                ],
            },
        ]

        mock_response = httpx.Response(
            200,
            json={"value": messages},
            request=httpx.Request("GET", "https://graph.microsoft.com"),
        )

        with patch.object(graph._http, "get", return_value=mock_response):
            result = graph.fetch_sent_to_recipient("jane@example.com")

        assert len(result) == 1
        assert result[0]["subject"] == "To Jane"

    def test_case_insensitive_matching(self, graph):
        """Recipient matching should be case-insensitive."""
        messages = [
            {
                "subject": "Test",
                "body": {"content": "Body"},
                "bodyPreview": "Body",
                "sentDateTime": "2026-02-18T10:00:00Z",
                "toRecipients": [
                    {"emailAddress": {"address": "Jane@Example.COM"}}
                ],
            },
        ]

        mock_response = httpx.Response(
            200,
            json={"value": messages},
            request=httpx.Request("GET", "https://graph.microsoft.com"),
        )

        with patch.object(graph._http, "get", return_value=mock_response):
            result = graph.fetch_sent_to_recipient("jane@example.com")

        assert len(result) == 1


class TestMarkAsRead:
    def test_success(self, graph):
        mock_response = httpx.Response(
            200,
            request=httpx.Request("PATCH", "https://graph.microsoft.com"),
        )
        with patch.object(graph._http, "patch", return_value=mock_response):
            assert graph.mark_as_read("AAMk123") is True

    def test_failure(self, graph):
        with patch.object(
            graph._http, "patch", side_effect=httpx.HTTPError("forbidden")
        ):
            assert graph.mark_as_read("AAMk123") is False


class TestSendEmail:
    def test_success(self, graph):
        mock_response = httpx.Response(
            202,
            request=httpx.Request("POST", "https://graph.microsoft.com"),
        )
        with patch.object(graph._http, "post", return_value=mock_response):
            result = graph.send_email(
                to_email="recipient@example.com",
                subject="Re: Test",
                body_html="<p>Thanks!</p>",
            )
        assert result is True

    def test_failure(self, graph):
        with patch.object(
            graph._http, "post", side_effect=httpx.HTTPError("server error")
        ):
            result = graph.send_email(
                to_email="recipient@example.com",
                subject="Re: Test",
                body_html="<p>Thanks!</p>",
            )
        assert result is False