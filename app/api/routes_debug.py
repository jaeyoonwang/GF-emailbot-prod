"""
Temporary live-data diagnostic page.

Tests the real Outlook connection with your authenticated account and
displays results on screen. Remove this file and its import in main.py
when done testing.

Access at: /debug
"""

import time
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.auth.dependencies import require_auth
from app.auth.session import SessionData
from app.graph.client import GraphClient
from app.agent.engine import AgentEngine
from app.agent.filters import check_filters
from app.agent.priority import TierConfig
from app.llm.client import LLMClient
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/debug", tags=["debug"])


def _row(label: str, value: str, ok: bool = True) -> str:
    color = "#166534" if ok else "#991b1b"
    bg = "#f0fdf4" if ok else "#fef2f2"
    return (
        f'<tr style="background:{bg}">'
        f'<td style="padding:8px 12px;font-weight:600;color:#374151">{label}</td>'
        f'<td style="padding:8px 12px;color:{color}">{value}</td>'
        f'</tr>'
    )


def _section(title: str, rows: list[str]) -> str:
    return (
        f'<h2 style="margin:32px 0 8px;font-size:16px;color:#1f2937">{title}</h2>'
        f'<table style="width:100%;border-collapse:collapse;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden">'
        + "".join(rows) +
        f'</table>'
    )


@router.get("", response_class=HTMLResponse)
async def debug_page(
    request: Request,
    session: SessionData = Depends(require_auth),
):
    """
    Run live diagnostics against the authenticated user's Outlook account.
    Tests inbox fetch, filtering, tier assignment, sent email lookup, and LLM.
    """
    graph = GraphClient(access_token=session.access_token)
    tiers = TierConfig(settings.tier_config_path)

    results = []
    email_samples = []

    # -------------------------------------------------------------------------
    # 1. INBOX FETCH
    # -------------------------------------------------------------------------
    t0 = time.monotonic()
    try:
        emails = graph.fetch_inbox(time_window="24 hours", max_emails=200)
        inbox_ms = int((time.monotonic() - t0) * 1000)

        tier_counts = {}
        actionable = []
        filtered_reasons: dict[str, int] = {}

        for email in emails:
            fr = check_filters(email, tiers)
            if fr.filtered:
                filtered_reasons[fr.reason or "unknown"] = filtered_reasons.get(fr.reason or "unknown", 0) + 1
            else:
                email.tier = tiers.get_tier(email.sender_email)
                tier_counts[email.tier.name] = tier_counts.get(email.tier.name, 0) + 1
                actionable.append(email)

        filter_summary = ", ".join(f"{r}: {c}" for r, c in filtered_reasons.items()) or "none"
        tier_summary = ", ".join(f"{t}: {c}" for t, c in sorted(tier_counts.items())) or "none"

        inbox_rows = [
            _row("Status", "✓ Connected to Outlook"),
            _row("Time window", "Last 24 hours"),
            _row("Total emails fetched", str(len(emails))),
            _row("Actionable (passed filters)", str(len(actionable))),
            _row("Filtered out", f"{len(emails) - len(actionable)}  ({filter_summary})"),
            _row("Tier breakdown (actionable)", tier_summary),
            _row("Fetch latency", f"{inbox_ms} ms"),
        ]

        # Collect first 5 actionable for the sample table
        email_samples = actionable[:5]

    except Exception as e:
        inbox_rows = [_row("Status", f"✗ Error: {e}", ok=False)]

    results.append(_section("1. Inbox Fetch (last 24 hours)", inbox_rows))

    # -------------------------------------------------------------------------
    # 2. SENT EMAILS — general style context
    # -------------------------------------------------------------------------
    t0 = time.monotonic()
    try:
        recent_sent = graph.fetch_recent_sent(max_emails=100)
        sent_ms = int((time.monotonic() - t0) * 1000)
        sent_rows = [
            _row("Status", "✓ Sent folder accessible"),
            _row("Recent sent emails retrieved", str(len(recent_sent))),
            _row("Cap", "100 (fetch stops here even if more exist)"),
            _row("Fetch latency", f"{sent_ms} ms"),
        ]
        if len(recent_sent) == 100:
            sent_rows.append(_row("Note", "Exactly 100 returned — you have ≥ 100 sent emails"))
    except Exception as e:
        sent_rows = [_row("Status", f"✗ Error: {e}", ok=False)]

    results.append(_section("2. Recent Sent Emails (general style context)", sent_rows))

    # -------------------------------------------------------------------------
    # 3. SENT EMAILS — specific sender lookup (uses first actionable sender)
    # -------------------------------------------------------------------------
    if email_samples:
        test_sender = email_samples[0].sender_email
        t0 = time.monotonic()
        try:
            sent_to = graph.fetch_sent_to_recipient(test_sender, max_emails=100)
            specific_ms = int((time.monotonic() - t0) * 1000)
            specific_rows = [
                _row("Test sender", test_sender),
                _row("Status", "✓ Lookup complete"),
                _row("Past emails found to this sender", str(len(sent_to))),
                _row("Style that would be used",
                     "Specific (personalized)" if sent_to else "General (fallback — no past emails to this sender)"),
                _row("Pages scanned", "up to 5 pages × 50 = 250 sent emails"),
                _row("Lookup latency", f"{specific_ms} ms"),
            ]
        except Exception as e:
            specific_rows = [_row("Status", f"✗ Error: {e}", ok=False)]
    else:
        specific_rows = [_row("Skipped", "No actionable emails in inbox to test with")]

    results.append(_section("3. Sent-to-Sender Lookup (specific style context)", specific_rows))

    # -------------------------------------------------------------------------
    # 4. LLM CONNECTIVITY
    # -------------------------------------------------------------------------
    t0 = time.monotonic()
    try:
        llm = LLMClient()
        result = llm.complete(
            system="You are a test assistant.",
            user="Reply with exactly: LLM_OK",
            max_tokens=10,
            purpose="debug",
        )
        llm_ms = int((time.monotonic() - t0) * 1000)
        llm_ok = "LLM_OK" in result.text
        llm_rows = [
            _row("Status", "✓ Anthropic API reachable" if llm_ok else "⚠ Reachable but unexpected response", ok=llm_ok),
            _row("Model", settings.anthropic_model),
            _row("Response", result.text),
            _row("Tokens used", f"{result.total_tokens} ({result.input_tokens} in / {result.output_tokens} out)"),
            _row("Cost", f"${result.cost:.6f}"),
            _row("Latency", f"{llm_ms} ms"),
        ]
    except Exception as e:
        llm_rows = [_row("Status", f"✗ Error: {e}", ok=False)]

    results.append(_section("4. LLM Connectivity (Anthropic)", llm_rows))

    # -------------------------------------------------------------------------
    # 5. SAMPLE EMAILS TABLE
    # -------------------------------------------------------------------------
    if email_samples:
        sample_rows_html = ""
        for e in email_samples:
            sample_rows_html += (
                f'<tr style="border-top:1px solid #e5e7eb">'
                f'<td style="padding:6px 10px;font-size:12px">{e.sender_name}<br>'
                f'<span style="color:#6b7280;font-size:11px">{e.sender_email}</span></td>'
                f'<td style="padding:6px 10px;font-size:12px">{e.subject[:60]}{"..." if len(e.subject)>60 else ""}</td>'
                f'<td style="padding:6px 10px;font-size:12px;text-align:center">{e.tier.name if e.tier else "—"}</td>'
                f'<td style="padding:6px 10px;font-size:12px">{"Unread" if not e.is_read else "Read"}</td>'
                f'</tr>'
            )
        sample_section = (
            '<h2 style="margin:32px 0 8px;font-size:16px;color:#1f2937">5. First 5 Actionable Emails (sample)</h2>'
            '<table style="width:100%;border-collapse:collapse;border:1px solid #e5e7eb">'
            '<thead><tr style="background:#f9fafb">'
            '<th style="padding:8px 10px;text-align:left;font-size:12px;color:#6b7280">Sender</th>'
            '<th style="padding:8px 10px;text-align:left;font-size:12px;color:#6b7280">Subject</th>'
            '<th style="padding:8px 10px;text-align:left;font-size:12px;color:#6b7280">Tier</th>'
            '<th style="padding:8px 10px;text-align:left;font-size:12px;color:#6b7280">Status</th>'
            '</tr></thead><tbody>'
            + sample_rows_html +
            '</tbody></table>'
        )
        results.append(sample_section)

    graph.close()

    # -------------------------------------------------------------------------
    # RENDER PAGE
    # -------------------------------------------------------------------------
    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Live Diagnostics — Trevor Email Assistant</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           margin: 0; padding: 32px; background: #f9fafb; color: #111827; }}
    h1 {{ font-size: 20px; font-weight: 700; margin-bottom: 4px; }}
    .meta {{ font-size: 13px; color: #6b7280; margin-bottom: 24px; }}
    .warn {{ background: #fffbeb; border: 1px solid #f59e0b; border-radius: 6px;
             padding: 10px 14px; font-size: 13px; color: #92400e; margin-bottom: 24px; }}
  </style>
</head>
<body>
  <h1>Live Diagnostics — Trevor Email Assistant</h1>
  <p class="meta">Authenticated as: <strong>{session.user_email or session.user_name}</strong>
     &nbsp;|&nbsp; Run at: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}</p>
  <div class="warn">⚠ This page is for testing only. Remove <code>routes_debug.py</code>
     and its import in <code>main.py</code> when done.</div>
  {"".join(results)}
  <p style="margin-top:40px;font-size:12px;color:#9ca3af">
    <a href="/" style="color:#6b7280">← Back to dashboard</a>
  </p>
</body>
</html>"""

    return HTMLResponse(content=html)
