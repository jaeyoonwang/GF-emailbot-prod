"""Tests for the tier-based priority assignment system."""

import pytest
import textwrap
from pathlib import Path
from app.agent.priority import TierConfig
from app.agent.schemas import Tier


@pytest.fixture
def tier_config(tmp_path) -> TierConfig:
    """Create a TierConfig from a temporary YAML file for testing."""
    yaml_content = textwrap.dedent("""\
        tier_1:
          emails:
            - "vip@example.com"
            - "ceo@bigcorp.com"
        tier_2:
          emails:
            - "director@example.com"
            - "manager@bigcorp.com"
            - "external@partner.org"
        tier_3:
          emails:
            - "analyst@example.com"
            - "contractor@vendor.com"
        filtered_senders:
          - "no-reply@teams.mail.microsoft"
          - "noreply@automated.com"
    """)
    yaml_file = tmp_path / "tiers.yaml"
    yaml_file.write_text(yaml_content)
    return TierConfig(str(yaml_file))


@pytest.fixture
def real_tier_config() -> TierConfig:
    """Load the actual production tier config for validation."""
    path = Path("config/tiers.yaml")
    if not path.exists():
        pytest.skip("config/tiers.yaml not found")
    return TierConfig(str(path))


class TestTierAssignment:
    def test_tier_1_exact_match(self, tier_config):
        assert tier_config.get_tier("vip@example.com") == Tier.VVIP

    def test_tier_2_exact_match(self, tier_config):
        assert tier_config.get_tier("director@example.com") == Tier.IMPORTANT

    def test_tier_3_exact_match(self, tier_config):
        assert tier_config.get_tier("analyst@example.com") == Tier.STANDARD

    def test_unknown_sender_gets_default(self, tier_config):
        assert tier_config.get_tier("random@gmail.com") == Tier.DEFAULT

    def test_case_insensitive_matching(self, tier_config):
        assert tier_config.get_tier("VIP@Example.COM") == Tier.VVIP
        assert tier_config.get_tier("DIRECTOR@EXAMPLE.COM") == Tier.IMPORTANT
        assert tier_config.get_tier("Analyst@Example.Com") == Tier.STANDARD

    def test_whitespace_trimmed(self, tier_config):
        assert tier_config.get_tier("  vip@example.com  ") == Tier.VVIP

    def test_empty_email_returns_default(self, tier_config):
        assert tier_config.get_tier("") == Tier.DEFAULT

    def test_tier_hierarchy(self, tier_config):
        assert Tier.VVIP < Tier.IMPORTANT < Tier.STANDARD < Tier.DEFAULT


class TestFilteredSenders:
    def test_explicit_filtered_sender(self, tier_config):
        assert tier_config.is_filtered_sender("no-reply@teams.mail.microsoft") is True

    def test_explicit_filtered_sender_case_insensitive(self, tier_config):
        assert tier_config.is_filtered_sender("No-Reply@Teams.Mail.Microsoft") is True

    def test_noreply_microsoft_pattern(self, tier_config):
        assert tier_config.is_filtered_sender("no-reply@teams.something.microsoft.com") is True

    def test_regular_sender_not_filtered(self, tier_config):
        assert tier_config.is_filtered_sender("person@company.com") is False

    def test_tier_1_sender_not_filtered(self, tier_config):
        assert tier_config.is_filtered_sender("vip@example.com") is False


class TestConfigLoading:
    def test_missing_file_raises_error(self):
        with pytest.raises(FileNotFoundError):
            TierConfig("/nonexistent/path/tiers.yaml")

    def test_empty_tier_section(self, tmp_path):
        yaml_content = textwrap.dedent("""\
            tier_1:
              emails: []
            tier_2:
              emails:
                - "someone@example.com"
            tier_3:
              emails: []
            filtered_senders: []
        """)
        yaml_file = tmp_path / "tiers.yaml"
        yaml_file.write_text(yaml_content)
        config = TierConfig(str(yaml_file))

        assert config.get_tier("someone@example.com") == Tier.IMPORTANT
        assert config.get_tier("anyone@else.com") == Tier.DEFAULT

    def test_missing_tier_section(self, tmp_path):
        yaml_content = textwrap.dedent("""\
            tier_1:
              emails:
                - "boss@example.com"
            filtered_senders: []
        """)
        yaml_file = tmp_path / "tiers.yaml"
        yaml_file.write_text(yaml_content)
        config = TierConfig(str(yaml_file))

        assert config.get_tier("boss@example.com") == Tier.VVIP
        assert config.get_tier("anyone@else.com") == Tier.DEFAULT


class TestRealConfig:
    """Tests against the actual production tier config."""

    def test_tier_1_mark_suzman(self, real_tier_config):
        assert real_tier_config.get_tier("mark.suzman@gatesfoundation.org") == Tier.VVIP

    def test_tier_1_case_insensitive(self, real_tier_config):
        assert real_tier_config.get_tier("Mark.Suzman@GatesFoundation.org") == Tier.VVIP

    def test_tier_2_foundation_staff(self, real_tier_config):
        assert real_tier_config.get_tier("chris.elias@gatesfoundation.org") == Tier.IMPORTANT

    def test_tier_2_external_contact(self, real_tier_config):
        assert real_tier_config.get_tier("ek@anthropic.com") == Tier.IMPORTANT

    def test_tier_3_external_contact(self, real_tier_config):
        assert real_tier_config.get_tier("natalie@openai.com") == Tier.STANDARD

    def test_unknown_sender(self, real_tier_config):
        assert real_tier_config.get_tier("random.person@gmail.com") == Tier.DEFAULT

    def test_filtered_senders(self, real_tier_config):
        assert real_tier_config.is_filtered_sender("no-reply@teams.mail.microsoft") is True
        assert real_tier_config.is_filtered_sender("mark.suzman@gatesfoundation.org") is False