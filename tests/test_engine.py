"""
Tests for the agent engine orchestrator.

Tests the full pipeline: filtering, tier assignment, summarization,
and draft generation with style context fallback.
"""

import pytest
import textwrap
from unittest.mock import MagicMock, patch
from app.agent.engine import AgentEngine
from app.agent.schemas import Email, Tier, DraftResponse
from app.agent.priority import TierConfig
from app.llm.client import LLMClient, LLMResult, LLMError
from app.logging.config import setup_logging


# --- Fixtures ---

@pytest.fixture(autouse=True)
def init_logging():
    setup_logging("debug")


@pytest.fixture
def tier_config(tmp_path) -> TierConfig:
    yaml_content = textwrap.dedent("""\
        tier_1:
          emails:
            - "ceo@org.com"
        tier_2:
          emails:
            - "director@org.com"
            - "vp@org.com"
        tier_3:
          emails:
            - "manager@org.com"
        filtered_senders:
          - "no-reply@teams.mail.microsoft"
    """)
    yaml_file = tmp_path / "tiers.yaml"
    yaml_file.write_text(yaml_content)
    return TierConfig(str(yaml_file))


@pytest.fixture
def mock_llm() -> MagicMock:
    """Create a mock LLM client."""
    llm = MagicMock(spec=LLMClient)
    llm.complete.return_value = LLMResult(
        text="SUMMARY: This is a test summary.",
        input_tokens=100,
        output_tokens=30,
        total_tokens=130,
        input_cost=0.0003,
        output_cost=0.00045,
        cost=0.00075,
        latency_ms=500,
        model="claude-sonnet-4-20250514",
    )
    return llm


@pytest.fixture
def engine(tier_config, mock_llm) -> AgentEngine:
    return AgentEngine(tier_config=tier_config, llm_client=mock_llm)


def make_email(**overrides) -> Email:
    defaults = {
        "id": "test-123",
        "subject": "Test Subject",
        "sender_name": "Test Sender",
        "sender_email": "test@example.com",
        "body_preview": "This is a test email.",
        "body": "This is the full body of the test email.",
    }
    defaults.update(overrides)
    return Email(**defaults)


# =============================================================================
# INBOX PROCESSING TESTS
# =============================================================================

class TestProcessInbox:
    def test_empty_inbox(self, engine):
        actionable, filtered = engine.process_inbox([])
        assert actionable == []
        assert filtered == []

    def test_filters_out_blocked_senders(self, engine):
        emails = [
            make_email(id="e1", sender_email="no-reply@teams.mail.microsoft"),
            make_email(id="e2", sender_email="director@org.com"),
        ]
        actionable, filtered = engine.process_inbox(emails)

        assert len(actionable) == 1
        assert actionable[0].id == "e2"
        assert len(filtered) == 1
        assert filtered[0].reason == "filtered_sender"

    def test_filters_out_calendar_invites(self, engine):
        emails = [
            make_email(id="e1", subject="Accepted: Weekly Sync"),
            make_email(id="e2", subject="Budget Review Notes"),
        ]
        actionable, filtered = engine.process_inbox(emails)

        assert len(actionable) == 1
        assert actionable[0].id == "e2"
        assert len(filtered) == 1
        assert filtered[0].reason == "calendar_invite"

    def test_assigns_correct_tiers(self, engine):
        emails = [
            make_email(id="e1", sender_email="ceo@org.com"),
            make_email(id="e2", sender_email="director@org.com"),
            make_email(id="e3", sender_email="manager@org.com"),
            make_email(id="e4", sender_email="random@gmail.com"),
        ]
        actionable, filtered = engine.process_inbox(emails)

        assert len(actionable) == 4
        assert actionable[0].tier == Tier.VVIP
        assert actionable[1].tier == Tier.IMPORTANT
        assert actionable[2].tier == Tier.STANDARD
        assert actionable[3].tier == Tier.DEFAULT

    def test_sorts_by_tier(self, engine):
        """Emails should be sorted by tier, highest priority first."""
        emails = [
            make_email(id="e1", sender_email="random@gmail.com"),  # Tier 4
            make_email(id="e2", sender_email="ceo@org.com"),       # Tier 1
            make_email(id="e3", sender_email="manager@org.com"),   # Tier 3
            make_email(id="e4", sender_email="director@org.com"),  # Tier 2
        ]
        actionable, _ = engine.process_inbox(emails)

        tiers = [e.tier for e in actionable]
        assert tiers == [Tier.VVIP, Tier.IMPORTANT, Tier.STANDARD, Tier.DEFAULT]

    def test_does_not_summarize_during_process(self, engine, mock_llm):
        """process_inbox should NOT call the LLM — summarization is separate."""
        emails = [make_email(id=f"e{i}") for i in range(5)]
        engine.process_inbox(emails)
        mock_llm.complete.assert_not_called()

    def test_summarize_batch(self, engine, mock_llm):
        """summarize_batch should summarize all emails in the list."""
        emails = [make_email(id=f"e{i}") for i in range(3)]
        engine.summarize_batch(emails)
        assert mock_llm.complete.call_count == 3
        for email in emails:
            assert email.summary is not None

    def test_does_not_summarize_filtered_emails(self, engine, mock_llm):
        """Filtered emails should not be summarized."""
        emails = [
            make_email(id="e1", sender_email="no-reply@teams.mail.microsoft"),
            make_email(id="e2", subject="Accepted: Standup"),
            make_email(id="e3", sender_email="director@org.com"),
        ]
        actionable, filtered = engine.process_inbox(emails)

        # process_inbox doesn't summarize at all
        assert mock_llm.complete.call_count == 0

        # Summarize only actionable
        engine.summarize_batch(actionable)
        assert mock_llm.complete.call_count == 1
        assert len(filtered) == 2

    def test_mixed_filters_and_tiers(self, engine):
        """Test a realistic mix of filtered and actionable emails."""
        emails = [
            make_email(id="e1", sender_email="no-reply@teams.mail.microsoft"),  # filtered
            make_email(id="e2", subject="Accepted: Standup"),                    # filtered
            make_email(id="e3", sender_email="ceo@org.com"),                     # Tier 1
            make_email(id="e4", sender_email="random@gmail.com"),                # Tier 4
            make_email(id="e5", sender_email="vp@org.com"),                      # Tier 2
        ]
        actionable, filtered = engine.process_inbox(emails)

        assert len(filtered) == 2
        assert len(actionable) == 3
        assert actionable[0].tier == Tier.VVIP      # CEO first
        assert actionable[1].tier == Tier.IMPORTANT  # VP second
        assert actionable[2].tier == Tier.DEFAULT    # Random last


# =============================================================================
# SUMMARIZATION TESTS
# =============================================================================

class TestSummarizeEmail:
    def test_successful_summary(self, engine, mock_llm):
        email = make_email()
        summary = engine.summarize_email(email)

        assert summary == "This is a test summary."
        assert email.summary == "This is a test summary."
        mock_llm.complete.assert_called_once()

        # Verify the call used the right purpose
        call_kwargs = mock_llm.complete.call_args
        assert call_kwargs.kwargs["purpose"] == "summarize"

    def test_summary_prompt_contains_email_fields(self, engine, mock_llm):
        email = make_email(
            subject="Q3 Budget Review",
            sender_name="Jane Smith",
            importance="high",
            body_preview="Please review the attached budget.",
        )
        engine.summarize_email(email)

        call_kwargs = mock_llm.complete.call_args
        user_prompt = call_kwargs.kwargs["user"]
        assert "Q3 Budget Review" in user_prompt
        assert "Jane Smith" in user_prompt
        assert "high" in user_prompt
        assert "Please review the attached budget" in user_prompt

    def test_summary_fallback_on_llm_error(self, engine, mock_llm):
        mock_llm.complete.side_effect = LLMError("API timeout")
        email = make_email(sender_name="Bob", subject="Important Update")

        summary = engine.summarize_email(email)

        assert "Bob" in summary
        assert "Important Update" in summary
        assert email.summary == summary

    def test_body_preview_truncated_to_500(self, engine, mock_llm):
        """Long body previews should be truncated to 500 chars in the prompt."""
        long_preview = "x" * 1000
        email = make_email(body_preview=long_preview)
        engine.summarize_email(email)

        call_kwargs = mock_llm.complete.call_args
        user_prompt = call_kwargs.kwargs["user"]
        # The prompt should contain at most 500 chars of preview
        assert "x" * 501 not in user_prompt


# =============================================================================
# DRAFT GENERATION TESTS — Style context fallback logic
# =============================================================================

class TestDraftReply:
    def _setup_draft_llm(self, mock_llm, draft_text="Thanks for your email."):
        mock_llm.complete.return_value = LLMResult(
            text=draft_text,
            input_tokens=200,
            output_tokens=80,
            total_tokens=280,
            input_cost=0.0006,
            output_cost=0.0012,
            cost=0.0018,
            latency_ms=1200,
            model="claude-sonnet-4-20250514",
        )

    def test_uses_specific_style_when_sent_emails_exist(self, engine, mock_llm):
        """If we have past emails to this sender, use 'specific' style."""
        self._setup_draft_llm(mock_llm)
        email = make_email(sender_email="director@org.com")
        sent_to_sender = [
            {"subject": "Re: Budget", "body": "Looks good, thanks."},
            {"subject": "FYI", "body": "Sharing this for your review."},
        ]

        result = engine.draft_reply(
            email=email,
            sent_to_sender=sent_to_sender,
            all_sent=[],
            user_name="Trevor",
        )

        assert result.style_source == "specific"
        assert result.style_email_count == 2

        # Verify the prompt contains the specific style block
        call_kwargs = mock_llm.complete.call_args
        user_prompt = call_kwargs.kwargs["user"]
        assert "PAST EMAILS TO THIS PERSON" in user_prompt
        assert "Looks good, thanks." in user_prompt

    def test_falls_back_to_general_style(self, engine, mock_llm):
        """If no emails to this sender, fall back to general style."""
        self._setup_draft_llm(mock_llm)
        email = make_email(sender_email="new.contact@external.com")
        general_sent = [
            {"subject": "Update", "body": "Here's the latest."},
        ]

        result = engine.draft_reply(
            email=email,
            sent_to_sender=[],  # No emails to this sender
            all_sent=general_sent,
            user_name="Trevor",
        )

        assert result.style_source == "general"
        assert result.style_email_count == 1

        call_kwargs = mock_llm.complete.call_args
        user_prompt = call_kwargs.kwargs["user"]
        assert "RECENT SENT EMAILS" in user_prompt

    def test_no_style_context_at_all(self, engine, mock_llm):
        """If no sent emails at all, draft without style context."""
        self._setup_draft_llm(mock_llm)
        email = make_email()

        result = engine.draft_reply(
            email=email,
            sent_to_sender=[],
            all_sent=[],
            user_name="Trevor",
        )

        assert result.style_source == "none"
        assert result.style_email_count == 0

        call_kwargs = mock_llm.complete.call_args
        user_prompt = call_kwargs.kwargs["user"]
        assert "PAST EMAILS" not in user_prompt

    def test_disclaimer_added_to_draft(self, engine, mock_llm):
        """Draft should include AI-generated disclaimer."""
        self._setup_draft_llm(mock_llm, "I'll review this and get back to you.")
        email = make_email()

        result = engine.draft_reply(
            email=email,
            sent_to_sender=[],
            all_sent=[],
            user_name="Trevor",
        )

        assert "AI-generated and reviewed by Trevor" in result.draft

    def test_disclaimer_not_doubled(self, engine, mock_llm):
        """If LLM already includes disclaimer, don't add another."""
        self._setup_draft_llm(
            mock_llm,
            "Reply text.\n\n*Note: This response was AI-generated and reviewed by Trevor*"
        )
        email = make_email()

        result = engine.draft_reply(
            email=email,
            sent_to_sender=[],
            all_sent=[],
            user_name="Trevor",
        )

        assert result.draft.count("AI-generated") == 1

    def test_user_guidance_included_in_prompt(self, engine, mock_llm):
        """Key points and context should appear in the prompt."""
        self._setup_draft_llm(mock_llm)
        email = make_email()

        engine.draft_reply(
            email=email,
            sent_to_sender=[],
            all_sent=[],
            user_name="Trevor",
            key_points="Agree to the meeting but suggest Thursday",
            additional_context="I'm out of office Monday and Tuesday",
        )

        call_kwargs = mock_llm.complete.call_args
        user_prompt = call_kwargs.kwargs["user"]
        assert "Agree to the meeting but suggest Thursday" in user_prompt
        assert "out of office Monday and Tuesday" in user_prompt

    def test_system_prompt_includes_user_name(self, engine, mock_llm):
        """System prompt should be personalized with user's name."""
        self._setup_draft_llm(mock_llm)
        email = make_email()

        engine.draft_reply(
            email=email,
            sent_to_sender=[],
            all_sent=[],
            user_name="Trevor",
        )

        call_kwargs = mock_llm.complete.call_args
        system_prompt = call_kwargs.kwargs["system"]
        assert "Trevor" in system_prompt

    def test_draft_uses_body_with_fallback_to_preview(self, engine, mock_llm):
        """If body is empty, body_preview should be used."""
        self._setup_draft_llm(mock_llm)
        email = make_email(body="", body_preview="Preview text here.")

        engine.draft_reply(
            email=email,
            sent_to_sender=[],
            all_sent=[],
            user_name="Trevor",
        )

        call_kwargs = mock_llm.complete.call_args
        user_prompt = call_kwargs.kwargs["user"]
        assert "Preview text here." in user_prompt

    def test_fallback_draft_on_llm_error(self, engine, mock_llm):
        """If LLM fails, return a safe fallback draft."""
        mock_llm.complete.side_effect = LLMError("API down")
        email = make_email(subject="Partnership Proposal")

        result = engine.draft_reply(
            email=email,
            sent_to_sender=[],
            all_sent=[],
            user_name="Trevor",
        )

        assert "Partnership Proposal" in result.draft
        assert "AI-generated" in result.draft
        assert result.style_source == "none"
        assert result.tokens_used == 0

    def test_returns_draft_response_type(self, engine, mock_llm):
        """Should return a proper DraftResponse object."""
        self._setup_draft_llm(mock_llm)
        email = make_email()

        result = engine.draft_reply(
            email=email,
            sent_to_sender=[],
            all_sent=[],
            user_name="Trevor",
        )

        assert isinstance(result, DraftResponse)
        assert result.tokens_used == 280

    def test_specific_style_preferred_over_general(self, engine, mock_llm):
        """When both specific and general emails are available, use specific."""
        self._setup_draft_llm(mock_llm)
        email = make_email()

        result = engine.draft_reply(
            email=email,
            sent_to_sender=[{"subject": "A", "body": "Specific tone."}],
            all_sent=[{"subject": "B", "body": "General tone."}],
            user_name="Trevor",
        )

        assert result.style_source == "specific"

        call_kwargs = mock_llm.complete.call_args
        user_prompt = call_kwargs.kwargs["user"]
        assert "Specific tone." in user_prompt
        assert "General tone." not in user_prompt