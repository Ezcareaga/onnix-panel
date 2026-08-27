"""Bot core layer — orchestration, state, and response formatting."""

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
from app.bot.core.response_builder import ResponseBuilder
from app.bot.core.orchestrator import Orchestrator
from app.bot.core.tool_executor import ToolExecutor

__all__ = [
    # Types
    "BotRequest",
    "BotResponse",
    "ConversationState",
    "HistoryMessage",
    "ContactInfo",
    "ConversationInfo",
    "ChannelPayload",
    "PayloadMessage",
    # Managers
    "ConversationManager",
    "ResponseBuilder",
    # Orchestration
    "Orchestrator",
    "ToolExecutor",
]
