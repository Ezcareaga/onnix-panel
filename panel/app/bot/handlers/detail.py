"""handler: VER_DETALLES shortcut (M4 Task 3.12).

Re-renders the detail card for the last viewed property using
``search_context.last_detalle_id``. Delegates to the IC-specific handler
when the conversation is in an IC lead context, and to the reenviado
search when there is no ``last_detalle_id`` but ciudad/barrio filtros
are present. Falls back to a conversacion response when no lookup path
succeeds.

Extracted from ``Orchestrator._handle_ver_detalles``.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.bot.core.types import BotRequest, BotResponse
from app.bot.handlers._types import HandlerResult
from app.bot.handlers.detail_ic import handle_ver_detalles_ic
from app.bot.handlers.reenviado import handle_si_mostrame_reenviado

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.bot.core.conversation import ConversationManager
    from app.bot.core.types import ConversationState
    from app.bot.search.search_service import SearchService

logger = logging.getLogger(__name__)


async def handle_ver_detalles(
    request: BotRequest,
    session: "AsyncSession",
    contact,
    conversation,
    search_context: "ConversationState",
    *,
    search_service: "SearchService",
    conversation_manager: "ConversationManager",
) -> HandlerResult:
    """Handle VER_DETALLES without AI roundtrip.

    Re-fetches the last viewed property by ID from search_context.last_detalle_id
    and returns it as a detalle response. Returns a conversacion fallback when
    the property ID is missing, the property was not found, or it is inactive.
    """
    prop_id = search_context.last_detalle_id

    # IC lead path — show data directly from infocasas_properties when available.
    # This handles the 39.6% of IC leads that have no cross-reference (property_id=NULL).
    # Fallback: when last_ic_prop_id is None but contact.infocasas_ref is set, we still
    # route through the IC path so handle_ver_detalles_ic can lookup by ref.
    ic_prop_id = search_context.last_ic_prop_id
    if ic_prop_id or getattr(contact, "infocasas_ref", None):
        return await handle_ver_detalles_ic(
            ic_prop_id, session, contact, conversation, search_context,
            conversation_manager=conversation_manager,
        )

    if not prop_id:
        filtros = search_context.filtros
        has_useful_filtros = bool(filtros.get("ciudad") or filtros.get("barrio"))

        if has_useful_filtros:
            # Pre-loaded filtros available — execute search directly instead of asking
            logger.info(
                "Orchestrator: VER_DETALLES with no last_detalle_id but filtros present "
                "(barrio=%s ciudad=%s) — delegating to search",
                filtros.get("barrio"),
                filtros.get("ciudad"),
            )
            return await handle_si_mostrame_reenviado(
                request, session, contact, conversation, search_context,
                search_service=search_service,
                conversation_manager=conversation_manager,
            )

        fallback_text = (
            "No tengo información sobre esa propiedad. "
            "¿Querés que busque opciones?"
        )
        bot_response = BotResponse(text=fallback_text, intent="conversacion")
        await conversation_manager.save_outbound_message(
            session, conversation.id, contact.id,
            bot_response.text, bot_response.intent,
        )
        return HandlerResult(response=bot_response, search_context=search_context)

    # Fetch property using the same method as handle_ver_mas
    result = await search_service.get_by_ids([prop_id], session)

    if not result.properties:
        bot_response = BotResponse(
            text="No pude encontrar esa propiedad. ¿Querés que busque opciones similares?",
            intent="conversacion",
        )
        await conversation_manager.save_outbound_message(
            session, conversation.id, contact.id,
            bot_response.text, bot_response.intent,
        )
        return HandlerResult(response=bot_response, search_context=search_context)

    prop = result.properties[0]

    if not prop.get("is_active", True):
        bot_response = BotResponse(
            text="Esa propiedad ya no está disponible. ¿Querés ver opciones similares?",
            intent="conversacion",
        )
        await conversation_manager.save_outbound_message(
            session, conversation.id, contact.id,
            bot_response.text, bot_response.intent,
        )
        return HandlerResult(response=bot_response, search_context=search_context)

    # Update shown_properties — avoid duplicates
    if prop_id not in search_context.shown_properties:
        search_context.shown_properties.append(prop_id)
        await conversation_manager.update_search_context(
            session, conversation.id, search_context,
        )

    shown_ids = [prop_id]
    bot_response = BotResponse(
        text="",
        intent="detalle",
        properties=[prop],
        shown_ids=shown_ids,
    )

    await conversation_manager.save_outbound_message(
        session, conversation.id, contact.id,
        bot_response.text, bot_response.intent,
        properties_shown=shown_ids,
    )

    logger.info(
        "Decision — {\"intent\": \"detalle\", \"model\": \"shortcut\", \"properties\": 1, \"is_lead\": false}",
    )

    return HandlerResult(response=bot_response, search_context=search_context)
