"""handler: new search shortcut (M4 Task 3.7).

Called when the user clicks a reset-search callback (e.g. ``seguir_buscando``).
Returns the busqueda_incompleta template without an AI roundtrip.

Extracted from ``Orchestrator._handle_new_search``. Follows the HandlerResult
contract from ``app.bot.handlers._types``.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import text as sa_text

from app.bot.ai.prompts import get_response_template
from app.bot.core.types import BotRequest, BotResponse
from app.bot.handlers._types import HandlerResult

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.bot.core.conversation import ConversationManager
    from app.bot.core.types import ConversationState

logger = logging.getLogger(__name__)


async def handle_new_search(
    request: BotRequest,
    session: "AsyncSession",
    contact,
    conversation,
    search_context: "ConversationState",
    *,
    conversation_manager: "ConversationManager",
) -> HandlerResult:
    """Reset search state and return busqueda_incompleta template.

    Also auto-advances ``contact.status`` from ``"new"`` to ``"bot_replied"``.
    The caller (orchestrator) already resets search_context before dispatching
    to this handler, so we don't mutate it here — we simply return it.
    """
    response_text = get_response_template("busqueda_incompleta")
    bot_response = BotResponse(
        text=response_text,
        intent="busqueda_incompleta",
    )

    await conversation_manager.save_outbound_message(
        session, conversation.id, contact.id,
        bot_response.text, bot_response.intent,
    )

    # Auto-advance contact status
    if contact.status == "new":
        await session.execute(sa_text(
            "UPDATE contacts SET status = 'bot_replied' "
            "WHERE id = :id AND status = 'new'"
        ), {"id": contact.id})

    logger.info(
        "Decision — {\"intent\": \"busqueda_incompleta\", \"model\": \"shortcut\", \"properties\": 0, \"is_lead\": false}",
    )

    return HandlerResult(response=bot_response, search_context=search_context)
