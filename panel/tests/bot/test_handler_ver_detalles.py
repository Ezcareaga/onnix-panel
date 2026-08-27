"""Tests for VER_DETALLES shortcut callback handler.

Phase 2 of GSD Templates v20.
Verifies the _handle_ver_detalles method bypasses Claude and directly
renders property details using the search_service.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bot.core.orchestrator import Orchestrator
from app.bot.core.types import (
    BotRequest,
    BotResponse,
    ContactInfo,
    ConversationInfo,
    ConversationState,
)
from app.bot.search.search_service import SearchResult


# ---------------------------------------------------------------------------
# Helpers — mirror the patterns in test_orchestrator.py exactly
# ---------------------------------------------------------------------------

def _make_orchestrator():
    """Create an Orchestrator with all dependencies mocked."""
    claude = AsyncMock()
    gemini = AsyncMock()
    circuit_breaker = MagicMock()
    circuit_breaker.is_open = False
    search_service = AsyncMock()
    conversation_manager = AsyncMock()
    conversation_manager.check_human_cooldown = MagicMock(return_value=False)
    response_builder = MagicMock()
    tool_executor = AsyncMock()
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


def _default_contact(status="contacted", is_baja=False):
    return ContactInfo(
        id=1, name="Test User", status=status, is_baja=is_baja,
        platform="whatsapp", source_id="+595981000001",
    )


def _default_conversation(is_bot_active=True):
    return ConversationInfo(
        id=10, contact_id=1, platform="whatsapp", chat_id="+595981000001",
        is_bot_active=is_bot_active,
    )


def _ver_detalles_request():
    """Build a BotRequest with VER_DETALLES callback."""
    return BotRequest(
        platform="whatsapp", chat_id="+595981000001", user_id="+595981000001",
        user_name="Test User", text="VER_DETALLES",
        external_id="msg_vd_001", callback_data="VER_DETALLES",
    )


def _make_active_property(prop_id: int = 42) -> dict:
    """Build a minimal active property dict as returned by search_service."""
    return {
        "id": prop_id,
        "title": f"Casa test {prop_id}",
        "city": "Asuncion",
        "neighborhood": "Recoleta",
        "operation": "venta",
        "property_type": "casa",
        "price_usd": 180000,
        "price_currency": "usd",
        "bedrooms": 3,
        "bathrooms": 2,
        "total_area_m2": 220,
        "source": "onnix",
        "external_id": f"ext_{prop_id}",
        "local_image_count": 3,
        "is_active": True,
        "description": "Casa amplia con jardín.",
    }


def _setup_ver_detalles_flow(mocks, last_detalle_id=42, prop=None, shown_properties=None):
    """Configure mocks for a VER_DETALLES shortcut flow."""
    ctx = ConversationState(
        etapa="viendo_detalle",
        filtros={"tipo": "casa", "ciudad": "Asuncion", "operacion": "venta"},
        last_detalle_id=last_detalle_id,
        shown_properties=shown_properties or [],
    )
    mocks["conversation_manager"].resolve_contact.return_value = _default_contact()
    mocks["conversation_manager"].get_or_create_conversation.return_value = _default_conversation()
    mocks["conversation_manager"].check_human_cooldown.return_value = False
    mocks["conversation_manager"].get_history.return_value = []
    mocks["conversation_manager"].get_search_context.return_value = ctx

    if prop is not False:
        result_prop = prop or _make_active_property(last_detalle_id or 42)
        mocks["search_service"].get_by_ids.return_value = SearchResult(
            properties=[result_prop], total_found=1,
        )

    return ctx


# ===========================================================================
# TestVERDETALLES
# ===========================================================================

class TestVERDETALLES:
    """Tests for the VER_DETALLES shortcut handler."""

    @pytest.mark.asyncio
    async def test_ver_detalles_con_prop_activa(self):
        """VER_DETALLES with last_detalle_id=42 returns intent='detalle' with property."""
        orch, mocks = _make_orchestrator()
        prop = _make_active_property(42)
        _setup_ver_detalles_flow(mocks, last_detalle_id=42, prop=prop)

        result = await orch.handle_message(_ver_detalles_request(), AsyncMock())

        assert result is not None
        assert result.intent == "detalle"
        assert len(result.properties) == 1
        assert result.properties[0]["id"] == 42

    @pytest.mark.asyncio
    async def test_ver_detalles_last_detalle_id_none(self):
        """VER_DETALLES with last_detalle_id=None returns fallback, NOT intent='detalle'."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(etapa="inicio", filtros={}, last_detalle_id=None)
        mocks["conversation_manager"].resolve_contact.return_value = _default_contact()
        mocks["conversation_manager"].get_or_create_conversation.return_value = _default_conversation()
        mocks["conversation_manager"].check_human_cooldown.return_value = False
        mocks["conversation_manager"].get_history.return_value = []
        mocks["conversation_manager"].get_search_context.return_value = ctx

        result = await orch.handle_message(_ver_detalles_request(), AsyncMock())

        assert result is not None
        assert result.intent != "detalle"
        assert result.text  # has some fallback message
        mocks["claude"].send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_ver_detalles_propiedad_inactiva(self):
        """VER_DETALLES with is_active=False returns fallback mentioning similares."""
        orch, mocks = _make_orchestrator()
        inactive_prop = _make_active_property(42)
        inactive_prop["is_active"] = False
        _setup_ver_detalles_flow(mocks, last_detalle_id=42, prop=inactive_prop)

        result = await orch.handle_message(_ver_detalles_request(), AsyncMock())

        assert result is not None
        assert result.intent != "detalle"
        assert result.text
        # Message should mention something about the property not being available
        # or suggest similares
        assert any(
            word in result.text.lower()
            for word in ("disponible", "similar", "encontr")
        )
        mocks["claude"].send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_ver_detalles_propiedad_no_encontrada(self):
        """VER_DETALLES when fetch returns empty list returns fallback."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(
            etapa="viendo_detalle", last_detalle_id=99,
            filtros={"tipo": "casa"},
        )
        mocks["conversation_manager"].resolve_contact.return_value = _default_contact()
        mocks["conversation_manager"].get_or_create_conversation.return_value = _default_conversation()
        mocks["conversation_manager"].check_human_cooldown.return_value = False
        mocks["conversation_manager"].get_history.return_value = []
        mocks["conversation_manager"].get_search_context.return_value = ctx
        mocks["search_service"].get_by_ids.return_value = SearchResult(
            properties=[], total_found=0,
        )

        result = await orch.handle_message(_ver_detalles_request(), AsyncMock())

        assert result is not None
        assert result.intent != "detalle"
        assert result.text
        mocks["claude"].send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_ver_detalles_shortcut_no_llama_claude(self):
        """VER_DETALLES dispatches without calling Claude client at all."""
        orch, mocks = _make_orchestrator()
        _setup_ver_detalles_flow(mocks, last_detalle_id=42)

        await orch.handle_message(_ver_detalles_request(), AsyncMock())

        mocks["claude"].send_message.assert_not_called()
        mocks["gemini"].send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_ver_detalles_actualiza_shown_properties(self):
        """VER_DETALLES appends prop_id to shown_properties when not already present."""
        orch, mocks = _make_orchestrator()
        ctx = _setup_ver_detalles_flow(
            mocks, last_detalle_id=42, shown_properties=[10, 20],
        )

        await orch.handle_message(_ver_detalles_request(), AsyncMock())

        assert 42 in ctx.shown_properties

    @pytest.mark.asyncio
    async def test_ver_detalles_no_duplica_shown_properties(self):
        """VER_DETALLES does NOT add prop_id twice if already in shown_properties."""
        orch, mocks = _make_orchestrator()
        ctx = _setup_ver_detalles_flow(
            mocks, last_detalle_id=42, shown_properties=[42, 10, 20],
        )

        await orch.handle_message(_ver_detalles_request(), AsyncMock())

        assert ctx.shown_properties.count(42) == 1

    @pytest.mark.asyncio
    async def test_ver_detalles_shown_ids_in_response(self):
        """BotResponse.shown_ids contains the fetched property ID."""
        orch, mocks = _make_orchestrator()
        _setup_ver_detalles_flow(mocks, last_detalle_id=42)

        result = await orch.handle_message(_ver_detalles_request(), AsyncMock())

        assert result is not None
        assert 42 in result.shown_ids


# ===========================================================================
# TestVERDETALLES_ICFallback — Bug 2 fix (Opción 2)
# ===========================================================================

class TestVERDETALLESICFallback:
    """VER_DETALLES with last_detalle_id=None delegates to search when filtros has ciudad/barrio.

    Bug 2 root cause: _preload_search_context guard left search_context empty when
    property_id IS NULL, so VER_DETALLES returned generic "No tengo información".
    Fix: when filtros has ciudad or barrio, delegate directly to _handle_si_mostrame_reenviado
    so the bot executes the search instead of asking "¿Buscamos?" and going silent.
    """

    @pytest.mark.asyncio
    async def test_sin_detalle_id_con_filtros_ic_ejecuta_busqueda(self):
        """VER_DETALLES with no last_detalle_id but IC filtros (ciudad) → executes search."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(
            etapa="inicio",
            filtros={
                "tipo": "casa",
                "ciudad": "San Lorenzo",
                "operacion": "venta",
            },
            last_detalle_id=None,
        )
        mocks["conversation_manager"].resolve_contact.return_value = _default_contact()
        mocks["conversation_manager"].get_or_create_conversation.return_value = _default_conversation()
        mocks["conversation_manager"].check_human_cooldown.return_value = False
        mocks["conversation_manager"].get_history.return_value = []
        mocks["conversation_manager"].get_search_context.return_value = ctx
        # Return an empty search result so the response is deterministic
        mocks["search_service"].search_properties.return_value = SearchResult(
            properties=[], total_found=0,
        )

        result = await orch.handle_message(_ver_detalles_request(), AsyncMock())

        assert result is not None
        # Search was delegated — Claude must NOT have been called
        mocks["claude"].send_message.assert_not_called()
        # Search service must have been called with the filtros
        mocks["search_service"].search_properties.assert_called_once()
        # Intent reflects a search operation, not a plain conversacion hold
        assert result.intent == "busqueda"

    @pytest.mark.asyncio
    async def test_sin_detalle_id_con_filtros_ic_barrio_ejecuta_busqueda(self):
        """VER_DETALLES with no last_detalle_id but IC filtros (barrio) → executes search."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(
            etapa="inicio",
            filtros={"tipo": "departamento", "barrio": "Villa Morra", "operacion": "alquiler"},
            last_detalle_id=None,
        )
        mocks["conversation_manager"].resolve_contact.return_value = _default_contact()
        mocks["conversation_manager"].get_or_create_conversation.return_value = _default_conversation()
        mocks["conversation_manager"].check_human_cooldown.return_value = False
        mocks["conversation_manager"].get_history.return_value = []
        mocks["conversation_manager"].get_search_context.return_value = ctx
        mocks["search_service"].search_properties.return_value = SearchResult(
            properties=[], total_found=0,
        )

        result = await orch.handle_message(_ver_detalles_request(), AsyncMock())

        assert result is not None
        mocks["claude"].send_message.assert_not_called()
        mocks["search_service"].search_properties.assert_called_once()
        assert result.intent == "busqueda"

    @pytest.mark.asyncio
    async def test_sin_detalle_id_sin_filtros_muestra_fallback_generico(self):
        """VER_DETALLES with no last_detalle_id AND empty filtros shows generic fallback."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(etapa="inicio", filtros={}, last_detalle_id=None)
        mocks["conversation_manager"].resolve_contact.return_value = _default_contact()
        mocks["conversation_manager"].get_or_create_conversation.return_value = _default_conversation()
        mocks["conversation_manager"].check_human_cooldown.return_value = False
        mocks["conversation_manager"].get_history.return_value = []
        mocks["conversation_manager"].get_search_context.return_value = ctx

        result = await orch.handle_message(_ver_detalles_request(), AsyncMock())

        assert result is not None
        assert result.intent == "conversacion"
        # Generic fallback is acceptable when there's no IC context
        assert result.text
        mocks["claude"].send_message.assert_not_called()
