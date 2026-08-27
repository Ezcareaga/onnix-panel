"""Tests unitarios para helpers del Orchestrator — Task 0.5.3 del M4.

Tests PRE-REFACTOR: cubren código existente sin modificarlo.
Todos los tests son independientes y corren sin DB ni llamadas reales a APIs.

TODO: mover _make_orchestrator() a conftest en Task 3.14 — ver PLAN_M4_REFACTOR.md
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.ai.types import AIResponse
from app.bot.core.orchestrator import Orchestrator
from app.bot.handlers._utils import build_context_desc as _build_context_desc
from app.bot.core.types import (
    BotRequest,
    BotResponse,
    ContactInfo,
    ConversationInfo,
    ConversationState,
    HistoryMessage,
)


# ===========================================================================
# Helpers — TODO: mover a conftest en Task 3.14 — ver PLAN_M4_REFACTOR.md
# ===========================================================================

def _make_orchestrator():
    """Crea un Orchestrator con todas las dependencias mockeadas."""
    claude = AsyncMock()
    gemini = AsyncMock()
    circuit_breaker = MagicMock()
    circuit_breaker.is_open = False
    search_service = AsyncMock()
    conversation_manager = AsyncMock()
    # check_human_cooldown es sync — override con MagicMock
    conversation_manager.check_human_cooldown = MagicMock(return_value=False)
    response_builder = MagicMock()
    tool_executor = AsyncMock()
    # build_tool_result_message es sync — override con MagicMock
    tool_executor.build_tool_result_message = MagicMock()

    orch = Orchestrator(
        claude=claude,
        gemini=gemini,
        circuit_breaker=circuit_breaker,
        search_service=search_service,
        conversation_manager=conversation_manager,
        response_builder=response_builder,
        tool_executor=tool_executor,
    )

    return orch, {
        "claude": claude,
        "gemini": gemini,
        "circuit_breaker": circuit_breaker,
        "search_service": search_service,
        "conversation_manager": conversation_manager,
        "response_builder": response_builder,
        "tool_executor": tool_executor,
    }


def _default_contact(status="new", is_baja=False):
    """Helper para construir un ContactInfo."""
    return ContactInfo(
        id=1, name="Test User", status=status, is_baja=is_baja,
        platform="telegram", source_id="12345",
    )


def _default_conversation(is_bot_active=True, last_human_reply_at=None):
    """Helper para construir un ConversationInfo."""
    return ConversationInfo(
        id=10, contact_id=1, platform="telegram", chat_id="12345",
        is_bot_active=is_bot_active, last_human_reply_at=last_human_reply_at,
    )


def _default_request():
    """Helper para construir un BotRequest."""
    return BotRequest(
        platform="telegram", chat_id="12345", user_id="12345",
        user_name="Test User", text="Busco casa en Asuncion",
        external_id="msg_001",
    )


# ===========================================================================
# 1. _build_context_desc
# ===========================================================================

class TestBuildContextDesc:
    """Tests para la función de módulo _build_context_desc."""

    def test_build_context_desc_full_filters_with_barrio(self):
        """Filtros completos con barrio producen descripción correcta."""
        result = _build_context_desc({
            "operacion": "alquiler",
            "tipo": "departamento",
            "ciudad": "Asuncion",
            "barrio": "Villa Morra",
        })
        assert result == "departamentos en alquiler en Villa Morra"

    def test_build_context_desc_prefers_barrio_over_ciudad(self):
        """Cuando hay barrio y ciudad, barrio tiene prioridad sobre ciudad."""
        result = _build_context_desc({
            "operacion": "venta",
            "tipo": "casa",
            "barrio": "Villa Morra",
            "ciudad": "Asuncion",
        })
        assert result == "casas en venta en Villa Morra"

    def test_build_context_desc_operation_only_without_tipo(self):
        """Sin tipo pero con operacion, usa 'opciones' como plural."""
        result = _build_context_desc({"operacion": "venta"})
        assert result == "opciones en venta"

    def test_build_context_desc_tipo_only_without_operation(self):
        """Solo tipo sin operación usa el plural del tipo."""
        result = _build_context_desc({"tipo": "casa"})
        assert result == "casas"

    def test_build_context_desc_empty_returns_opciones(self):
        """Filtros vacíos retornan 'opciones'."""
        result = _build_context_desc({})
        assert result == "opciones"


# ===========================================================================
# 2. Orchestrator._handle_new_search
# ===========================================================================

class TestHandleNewSearch:
    """Tests para handlers.new_search.handle_new_search (M4 Task 3.7)."""

    async def test_handle_new_search_returns_busqueda_incompleta_response(self):
        """Happy path: HandlerResult con BotResponse busqueda_incompleta + save_outbound_message."""
        from app.bot.core.types import ConversationState
        from app.bot.handlers.new_search import handle_new_search

        conv_mgr = AsyncMock()
        session = AsyncMock()
        contact = _default_contact(status="bot_replied")
        conversation = _default_conversation()
        ctx = ConversationState()

        with patch(
            "app.bot.handlers.new_search.get_response_template",
            return_value="¿Qué querés buscar?",
        ):
            result = await handle_new_search(
                _default_request(), session, contact, conversation, ctx,
                conversation_manager=conv_mgr,
            )

        assert result.response is not None
        assert result.response.text == "¿Qué querés buscar?"
        assert result.response.intent == "busqueda_incompleta"
        assert result.search_context is ctx  # passthrough

        conv_mgr.save_outbound_message.assert_awaited_once_with(
            session, conversation.id, contact.id,
            "¿Qué querés buscar?", "busqueda_incompleta",
        )

    async def test_handle_new_search_advances_new_contact_to_bot_replied(self):
        """Contacto con status='new' dispara UPDATE contacts SET status='bot_replied'."""
        from app.bot.core.types import ConversationState
        from app.bot.handlers.new_search import handle_new_search

        conv_mgr = AsyncMock()
        session = AsyncMock()
        contact = _default_contact(status="new")
        conversation = _default_conversation()

        with patch(
            "app.bot.handlers.new_search.get_response_template",
            return_value="¿Qué querés buscar?",
        ):
            await handle_new_search(
                _default_request(), session, contact, conversation, ConversationState(),
                conversation_manager=conv_mgr,
            )

        session.execute.assert_awaited_once()
        call_args = session.execute.call_args
        sql_text_obj = call_args[0][0]
        assert "UPDATE contacts" in str(sql_text_obj)
        assert "bot_replied" in str(sql_text_obj)

    async def test_handle_new_search_does_not_advance_non_new_contact(self):
        """Contacto con status distinto de 'new' no dispara UPDATE SQL."""
        from app.bot.core.types import ConversationState
        from app.bot.handlers.new_search import handle_new_search

        conv_mgr = AsyncMock()
        session = AsyncMock()
        contact = _default_contact(status="bot_replied")
        conversation = _default_conversation()

        with patch(
            "app.bot.handlers.new_search.get_response_template",
            return_value="¿Qué querés buscar?",
        ):
            await handle_new_search(
                _default_request(), session, contact, conversation, ConversationState(),
                conversation_manager=conv_mgr,
            )

        session.execute.assert_not_awaited()


# ===========================================================================
# 3. ai.message_builder.build_messages
# ===========================================================================

class TestBuildMessages:
    """Tests para ai.message_builder.build_messages."""

    def test_build_messages_empty_history_returns_only_current_user(self):
        """Sin historial, retorna solo el mensaje actual como user."""
        from app.bot.ai.message_builder import build_messages
        result = build_messages([], "hola")
        assert result == [{"role": "user", "content": "hola"}]

    def test_build_messages_alternating_roles_preserved(self):
        """Historial alternado user/bot + current produce 3 mensajes separados."""
        from app.bot.ai.message_builder import build_messages
        history = [
            HistoryMessage(direction="inbound", sender_type="contact", body="busco casa"),
            HistoryMessage(direction="outbound", sender_type="bot", body="encontré opciones"),
        ]
        result = build_messages(history, "gracias")
        assert len(result) == 3
        assert result[0] == {"role": "user", "content": "busco casa"}
        assert result[1] == {"role": "assistant", "content": "encontré opciones"}
        assert result[2] == {"role": "user", "content": "gracias"}

    def test_build_messages_merges_consecutive_user_messages(self):
        """Dos mensajes inbound seguidos se fusionan en un solo user message."""
        from app.bot.ai.message_builder import build_messages
        history = [
            HistoryMessage(direction="inbound", sender_type="contact", body="primero"),
            HistoryMessage(direction="inbound", sender_type="contact", body="segundo"),
        ]
        result = build_messages(history, "tercero")
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "primero\nsegundo\ntercero"

    def test_build_messages_sanitizes_bot_response_with_listings(self):
        """Mensaje bot con properties_shown es reemplazado por resumen de búsqueda."""
        from app.bot.ai.message_builder import build_messages
        history = [
            HistoryMessage(
                direction="outbound",
                sender_type="bot",
                body="**1. Casa linda en Asuncion — 3 dorm, 200 m²",
                properties_shown=[101, 102],
            ),
        ]
        result = build_messages(history, "quiero más detalles")
        assert len(result) == 2
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == (
            "[Usé la herramienta de búsqueda y mostré 2 propiedades con fotos al usuario]"
        )
        assert result[1] == {"role": "user", "content": "quiero más detalles"}


# ===========================================================================
# 4. ai.message_builder.sanitize_bot_response
# ===========================================================================

class TestSanitizeBotResponse:
    """Tests para ai.message_builder.sanitize_bot_response."""

    def test_sanitize_with_properties_shown_replaces_body_with_count_message(self):
        """properties_shown truthy genera resumen con cantidad exacta."""
        from app.bot.ai.message_builder import sanitize_bot_response
        msg = HistoryMessage(
            direction="outbound",
            sender_type="bot",
            body="Casa 1, Casa 2, Casa 3",
            properties_shown=[10, 20, 30],
        )
        result = sanitize_bot_response(msg)
        assert result == "[Usé la herramienta de búsqueda y mostré 3 propiedades con fotos al usuario]"

    def test_sanitize_with_listing_marker_replaces_body(self):
        """Body con marker de listing (sin properties_shown) produce resumen genérico."""
        from app.bot.ai.message_builder import sanitize_bot_response
        msg = HistoryMessage(
            direction="outbound",
            sender_type="bot",
            body="📍 Casa en Lambaré — 180 m² — USD 90,000",
            properties_shown=None,
        )
        result = sanitize_bot_response(msg)
        assert result == "[Usé la herramienta de búsqueda y mostré propiedades con fotos al usuario]"

    def test_sanitize_plain_text_returns_body_unchanged(self):
        """Texto sin markers ni properties_shown se retorna sin cambios."""
        from app.bot.ai.message_builder import sanitize_bot_response
        msg = HistoryMessage(
            direction="outbound",
            sender_type="bot",
            body="Claro, con gusto te ayudo a encontrar una propiedad.",
            properties_shown=None,
        )
        result = sanitize_bot_response(msg)
        assert result == "Claro, con gusto te ayudo a encontrar una propiedad."

    def test_sanitize_empty_body_returns_empty_string(self):
        """Body vacío retorna string vacío."""
        from app.bot.ai.message_builder import sanitize_bot_response
        msg = HistoryMessage(
            direction="outbound",
            sender_type="bot",
            body="",
            properties_shown=None,
        )
        result = sanitize_bot_response(msg)
        assert result == ""


# ===========================================================================
# 5. Orchestrator._call_gemini
# ===========================================================================

class TestCallGemini:
    """Tests para ai.gemini_fallback.call_gemini (M4 Task 3.10)."""

    async def test_call_gemini_with_history_builds_user_content_correctly(self):
        """Con historial, user_content incluye 'Historial:' + líneas + 'Mensaje actual:'."""
        from app.bot.ai.gemini_fallback import call_gemini
        gemini = AsyncMock()
        gemini.send_message = AsyncMock(return_value=AIResponse(text="r", model="gemini-flash"))

        history = [
            HistoryMessage(direction="inbound", sender_type="contact", body="busco depto"),
            HistoryMessage(direction="outbound", sender_type="bot", body="encontré opciones"),
        ]
        await call_gemini(gemini, "SYS", history, "quiero ver más")

        call_kwargs = gemini.send_message.call_args[1]
        user_content = call_kwargs["user_content"]

        # System prompt = base + current-date line (per-request injection)
        assert call_kwargs["system"].startswith("SYS")
        assert "Hoy es" in call_kwargs["system"]
        assert user_content.startswith("Historial:\n")
        assert "Usuario: busco depto" in user_content
        assert "Bot: encontré opciones" in user_content
        assert "Mensaje actual: quiero ver más" in user_content

    async def test_call_gemini_without_history_omits_historial_prefix(self):
        """Sin historial, user_content solo contiene 'Mensaje actual: ...'."""
        from app.bot.ai.gemini_fallback import call_gemini
        gemini = AsyncMock()
        gemini.send_message = AsyncMock(return_value=AIResponse(text="r", model="gemini-flash"))

        await call_gemini(gemini, "SYS", [], "hola")

        call_kwargs = gemini.send_message.call_args[1]
        user_content = call_kwargs["user_content"]

        assert "Historial:" not in user_content
        assert user_content == "Mensaje actual: hola"

    async def test_call_gemini_returns_gemini_response_as_is(self):
        """El retorno de call_gemini es exactamente lo que devuelve gemini.send_message."""
        from app.bot.ai.gemini_fallback import call_gemini
        gemini = AsyncMock()
        expected = AIResponse(text="mi respuesta gemini", model="gemini-flash", input_tokens=5)
        gemini.send_message = AsyncMock(return_value=expected)

        result = await call_gemini(gemini, "SYS", [], "consulta")

        assert result is expected


# ===========================================================================
# 6. ai.prompt_builder.build_dynamic_prompt
# ===========================================================================

class TestBuildDynamicPrompt:
    """Tests para ai.prompt_builder.build_dynamic_prompt."""

    def test_build_dynamic_prompt_without_url_context_appends_section(self):
        """Sin url_context, retorna lista con bloque base (cache_control) + bloque dinámico."""
        from app.bot.ai.prompt_builder import build_dynamic_prompt
        search_context = ConversationState()
        base = "SYSTEM_PROMPT_BASE"

        with patch(
            "app.bot.ai.prompt_builder.build_search_context_section",
            return_value="\n\n[CONTEXTO DE BUSQUEDA]",
        ) as mock_section:
            result = build_dynamic_prompt(base, search_context)

        mock_section.assert_called_once_with(search_context)
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0] == {"type": "text", "text": base, "cache_control": {"type": "ephemeral"}}
        # Dynamic block = fecha actual + section
        assert result[1]["text"].startswith("Hoy es ")
        assert result[1]["text"].endswith("\n\n[CONTEXTO DE BUSQUEDA]")
        assert "cache_control" not in result[1]

    def test_build_dynamic_prompt_with_url_context_appends_both(self):
        """Con url_context, bloque dinámico contiene section + newlines + url_context."""
        from app.bot.ai.prompt_builder import build_dynamic_prompt
        search_context = ConversationState()
        url_context = "[Sistema: propiedad ID 12345 de InfoCasas]"
        base = "SYSTEM_PROMPT_BASE"

        with patch(
            "app.bot.ai.prompt_builder.build_search_context_section",
            return_value="\n\n[CONTEXTO]",
        ):
            result = build_dynamic_prompt(base, search_context, url_context=url_context)

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0] == {"type": "text", "text": base, "cache_control": {"type": "ephemeral"}}
        # Dynamic block = fecha actual + section + url_context
        assert result[1]["text"].startswith("Hoy es ")
        assert result[1]["text"].endswith("\n\n[CONTEXTO]" + "\n\n" + url_context)
        assert "cache_control" not in result[1]

    def test_build_dynamic_prompt_rstrips_url_context_trailing_newlines(self):
        """url_context con newlines al final es limpiado con rstrip() en el bloque dinámico."""
        from app.bot.ai.prompt_builder import build_dynamic_prompt
        search_context = ConversationState()
        url_context_dirty = "[Sistema: nota]\n\n\n"
        url_context_clean = "[Sistema: nota]"
        base = "SYSTEM_PROMPT_BASE"

        with patch(
            "app.bot.ai.prompt_builder.build_search_context_section",
            return_value="",
        ):
            result = build_dynamic_prompt(
                base, search_context, url_context=url_context_dirty,
            )

        assert isinstance(result, list)
        assert len(result) == 2
        # rstrip() elimina los newlines del final
        assert result[1]["text"].endswith(url_context_clean)
        assert not result[1]["text"].endswith("\n")
        # bloque base siempre tiene cache_control
        assert result[0]["cache_control"] == {"type": "ephemeral"}

    def test_build_dynamic_prompt_no_dynamic_content_still_has_fecha_block(self):
        """Sin search_context activo y sin url_context, el bloque dinámico
        existe igual: lleva la fecha actual (nunca en el bloque cacheado)."""
        from app.bot.ai.prompt_builder import build_dynamic_prompt
        search_context = ConversationState()
        base = "SYSTEM_PROMPT_BASE"

        with patch(
            "app.bot.ai.prompt_builder.build_search_context_section",
            return_value="",
        ):
            result = build_dynamic_prompt(base, search_context)

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0] == {
            "type": "text",
            "text": base,
            "cache_control": {"type": "ephemeral"},
        }
        assert result[1]["text"].startswith("Hoy es ")
        assert "cache_control" not in result[1]
