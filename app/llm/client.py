"""
Anthropic LLM client wrapper.

Provides a clean interface for making LLM calls with:
- Automatic retry on transient errors (timeouts, rate limits, server errors)
- Structured logging of every call (tokens, cost, latency — never content)
- Token usage and cost tracking per call and per session
- Configurable model and token limits

Usage:
    from app.llm.client import LLMClient

    client = LLMClient()
    result = client.complete(
        system="You are an email assistant.",
        user="Summarize this email: ...",
        max_tokens=200,
    )
    print(result.text)
    print(result.cost)
"""

import time
import logging
from dataclasses import dataclass
from typing import Optional

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

# Claude Sonnet 4 pricing (per 1M tokens) — update if model changes
PRICING = {
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
}
# Fallback pricing if model not in pricing table
DEFAULT_PRICING = {"input": 3.00, "output": 15.00}


@dataclass
class LLMResult:
    """Result of an LLM API call."""
    text: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    input_cost: float
    output_cost: float
    cost: float
    latency_ms: int
    model: str


class LLMClient:
    """
    Wrapper around the Anthropic API client.

    Handles retries, logging, and cost tracking so the rest of the app
    doesn't need to know about API details.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_retries: int = 3,
        timeout_seconds: float = 60.0,
    ):
        self._api_key = api_key or settings.anthropic_api_key
        self._model = model or settings.anthropic_model
        self._max_retries = max_retries
        self._timeout = timeout_seconds

        self._client = anthropic.Anthropic(
            api_key=self._api_key,
            timeout=self._timeout,
            max_retries=0,  # We handle retries ourselves for better logging
        )

        # Get pricing for this model
        self._pricing = PRICING.get(self._model, DEFAULT_PRICING)

        # Session-level cost tracking
        self.session_total_cost: float = 0.0
        self.session_total_input_tokens: int = 0
        self.session_total_output_tokens: int = 0
        self.session_call_count: int = 0

        logger.info(
            "llm_client.initialized",
            extra={
                "action": "llm_client.initialized",
                "model": self._model,
                "max_retries": self._max_retries,
                "timeout_seconds": self._timeout,
            },
        )

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: Optional[int] = None,
        purpose: str = "unknown",
    ) -> LLMResult:
        """
        Send a completion request to the Anthropic API.

        Args:
            system: System prompt.
            user: User message content.
            max_tokens: Max output tokens (defaults to settings value).
            purpose: What this call is for (e.g., "summarize", "draft").
                     Used in logs to distinguish different call types.
                     NEVER include email content in this field.

        Returns:
            LLMResult with the response text, token usage, and cost.

        Raises:
            LLMError: If all retries are exhausted.
        """
        if max_tokens is None:
            max_tokens = settings.anthropic_max_tokens_draft

        last_error = None

        for attempt in range(1, self._max_retries + 1):
            start = time.monotonic()

            try:
                response = self._client.messages.create(
                    model=self._model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )

                latency_ms = int((time.monotonic() - start) * 1000)

                # Calculate cost
                input_tokens = response.usage.input_tokens
                output_tokens = response.usage.output_tokens
                input_cost = (input_tokens / 1_000_000) * self._pricing["input"]
                output_cost = (output_tokens / 1_000_000) * self._pricing["output"]
                total_cost = input_cost + output_cost

                # Update session totals
                self.session_total_cost += total_cost
                self.session_total_input_tokens += input_tokens
                self.session_total_output_tokens += output_tokens
                self.session_call_count += 1

                result = LLMResult(
                    text=response.content[0].text.strip(),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                    input_cost=input_cost,
                    output_cost=output_cost,
                    cost=total_cost,
                    latency_ms=latency_ms,
                    model=self._model,
                )

                # Log success — NEVER log prompt or response content
                logger.info(
                    "llm.call.success",
                    extra={
                        "action": "llm.call.success",
                        "purpose": purpose,
                        "attempt": attempt,
                        "model": self._model,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cost_usd": round(total_cost, 6),
                        "latency_ms": latency_ms,
                        "session_total_cost_usd": round(self.session_total_cost, 4),
                        "session_call_count": self.session_call_count,
                    },
                )

                return result

            except anthropic.RateLimitError as e:
                last_error = e
                wait = min(2 ** attempt, 30)  # Exponential backoff: 2s, 4s, 8s...
                logger.warning(
                    "llm.call.rate_limited",
                    extra={
                        "action": "llm.call.rate_limited",
                        "purpose": purpose,
                        "attempt": attempt,
                        "wait_seconds": wait,
                    },
                )
                time.sleep(wait)

            except anthropic.APITimeoutError as e:
                last_error = e
                latency_ms = int((time.monotonic() - start) * 1000)
                logger.warning(
                    "llm.call.timeout",
                    extra={
                        "action": "llm.call.timeout",
                        "purpose": purpose,
                        "attempt": attempt,
                        "latency_ms": latency_ms,
                        "timeout_seconds": self._timeout,
                    },
                )
                # Don't sleep on timeout — the wait already happened

            except anthropic.APIStatusError as e:
                last_error = e
                # 5xx errors are transient — retry. 4xx errors (except 429) are not.
                if e.status_code >= 500:
                    wait = min(2 ** attempt, 30)
                    logger.warning(
                        "llm.call.server_error",
                        extra={
                            "action": "llm.call.server_error",
                            "purpose": purpose,
                            "attempt": attempt,
                            "status_code": e.status_code,
                            "wait_seconds": wait,
                        },
                    )
                    time.sleep(wait)
                else:
                    # Non-retryable error (auth, bad request, etc.)
                    logger.error(
                        "llm.call.client_error",
                        extra={
                            "action": "llm.call.client_error",
                            "purpose": purpose,
                            "attempt": attempt,
                            "status_code": e.status_code,
                            "error": str(e),
                        },
                    )
                    raise LLMError(f"Anthropic API error (HTTP {e.status_code}): {e}") from e

            except anthropic.APIConnectionError as e:
                last_error = e
                wait = min(2 ** attempt, 30)
                logger.warning(
                    "llm.call.connection_error",
                    extra={
                        "action": "llm.call.connection_error",
                        "purpose": purpose,
                        "attempt": attempt,
                        "wait_seconds": wait,
                        "error": str(e),
                    },
                )
                time.sleep(wait)

        # All retries exhausted
        logger.error(
            "llm.call.failed",
            extra={
                "action": "llm.call.failed",
                "purpose": purpose,
                "max_retries": self._max_retries,
                "error": str(last_error),
            },
        )
        raise LLMError(
            f"LLM call failed after {self._max_retries} attempts: {last_error}"
        ) from last_error

    def get_session_stats(self) -> dict:
        """Get session-level usage statistics."""
        return {
            "total_cost_usd": round(self.session_total_cost, 4),
            "total_input_tokens": self.session_total_input_tokens,
            "total_output_tokens": self.session_total_output_tokens,
            "total_calls": self.session_call_count,
            "model": self._model,
        }

    def reset_session_stats(self) -> None:
        """Reset session-level counters."""
        self.session_total_cost = 0.0
        self.session_total_input_tokens = 0
        self.session_total_output_tokens = 0
        self.session_call_count = 0


class LLMError(Exception):
    """Raised when an LLM API call fails after all retries."""
    pass