"""
Audit logging for tracking user actions.

SECURITY: Never log email content, subjects, body text, recipient addresses,
LLM prompts, or LLM responses. Only log metadata.

Usage:
    from app.logging.audit import audit
    audit.info("email.summarized", email_id="AAMk...", latency_ms=1200)
"""

import logging
from typing import Any


class AuditLogger:
    """Thin wrapper around logging that enforces structured action fields."""

    def __init__(self):
        self._logger = logging.getLogger("audit")

    def info(self, action: str, **fields: Any) -> None:
        self._logger.info(action, extra={"action": action, **fields})

    def warning(self, action: str, **fields: Any) -> None:
        self._logger.warning(action, extra={"action": action, **fields})

    def error(self, action: str, **fields: Any) -> None:
        self._logger.error(action, extra={"action": action, **fields})


audit = AuditLogger()