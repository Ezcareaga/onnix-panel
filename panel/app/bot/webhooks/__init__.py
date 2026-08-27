"""Bot webhook package — unified router for Telegram and WhatsApp endpoints."""
from app.bot.webhooks.router import webhook_router

__all__ = ["webhook_router"]
