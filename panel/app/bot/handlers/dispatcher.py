"""Shortcut dispatcher for the orchestrator (M4 Fase 6.A).

Centralizes the fast-path routing that used to live as a stack of
``if request.callback_data == "..."`` branches at the top of
``Orchestrator.handle_message``. A shortcut is any flow that bypasses
the Claude tool-use loop and returns a ``BotResponse`` directly from
one of the handlers in ``app.bot.handlers.*``.

Matching order is significant and is preserved from the original
implementation:

1. ``ver_mas`` callback (with pending results) or pagination text.
2. ``ALT:<id>`` callback → ``handle_alternative_callback`` (M5).
3. Reset-search callbacks → ``handle_new_search``.
4. ``VER_DETALLES`` callback → ``handle_ver_detalles``.
5. ``SI_MOSTRAME_REENVIADO`` callback → ``handle_si_mostrame_reenviado``.
6. ``AHORA_NO_REENVIADO`` callback → ``handle_ahora_no_reenviado``.

Returns ``None`` when no shortcut matches — the caller then proceeds
to the Claude path.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.bot.handlers.alternatives import handle_alternative_callback
from app.bot.handlers.intent_detector import is_pagination_text
from app.bot.handlers.new_search import handle_new_search
from app.bot.handlers.opt_out import handle_ahora_no_reenviado
from app.bot.handlers.pagination import handle_ver_mas
from app.bot.handlers.detail import handle_ver_detalles
from app.bot.handlers.reenviado import handle_si_mostrame_reenviado

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.bot.core.conversation import ConversationManager
    from app.bot.core.types import (
        BotRequest,
        BotResponse,
        ConversationState,
    )
    from app.bot.search.search_service import SearchService

logger = logging.getLogger(__name__)


# Callbacks that trigger a full search_context reset before new_search.
# Only `seguir_buscando` is emitted today by response_builder.py — the BTN_*
# variants were N8N-era aliases (audit confirmed 0 production uses in 60d).
_RESET_SEARCH_CALLBACKS: frozenset[str] = frozenset({
    "seguir_buscando",
})


async def _reset_search_context(
    session: "AsyncSession",
    conversation,
    search_context: "ConversationState",
    conversation_manager: "ConversationManager",
) -> None:
    """Wipe pagination/filter state so a brand-new search starts clean."""
    search_context.etapa = "inicio"
    search_context.filtros = {}
    search_context.resultados_pendientes = []
    search_context.current_page_ids = []
    await conversation_manager.update_search_context(
        session, conversation.id, search_context,
    )


async def try_shortcut_dispatch(
    request: "BotRequest",
    session: "AsyncSession",
    contact,
    conversation,
    search_context: "ConversationState",
    *,
    search_service: "SearchService",
    conversation_manager: "ConversationManager",
) -> "BotResponse | None":
    """Run the first matching shortcut handler; return None if nothing matches."""
    cb = request.callback_data
    text = request.text or ""
    has_pending = bool(search_context.resultados_pendientes)

    # 1. Pagination — callback "ver_mas" or free-text phrase ("más opciones",
    # "las demás", etc.). Both require pending results in context.
    pagination_via_callback = cb == "ver_mas" and has_pending
    pagination_via_text = (
        has_pending
        and bool(text)
        and not cb
        and is_pagination_text(text)
    )
    if pagination_via_callback or pagination_via_text:
        if pagination_via_text:
            logger.info(
                "Pagination detected from text — \"%s\" (pending=%d)",
                text[:50], len(search_context.resultados_pendientes),
            )
        result = await handle_ver_mas(
            request, session, contact, conversation, search_context,
            search_service=search_service,
            conversation_manager=conversation_manager,
        )
        return result.response

    # 1b. ALT:<id> callbacks — client chose an alternative from zero-results.
    # Must come BEFORE _RESET_SEARCH_CALLBACKS so "ALT:..." is not treated as
    # an unknown reset callback and dropped silently.
    if cb and cb.startswith("ALT:"):
        response = await handle_alternative_callback(
            request, session, contact, conversation, search_context,
            search_service=search_service,
            conversation_manager=conversation_manager,
        )
        return response

    # 2. New-search reset callbacks — wipe context then acknowledge.
    if cb in _RESET_SEARCH_CALLBACKS:
        await _reset_search_context(
            session, conversation, search_context, conversation_manager,
        )
        result = await handle_new_search(
            request, session, contact, conversation, search_context,
            conversation_manager=conversation_manager,
        )
        return result.response

    # 3-5. Simple callback → handler mapping.
    if cb == "VER_DETALLES":
        result = await handle_ver_detalles(
            request, session, contact, conversation, search_context,
            search_service=search_service,
            conversation_manager=conversation_manager,
        )
        return result.response

    if cb == "SI_MOSTRAME_REENVIADO":
        result = await handle_si_mostrame_reenviado(
            request, session, contact, conversation, search_context,
            search_service=search_service,
            conversation_manager=conversation_manager,
        )
        return result.response

    if cb == "AHORA_NO_REENVIADO":
        result = await handle_ahora_no_reenviado(
            request, session, contact, conversation, search_context,
            conversation_manager=conversation_manager,
        )
        return result.response

    return None
