"""Tests for prompt templates and helper functions."""

from app.agent.prompts import (
    parse_summary,
    build_style_block,
    format_style_context,
    ensure_disclaimer,
    SUMMARIZE_SYSTEM,
    SUMMARIZE_USER,
    DRAFT_SYSTEM,
    DRAFT_USER,
)


class TestParseSummary:
    def test_standard_format(self):
        raw = "SUMMARY: This is an important email about the Q3 budget review."
        assert parse_summary(raw) == "This is an important email about the Q3 budget review."

    def test_with_extra_whitespace(self):
        raw = "SUMMARY:   Spaced out summary.  "
        assert parse_summary(raw) == "Spaced out summary."

    def test_with_extra_content_after_summary(self):
        raw = "SUMMARY: Main point here.\n\nSome extra analysis the LLM added."
        assert parse_summary(raw) == "Main point here."

    def test_no_summary_marker(self):
        """If LLM doesn't follow format, return the raw text."""
        raw = "This email is about vaccines."
        assert parse_summary(raw) == "This email is about vaccines."

    def test_empty_after_marker(self):
        raw = "SUMMARY: "
        result = parse_summary(raw)
        assert result == ""


class TestBuildStyleBlock:
    def test_specific_style(self):
        block = build_style_block(
            style_source="specific",
            style_context="Subject: Hello\nHi Mark, thanks for the update.",
            user_name="Trevor",
        )
        assert "TREVOR'S PAST EMAILS TO THIS PERSON" in block
        assert "Hi Mark, thanks for the update." in block
        assert "THIS specific person" in block

    def test_general_style(self):
        block = build_style_block(
            style_source="general",
            style_context="Subject: FYI\nLooping you in on this.",
            user_name="Trevor",
        )
        assert "TREVOR'S RECENT SENT EMAILS" in block
        assert "general writing style" in block

    def test_none_style(self):
        block = build_style_block(
            style_source="none",
            style_context="",
            user_name="Trevor",
        )
        assert block == ""

    def test_specific_but_empty_context(self):
        """If style_source is specific but context is empty, return empty."""
        block = build_style_block(
            style_source="specific",
            style_context="",
            user_name="Trevor",
        )
        assert block == ""


class TestFormatStyleContext:
    def test_basic_formatting(self):
        emails = [
            {"subject": "Hello", "body": "Hi, how are you?"},
            {"subject": "Follow up", "body": "Just checking in."},
        ]
        result = format_style_context(emails)
        assert "Subject: Hello" in result
        assert "Hi, how are you?" in result
        assert "Subject: Follow up" in result

    def test_html_stripped(self):
        emails = [{"subject": "Test", "body": "<p>Hello <b>world</b></p>"}]
        result = format_style_context(emails)
        assert "<p>" not in result
        assert "<b>" not in result
        assert "Hello world" in result

    def test_max_chars_respected(self):
        emails = [
            {"subject": f"Email {i}", "body": "x" * 500}
            for i in range(20)
        ]
        result = format_style_context(emails, max_chars=1000)
        assert len(result) <= 1200  # Some overhead from formatting

    def test_empty_list(self):
        assert format_style_context([]) == ""

    def test_empty_bodies_skipped(self):
        emails = [
            {"subject": "Empty", "body": ""},
            {"subject": "Has content", "body": "Real content here."},
        ]
        result = format_style_context(emails)
        assert "Empty" not in result
        assert "Real content here." in result


class TestEnsureDisclaimer:
    def test_adds_disclaimer(self):
        draft = "Thanks for the update. I'll review this week."
        result = ensure_disclaimer(draft, "Trevor")
        assert "AI-generated and reviewed by Trevor" in result
        assert result.startswith("Thanks for the update")

    def test_skips_if_already_present(self):
        draft = "Here's my reply.\n\n*Note: This response was AI-generated and reviewed by Trevor*"
        result = ensure_disclaimer(draft, "Trevor")
        # Should not double-add
        assert result.count("AI-generated") == 1

    def test_case_insensitive_check(self):
        draft = "Reply text.\n\nThis was ai-generated."
        result = ensure_disclaimer(draft, "Trevor")
        assert result.count("ai-generated") == 1  # Original only, not added again


class TestPromptTemplatesExist:
    """Smoke tests to verify templates have expected placeholders."""

    def test_summarize_system_is_string(self):
        assert isinstance(SUMMARIZE_SYSTEM, str)
        assert len(SUMMARIZE_SYSTEM) > 0

    def test_summarize_user_has_placeholders(self):
        assert "{subject}" in SUMMARIZE_USER
        assert "{sender_name}" in SUMMARIZE_USER
        assert "{importance}" in SUMMARIZE_USER
        assert "{body_preview}" in SUMMARIZE_USER

    def test_draft_system_has_user_name(self):
        assert "{user_name}" in DRAFT_SYSTEM

    def test_draft_user_has_placeholders(self):
        assert "{subject}" in DRAFT_USER
        assert "{sender_name}" in DRAFT_USER
        assert "{body}" in DRAFT_USER
        assert "{key_points}" in DRAFT_USER
        assert "{additional_context}" in DRAFT_USER
        assert "{style_block}" in DRAFT_USER
        assert "{user_name}" in DRAFT_USER