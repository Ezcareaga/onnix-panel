"""Bot channel adapters — Telegram and WhatsApp senders."""

from app.bot.channels.base import BaseSender
from app.bot.channels.telegram import TelegramSender
from app.bot.channels.whatsapp import WhatsAppSender

__all__ = [
    "BaseSender",
    "TelegramSender",
    "WhatsAppSender",
]
