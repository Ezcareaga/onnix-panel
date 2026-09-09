"""Bot channel adapters — transporte de salida del envio manual del panel."""

from app.bot.channels.base import BaseSender
from app.bot.channels.whatsapp import WhatsAppSender

__all__ = [
    "BaseSender",
    "WhatsAppSender",
]
