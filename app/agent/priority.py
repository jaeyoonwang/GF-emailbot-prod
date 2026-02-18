"""
Email priority assignment based on sender tier lists.

Loads tier configuration from a YAML file and assigns priority tiers
to emails based on exact, case-insensitive sender email matching.

Usage:
    from app.agent.priority import TierConfig
    tiers = TierConfig("config/tiers.yaml")
    tier = tiers.get_tier("mark.suzman@gatesfoundation.org")  # → Tier.VVIP
"""

import logging
from pathlib import Path

import yaml

from app.agent.schemas import Tier

logger = logging.getLogger(__name__)


class TierConfig:
    """
    Loads and queries tier configuration from a YAML file.

    All email addresses are lowercased and stripped at load time,
    so matching is always case-insensitive.
    """

    def __init__(self, yaml_path: str):
        path = Path(yaml_path)
        if not path.exists():
            raise FileNotFoundError(
                f"Tier config not found: {yaml_path}. "
                f"Create it from the template in config/tiers.yaml."
            )

        with open(path) as f:
            data = yaml.safe_load(f)

        self.tier_1: set[str] = self._load_emails(data, "tier_1")
        self.tier_2: set[str] = self._load_emails(data, "tier_2")
        self.tier_3: set[str] = self._load_emails(data, "tier_3")
        self.filtered_senders: set[str] = {
            e.lower().strip() for e in data.get("filtered_senders", [])
        }

        total = len(self.tier_1) + len(self.tier_2) + len(self.tier_3)
        logger.info(
            "tier_config.loaded",
            extra={
                "action": "tier_config.loaded",
                "tier_1_count": len(self.tier_1),
                "tier_2_count": len(self.tier_2),
                "tier_3_count": len(self.tier_3),
                "filtered_count": len(self.filtered_senders),
                "total_contacts": total,
            },
        )

    @staticmethod
    def _load_emails(data: dict, tier_key: str) -> set[str]:
        """Extract and normalize emails from a tier section."""
        tier_data = data.get(tier_key, {})
        if not isinstance(tier_data, dict):
            return set()
        emails = tier_data.get("emails", [])
        if not isinstance(emails, list):
            return set()
        return {e.lower().strip() for e in emails if isinstance(e, str)}

    def get_tier(self, sender_email: str) -> Tier:
        """Get the priority tier for a sender email address."""
        email = sender_email.lower().strip()
        if email in self.tier_1:
            return Tier.VVIP
        if email in self.tier_2:
            return Tier.IMPORTANT
        if email in self.tier_3:
            return Tier.STANDARD
        return Tier.DEFAULT

    def is_filtered_sender(self, sender_email: str) -> bool:
        """Check if a sender should be completely filtered out."""
        email = sender_email.lower().strip()
        if email in self.filtered_senders:
            return True
        if ("no-reply@" in email or "noreply@" in email) and (
            "teams" in email or "microsoft" in email
        ):
            return True
        return False