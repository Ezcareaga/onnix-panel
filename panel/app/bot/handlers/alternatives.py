"""Handler para callbacks ALT:<id> — el cliente eligió una alternativa ofrecida.

Cuando el cliente toca el botón de una alternativa (o escribe texto que
el dispatcher resuelve como ALT:<id>), este handler aplica los filtros
de esa alternativa al estado actual y delega a handle_new_search para
ejecutar la búsqueda con los filtros actualizados.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import text as sa_text

from app.bot.core.types import BotResponse
from app.bot.handlers._types import HandlerResult
from app.bot.handlers.new_search import handle_new_search
from app.services.lead_event_service import record_event

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.bot.core.conversation import ConversationManager
    from app.bot.core.types import BotRequest, ConversationState
    from app.bot.search.search_service import SearchService

logger = logging.getLogger(__name__)


async def handle_alternative_callback(
    request: "BotRequest",
    session: "AsyncSession",
    contact,
    conversation,
    search_context: "ConversationState",
    *,
    search_service: "SearchService",
    conversation_manager: "ConversationManager",
) -> "BotResponse":
    """Procesa ALT:<id> — aplica filtros de la alternativa y busca.

    Extrae el id de la alternativa desde el callback_data, busca la
    alternativa en el estado actual y aplica sus filtros via merge_filters.
    Luego delega a handle_new_search para que el usuario vea el prompt de
    búsqueda incompleta, o llama directamente a la búsqueda si los filtros
    de la alternativa son suficientes.

    Si la alternativa no existe (expiró o el id es inválido), devuelve un
    mensaje suave sin exponer detalles técnicos.
    """
    payload = request.callback_data or ""
    # "ALT:<id>" → "<id>"
    alt_id = payload[4:] if payload.startswith("ALT:") else ""

    alt = conversation_manager.find_pending_alternative(search_context, alt_id)

    if alt is None:
        # Alternativa expiró o nunca existió — limpiar estado y mensaje suave
        logger.info(
            "ALT callback with no pending alternative — id=%s conv=%s",
            alt_id, conversation.id,
        )
        conversation_manager.clear_pending_alternatives(search_context)
        await conversation_manager.update_search_context(
            session, conversation.id, search_context,
        )
        graceful = BotResponse(
            text="No encontré esa opción. ¿Qué estás buscando?",
            intent="alternativa_expirada",
        )
        await conversation_manager.save_outbound_message(
            session, conversation.id, contact.id,
            graceful.text, graceful.intent,
        )
        # Auto-advance contact status
        if contact.status == "new":
            await session.execute(sa_text(
                "UPDATE contacts SET status = 'bot_replied' "
                "WHERE id = :id AND status = 'new'"
            ), {"id": contact.id})
        return graceful

    # Aplicar filtros de la alternativa al estado de búsqueda
    alt_filters: dict = alt.get("filters", {})
    search_context.merge_filters(alt_filters)

    # Limpiar alternativas — ya no están pendientes
    conversation_manager.clear_pending_alternatives(search_context)

    # --- Fase I (M5): emit zero_results_accepted — callback trigger ---
    await record_event(
        session,
        contact_id=contact.id,
        conversation_id=conversation.id,
        event_type="zero_results_accepted",
        trigger="callback",
        metadata={
            "alt_id": alt_id,
            "trigger": "callback",
        },
    )
    # --- end Fase I ---

    # Persistir el clear + merge ANTES de delegar a handle_new_search.
    # Si handle_new_search falla o hace crash, las alternativas no quedan
    # stale en DB con filtros viejos mezclados.
    await conversation_manager.update_search_context(
        session, conversation.id, search_context,
    )

    logger.info(
        "ALT callback applied — id=%s filters=%s conv=%s",
        alt_id, alt_filters, conversation.id,
    )

    # Delegar a handle_new_search para retornar busqueda_incompleta.
    # El orquestador (o Claude en el siguiente turno) tomará los filtros
    # actualizados y ejecutará la búsqueda real.
    result = await handle_new_search(
        request, session, contact, conversation, search_context,
        conversation_manager=conversation_manager,
    )
    result.response.intent = f"alternativa_elegida:{alt_id}"
    return result.response
