"""
Structured JSON logging configuration.

Usage:
    # At app startup (once):
    from app.logging.config import setup_logging
    setup_logging()

    # In any module:
    import logging
    logger = logging.getLogger(__name__)
    logger.info("something happened", extra={"email_id": "AAMk..."})
"""

import logging
import json
import sys
from datetime import datetime, timezone
from contextvars import ContextVar


# Context variables — set once per request in middleware,
# automatically included in every log line within that request.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
current_user_var: ContextVar[str] = ContextVar("current_user", default="anonymous")


class JSONFormatter(logging.Formatter):
    """Formats every log record as a single JSON line."""

    INTERNAL_FIELDS = {
        "name", "msg", "args", "created", "relativeCreated", "exc_info",
        "exc_text", "stack_info", "lineno", "funcName", "pathname",
        "filename", "module", "thread", "threadName", "process",
        "processName", "msecs", "levelname", "levelno", "message",
        "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        log = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
            "user": current_user_var.get(),
        }

        for key, val in record.__dict__.items():
            if key not in self.INTERNAL_FIELDS and key not in log:
                log[key] = val

        if record.exc_info and record.exc_info[0] is not None:
            log["exception_type"] = record.exc_info[0].__name__
            log["exception_message"] = str(record.exc_info[1])
            log["traceback"] = self.formatException(record.exc_info)

        return json.dumps(log, default=str)


def setup_logging(level: str = "info") -> None:
    """Configure the root logger to output structured JSON to stdout."""
    root = logging.getLogger()
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("msal").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)