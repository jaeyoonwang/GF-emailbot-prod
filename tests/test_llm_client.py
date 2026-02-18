"""
Tests for the LLM client wrapper.

Uses mocked Anthropic API responses to test retry logic, cost calculation,
error handling, and session tracking without making real API calls.
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from app.llm.client import LLMClient, LLMError, LLMResult
from app.logging.config import setup_logging

import anthropic


# --- Helpers to create mock responses ---

def make_mock_response(text="Hello", input_tokens=100, output_tokens=50):
    """Create a mock Anthropic API response."""
    response = MagicMock()
    response.content = [MagicMock(text=text)]
    response.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)
    return response


def make_client_with_mock(**kwargs) -> tuple[LLMClient, MagicMock]:
    """Create an LLMClient with a mocked Anthropic client inside."""
    with patch("app.llm.client.anthropic.Anthropic") as mock_cls:
        mock_anthropic = MagicMock()
        mock_cls.return_value = mock_anthropic
        client = LLMClient(
            api_key="test-key",
            model="claude-sonnet-4-20250514",
            **kwargs,
        )
        return client, mock_anthropic


# --- Tests ---

class TestSuccessfulCalls:
    def test_basic_completion(self):
        """A successful call should return an LLMResult with correct fields."""
        setup_logging("debug")
        client, mock = make_client_with_mock()
        mock.messages.create.return_value = make_mock_response(
            text="This is a summary.",
            input_tokens=150,
            output_tokens=40,
        )

        result = client.complete(
            system="You are helpful.",
            user="Summarize this.",
            max_tokens=200,
            purpose="summarize",
        )

        assert isinstance(result, LLMResult)
        assert result.text == "This is a summary."
        assert result.input_tokens == 150
        assert result.output_tokens == 40
        assert result.total_tokens == 190
        assert result.latency_ms >= 0
        assert result.model == "claude-sonnet-4-20250514"

    def test_cost_calculation(self):
        """Cost should be calculated based on token counts and pricing."""
        setup_logging("debug")
        client, mock = make_client_with_mock()
        # 1000 input tokens at $3/1M = $0.003
        # 500 output tokens at $15/1M = $0.0075
        mock.messages.create.return_value = make_mock_response(
            input_tokens=1000,
            output_tokens=500,
        )

        result = client.complete(system="test", user="test", purpose="test")

        assert abs(result.input_cost - 0.003) < 0.0001
        assert abs(result.output_cost - 0.0075) < 0.0001
        assert abs(result.cost - 0.0105) < 0.0001

    def test_text_is_stripped(self):
        """Response text should be stripped of whitespace."""
        setup_logging("debug")
        client, mock = make_client_with_mock()
        mock.messages.create.return_value = make_mock_response(text="  hello world  ")

        result = client.complete(system="test", user="test", purpose="test")

        assert result.text == "hello world"


class TestSessionTracking:
    def test_session_cost_accumulates(self):
        """Multiple calls should accumulate session totals."""
        setup_logging("debug")
        client, mock = make_client_with_mock()
        mock.messages.create.return_value = make_mock_response(
            input_tokens=1000, output_tokens=500
        )

        client.complete(system="test", user="test", purpose="test")
        client.complete(system="test", user="test", purpose="test")

        stats = client.get_session_stats()
        assert stats["total_calls"] == 2
        assert stats["total_input_tokens"] == 2000
        assert stats["total_output_tokens"] == 1000
        assert stats["total_cost_usd"] > 0

    def test_session_reset(self):
        """Reset should clear all session counters."""
        setup_logging("debug")
        client, mock = make_client_with_mock()
        mock.messages.create.return_value = make_mock_response()

        client.complete(system="test", user="test", purpose="test")
        client.reset_session_stats()

        stats = client.get_session_stats()
        assert stats["total_calls"] == 0
        assert stats["total_cost_usd"] == 0


class TestRetryLogic:
    def test_retry_on_rate_limit(self):
        """Rate limit errors should be retried."""
        setup_logging("debug")
        client, mock = make_client_with_mock(max_retries=3, timeout_seconds=5)

        # Fail twice with rate limit, succeed on third
        mock.messages.create.side_effect = [
            anthropic.RateLimitError(
                message="rate limited",
                response=MagicMock(status_code=429, headers={}),
                body={"error": {"message": "rate limited", "type": "rate_limit_error"}},
            ),
            anthropic.RateLimitError(
                message="rate limited",
                response=MagicMock(status_code=429, headers={}),
                body={"error": {"message": "rate limited", "type": "rate_limit_error"}},
            ),
            make_mock_response(text="success after retries"),
        ]

        result = client.complete(system="test", user="test", purpose="test")
        assert result.text == "success after retries"
        assert mock.messages.create.call_count == 3

    def test_retry_on_server_error(self):
        """5xx server errors should be retried."""
        setup_logging("debug")
        client, mock = make_client_with_mock(max_retries=2)

        mock.messages.create.side_effect = [
            anthropic.APIStatusError(
                message="server error",
                response=MagicMock(status_code=500, headers={}),
                body={"error": {"message": "server error", "type": "server_error"}},
            ),
            make_mock_response(text="recovered"),
        ]

        result = client.complete(system="test", user="test", purpose="test")
        assert result.text == "recovered"

    def test_retry_on_timeout(self):
        """Timeout errors should be retried."""
        setup_logging("debug")
        client, mock = make_client_with_mock(max_retries=2)

        mock.messages.create.side_effect = [
            anthropic.APITimeoutError(request=MagicMock()),
            make_mock_response(text="recovered after timeout"),
        ]

        result = client.complete(system="test", user="test", purpose="test")
        assert result.text == "recovered after timeout"

    def test_retry_on_connection_error(self):
        """Connection errors should be retried."""
        setup_logging("debug")
        client, mock = make_client_with_mock(max_retries=2)

        mock.messages.create.side_effect = [
            anthropic.APIConnectionError(request=MagicMock(), message="connection failed"),
            make_mock_response(text="reconnected"),
        ]

        result = client.complete(system="test", user="test", purpose="test")
        assert result.text == "reconnected"


class TestErrorHandling:
    def test_all_retries_exhausted_raises_llm_error(self):
        """If all retries fail, should raise LLMError."""
        setup_logging("debug")
        client, mock = make_client_with_mock(max_retries=2)

        mock.messages.create.side_effect = anthropic.RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429, headers={}),
            body={"error": {"message": "rate limited", "type": "rate_limit_error"}},
        )

        with pytest.raises(LLMError, match="failed after 2 attempts"):
            client.complete(system="test", user="test", purpose="test")

        assert mock.messages.create.call_count == 2

    def test_auth_error_not_retried(self):
        """4xx errors (except 429) should NOT be retried."""
        setup_logging("debug")
        client, mock = make_client_with_mock(max_retries=3)

        mock.messages.create.side_effect = anthropic.APIStatusError(
            message="invalid api key",
            response=MagicMock(status_code=401, headers={}),
            body={"error": {"message": "invalid api key", "type": "authentication_error"}},
        )

        with pytest.raises(LLMError, match="HTTP 401"):
            client.complete(system="test", user="test", purpose="test")

        # Should only be called once — no retries for auth errors
        assert mock.messages.create.call_count == 1

    def test_bad_request_not_retried(self):
        """400 errors should NOT be retried."""
        setup_logging("debug")
        client, mock = make_client_with_mock(max_retries=3)

        mock.messages.create.side_effect = anthropic.APIStatusError(
            message="bad request",
            response=MagicMock(status_code=400, headers={}),
            body={"error": {"message": "bad request", "type": "invalid_request_error"}},
        )

        with pytest.raises(LLMError, match="HTTP 400"):
            client.complete(system="test", user="test", purpose="test")

        assert mock.messages.create.call_count == 1