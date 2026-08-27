"""Núcleo de la bandeja — tipos y manejo de conversaciones.

`Orchestrator`, `ToolExecutor` y `ResponseBuilder` se fueron con el bot
conversacional. Lo que queda es lo que la bandeja necesita para guardar un
entrante y resolver a qué contacto y conversación pertenece.
"""

from app.bot.core.types import (
    BotRequest,
    BotResponse,
    ChannelPayload,
    ContactInfo,
    ConversationInfo,
    ConversationState,
    HistoryMessage,
    PayloadMessage,
)
from app.bot.core.conversation import ConversationManager

__all__ = [
    "BotRequest",
    "BotResponse",
    "ConversationState",
    "HistoryMessage",
    "ContactInfo",
    "ConversationInfo",
    "ChannelPayload",
    "PayloadMessage",
    "ConversationManager",
]
