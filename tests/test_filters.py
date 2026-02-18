"""Tests for email content filters."""

import pytest
import textwrap
from app.agent.filters import is_calendar_invite, check_filters
from app.agent.schemas import Email
from app.agent.priority import TierConfig


def make_email(**overrides) -> Email:
    """Helper to create test Email objects with sensible defaults."""
    defaults = {
        "id": "test-id-123",
        "subject": "Regular email subject",
        "sender_name": "Test Sender",
        "sender_email": "test@example.com",
        "body_preview": "This is a regular email.",
        "body": "This is a regular email body.",
    }
    defaults.update(overrides)
    return Email(**defaults)


@pytest.fixture
def tier_config(tmp_path) -> TierConfig:
    """Create a TierConfig for filter tests."""
    yaml_content = textwrap.dedent("""\
        tier_1:
          emails:
            - "vip@example.com"
        tier_2:
          emails: []
        tier_3:
          emails: []
        filtered_senders:
          - "no-reply@teams.mail.microsoft"
          - "noreply@automated.com"
    """)
    yaml_file = tmp_path / "tiers.yaml"
    yaml_file.write_text(yaml_content)
    return TierConfig(str(yaml_file))


class TestCalendarInviteDetection:
    def test_accepted_meeting(self):
        email = make_email(subject="Accepted: Weekly Team Sync")
        assert is_calendar_invite(email) is True

    def test_declined_meeting(self):
        email = make_email(subject="Declined: Budget Review")
        assert is_calendar_invite(email) is True

    def test_tentative_meeting(self):
        email = make_email(subject="Tentative: 1:1 with Manager")
        assert is_calendar_invite(email) is True

    def test_canceled_meeting(self):
        email = make_email(subject="Canceled: All Hands")
        assert is_calendar_invite(email) is True

    def test_cancelled_british_spelling(self):
        email = make_email(subject="Cancelled: Strategy Review")
        assert is_calendar_invite(email) is True

    def test_updated_invitation(self):
        email = make_email(subject="Updated invitation: Project Kickoff")
        assert is_calendar_invite(email) is True

    def test_meeting_request(self):
        email = make_email(subject="Meeting Request: Quarterly Review")
        assert is_calendar_invite(email) is True

    def test_subject_case_insensitive(self):
        email = make_email(subject="ACCEPTED: Board Meeting")
        assert is_calendar_invite(email) is True

    def test_ics_calendar_format(self):
        email = make_email(body="BEGIN:VCALENDAR\nVERSION:2.0\n...")
        assert is_calendar_invite(email) is True

    def test_teams_meeting_link(self):
        email = make_email(body="Join Microsoft Teams Meeting https://teams.microsoft.com/l/meetup-join/...")
        assert is_calendar_invite(email) is True

    def test_zoom_link(self):
        email = make_email(body="Join Zoom Meeting https://zoom.us/j/123456789")
        assert is_calendar_invite(email) is True

    def test_google_meet_link(self):
        email = make_email(body="Join at https://meet.google.com/abc-defg-hij")
        assert is_calendar_invite(email) is True

    def test_body_preview_also_checked(self):
        email = make_email(body="", body_preview="Join Microsoft Teams Meeting")
        assert is_calendar_invite(email) is True

    def test_meeting_message_type_field(self):
        email = make_email(meeting_message_type="meetingRequest")
        assert is_calendar_invite(email) is True

    def test_regular_email_not_filtered(self):
        email = make_email(
            subject="Project Update",
            body="Here's the latest status on the vaccine initiative.",
        )
        assert is_calendar_invite(email) is False

    def test_email_mentioning_meeting_not_filtered(self):
        email = make_email(
            subject="Let's schedule a meeting",
            body="Can we find time to discuss the Q3 strategy?",
        )
        assert is_calendar_invite(email) is False

    def test_email_with_meeting_in_subject_not_filtered(self):
        email = make_email(
            subject="Notes from today's meeting",
            body="Here are the action items we discussed.",
        )
        assert is_calendar_invite(email) is False


class TestCheckFilters:
    def test_filtered_sender(self, tier_config):
        email = make_email(
            sender_email="no-reply@teams.mail.microsoft",
            sender_name="Microsoft Teams",
        )
        result = check_filters(email, tier_config)
        assert result.filtered is True
        assert result.reason == "filtered_sender"

    def test_calendar_invite_filtered(self, tier_config):
        email = make_email(subject="Accepted: Weekly Sync")
        result = check_filters(email, tier_config)
        assert result.filtered is True
        assert result.reason == "calendar_invite"

    def test_normal_email_passes(self, tier_config):
        email = make_email(
            subject="Budget proposal",
            body="Please review the attached proposal.",
        )
        result = check_filters(email, tier_config)
        assert result.filtered is False
        assert result.reason is None

    def test_sender_filter_takes_precedence(self, tier_config):
        email = make_email(
            subject="Accepted: Meeting",
            sender_email="no-reply@teams.mail.microsoft",
            sender_name="Microsoft Teams",
        )
        result = check_filters(email, tier_config)
        assert result.filtered is True
        assert result.reason == "filtered_sender"