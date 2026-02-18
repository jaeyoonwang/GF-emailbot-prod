"""
All LLM prompt templates for the email agent.

This is the single file to edit when you need to change how the AI
summarizes emails or drafts responses. No other code changes needed.

IMPORTANT:
- Never put actual email content in this file — these are templates.
- The {placeholders} are filled in at runtime by the agent engine.
- Keep prompts focused and concise to minimize token usage and cost.
"""

# =============================================================================
# SYSTEM PROMPTS — Define the AI's role and constraints
# =============================================================================

SUMMARIZE_SYSTEM = (
    "You are an email assistant that summarizes emails concisely. "
    "Always respond in the exact format requested. "
    "Be direct and factual — no filler phrases."
)

DRAFT_SYSTEM = (
    "You are {user_name}'s email assistant. "
    "Your ONLY job is to draft email responses. "
    "NEVER ask for clarification or say you need more information. "
    "ALWAYS output a ready-to-send email draft based on whatever information is provided. "
    "Be professional, concise, and helpful."
)

# =============================================================================
# USER PROMPTS — The actual instructions sent with each request
# =============================================================================

SUMMARIZE_USER = """\
Summarize the following email in 2-3 sentences:

Email Subject: {subject}
Sender: {sender_name}
Importance (from Outlook): {importance}
Preview: {body_preview}

Respond in this exact format:
SUMMARY: [2-3 sentence summary]"""

DRAFT_USER = """\
Draft an email response to the following email.

Original Email:
Subject: {subject}
From: {sender_name}
Body: {body}

Additional guidance (if any):
- Key points: {key_points}
- Context: {additional_context}
{style_block}
IMPORTANT: You MUST draft the email response now. Do not ask for clarification \
or more information. Work with what you have. If the email body is truncated, \
respond to what's visible. If no key points are specified, draft a professional, \
helpful response based on the email content.

Output ONLY the email body text. No subject line, no "Dear X" greeting unless \
appropriate, no "Best regards" signature unless the style examples show \
{user_name} uses them."""

# =============================================================================
# STYLE CONTEXT BLOCKS — Inserted into DRAFT_USER when past emails are available
# =============================================================================

STYLE_BLOCK_SPECIFIC = """\

--- {user_name_upper}'S PAST EMAILS TO THIS PERSON (use for style/tone guidance) ---
{style_context}
--- END PAST EMAILS ---

Match {user_name}'s tone, style, and formatting from the examples above. \
This shows how {user_name} typically communicates with THIS specific person. \
Do NOT add sign-offs like 'Best regards' unless {user_name} typically uses them."""

STYLE_BLOCK_GENERAL = """\

--- {user_name_upper}'S RECENT SENT EMAILS (use for general style/tone guidance) ---
{style_context}
--- END PAST EMAILS ---

These are {user_name}'s recent emails to various people. Use them to understand \
their general writing style, tone, and formatting preferences. Adapt the tone \
appropriately for the current recipient. \
Do NOT add sign-offs like 'Best regards' unless {user_name} typically uses them."""

# No style context available
STYLE_BLOCK_NONE = ""

# =============================================================================
# RESPONSE PARSING — How we extract structured data from LLM responses
# =============================================================================

def parse_summary(raw_response: str) -> str:
    """
    Extract the summary from the LLM's response.

    Expects format: "SUMMARY: [text]"
    Falls back to the raw response if the format doesn't match.
    """
    if "SUMMARY:" in raw_response:
        # Take everything after "SUMMARY:" up to the next newline
        after_marker = raw_response.split("SUMMARY:", 1)[1].strip()
        # Take first paragraph (in case the LLM added extra content)
        first_paragraph = after_marker.split("\n\n")[0].strip()
        return first_paragraph if first_paragraph else after_marker
    return raw_response.strip()


def build_style_block(
    style_source: str,
    style_context: str,
    user_name: str,
) -> str:
    """
    Build the style context block to insert into the draft prompt.

    Args:
        style_source: "specific", "general", or "none"
        style_context: Formatted text of past sent emails
        user_name: The user's display name

    Returns:
        Formatted style block string, or empty string if no context.
    """
    if style_source == "specific" and style_context:
        return STYLE_BLOCK_SPECIFIC.format(
            user_name_upper=user_name.upper(),
            user_name=user_name,
            style_context=style_context,
        )
    elif style_source == "general" and style_context:
        return STYLE_BLOCK_GENERAL.format(
            user_name_upper=user_name.upper(),
            user_name=user_name,
            style_context=style_context,
        )
    return STYLE_BLOCK_NONE


def format_style_context(sent_emails: list[dict], max_chars: int = 6000) -> str:
    """
    Format sent emails into a text block for style context.

    Takes a list of sent email dicts and creates a condensed text
    representation, limited to max_chars to control token usage.

    Args:
        sent_emails: List of sent email dicts with 'subject' and 'body' keys.
        max_chars: Maximum total characters for the style context.

    Returns:
        Formatted string of past emails, or empty string if none.
    """
    import re

    if not sent_emails:
        return ""

    parts = []
    total = 0

    for email in sent_emails:
        body = email.get("body", "") or email.get("body_preview", "")
        # Strip HTML tags if present
        body = re.sub(r"<[^>]+>", "", body).strip()

        if not body:
            continue

        entry = f"---\nSubject: {email.get('subject', 'No Subject')}\n{body}\n"

        if total + len(entry) > max_chars:
            break

        parts.append(entry)
        total += len(entry)

    return "\n".join(parts)


# =============================================================================
# DISCLAIMER — Appended to all AI-generated drafts
# =============================================================================

def get_disclaimer(user_name: str) -> str:
    """Get the AI-generated disclaimer text."""
    return f"Note: This response was AI-generated and reviewed by {user_name}"


def ensure_disclaimer(draft: str, user_name: str) -> str:
    """
    Ensure the draft includes the AI-generated disclaimer.

    If the draft already mentions "ai-generated", skip.
    Otherwise, append the disclaimer in italics.
    """
    if "ai-generated" in draft.lower():
        return draft
    disclaimer = get_disclaimer(user_name)
    return f"{draft}\n\n*{disclaimer}*"