"""handler: pagination shortcut (M4 Task 3.8).

When the user clicks the ``ver_mas`` button (or types "más opciones"), this
handler bypasses the AI and returns the next 2 property IDs from
``search_context.resultados_pendientes`` directly. Mutates search_context
internally and returns the updated state via HandlerResult.

Extracted from ``Orchestrator._handle_ver_mas``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.bot.core.types import BotRequest, BotResponse
from app.bot.handlers._types import HandlerResult
from app.bot.handlers._utils import build_context_desc

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.bot.core.conversation import ConversationManager
    from app.bot.core.types import ConversationState
    from app.bot.search.search_service import SearchService


async def handle_ver_mas(
    request: BotRequest,
    session: "AsyncSession",
    contact,
    conversation,
    search_context: "ConversationState",
    *,
    search_service: "SearchService",
    conversation_manager: "ConversationManager",
) -> HandlerResult:
    """Pop next 2 pending property IDs, fetch them, build response.

    Returns HandlerResult whose search_context reflects the updated state:
    - current_page_ids = shown IDs
    - shown_properties extended
    - search_shown_count incremented
    - resultados_pendientes = remaining
    - etapa = "mostrando_resultados"
    """
    pending = search_context.resultados_pendientes
    next_ids = pending[:2]
    remaining = pending[2:]

    result = await search_service.get_by_ids(next_ids, session)
    properties = result.properties

    if not properties:
        return HandlerResult(
            response=BotResponse(
                text="No encontre mas propiedades disponibles en este momento.",
                intent="paginacion",
            ),
            search_context=search_context,
        )

    shown_ids = [p["id"] for p in properties]

    search_context.current_page_ids = shown_ids
    search_context.shown_properties.extend(shown_ids)
    search_context.search_shown_count += len(shown_ids)
    search_context.resultados_pendientes = remaining
    search_context.etapa = "mostrando_resultados"
    await conversation_manager.update_search_context(
        session, conversation.id, search_context,
    )

    shown_count = search_context.search_shown_count
    total = search_context.total_found
    if total > 0:
        context_desc = build_context_desc(search_context.filtros)
        if not remaining:
            intro = f"Estas son las \u00faltimas {context_desc} ({shown_count} de {total}):"
        else:
            intro = f"Te muestro m\u00e1s {context_desc} ({shown_count} de {total}):"
    else:
        intro = "Ac\u00e1 ten\u00e9s m\u00e1s opciones:"

    bot_response = BotResponse(
        text=intro,
        intent="paginacion",
        properties=properties,
        shown_ids=shown_ids,
        pending_ids=remaining,
    )

    await conversation_manager.save_outbound_message(
        session, conversation.id, contact.id,
        bot_response.text, bot_response.intent,
        properties_shown=shown_ids,
    )

    return HandlerResult(response=bot_response, search_context=search_context)
