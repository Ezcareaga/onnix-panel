"""Tests for bot configuration."""
import os
from importlib import reload
from unittest.mock import patch


class TestBotSettings:
    """Verify bot config reads environment variables correctly."""

    def test_anthropic_key_reads_from_env(self) -> None:
        """ANTHROPIC_API_KEY is read from environment."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-123"}):
            from app.bot import config
            reload(config)
            assert config.bot_settings.ANTHROPIC_API_KEY == "sk-test-123"

    def test_claude_model_default(self) -> None:
        """CLAUDE_MODEL has sensible default."""
        from app.bot.config import bot_settings
        assert "haiku" in bot_settings.CLAUDE_MODEL.lower()

    def test_database_url_format(self) -> None:
        """DATABASE_URL uses asyncpg driver."""
        from app.bot.config import bot_settings
        assert bot_settings.DATABASE_URL.startswith("postgresql+asyncpg://")

    def test_geo_data_path_exists(self) -> None:
        """GEO_DATA_PATH points to a valid directory (on host)."""
        host_path = os.environ["GEO_DATA_PATH"]
        assert os.path.isdir(host_path), f"Not found: {host_path}"

    def test_timeout_defaults(self) -> None:
        """Resilience settings have numeric defaults."""
        from app.bot.config import bot_settings
        assert bot_settings.BOT_MAX_RETRIES == 3
        assert bot_settings.BOT_TIMEOUT_SECONDS == 30
        assert bot_settings.BOT_CIRCUIT_BREAKER_THRESHOLD == 3
        assert bot_settings.BOT_CIRCUIT_BREAKER_RESET_SECONDS == 300

    def test_rate_limit_defaults(self) -> None:
        """Rate limiting has sensible defaults."""
        from app.bot.config import bot_settings
        assert bot_settings.RATE_LIMIT_MAX_MESSAGES == 5
        assert bot_settings.RATE_LIMIT_WINDOW_SECONDS == 60

    def test_cooldown_default(self) -> None:
        """Human cooldown defaults to 30 minutes."""
        from app.bot.config import bot_settings
        assert bot_settings.HUMAN_COOLDOWN_MINUTES == 30

    def test_twilio_whatsapp_from_format(self) -> None:
        """TWILIO_WHATSAPP_FROM contains whatsapp: prefix."""
        from app.bot.config import bot_settings
        assert bot_settings.TWILIO_WHATSAPP_FROM.startswith("whatsapp:")
