"""Tests for the structured logging system."""

import json
import logging
from app.logging.config import setup_logging, request_id_var, current_user_var
from app.logging.audit import audit


def test_json_format(capsys):
    """Log output should be valid JSON with expected fields."""
    setup_logging(level="debug")
    logger = logging.getLogger("test")
    logger.info("test message")

    captured = capsys.readouterr()
    log = json.loads(captured.out.strip())

    assert log["level"] == "info"
    assert log["message"] == "test message"
    assert log["logger"] == "test"
    assert "timestamp" in log
    assert "request_id" in log
    assert "user" in log


def test_context_vars_appear_in_log(capsys):
    """Context variables should be included in every log line."""
    setup_logging(level="debug")
    logger = logging.getLogger("test")

    req_token = request_id_var.set("abc123")
    user_token = current_user_var.set("trevor@org.com")

    try:
        logger.info("user action")
        captured = capsys.readouterr()
        log = json.loads(captured.out.strip())

        assert log["request_id"] == "abc123"
        assert log["user"] == "trevor@org.com"
    finally:
        request_id_var.reset(req_token)
        current_user_var.reset(user_token)


def test_extra_fields(capsys):
    """Extra kwargs should appear as top-level fields in the JSON."""
    setup_logging(level="debug")
    logger = logging.getLogger("test")
    logger.info("email processed", extra={"email_id": "AAMk123", "latency_ms": 450})

    captured = capsys.readouterr()
    log = json.loads(captured.out.strip())

    assert log["email_id"] == "AAMk123"
    assert log["latency_ms"] == 450


def test_exception_logging(capsys):
    """Exceptions should include type, message, and traceback."""
    setup_logging(level="debug")
    logger = logging.getLogger("test")

    try:
        raise ValueError("something went wrong")
    except ValueError:
        logger.exception("operation failed")

    captured = capsys.readouterr()
    log = json.loads(captured.out.strip())

    assert log["level"] == "error"
    assert log["exception_type"] == "ValueError"
    assert log["exception_message"] == "something went wrong"
    assert "traceback" in log


def test_audit_info(capsys):
    """Audit logger should produce structured JSON with action field."""
    setup_logging(level="debug")
    audit.info("email.summarized", email_id="AAMk456", latency_ms=1200)

    captured = capsys.readouterr()
    log = json.loads(captured.out.strip())

    assert log["level"] == "info"
    assert log["action"] == "email.summarized"
    assert log["email_id"] == "AAMk456"
    assert log["latency_ms"] == 1200


def test_audit_error(capsys):
    """Audit error should log at error level."""
    setup_logging(level="debug")
    audit.error("draft.failed", email_id="AAMk789", error_type="timeout")

    captured = capsys.readouterr()
    log = json.loads(captured.out.strip())

    assert log["level"] == "error"
    assert log["action"] == "draft.failed"
    assert log["error_type"] == "timeout"


def test_default_context_values(capsys):
    """Without middleware setting context, defaults should appear."""
    setup_logging(level="debug")
    request_id_var.set("-")
    current_user_var.set("anonymous")

    logger = logging.getLogger("test")
    logger.info("no context")

    captured = capsys.readouterr()
    log = json.loads(captured.out.strip())

    assert log["request_id"] == "-"
    assert log["user"] == "anonymous"