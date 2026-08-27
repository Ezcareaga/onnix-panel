"""Bot configuration loaded from environment variables."""
from pathlib import Path
import os

from dotenv import load_dotenv

from app.config import Settings

load_dotenv(Path(__file__).resolve().parents[3] / ".env")


class BotSettings(Settings):
    """Configuration for the Onnix SA bot.

    Inherits database settings (POSTGRES_*, DATABASE_URL) from Settings.
    Reads all bot-specific values from environment variables.
    Defaults are provided for non-sensitive settings.
    """

    # --- AI ---
    ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL: str = os.environ.get(
        "CLAUDE_MODEL", "claude-haiku-4-5-20251001"
    )
    GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
    GEMINI_EMBEDDING_MODEL: str = os.environ.get(
        "GEMINI_EMBEDDING_MODEL", "gemini-embedding-001"
    )

    # --- Twilio (WhatsApp) ---
    TWILIO_ACCOUNT_SID: str = os.environ.get("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN: str = os.environ.get("TWILIO_AUTH_TOKEN", "")
    TWILIO_WHATSAPP_FROM: str = os.environ.get(
        "TWILIO_WHATSAPP_NUMBER", "whatsapp:+595900000000"
    )
    TWILIO_STATUS_CALLBACK_URL: str = os.environ.get(
        "TWILIO_STATUS_CALLBACK_URL", ""
    )

    # --- Telegram ---
    TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_BOT_USERNAME: str = os.environ.get("TELEGRAM_BOT_USERNAME", "")
    TELEGRAM_EZ_CHAT_ID: str = os.environ.get("TELEGRAM_EZ_CHAT_ID", "")
    TELEGRAM_WEBHOOK_SECRET: str = os.environ.get(
        "TELEGRAM_WEBHOOK_SECRET", ""
    )

    # --- Geographic data ---
    GEO_DATA_PATH: str = os.environ.get(
        "GEO_DATA_PATH", "/app/data/geografia"
    )

    # --- Resilience ---
    BOT_MAX_RETRIES: int = int(os.environ.get("BOT_MAX_RETRIES", "3"))
    BOT_TIMEOUT_SECONDS: int = int(
        os.environ.get("BOT_TIMEOUT_SECONDS", "30")
    )
    BOT_CIRCUIT_BREAKER_THRESHOLD: int = int(
        os.environ.get("BOT_CIRCUIT_BREAKER_THRESHOLD", "3")
    )
    BOT_CIRCUIT_BREAKER_RESET_SECONDS: int = int(
        os.environ.get("BOT_CIRCUIT_BREAKER_RESET_SECONDS", "300")
    )

    # --- Rate limiting ---
    RATE_LIMIT_MAX_MESSAGES: int = int(
        os.environ.get("RATE_LIMIT_MAX_MESSAGES", "5")
    )
    RATE_LIMIT_WINDOW_SECONDS: int = int(
        os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60")
    )

    # --- Cooldown ---
    HUMAN_COOLDOWN_MINUTES: int = int(
        os.environ.get("HUMAN_COOLDOWN_MINUTES", "30")
    )

    # --- SMTP (daily report) ---
    SMTP_EMAIL: str = os.environ.get("SMTP_EMAIL", "")
    SMTP_PASSWORD: str = os.environ.get("SMTP_PASSWORD", "")
    REPORT_EMAIL_TO: str = os.environ.get("REPORT_EMAIL_TO", "")

    # --- Scheduler ---
    SCHEDULER_ENABLED: bool = os.environ.get(
        "SCHEDULER_ENABLED", "true"
    ).lower() in ("true", "1", "yes")


bot_settings = BotSettings()
