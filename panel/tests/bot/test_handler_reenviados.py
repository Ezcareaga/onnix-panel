"""Tests for Orchestrator reenviado handlers.

Covers:
- _handle_si_mostrame_reenviado: search with filtros, intent=busqueda, no Claude call
- _handle_ahora_no_reenviado: sets no_response status, creates lead_event, no Claude call
- Dispatch shortcuts: SI_MOSTRAME_REENVIADO and AHORA_NO_REENVIADO bypass AI
- Bug 3: IC data fallback when search_context.filtros is empty
- Bug 5: busquedas_historicas and filtros persisted after IC reenviado search
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

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
# Helpers shared with test_orchestrator.py patterns
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


def _default_contact(status="new", is_baja=False):
    return ContactInfo(
        id=1, name="Daniel Perez", status=status, is_baja=is_baja,
        platform="whatsapp", source_id="99990001",
    )


def _default_conversation():
    return ConversationInfo(
        id=10, contact_id=1, platform="whatsapp", chat_id="+595981500746",
        is_bot_active=True,
    )


def _callback_request(callback_data: str) -> BotRequest:
    return BotRequest(
        platform="whatsapp",
        chat_id="+595981500746",
        user_id="+595981500746",
        user_name="Daniel Perez",
        text=None,
        callback_data=callback_data,
    )


def _search_result(count: int = 2) -> SearchResult:
    props = [
        {
            "id": 200 + i,
            "title": f"Casa reenviado {i+1}",
            "city": "Asuncion",
            "operation": "alquiler",
            "property_type": "departamento",
            "price_usd": 500 + i * 100,
            "bedrooms": 2,
            "bathrooms": 1,
            "total_area_m2": 80,
            "source": "onnix",
            "external_id": f"ext_{200+i}",
            "local_image_count": 2,
        }
        for i in range(count)
    ]
    return SearchResult(properties=props, total_found=count)


def _setup_flow_for_callback(mocks, callback_data, search_context=None, contact=None):
    """Configure mocks to reach the shortcut dispatch point."""
    mocks["conversation_manager"].resolve_contact.return_value = (
        contact or _default_contact()
    )
    mocks["conversation_manager"].get_or_create_conversation.return_value = (
        _default_conversation()
    )
    mocks["conversation_manager"].check_human_cooldown.return_value = False
    mocks["conversation_manager"].get_history.return_value = []
    mocks["conversation_manager"].get_search_context.return_value = (
        search_context or ConversationState()
    )


# ---------------------------------------------------------------------------
# Tests 13-15: _handle_si_mostrame_reenviado
# ---------------------------------------------------------------------------

class TestHandleSiMostrame:
    """_handle_si_mostrame_reenviado searches with filtros from search_context."""

    @pytest.mark.asyncio
    async def test_executa_busqueda_con_filtros(self):
        """search_context filtros are passed to search_properties."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(
            etapa="esperando_confirmacion_busqueda",
            filtros={"ciudad": "Asuncion", "operacion": "alquiler", "tipo": "departamento"},
        )
        _setup_flow_for_callback(mocks, "SI_MOSTRAME_REENVIADO", search_context=ctx)
        mocks["search_service"].search_properties.return_value = _search_result(2)

        request = _callback_request("SI_MOSTRAME_REENVIADO")
        result = await orch.handle_message(request, AsyncMock())

        mocks["search_service"].search_properties.assert_awaited_once()
        call_filters = mocks["search_service"].search_properties.call_args[0][0]
        assert call_filters.ciudad == "Asuncion"
        assert call_filters.operacion == "alquiler"

    @pytest.mark.asyncio
    async def test_devuelve_intent_busqueda(self):
        """BotResponse.intent is 'busqueda' for SI_MOSTRAME_REENVIADO."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(
            etapa="esperando_confirmacion_busqueda",
            filtros={"ciudad": "Luque", "operacion": "venta"},
        )
        _setup_flow_for_callback(mocks, "SI_MOSTRAME_REENVIADO", search_context=ctx)
        mocks["search_service"].search_properties.return_value = _search_result(2)

        request = _callback_request("SI_MOSTRAME_REENVIADO")
        result = await orch.handle_message(request, AsyncMock())

        assert result is not None
        assert result.intent == "busqueda"

    @pytest.mark.asyncio
    async def test_sin_filtros_no_crash(self):
        """If filtros is empty and no IC data, search still executes without crashing."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(etapa="esperando_confirmacion_busqueda", filtros={})
        _setup_flow_for_callback(mocks, "SI_MOSTRAME_REENVIADO", search_context=ctx)
        mocks["search_service"].search_properties.return_value = _search_result(0)

        # No IC data for this contact — should gracefully fall through to empty search
        with patch(
            "app.repositories.contact_repo.ContactRepository.get_by_id",
            new=AsyncMock(return_value=None),
        ):
            request = _callback_request("SI_MOSTRAME_REENVIADO")
            result = await orch.handle_message(request, AsyncMock())

        # Must not raise and must call search
        mocks["search_service"].search_properties.assert_awaited_once()
        assert result is not None


# ---------------------------------------------------------------------------
# Tests 16-18: _handle_ahora_no_reenviado
# ---------------------------------------------------------------------------

class TestHandleAhoraNo:
    """_handle_ahora_no_reenviado sets no_response status and records lead_event."""

    @pytest.mark.asyncio
    async def test_setea_no_response(self):
        """contact.status is updated to 'no_response'."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(etapa="esperando_confirmacion_busqueda")
        contact = _default_contact(status="new")
        _setup_flow_for_callback(mocks, "AHORA_NO_REENVIADO", search_context=ctx, contact=contact)

        mock_db_session = AsyncMock()

        with patch(
            "app.repositories.contact_repo.ContactRepository.update_status",
            new=AsyncMock(return_value=contact),
        ) as mock_update_status, patch(
            "app.repositories.lead_event_repo.LeadEventRepository.create",
            new=AsyncMock(),
        ):
            request = _callback_request("AHORA_NO_REENVIADO")
            result = await orch.handle_message(request, mock_db_session)

        mock_update_status.assert_awaited_once()
        call_kwargs = mock_update_status.call_args
        # new_status should be "no_response"
        assert call_kwargs[0][2] == "no_response" or call_kwargs[1].get("new_status") == "no_response"

    @pytest.mark.asyncio
    async def test_status_is_not_discarded(self):
        """Status must be 'no_response', NOT 'discarded'."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(etapa="esperando_confirmacion_busqueda")
        contact = _default_contact(status="new")
        _setup_flow_for_callback(mocks, "AHORA_NO_REENVIADO", search_context=ctx, contact=contact)

        mock_db_session = AsyncMock()

        with patch(
            "app.repositories.contact_repo.ContactRepository.update_status",
            new=AsyncMock(return_value=contact),
        ) as mock_update_status, patch(
            "app.repositories.lead_event_repo.LeadEventRepository.create",
            new=AsyncMock(),
        ):
            request = _callback_request("AHORA_NO_REENVIADO")
            await orch.handle_message(request, mock_db_session)

        mock_update_status.assert_awaited_once()
        call_args = mock_update_status.call_args
        # Verify NOT discarded
        new_status = call_args[0][2] if len(call_args[0]) > 2 else call_args[1].get("new_status")
        assert new_status != "discarded"
        assert new_status == "no_response"

    @pytest.mark.asyncio
    async def test_registra_lead_event_con_declined(self):
        """A lead_event with event_type containing 'declined' is created."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(etapa="esperando_confirmacion_busqueda")
        contact = _default_contact(status="new")
        _setup_flow_for_callback(mocks, "AHORA_NO_REENVIADO", search_context=ctx, contact=contact)

        mock_db_session = AsyncMock()

        with patch(
            "app.repositories.contact_repo.ContactRepository.update_status",
            new=AsyncMock(return_value=contact),
        ), patch(
            "app.repositories.lead_event_repo.LeadEventRepository.create",
            new=AsyncMock(),
        ) as mock_create_event:
            request = _callback_request("AHORA_NO_REENVIADO")
            await orch.handle_message(request, mock_db_session)

        mock_create_event.assert_awaited_once()
        call_kwargs = mock_create_event.call_args[1]
        assert "declined" in call_kwargs.get("event_type", "")


# ---------------------------------------------------------------------------
# Tests 19-20: Dispatch bypasses AI
# ---------------------------------------------------------------------------

class TestDispatchBypassAI:
    """SI_MOSTRAME_REENVIADO and AHORA_NO_REENVIADO do not call Claude."""

    @pytest.mark.asyncio
    async def test_si_mostrame_bypass_ai(self):
        """SI_MOSTRAME_REENVIADO shortcut does NOT call Claude."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(
            etapa="esperando_confirmacion_busqueda",
            filtros={"ciudad": "Asuncion"},
        )
        _setup_flow_for_callback(mocks, "SI_MOSTRAME_REENVIADO", search_context=ctx)
        mocks["search_service"].search_properties.return_value = _search_result(2)

        request = _callback_request("SI_MOSTRAME_REENVIADO")
        await orch.handle_message(request, AsyncMock())

        mocks["claude"].send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_ahora_no_bypass_ai(self):
        """AHORA_NO_REENVIADO shortcut does NOT call Claude."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(etapa="esperando_confirmacion_busqueda")
        contact = _default_contact(status="new")
        _setup_flow_for_callback(mocks, "AHORA_NO_REENVIADO", search_context=ctx, contact=contact)

        with patch(
            "app.repositories.contact_repo.ContactRepository.update_status",
            new=AsyncMock(return_value=contact),
        ), patch(
            "app.repositories.lead_event_repo.LeadEventRepository.create",
            new=AsyncMock(),
        ):
            request = _callback_request("AHORA_NO_REENVIADO")
            await orch.handle_message(request, AsyncMock())

        mocks["claude"].send_message.assert_not_called()


# ---------------------------------------------------------------------------
# Tests Bug 3: IC data fallback when filtros is empty
# ---------------------------------------------------------------------------

class TestHandleSiMostrameIcFallback:
    """Bug 3: when search_context.filtros is empty, handler fetches IC lead data."""

    @pytest.mark.asyncio
    async def test_filtros_vacios_usa_ic_data_para_buscar(self):
        """When filtros empty, handler fetches IC lead data and uses city/operation for search."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(etapa="esperando_confirmacion_busqueda", filtros={})
        _setup_flow_for_callback(mocks, "SI_MOSTRAME_REENVIADO", search_context=ctx)
        mocks["search_service"].search_properties.return_value = _search_result(2)

        mock_contact_db = MagicMock()
        mock_contact_db.infocasas_ref = "REF_SLO_001"

        mock_ic_prop = MagicMock()
        mock_ic_prop.city = "San Lorenzo"
        mock_ic_prop.neighborhood = None
        mock_ic_prop.operation = "alquiler"
        mock_ic_prop.property_type = "departamento"
        mock_ic_prop.bedrooms = 2
        mock_ic_prop.price_rent = Decimal("2000000")
        mock_ic_prop.price_sale = None
        mock_ic_prop.currency_rent = "gs"
        mock_ic_prop.currency_sale = None

        with patch(
            "app.repositories.contact_repo.ContactRepository.get_by_id",
            new=AsyncMock(return_value=mock_contact_db),
        ), patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=mock_ic_prop),
        ):
            request = _callback_request("SI_MOSTRAME_REENVIADO")
            result = await orch.handle_message(request, AsyncMock())

        mocks["search_service"].search_properties.assert_awaited_once()
        call_filters = mocks["search_service"].search_properties.call_args[0][0]
        assert call_filters.ciudad == "San Lorenzo", (
            f"Expected ciudad='San Lorenzo', got '{call_filters.ciudad}'. "
            "Handler must use IC data when filtros is empty."
        )
        assert call_filters.operacion == "alquiler", (
            f"Expected operacion='alquiler', got '{call_filters.operacion}'."
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_filtros_vacios_sin_ic_data_no_crash(self):
        """When filtros empty AND no IC data found, search still runs without crashing."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(etapa="esperando_confirmacion_busqueda", filtros={})
        _setup_flow_for_callback(mocks, "SI_MOSTRAME_REENVIADO", search_context=ctx)
        mocks["search_service"].search_properties.return_value = _search_result(0)

        with patch(
            "app.repositories.contact_repo.ContactRepository.get_by_id",
            new=AsyncMock(return_value=None),
        ):
            request = _callback_request("SI_MOSTRAME_REENVIADO")
            result = await orch.handle_message(request, AsyncMock())

        mocks["search_service"].search_properties.assert_awaited_once()
        assert result is not None


# ---------------------------------------------------------------------------
# Tests Bug 5: busquedas_historicas and filtros persisted after search
# ---------------------------------------------------------------------------

class TestHandleSiMostrameContextPersistence:
    """Bug 5: after IC reenviado search, filtros and busquedas_historicas are persisted."""

    @pytest.mark.asyncio
    async def test_busquedas_historicas_se_popula_despues_de_busqueda(self):
        """After IC reenviado search, busquedas_historicas has one entry with search data."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(
            etapa="esperando_confirmacion_busqueda",
            filtros={"ciudad": "San Lorenzo", "operacion": "alquiler", "tipo": "departamento"},
        )
        _setup_flow_for_callback(mocks, "SI_MOSTRAME_REENVIADO", search_context=ctx)
        mocks["search_service"].search_properties.return_value = _search_result(2)

        request = _callback_request("SI_MOSTRAME_REENVIADO")
        await orch.handle_message(request, AsyncMock())

        mocks["conversation_manager"].update_search_context.assert_awaited()
        saved_state = mocks["conversation_manager"].update_search_context.call_args[0][2]

        assert len(saved_state.busquedas_historicas) == 1, (
            "busquedas_historicas must have one entry after IC reenviado search. "
            f"Got {len(saved_state.busquedas_historicas)} entries."
        )
        historico = saved_state.busquedas_historicas[0]
        assert historico["ciudad"] == "San Lorenzo"
        assert historico["operacion"] == "alquiler"
        assert historico["resultados_encontrados"] == 2

    @pytest.mark.asyncio
    async def test_filtros_explicitamente_en_contexto_guardado(self):
        """After IC reenviado search, search_context.filtros contains the used filtros."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(
            etapa="esperando_confirmacion_busqueda",
            filtros={"ciudad": "Luque", "operacion": "venta", "tipo": "casa"},
        )
        _setup_flow_for_callback(mocks, "SI_MOSTRAME_REENVIADO", search_context=ctx)
        mocks["search_service"].search_properties.return_value = _search_result(3)

        request = _callback_request("SI_MOSTRAME_REENVIADO")
        await orch.handle_message(request, AsyncMock())

        saved_state = mocks["conversation_manager"].update_search_context.call_args[0][2]
        assert saved_state.filtros.get("ciudad") == "Luque"
        assert saved_state.filtros.get("operacion") == "venta"

    @pytest.mark.asyncio
    async def test_last_search_at_seteado(self):
        """After IC reenviado search, last_search_at is set in saved context."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(
            etapa="esperando_confirmacion_busqueda",
            filtros={"ciudad": "Asuncion", "operacion": "alquiler"},
        )
        _setup_flow_for_callback(mocks, "SI_MOSTRAME_REENVIADO", search_context=ctx)
        mocks["search_service"].search_properties.return_value = _search_result(1)

        request = _callback_request("SI_MOSTRAME_REENVIADO")
        await orch.handle_message(request, AsyncMock())

        saved_state = mocks["conversation_manager"].update_search_context.call_args[0][2]
        assert saved_state.last_search_at is not None, (
            "last_search_at must be set after IC reenviado search."
        )
