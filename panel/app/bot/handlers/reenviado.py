"""handler: si_mostrame_reenviado shortcut (M4 Task 3.13).

Handles the ``SI_MOSTRAME_REENVIADO`` callback (and the delegation path
from ``_handle_ver_detalles`` when the user clicks VER_DETALLES without
a ``last_detalle_id`` but has ciudad/barrio filtros). Bypasses the AI
and executes a search directly using ``search_context.filtros``. Falls
back to loading IC filtros from ``contact.infocasas_ref`` when the
stored filtros are effectively empty.

Extracted from ``Orchestrator._handle_si_mostrame_reenviado``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.bot.core.types import BotRequest, BotResponse
from app.bot.handlers._types import HandlerResult
from app.bot.handlers._utils import build_context_desc, no_results_text
from app.bot.handlers.detail_ic import load_ic_filtros_for_contact

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.bot.core.conversation import ConversationManager
    from app.bot.core.types import ConversationState
    from app.bot.search.search_service import SearchService

logger = logging.getLogger(__name__)


async def handle_si_mostrame_reenviado(
    request: BotRequest,
    session: "AsyncSession",
    contact,
    conversation,
    search_context: "ConversationState",
    *,
    search_service: "SearchService",
    conversation_manager: "ConversationManager",
) -> HandlerResult:
    """Execute a search without AI using search_context.filtros.

    Unlike ver_similares, there is no specific property to exclude — all
    previously shown properties are excluded. When the stored filtros are
    effectively empty (no operacion/ciudad/barrio), falls back to loading
    IC filtros from the contact's ``infocasas_ref``.

    Returns a busqueda-intent HandlerResult with the first 2 results and
    the rest stashed in ``search_context.resultados_pendientes``.
    """
    from app.bot.search.sql_filters import SearchFilters

    filtros = search_context.filtros

    # Bug 3: IC data fallback when filtros is effectively empty
    if not filtros.get("operacion") and not filtros.get("ciudad") and not filtros.get("barrio"):
        ic_filtros = await load_ic_filtros_for_contact(contact.id, session)
        if ic_filtros:
            search_context.filtros = ic_filtros
            filtros = ic_filtros

    # Exclude previously shown properties only (no last_detalle_id)
    excluded = list(search_context.shown_properties)

    dormitorios_min = filtros.get("dormitorios_min")
    dormitorios_max = filtros.get("dormitorios_max")
    filters = SearchFilters(
        operacion=filtros.get("operacion"),
        tipo=filtros.get("tipo"),
        ciudad=filtros.get("ciudad"),
        barrio=filtros.get("barrio") or None,
        precio_max=filtros.get("precio_max"),
        moneda=filtros.get("moneda", "usd"),
        dormitorios_min=int(dormitorios_min) if dormitorios_min is not None else None,
        dormitorios_max=int(dormitorios_max) if dormitorios_max is not None else None,
        excluded_ids=excluded,
    )

    result = await search_service.search_properties(filters, session)

    if not result.properties:
        bot_response = BotResponse(
            text=no_results_text(result, filters),
            intent="busqueda",
        )
        await conversation_manager.save_outbound_message(
            session, conversation.id, contact.id,
            bot_response.text, bot_response.intent,
        )
        return HandlerResult(response=bot_response, search_context=search_context)

    shown = result.properties[:2]
    pending = [p["id"] for p in result.properties[2:]]
    shown_ids = [p["id"] for p in shown]

    search_context.current_page_ids = shown_ids
    search_context.shown_properties.extend(shown_ids)
    search_context.search_shown_count += len(shown_ids)
    search_context.resultados_pendientes = pending
    search_context.total_found = result.total_found
    search_context.etapa = "mostrando_resultados"

    # Bug 5: persist filtros and busquedas_historicas (mirrors the Claude loop path)
    search_context.filtros = filtros
    _now = datetime.now(timezone.utc).isoformat()
    search_context.last_search_at = _now
    search_context.busquedas_historicas.append({
        "fecha": _now,
        "operacion": filtros.get("operacion", ""),
        "tipo": filtros.get("tipo", ""),
        "ciudad": filtros.get("ciudad", ""),
        "barrio": filtros.get("barrio", ""),
        "presupuesto_max": filtros.get("precio_max"),
        "moneda": filtros.get("moneda", ""),
        "resultados_encontrados": result.total_found,
    })
    if len(search_context.busquedas_historicas) > 20:
        search_context.busquedas_historicas = search_context.busquedas_historicas[-20:]

    await conversation_manager.update_search_context(
        session, conversation.id, search_context,
    )

    context_desc = build_context_desc(search_context.filtros)
    total = result.total_found
    if pending:
        intro = f"Encontré {total} {context_desc} disponibles en la zona. Acá van las primeras:"
    else:
        intro = f"Encontré {total} {context_desc} disponibles:"

    bot_response = BotResponse(
        text=intro,
        intent="busqueda",
        properties=shown,
        shown_ids=shown_ids,
        pending_ids=pending,
    )

    await conversation_manager.save_outbound_message(
        session, conversation.id, contact.id,
        bot_response.text, bot_response.intent,
        properties_shown=shown_ids,
    )

    logger.info(
        "Decision — {\"intent\": \"busqueda\", \"model\": \"shortcut\", "
        "\"properties\": %d, \"is_lead\": false}",
        len(shown),
    )

    return HandlerResult(response=bot_response, search_context=search_context)
