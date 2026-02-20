# Copilot/AI Agent Instructions for Trevor Email Assistant

## Project Overview
- **Purpose:** AI-powered assistant for executive email management (summarization, prioritization, and drafting replies).
- **Architecture:**
  - `app/agent/engine.py`: Orchestrates email processing (filtering, tier assignment, summarization, drafting).
  - `app/agent/priority.py`: Loads and applies sender tier config from `config/tiers.yaml`.
  - `app/agent/filters.py`: Filters out non-actionable emails (calendar invites, automated senders).
  - `app/agent/prompts.py`: All LLM prompt templates and response parsing logic.
  - `app/llm/client.py`: Anthropic LLM client with retry, cost tracking, and structured logging.
  - `app/logging/`: Structured JSON logging and audit logging (never log email content).
  - `app/config.py`: Loads all settings from environment variables (see class `Settings`).
  - `config/tiers.yaml`: Maintains sender priority tiers and filtered senders.

## Key Patterns & Conventions
- **Data Models:** All email and agent data flows use Pydantic models in `app/agent/schemas.py`.
- **Prompt Engineering:** All LLM prompt templates and style context logic are centralized in `app/agent/prompts.py`. Only edit this file to change prompt wording.
- **Filtering:**
  - Use `check_filters(email, tier_config)` to determine if an email should be hidden.
  - Calendar invites and automated senders are filtered before any LLM calls.
- **Tier Assignment:**
  - Use `TierConfig.get_tier(sender_email)` to assign priority (VVIP, Important, Standard, Default).
  - All matching is case-insensitive and exact on email address.
- **LLM Usage:**
  - All LLM calls go through `LLMClient.complete()`. Never log or persist prompt/response content.
  - Cost and token usage are tracked per call and per session.
- **Drafting Replies:**
  - Style context is inserted if past sent emails are available ("specific" for same sender, "general" for any).
  - All AI-generated drafts must include a disclaimer (see `ensure_disclaimer`).
- **Logging:**
  - Use structured JSON logging (`app/logging/config.py`).
  - Use `audit.info()` for user/audit actions. Never log sensitive content.

## Developer Workflows
- **Testing:**
  - Run all tests: `pytest`
  - Tests are in `tests/` and use pytest fixtures and mocks for LLM and config.
- **Linting:**
  - Run `ruff .` for linting (configured in `pyproject.toml`).
- **Config:**
  - All secrets and settings are loaded from environment variables or `.env` (see `app/config.py`).
  - Update sender tiers in `config/tiers.yaml` and restart the service.

## Integration Points
- **Microsoft Graph API:** Email data is expected in the format defined by `Email` model.
- **Anthropic API:** Used for all LLM operations via `app/llm/client.py`.

## Examples
- To process a batch of emails:
  ```python
  engine = AgentEngine(tier_config, llm_client)
  actionable, filtered = engine.process_inbox(emails)
  ```
- To draft a reply:
  ```python
  draft = engine.draft_reply(email, sent_to_sender, all_sent, user_name)
  ```

## Do/Don't
- **Do:**
  - Centralize prompt changes in `app/agent/prompts.py`.
  - Use Pydantic models for all data passed between components.
  - Use structured logging and audit logging for all actions.
- **Don't:**
  - Never log or persist email content, LLM prompts, or responses.
  - Never hardcode secrets or config values in code.

---
_Last updated: 2026-02-18_
