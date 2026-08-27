"""handler: opt-out shortcut for AHORA_NO_REENVIADO (M4 Task 3.9).

Click on "Ahora no" button of the reenviado welcome template → sets contact
status to ``no_response`` (reversible — distinct from the irreversible
``discarded`` state used by proper opt-out) + audit event.

Extracted from ``Orchestrator._handle_ahora_no_reenviado``.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.bot.core.types import BotRequest, BotResponse
from app.bot.handlers._types import HandlerResult
from app.repositories.contact_repo import ContactRepository
from app.repositories.lead_event_repo import LeadEventRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.bot.core.conversation import ConversationManager
    from app.bot.core.types import ConversationState

logger = logging.getLogger(__name__)


async def handle_ahora_no_reenviado(
    request: BotRequest,
    session: "AsyncSession",
    contact,
    conversation,
    search_context: "ConversationState",
    *,
    conversation_manager: "ConversationManager",
) -> HandlerResult:
    """Mark contact as no_response (reversible) and record audit event.

    The contact is NOT fully opted out — they can still be followed up via
    reenviado flow later. Uses ``no_response`` status to reflect "not now".
    """
    await ContactRepository.update_status(session, contact.id, "no_response")

    await LeadEventRepository.create(
        session,
        contact_id=contact.id,
        event_type="client_declined_now",
        old_status=contact.status,
        new_status="no_response",
        triggered_by="wa_callback",
        metadata={"callback": "AHORA_NO_REENVIADO"},
    )

    bot_response = BotResponse(
        text="¡Entendido! Si en algún momento querés ver opciones, "
             "escribinos cuando quieras.",
        intent="conversacion",
    )

    await conversation_manager.save_outbound_message(
        session, conversation.id, contact.id,
        bot_response.text, bot_response.intent,
    )

    logger.info(
        "Decision — {\"intent\": \"conversacion\", \"model\": \"shortcut\", "
        "\"properties\": 0, \"is_lead\": false}",
    )

    return HandlerResult(response=bot_response, search_context=search_context)
