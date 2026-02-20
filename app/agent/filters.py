"""
Email content filters.

Determines which emails should be hidden from the user because they
are automated messages (calendar invites, Teams notifications, etc.)
that don't require a human response.

Usage:
    from app.agent.filters import check_filters
    result = check_filters(email, tier_config)
    if result.filtered:
        print(f"Filtered: {result.detail}")
"""

from app.agent.schemas import Email, FilterResult
from app.agent.priority import TierConfig


# Calendar-related subject prefixes (matched case-insensitively).
CALENDAR_SUBJECT_PREFIXES = (
    "accepted:",
    "declined:",
    "tentative:",
    "canceled:",
    "cancelled:",
    "updated invitation:",
    "invitation:",
    "meeting request:",
    "meeting canceled:",
    "meeting cancelled:",
)

# Body content patterns indicating calendar invites (matched case-insensitively).
CALENDAR_BODY_PATTERNS = (
    "begin:vcalendar",
    "microsoft teams meeting",
    "join microsoft teams meeting",
    "teams.microsoft.com/l/meetup-join",
    "zoom.us/j/",
    "join zoom meeting",
    "webex.com/meet",
    "meet.google.com/",
    "calendly.com/",
    "when: ",
    "location: microsoft teams",
    "join the meeting",
    "click here to join the meeting",
)


def is_calendar_invite(email: Email) -> bool:
    """
    Determine if an email is an automated calendar invite.

    Checks:
    1. Graph API meetingMessageType field (most reliable)
    2. Calendar-related subject prefixes (Accepted:, Declined:, etc.)
    3. Calendar/meeting content in the body (ICS data, Teams/Zoom links)

    Does NOT filter emails that merely discuss scheduling.
    """
    # Graph API meeting message type
    if email.meeting_message_type:
        return True

    # Subject line patterns
    subject_lower = email.subject.lower()
    if any(subject_lower.startswith(p) for p in CALENDAR_SUBJECT_PREFIXES):
        return True

    # Body content patterns
    combined_body = f"{email.body} {email.body_preview} {email.body_html}".lower()
    if any(p in combined_body for p in CALENDAR_BODY_PATTERNS):
        return True

    return False


def check_filters(email: Email, tier_config: TierConfig) -> FilterResult:
    """
    Run all filters on an email.

    Returns FilterResult with filtered=True if the email should be hidden.
    Filter order: sender filter first, then calendar invite check.
    """
    if tier_config.is_filtered_sender(email.sender_email):
        return FilterResult(
            filtered=True,
            reason="filtered_sender",
            detail=f"Blocked sender: {email.sender_name} ({email.sender_email})",
        )

    if is_calendar_invite(email):
        return FilterResult(
            filtered=True,
            reason="calendar_invite",
            detail=f"Calendar invite: {email.sender_name} - {email.subject[:50]}",
        )

    return FilterResult(filtered=False)