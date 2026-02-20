"""
Application configuration.

All settings are loaded from environment variables. No defaults for secrets —
if a required secret is missing, the app fails to start with a clear error.

Usage:
    from app.config import settings
    print(settings.azure_client_id)
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Literal


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # --- Azure / Microsoft Graph ---
    azure_client_id: str = Field(description="Azure AD app registration client ID")
    azure_client_secret: str = Field(description="Azure AD app registration client secret")
    azure_tenant_id: str = Field(description="Azure AD tenant ID")
    azure_redirect_uri: str = Field(
        default="http://localhost:8000/auth/callback",
        description="OAuth redirect URI (must match Azure app registration)",
    )

    # --- Anthropic LLM ---
    anthropic_api_key: str = Field(description="Anthropic API key")
    anthropic_model: str = Field(
        default="claude-sonnet-4-20250514",
        description="Anthropic model to use",
    )
    anthropic_max_tokens_summary: int = Field(default=200)
    anthropic_max_tokens_draft: int = Field(default=500)

    # --- Session / Security ---
    session_secret_key: str = Field(description="Secret key for encrypting session cookies")
    session_max_age_seconds: int = Field(default=3600)

    # --- App ---
    app_name: str = Field(default="Email Agent")
    app_env: Literal["development", "staging", "production"] = Field(default="development")
    app_base_url: str = Field(default="http://localhost:8000")
    log_level: str = Field(default="info")

    # --- Tier config ---
    tier_config_path: str = Field(default="config/tiers.yaml")

    # --- Microsoft Graph API ---
    graph_base_url: str = Field(default="https://graph.microsoft.com/v1.0")
    graph_scopes: list[str] = Field(
        default=[
            "https://graph.microsoft.com/Mail.Read",
            "https://graph.microsoft.com/Mail.Send",
            "https://graph.microsoft.com/Mail.ReadWrite",
        ],
        description=(
            "Microsoft Graph API scopes. Only include Graph resource scopes here. "
            "MSAL automatically requests openid, profile, and offline_access."
        ),
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()