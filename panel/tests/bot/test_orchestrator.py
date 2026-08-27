"""Tests for ToolExecutor and Orchestrator.

Plan 62-04: CORE-01 Orchestrator + ToolExecutor unit tests.
All dependencies are mocked — no real API calls or DB access.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
from anthropic import APIConnectionError

from app.bot.ai.types import AIResponse, ToolCall
from app.bot.core.orchestrator import Orchestrator
from app.bot.core.tool_executor import ToolExecutor
from app.bot.core.types import (
    BotRequest, BotResponse, ContactInfo, ConversationInfo,
    ConversationState, HistoryMessage,
)
from app.bot.search.search_service import SearchResult

# Fase 13 dual-fail fallback expected text (drift guard)
_DUAL_FAIL_KEYWORDS = ("problema técnico", "ASESOR")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_search_result(count: int = 2) -> SearchResult:
    """Build a mock SearchResult with *count* properties."""
    props = []
    for i in range(count):
        props.append({
            "id": 100 + i,
            "title": f"Casa test {i+1}",
            "city": "Asuncion",
            "operation": "venta",
            "property_type": "casa",
            "price_usd": 150000 + i * 10000,
            "bedrooms": 3,
            "bathrooms": 2,
            "total_area_m2": 200,
            "source": "onnix",
            "external_id": f"ext_{100+i}",
            "local_image_count": 3,
        })
    return SearchResult(properties=props, total_found=count)


# ===========================================================================
# TestToolExecutor
# ===========================================================================

class TestToolExecutor:
    """Unit tests for ToolExecutor with mocked SearchService."""

    def _make_executor(self) -> tuple[ToolExecutor, AsyncMock]:
        search_service = AsyncMock()
        executor = ToolExecutor(search_service)
        return executor, search_service

    @pytest.mark.asyncio
    async def test_execute_search_properties(self):
        """search_properties tool returns JSON with properties and total_found."""
        executor, search_svc = self._make_executor()
        search_svc.search_properties.return_value = _make_search_result(2)

        tc = ToolCall(id="toolu_001", name="search_properties", input={
            "operacion": "venta", "ciudad": "asuncion", "tipo": "casa",
        })
        session = AsyncMock()

        result = await executor.execute(tc, session)

        assert isinstance(result, dict)
        assert "properties" in result
        assert len(result["properties"]) == 2
        assert "total_found" in result
        assert result["total_found"] == 2
        search_svc.search_properties.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_search_builds_filters(self):
        """Verify ToolExecutor passes correct SearchFilters to SearchService."""
        executor, search_svc = self._make_executor()
        search_svc.search_properties.return_value = _make_search_result(0)

        tc = ToolCall(id="toolu_002", name="search_properties", input={
            "operacion": "alquiler", "ciudad": "luque", "tipo": "departamento",
            "dormitorios_min": 2, "precio_max": 1000,
        })
        session = AsyncMock()

        await executor.execute(tc, session)

        call_args = search_svc.search_properties.call_args
        filters = call_args[0][0]  # first positional arg
        assert filters.operacion == "alquiler"
        assert filters.ciudad == "luque"
        assert filters.tipo == "departamento"
        assert filters.dormitorios_min == 2
        assert filters.precio_max == 1000

    @pytest.mark.asyncio
    async def test_execute_search_excludes_shown_properties(self):
        """search_properties with search_context excludes shown IDs."""
        executor, search_svc = self._make_executor()
        search_svc.search_properties.return_value = _make_search_result(2)

        ctx = ConversationState(shown_properties=[50, 51, 52])
        tc = ToolCall(id="toolu_exc", name="search_properties", input={
            "operacion": "venta", "ciudad": "asuncion",
        })
        session = AsyncMock()

        await executor.execute(tc, session, search_context=ctx)

        call_args = search_svc.search_properties.call_args
        filters = call_args[0][0]
        assert filters.excluded_ids == [50, 51, 52]

    @pytest.mark.asyncio
    async def test_execute_search_no_context_no_excluded(self):
        """search_properties without context has empty excluded_ids."""
        executor, search_svc = self._make_executor()
        search_svc.search_properties.return_value = _make_search_result(1)

        tc = ToolCall(id="toolu_noex", name="search_properties", input={
            "ciudad": "luque",
        })
        session = AsyncMock()

        await executor.execute(tc, session)

        call_args = search_svc.search_properties.call_args
        filters = call_args[0][0]
        assert filters.excluded_ids == []

    @pytest.mark.asyncio
    async def test_execute_get_property_detail_by_id(self):
        """get_property_detail with numeric referencia fetches by ID."""
        executor, search_svc = self._make_executor()
        detail_result = SearchResult(
            properties=[{"id": 12345, "title": "Casa detalle"}],
            total_found=1,
        )
        search_svc.get_by_ids.return_value = detail_result

        tc = ToolCall(id="toolu_003", name="get_property_detail", input={
            "referencia": "12345",
        })
        session = AsyncMock()

        result = await executor.execute(tc, session)

        assert "id" in result or "properties" in result or "title" in result
        search_svc.get_by_ids.assert_awaited_once_with([12345], session)

    @pytest.mark.asyncio
    async def test_execute_get_property_detail_ordinal(self):
        """get_property_detail resolves 'la primera' to current_page_ids[0]."""
        executor, search_svc = self._make_executor()
        detail_result = SearchResult(
            properties=[{"id": 100, "title": "Primera propiedad"}],
            total_found=1,
        )
        search_svc.get_by_ids.return_value = detail_result

        tc = ToolCall(id="toolu_004", name="get_property_detail", input={
            "referencia": "la primera",
        })
        session = AsyncMock()
        ctx = ConversationState(current_page_ids=[100, 200])

        result = await executor.execute(tc, session, search_context=ctx)

        search_svc.get_by_ids.assert_awaited_once_with([100], session)

    @pytest.mark.asyncio
    async def test_execute_register_lead(self):
        """register_lead returns success JSON with motivo."""
        executor, search_svc = self._make_executor()

        tc = ToolCall(id="toolu_005", name="register_lead", input={
            "motivo": "Quiero visitar",
        })
        session = AsyncMock()

        result = await executor.execute(tc, session)

        assert result["success"] is True
        assert result["motivo"] == "Quiero visitar"

    @pytest.mark.asyncio
    async def test_execute_process_opt_out(self):
        """process_opt_out tool returns success dict."""
        executor, search_svc = self._make_executor()

        tc = ToolCall(id="toolu_007", name="process_opt_out", input={
            "confirmacion": "Usuario solicita baja",
        })
        session = AsyncMock()

        result = await executor.execute(tc, session)

        assert result["success"] is True
        assert result["message"] == "Opt-out registrado"

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self):
        """Unknown tool returns error JSON, not an exception."""
        executor, search_svc = self._make_executor()

        tc = ToolCall(id="toolu_006", name="unknown_tool", input={})
        session = AsyncMock()

        result = await executor.execute(tc, session)

        assert "error" in result

    def test_build_tool_result_message(self):
        """build_tool_result_message returns Anthropic SDK format."""
        executor, _ = self._make_executor()

        tc = ToolCall(id="toolu_001", name="search_properties", input={})
        result = {"properties": [], "total_found": 0}

        msg = executor.build_tool_result_message(tc, result)

        assert msg["type"] == "tool_result"
        assert msg["tool_use_id"] == "toolu_001"
        assert isinstance(msg["content"], str)
        parsed = json.loads(msg["content"])
        assert parsed["total_found"] == 0


# ===========================================================================
# TestToolExecutorPriceStats
# ===========================================================================

class TestToolExecutorPriceStats:
    """Verify ToolExecutor passes price_stats through when present."""

    def _make_executor(self) -> tuple[ToolExecutor, AsyncMock]:
        search_service = AsyncMock()
        executor = ToolExecutor(search_service)
        return executor, search_service

    @pytest.mark.asyncio
    async def test_search_result_includes_price_stats_in_tool_result(self):
        """When SearchResult has price_stats, ToolExecutor includes it in the result dict."""
        executor, search_svc = self._make_executor()

        sr = SearchResult(
            properties=[
                {"id": 100, "title": "Casa test", "price_usd": 150000},
                {"id": 101, "title": "Casa test 2", "price_usd": 160000},
            ],
            total_found=10,
            price_stats={"avg_usd": 180000.0, "min_usd": 100000.0, "max_usd": 300000.0},
        )
        search_svc.search_properties.return_value = sr

        tc = ToolCall(id="toolu_ps", name="search_properties", input={
            "operacion": "venta", "ciudad": "asuncion",
        })
        session = AsyncMock()

        result = await executor.execute(tc, session)

        assert "price_stats" in result
        assert result["price_stats"]["avg_usd"] == 180000.0
        assert result["price_stats"]["min_usd"] == 100000.0
        assert result["price_stats"]["max_usd"] == 300000.0

    @pytest.mark.asyncio
    async def test_search_result_no_price_stats_when_none(self):
        """When SearchResult has no price_stats, the key is absent from result dict."""
        executor, search_svc = self._make_executor()

        sr = SearchResult(
            properties=[{"id": 100, "title": "Casa test", "price_usd": 150000}],
            total_found=5,
            price_stats=None,
        )
        search_svc.search_properties.return_value = sr

        tc = ToolCall(id="toolu_nps", name="search_properties", input={
            "operacion": "venta", "ciudad": "asuncion", "precio_max": 200000,
        })
        session = AsyncMock()

        result = await executor.execute(tc, session)

        assert "price_stats" not in result


# ===========================================================================
# Orchestrator test helpers
# ===========================================================================

def _make_orchestrator():
    """Create an Orchestrator with all dependencies mocked."""
    claude = AsyncMock()
    gemini = AsyncMock()
    circuit_breaker = MagicMock()
    circuit_breaker.is_open = False
    search_service = AsyncMock()
    conversation_manager = AsyncMock()
    # check_human_cooldown is a sync method — override with MagicMock
    conversation_manager.check_human_cooldown = MagicMock(return_value=False)
    response_builder = MagicMock()
    tool_executor = AsyncMock()
    # build_tool_result_message is a sync method — override with MagicMock
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
    """Helper to build a ContactInfo."""
    return ContactInfo(
        id=1, name="Test User", status=status, is_baja=is_baja,
        platform="telegram", source_id="12345",
    )


def _default_conversation(is_bot_active=True, last_human_reply_at=None):
    """Helper to build a ConversationInfo."""
    return ConversationInfo(
        id=10, contact_id=1, platform="telegram", chat_id="12345",
        is_bot_active=is_bot_active, last_human_reply_at=last_human_reply_at,
    )


def _default_request():
    """Helper to build a BotRequest."""
    return BotRequest(
        platform="telegram", chat_id="12345", user_id="12345",
        user_name="Test User", text="Busco casa en Asuncion",
        external_id="msg_001",
    )


def _text_ai_response(text="Hola!", model="claude-haiku", stop_reason="end_turn"):
    """Build a text-only AIResponse."""
    return AIResponse(
        text=text,
        tool_calls=[],
        model=model,
        input_tokens=100,
        output_tokens=25,
        stop_reason=stop_reason,
        raw_content=[],
    )


def _tool_use_ai_response(tool_calls=None, text=None, raw_content=None):
    """Build a tool_use AIResponse."""
    if tool_calls is None:
        tool_calls = [ToolCall(id="t1", name="search_properties", input={"ciudad": "asuncion"})]
    return AIResponse(
        text=text,
        tool_calls=tool_calls,
        model="claude-haiku",
        input_tokens=150,
        output_tokens=50,
        stop_reason="tool_use",
        raw_content=raw_content or [{"type": "tool_use", "id": "t1", "name": "search_properties", "input": {}}],
    )


def _setup_normal_flow(mocks, contact=None, conversation=None, history=None):
    """Configure mocks for a normal (non-short-circuit) flow."""
    mocks["conversation_manager"].resolve_contact.return_value = contact or _default_contact()
    mocks["conversation_manager"].get_or_create_conversation.return_value = (
        conversation or _default_conversation()
    )
    mocks["conversation_manager"].check_human_cooldown.return_value = False
    mocks["conversation_manager"].get_history.return_value = history or []
    mocks["conversation_manager"].get_search_context.return_value = ConversationState()


# ===========================================================================
# TestOrchestratorPreconditions
# ===========================================================================

class TestOrchestratorPreconditions:
    """Tests for early-exit precondition checks."""

    @pytest.mark.asyncio
    async def test_baja_contact_returns_early(self):
        """is_baja contact gets opt_out response, no Claude call."""
        orch, mocks = _make_orchestrator()
        mocks["conversation_manager"].resolve_contact.return_value = (
            _default_contact(is_baja=True, status="discarded")
        )

        with patch(
            "app.bot.core.orchestrator.get_opt_out_text",
            new=AsyncMock(return_value="opt-out text"),
        ):
            result = await orch.handle_message(_default_request(), AsyncMock())

        assert result is not None
        assert result.intent == "opt_out"
        mocks["claude"].send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_human_cooldown_returns_none(self):
        """Human cooldown active -> returns None, no Claude call."""
        orch, mocks = _make_orchestrator()
        mocks["conversation_manager"].resolve_contact.return_value = _default_contact()
        mocks["conversation_manager"].get_or_create_conversation.return_value = (
            _default_conversation()
        )
        mocks["conversation_manager"].check_human_cooldown.return_value = True

        result = await orch.handle_message(_default_request(), AsyncMock())

        assert result is None
        mocks["claude"].send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_bot_inactive_returns_none(self):
        """Bot inactive for conversation -> returns None."""
        orch, mocks = _make_orchestrator()
        mocks["conversation_manager"].resolve_contact.return_value = _default_contact()
        mocks["conversation_manager"].get_or_create_conversation.return_value = (
            _default_conversation(is_bot_active=False)
        )

        with patch(
            "app.bot.core.orchestrator.check_bot_active_locked",
            new=AsyncMock(return_value=False),
        ):
            result = await orch.handle_message(_default_request(), AsyncMock())

        assert result is None
        mocks["claude"].send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_el_orquestador_ya_no_guarda_el_entrante(self):
        """El INSERT del entrante tiene un solo dueño, y no es este.

        Estaba aca, debajo de `is_bot_active` y del cooldown humano, asi que
        cualquiera de los dos hacia desaparecer el mensaje del cliente. Lo
        guarda `persist_inbound` en el webhook, antes del grafo y antes de toda
        compuerta (ver tests/bot/test_entrante_nunca_se_pierde.py).
        """
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)
        mocks["claude"].send_message.return_value = _text_ai_response()

        request = _default_request()
        await orch.handle_message(request, AsyncMock())

        mocks["conversation_manager"].save_inbound_message.assert_not_awaited()


# ===========================================================================
# TestOrchestratorTextOnly
# ===========================================================================

class TestOrchestratorTextOnly:
    """Tests for text-only Claude responses (no tool use)."""

    @pytest.mark.asyncio
    async def test_text_response_saludo(self):
        """Text-only Claude response produces BotResponse with text."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)
        mocks["claude"].send_message.return_value = _text_ai_response("Hola! Soy el asistente.")

        result = await orch.handle_message(_default_request(), AsyncMock())

        assert result is not None
        assert "Hola" in result.text

    @pytest.mark.asyncio
    async def test_text_response_saves_outbound(self):
        """After text response, outbound message is saved."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)
        mocks["claude"].send_message.return_value = _text_ai_response("Respuesta test")

        await orch.handle_message(_default_request(), AsyncMock())

        mocks["conversation_manager"].save_outbound_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_circuit_breaker_success_recorded(self):
        """Successful Claude call records success on circuit breaker."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)
        mocks["claude"].send_message.return_value = _text_ai_response()

        await orch.handle_message(_default_request(), AsyncMock())

        mocks["circuit_breaker"].record_success.assert_called()

    @pytest.mark.asyncio
    async def test_auto_status_advances_new_to_bot_replied(self):
        """Contact with status='new' is advanced to 'bot_replied' after bot outbound."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks, contact=_default_contact(status="new"))
        mocks["claude"].send_message.return_value = _text_ai_response()

        session = AsyncMock()
        await orch.handle_message(_default_request(), session)

        execute_calls = session.execute.call_args_list
        sql_texts = [
            getattr(c[0][0], "text", str(c[0][0])) if c[0] else ""
            for c in execute_calls
        ]
        bot_replied_found = any("status = 'bot_replied'" in sql for sql in sql_texts)
        assert bot_replied_found, (
            f"Expected status = 'bot_replied' in execute calls, got: {sql_texts}"
        )


# ===========================================================================
# TestOrchestratorToolUseLoop
# ===========================================================================

class TestOrchestratorToolUseLoop:
    """Tests for the Claude tool-use loop."""

    @pytest.mark.asyncio
    async def test_single_tool_call(self):
        """Single tool_use -> execute -> final text response."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)

        # First call: tool_use; second call: end_turn with text
        tool_response = _tool_use_ai_response()
        text_response = _text_ai_response("Encontre 2 casas en Asuncion.")
        mocks["claude"].send_message.side_effect = [tool_response, text_response]
        mocks["tool_executor"].execute.return_value = {
            "properties": [{"id": 100}, {"id": 101}],
            "total_found": 2,
            "all_ids": [100, 101],
        }
        mocks["tool_executor"].build_tool_result_message.return_value = {
            "type": "tool_result", "tool_use_id": "t1", "content": "{}",
        }

        result = await orch.handle_message(_default_request(), AsyncMock())

        assert result is not None
        assert "Encontre" in result.text
        mocks["tool_executor"].execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tool_result_message_format(self):
        """After tool execution, messages array has assistant + tool_result."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)

        raw_content = [{"type": "tool_use", "id": "t1", "name": "search_properties", "input": {}}]
        tool_response = _tool_use_ai_response(raw_content=raw_content)
        text_response = _text_ai_response("Resultado")
        mocks["claude"].send_message.side_effect = [tool_response, text_response]
        mocks["tool_executor"].execute.return_value = {"properties": [], "total_found": 0, "all_ids": []}
        mocks["tool_executor"].build_tool_result_message.return_value = {
            "type": "tool_result", "tool_use_id": "t1", "content": "{}",
        }

        await orch.handle_message(_default_request(), AsyncMock())

        # Second call to Claude should have the assistant + tool_result messages
        assert mocks["claude"].send_message.call_count == 2
        second_call_kwargs = mocks["claude"].send_message.call_args_list[1]
        messages = second_call_kwargs[1].get("messages") or second_call_kwargs[0][1] if len(second_call_kwargs[0]) > 1 else second_call_kwargs[1]["messages"]
        # Find assistant message with raw_content and user message with tool_result
        has_assistant_raw = any(
            m.get("role") == "assistant" and m.get("content") == raw_content
            for m in messages
        )
        has_tool_result = any(
            m.get("role") == "user" and isinstance(m.get("content"), list) and
            any(tr.get("type") == "tool_result" for tr in m["content"])
            for m in messages
        )
        assert has_assistant_raw, "Missing assistant message with raw_content"
        assert has_tool_result, "Missing user message with tool_result"

    @pytest.mark.asyncio
    async def test_max_iterations_cap(self):
        """Tool-use loop is capped at MAX_TOOL_ITERATIONS (5)."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)

        # Claude always returns tool_use
        tool_response = _tool_use_ai_response()
        mocks["claude"].send_message.return_value = tool_response
        mocks["tool_executor"].execute.return_value = {"properties": [], "total_found": 0, "all_ids": []}
        mocks["tool_executor"].build_tool_result_message.return_value = {
            "type": "tool_result", "tool_use_id": "t1", "content": "{}",
        }

        result = await orch.handle_message(_default_request(), AsyncMock())

        # Should not exceed MAX_TOOL_ITERATIONS + 1 (initial) = 6 Claude calls
        assert mocks["claude"].send_message.call_count <= 6
        assert result is not None

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_single_response(self):
        """Claude returns 2 tool_calls in one response; both are executed."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)

        two_tools = _tool_use_ai_response(
            tool_calls=[
                ToolCall(id="t1", name="search_properties", input={"ciudad": "asuncion"}),
                ToolCall(id="t2", name="get_property_detail", input={"referencia": "100"}),
            ],
            raw_content=[
                {"type": "tool_use", "id": "t1", "name": "search_properties", "input": {}},
                {"type": "tool_use", "id": "t2", "name": "get_property_detail", "input": {}},
            ],
        )
        text_response = _text_ai_response("Resultado con detalle")
        mocks["claude"].send_message.side_effect = [two_tools, text_response]
        mocks["tool_executor"].execute.return_value = {"properties": [], "total_found": 0, "all_ids": []}
        mocks["tool_executor"].build_tool_result_message.return_value = {
            "type": "tool_result", "tool_use_id": "t1", "content": "{}",
        }

        await orch.handle_message(_default_request(), AsyncMock())

        assert mocks["tool_executor"].execute.await_count == 2

    @pytest.mark.asyncio
    async def test_search_context_updated_after_search(self):
        """After search tool, update_search_context is called."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)

        tool_response = _tool_use_ai_response()
        text_response = _text_ai_response("Encontre casas")
        mocks["claude"].send_message.side_effect = [tool_response, text_response]
        mocks["tool_executor"].execute.return_value = {
            "properties": [{"id": 100}, {"id": 101}],
            "total_found": 5,
            "all_ids": [100, 101, 102, 103, 104],
        }
        mocks["tool_executor"].build_tool_result_message.return_value = {
            "type": "tool_result", "tool_use_id": "t1", "content": "{}",
        }

        await orch.handle_message(_default_request(), AsyncMock())

        mocks["conversation_manager"].update_search_context.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_relaxed_filters_in_bot_response_metadata(self):
        """Bug 2026-04-26: cuando un search relaja filtros, BotResponse.metadata
        debe incluir relaxed_filters para que ResponseBuilder no trunque el
        intro a 150 chars (donde Claude explica QUÉ se relajó).
        """
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)

        tool_response = _tool_use_ai_response()
        text_response = _text_ai_response(
            "No encontré en Villa Morra. Lo más cercano son zonas vecinas."
        )
        mocks["claude"].send_message.side_effect = [tool_response, text_response]
        mocks["tool_executor"].execute.return_value = {
            "properties": [{"id": 100}],
            "total_found": 1,
            "all_ids": [100],
            "degradation_level": 3,
            "relaxed_filters": [
                "barrio Villa Morra eliminado, búsqueda ampliada a toda la ciudad",
            ],
        }
        mocks["tool_executor"].build_tool_result_message.return_value = {
            "type": "tool_result", "tool_use_id": "t1", "content": "{}",
        }

        result = await orch.handle_message(_default_request(), AsyncMock())

        assert result is not None
        assert "relaxed_filters" in result.metadata
        assert result.metadata["relaxed_filters"] == [
            "barrio Villa Morra eliminado, búsqueda ampliada a toda la ciudad"
        ]

    @pytest.mark.asyncio
    async def test_no_relaxed_filters_in_metadata_when_search_not_degraded(self):
        """Sin relajación, metadata['relaxed_filters'] queda como lista vacía
        — ResponseBuilder debe seguir aplicando el truncado de 150 chars.
        """
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)

        tool_response = _tool_use_ai_response()
        text_response = _text_ai_response("Encontre 2 casas.")
        mocks["claude"].send_message.side_effect = [tool_response, text_response]
        mocks["tool_executor"].execute.return_value = {
            "properties": [{"id": 100}, {"id": 101}],
            "total_found": 2,
            "all_ids": [100, 101],
        }
        mocks["tool_executor"].build_tool_result_message.return_value = {
            "type": "tool_result", "tool_use_id": "t1", "content": "{}",
        }

        result = await orch.handle_message(_default_request(), AsyncMock())

        assert result is not None
        # Either absent or empty list — both keep ResponseBuilder truncating
        assert not result.metadata.get("relaxed_filters")

    @pytest.mark.asyncio
    async def test_lead_detection(self):
        """register_lead tool sets BotResponse.is_lead=True."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)

        lead_tool = _tool_use_ai_response(
            tool_calls=[ToolCall(id="t1", name="register_lead", input={"motivo": "Quiero visitar"})],
            raw_content=[{"type": "tool_use", "id": "t1", "name": "register_lead", "input": {}}],
        )
        text_response = _text_ai_response("Un asesor te contactara.")
        mocks["claude"].send_message.side_effect = [lead_tool, text_response]
        mocks["tool_executor"].execute.return_value = {
            "success": True, "motivo": "Quiero visitar", "message": "Lead registrado",
        }
        mocks["tool_executor"].build_tool_result_message.return_value = {
            "type": "tool_result", "tool_use_id": "t1", "content": "{}",
        }

        result = await orch.handle_message(_default_request(), AsyncMock())

        assert result is not None
        assert result.is_lead is True

    @pytest.mark.asyncio
    async def test_polish05_lead_ref_emitted_on_empty_text_fallback(self):
        """POLISH-05: an is_lead turn whose final Claude text is empty (static
        template fallback) must still emit LEAD-{contact.id} in the reply so the
        lead is trackable."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)  # _default_contact() -> id=1

        lead_tool = _tool_use_ai_response(
            tool_calls=[ToolCall(id="t1", name="register_lead", input={"motivo": "x"})],
            raw_content=[{"type": "tool_use", "id": "t1", "name": "register_lead", "input": {}}],
        )
        # Empty final text -> static-template fallback path.
        empty_text = _text_ai_response("")
        mocks["claude"].send_message.side_effect = [lead_tool, empty_text]
        mocks["tool_executor"].execute.return_value = {
            "success": True, "motivo": "x", "message": "Lead registrado",
        }
        mocks["tool_executor"].build_tool_result_message.return_value = {
            "type": "tool_result", "tool_use_id": "t1", "content": "{}",
        }

        result = await orch.handle_message(_default_request(), AsyncMock())

        assert result is not None
        assert result.is_lead is True
        assert "LEAD-" in result.text

    @pytest.mark.asyncio
    async def test_polish05_lead_ref_not_duplicated_when_prose_has_it(self):
        """POLISH-05: when Claude's prose already contains LEAD-{contact.id}, the
        code must NOT append a second one — idempotent."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)  # _default_contact() -> id=1

        lead_tool = _tool_use_ai_response(
            tool_calls=[ToolCall(id="t1", name="register_lead", input={"motivo": "x"})],
            raw_content=[{"type": "tool_use", "id": "t1", "name": "register_lead", "input": {}}],
        )
        # Prose already contains the literal LEAD-1 token.
        prose = _text_ai_response("Listo, queda registrada con el código LEAD-1.")
        mocks["claude"].send_message.side_effect = [lead_tool, prose]
        mocks["tool_executor"].execute.return_value = {
            "success": True, "motivo": "x", "message": "Lead registrado",
        }
        mocks["tool_executor"].build_tool_result_message.return_value = {
            "type": "tool_result", "tool_use_id": "t1", "content": "{}",
        }

        result = await orch.handle_message(_default_request(), AsyncMock())

        assert result is not None
        assert result.is_lead is True
        assert result.text.count("LEAD-1") == 1


# ===========================================================================
# TestOrchestratorFallback
# ===========================================================================

class TestOrchestratorFallback:
    """Tests for circuit breaker and Gemini fallback."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_open_uses_gemini(self):
        """When circuit breaker is open, Gemini is called, not Claude."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)
        mocks["circuit_breaker"].is_open = True
        mocks["gemini"].send_message.return_value = _text_ai_response(
            "Respuesta Gemini", model="gemini-flash",
        )

        await orch.handle_message(_default_request(), AsyncMock())

        mocks["gemini"].send_message.assert_awaited_once()
        mocks["claude"].send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_gemini_fallback_text_only(self):
        """Gemini fallback produces text response with gemini model."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)
        mocks["circuit_breaker"].is_open = True
        mocks["gemini"].send_message.return_value = _text_ai_response(
            "Respuesta Gemini", model="gemini-flash",
        )

        result = await orch.handle_message(_default_request(), AsyncMock())

        assert result is not None
        assert "Gemini" in result.text
        assert "gemini" in result.ai_model

    @pytest.mark.asyncio
    async def test_claude_error_records_failure(self):
        """Anthropic API exception records failure on circuit breaker.

        Post M4 Task 2.2: solo errores del SDK de Anthropic (no Exception
        genérica) disparan record_failure + fallback a Gemini.
        """
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)
        mocks["claude"].send_message.side_effect = APIConnectionError(request=MagicMock())
        mocks["gemini"].send_message.return_value = _text_ai_response(
            "Fallback response", model="gemini-flash",
        )

        await orch.handle_message(_default_request(), AsyncMock())

        mocks["circuit_breaker"].record_failure.assert_called()

    @pytest.mark.asyncio
    async def test_claude_error_gemini_fallback(self):
        """Anthropic API error triggers Gemini fallback; response comes from Gemini."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)
        mocks["claude"].send_message.side_effect = APIConnectionError(request=MagicMock())
        mocks["gemini"].send_message.return_value = _text_ai_response(
            "Respuesta de emergencia", model="gemini-flash",
        )

        result = await orch.handle_message(_default_request(), AsyncMock())

        assert result is not None
        assert "emergencia" in result.text
        mocks["gemini"].send_message.assert_awaited_once()


# ===========================================================================
# TestOrchestratorHistory
# ===========================================================================

class TestOrchestratorHistory:
    """Tests for conversation history and prompt integration."""

    @pytest.mark.asyncio
    async def test_history_included_in_messages(self):
        """Conversation history is included in Claude messages array."""
        orch, mocks = _make_orchestrator()
        history = [
            HistoryMessage(direction="inbound", sender_type="contact", body="Busco casa"),
            HistoryMessage(direction="outbound", sender_type="bot", body="Encontre opciones"),
        ]
        _setup_normal_flow(mocks, history=history)
        mocks["claude"].send_message.return_value = _text_ai_response()

        await orch.handle_message(_default_request(), AsyncMock())

        call_kwargs = mocks["claude"].send_message.call_args
        messages = call_kwargs[1].get("messages") or call_kwargs[0][1] if len(call_kwargs[0]) > 1 else call_kwargs[1]["messages"]
        # Should have history messages + current message
        assert len(messages) >= 3  # 2 history + 1 current
        # First message should be user (from history)
        assert messages[0]["role"] == "user"
        assert "Busco casa" in messages[0]["content"]

    @pytest.mark.asyncio
    async def test_system_prompt_sent(self):
        """Claude is called with a non-empty system prompt."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)
        mocks["claude"].send_message.return_value = _text_ai_response()

        await orch.handle_message(_default_request(), AsyncMock())

        call_kwargs = mocks["claude"].send_message.call_args
        system = call_kwargs[1].get("system") or call_kwargs[0][0]
        assert system is not None
        assert len(system) > 0

    @pytest.mark.asyncio
    async def test_dynamic_prompt_includes_search_context(self):
        """When pending results exist, system prompt includes context section."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(
            resultados_pendientes=[201, 202, 203],
            shown_properties=[100, 101],
            filtros={"operacion": "alquiler", "ciudad": "asuncion"},
            etapa="mostrando_resultados",
        )
        _setup_normal_flow(mocks)
        mocks["conversation_manager"].get_search_context.return_value = ctx
        mocks["claude"].send_message.return_value = _text_ai_response("Te muestro más")

        await orch.handle_message(
            BotRequest(
                platform="whatsapp", chat_id="+5959999", user_id="+5959999",
                user_name="Test", text="Hola qué tal",
                external_id="msg_dyn_001",
            ),
            AsyncMock(),
        )

        call_kwargs = mocks["claude"].send_message.call_args
        system = call_kwargs[1].get("system") or call_kwargs[0][0]
        # system is now list[dict] — check dynamic block (index 1) contains context
        system_text = system if isinstance(system, str) else " ".join(b.get("text", "") for b in system)
        assert "CONTEXTO DE BÚSQUEDA ACTUAL" in system_text
        assert "alquiler" in system_text
        assert "Pendientes de mostrar: 3" in system_text

    @pytest.mark.asyncio
    async def test_tools_sent_to_claude(self):
        """Claude is called with tools parameter."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)
        mocks["claude"].send_message.return_value = _text_ai_response()

        await orch.handle_message(_default_request(), AsyncMock())

        call_kwargs = mocks["claude"].send_message.call_args
        tools = call_kwargs[1].get("tools")
        assert tools is not None
        assert len(tools) >= 3  # search_properties, get_property_detail, register_lead


# ===========================================================================
# TestOrchestratorOptOut
# ===========================================================================

def _setup_opt_out_flow(mocks, contact=None):
    """Configure mocks for an opt-out flow where Claude calls process_opt_out."""
    _setup_normal_flow(mocks, contact=contact or _default_contact(status="contacted"))

    opt_out_tool = _tool_use_ai_response(
        tool_calls=[ToolCall(id="t1", name="process_opt_out", input={"confirmacion": "baja"})],
        raw_content=[{"type": "tool_use", "id": "t1", "name": "process_opt_out", "input": {}}],
    )
    text_response = _text_ai_response("opt-out acknowledged")
    mocks["claude"].send_message.side_effect = [opt_out_tool, text_response]
    mocks["tool_executor"].execute.return_value = {
        "success": True, "message": "Opt-out registrado",
    }
    mocks["tool_executor"].build_tool_result_message.return_value = {
        "type": "tool_result", "tool_use_id": "t1", "content": "{}",
    }


class TestOrchestratorOptOut:
    """Tests for the opt-out flow triggered by process_opt_out tool."""

    @pytest.mark.asyncio
    async def test_opt_out_tool_sets_intent(self):
        """When Claude calls process_opt_out, BotResponse has intent == 'opt_out'."""
        orch, mocks = _make_orchestrator()
        _setup_opt_out_flow(mocks)

        result = await orch.handle_message(_default_request(), AsyncMock())

        assert result is not None
        assert result.intent == "opt_out"

    @pytest.mark.asyncio
    async def test_opt_out_writes_baja_at(self):
        """After process_opt_out tool, session.execute is called with UPDATE contacts SET baja_at."""
        orch, mocks = _make_orchestrator()
        _setup_opt_out_flow(mocks)

        session = AsyncMock()
        await orch.handle_message(_default_request(), session)

        # Check that session.execute was called with baja_at SQL
        # sa_text() creates TextClause objects — use .text attribute
        execute_calls = session.execute.call_args_list
        sql_texts = [
            getattr(c[0][0], "text", str(c[0][0])) if c[0] else ""
            for c in execute_calls
        ]
        baja_sql_found = any(
            "baja_at" in sql and "UPDATE contacts" in sql
            for sql in sql_texts
        )
        assert baja_sql_found, (
            f"Expected UPDATE contacts SET baja_at in execute calls, got: {sql_texts}"
        )

    @pytest.mark.asyncio
    async def test_opt_out_deactivates_conversations(self):
        """After opt-out, UPDATE conversations SET is_bot_active = false."""
        orch, mocks = _make_orchestrator()
        _setup_opt_out_flow(mocks)

        session = AsyncMock()
        await orch.handle_message(_default_request(), session)

        execute_calls = session.execute.call_args_list
        sql_texts = [
            getattr(c[0][0], "text", str(c[0][0])) if c[0] else ""
            for c in execute_calls
        ]
        conv_sql_found = any(
            "is_bot_active" in sql and "UPDATE conversations" in sql
            for sql in sql_texts
        )
        assert conv_sql_found, (
            f"Expected UPDATE conversations SET is_bot_active in execute calls, got: {sql_texts}"
        )

    @pytest.mark.asyncio
    async def test_opt_out_creates_lead_event(self):
        """After opt-out, INSERT INTO lead_events with event_type = 'opt_out'."""
        orch, mocks = _make_orchestrator()
        _setup_opt_out_flow(mocks)

        session = AsyncMock()
        await orch.handle_message(_default_request(), session)

        execute_calls = session.execute.call_args_list
        sql_texts = [
            getattr(c[0][0], "text", str(c[0][0])) if c[0] else ""
            for c in execute_calls
        ]
        event_sql_found = any(
            "lead_events" in sql and "opt_out" in sql
            for sql in sql_texts
        )
        assert event_sql_found, (
            f"Expected INSERT INTO lead_events with opt_out in execute calls, got: {sql_texts}"
        )

    @pytest.mark.asyncio
    async def test_opt_out_skips_auto_advance(self):
        """Opt-out on a 'new' contact does NOT auto-advance to 'bot_replied'."""
        orch, mocks = _make_orchestrator()
        _setup_opt_out_flow(mocks, contact=_default_contact(status="new"))

        session = AsyncMock()
        await orch.handle_message(_default_request(), session)

        execute_calls = session.execute.call_args_list
        sql_texts = [
            getattr(c[0][0], "text", str(c[0][0])) if c[0] else ""
            for c in execute_calls
        ]
        # Should NOT find the auto-advance SQL (status = 'bot_replied')
        auto_advance_found = any(
            "status = 'bot_replied'" in sql
            for sql in sql_texts
        )
        assert not auto_advance_found, (
            "Opt-out should NOT auto-advance new -> bot_replied"
        )


# ===========================================================================
# TestOrchestratorLeadRegistration
# ===========================================================================

def _setup_lead_flow(mocks, contact=None, search_context=None, history=None,
                     profile_response=None):
    """Configure mocks for a lead registration flow with profiling.

    Claude calls register_lead tool, then returns final text, then
    gets called again for profiling (SUMMARIZER_PROMPT).
    """
    ctx = search_context or ConversationState()
    _setup_normal_flow(mocks, contact=contact or _default_contact(status="contacted"),
                       history=history)
    mocks["conversation_manager"].get_search_context.return_value = ctx

    lead_tool = _tool_use_ai_response(
        tool_calls=[ToolCall(id="t1", name="register_lead", input={"motivo": "Quiero visitar"})],
        raw_content=[{"type": "tool_use", "id": "t1", "name": "register_lead", "input": {}}],
    )
    text_response = _text_ai_response("Un asesor te contactara.")
    summarizer_response = profile_response or _text_ai_response(
        '{"perfil": "familia joven", "interes": "casa en Asuncion", "notas": "urgente"}',
    )
    mocks["claude"].send_message.side_effect = [lead_tool, text_response, summarizer_response]
    mocks["tool_executor"].execute.return_value = {
        "success": True, "motivo": "Quiero visitar", "message": "Lead registrado",
    }
    mocks["tool_executor"].build_tool_result_message.return_value = {
        "type": "tool_result", "tool_use_id": "t1", "content": "{}",
    }


def _extract_sql_texts(session_mock):
    """Extract SQL text strings from all session.execute calls."""
    return [
        getattr(c[0][0], "text", str(c[0][0])) if c[0] else ""
        for c in session_mock.execute.call_args_list
    ]


class TestOrchestratorLeadRegistration:
    """Tests for lead registration DB writes and profiling."""

    @pytest.mark.asyncio
    async def test_register_lead_writes_status_and_event(self):
        """register_lead advances status to 'interested' and inserts lead_event."""
        orch, mocks = _make_orchestrator()
        _setup_lead_flow(mocks)

        session = AsyncMock()
        await orch.handle_message(_default_request(), session)

        sql_texts = _extract_sql_texts(session)

        # Verify UPDATE contacts SET status = 'interested'
        status_update = any(
            "UPDATE contacts" in sql and "interested" in sql
            for sql in sql_texts
        )
        assert status_update, (
            f"Expected UPDATE contacts SET status = 'interested', got: {sql_texts}"
        )

        # Verify INSERT INTO lead_events with 'lead_registered'
        event_insert = any(
            "lead_events" in sql and "lead_registered" in sql
            for sql in sql_texts
        )
        assert event_insert, (
            f"Expected INSERT INTO lead_events with lead_registered, got: {sql_texts}"
        )

    @pytest.mark.asyncio
    async def test_register_lead_does_not_downgrade_interested(self):
        """Contact already 'interested' is NOT downgraded — SQL guard prevents it."""
        orch, mocks = _make_orchestrator()
        _setup_lead_flow(mocks, contact=_default_contact(status="interested"))

        session = AsyncMock()
        await orch.handle_message(_default_request(), session)

        sql_texts = _extract_sql_texts(session)

        # The UPDATE has a WHERE status IN (...) guard,
        # so 'interested' contacts won't be modified.
        # Verify the guard is present in the SQL.
        status_sql = [sql for sql in sql_texts if "UPDATE contacts" in sql and "interested" in sql]
        assert len(status_sql) >= 1, "Should still execute the UPDATE (guard in WHERE clause)"
        assert "bot_replied" in status_sql[0], (
            f"Expected status guard in SQL, got: {status_sql[0]}"
        )

    @pytest.mark.asyncio
    async def test_register_lead_captures_property_id(self):
        """lead_event metadata includes property_id from search_context."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(last_detalle_id=42)
        _setup_lead_flow(mocks, search_context=ctx)

        session = AsyncMock()
        await orch.handle_message(_default_request(), session)

        # Find the INSERT INTO lead_events call and check its params
        for call_args in session.execute.call_args_list:
            sql_text = getattr(call_args[0][0], "text", str(call_args[0][0])) if call_args[0] else ""
            if "lead_events" in sql_text and "lead_registered" in sql_text:
                params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1]
                metadata_str = params.get("metadata", "{}")
                assert "42" in metadata_str, (
                    f"Expected property_id=42 in metadata, got: {metadata_str}"
                )
                assert "Quiero visitar" in metadata_str, (
                    f"Expected motivo in metadata, got: {metadata_str}"
                )
                return
        pytest.fail("INSERT INTO lead_events with lead_registered not found")

    @pytest.mark.asyncio
    async def test_lead_profiling_calls_claude_and_saves(self):
        """After lead registration, Claude is called with SUMMARIZER_PROMPT and preferences are saved."""
        orch, mocks = _make_orchestrator()
        history = [
            HistoryMessage(direction="inbound", sender_type="contact", body="Busco casa en Asuncion"),
            HistoryMessage(direction="outbound", sender_type="bot", body="Encontre opciones"),
        ]
        _setup_lead_flow(mocks, history=history)

        session = AsyncMock()
        await orch.handle_message(_default_request(), session)

        # Claude should be called 3 times: tool_use, final text, profiling
        assert mocks["claude"].send_message.call_count == 3

        # Third call should have SUMMARIZER_PROMPT as system
        third_call = mocks["claude"].send_message.call_args_list[2]
        system_prompt = third_call[1].get("system", "")
        assert "perfil" in system_prompt.lower() or "resumen" in system_prompt.lower(), (
            f"Expected SUMMARIZER_PROMPT in third call, got system='{system_prompt[:100]}'"
        )

        # Verify preferences UPDATE on contacts
        sql_texts = _extract_sql_texts(session)
        prefs_update = any(
            "UPDATE contacts" in sql and "preferences" in sql
            for sql in sql_texts
        )
        assert prefs_update, (
            f"Expected UPDATE contacts SET preferences, got: {sql_texts}"
        )

    @pytest.mark.asyncio
    async def test_lead_profiling_failure_is_non_fatal(self):
        """Profiling exception does NOT break the pipeline; response is still returned."""
        orch, mocks = _make_orchestrator()
        # Make the third Claude call (profiling) raise an exception
        lead_tool = _tool_use_ai_response(
            tool_calls=[ToolCall(id="t1", name="register_lead", input={"motivo": "Quiero visitar"})],
            raw_content=[{"type": "tool_use", "id": "t1", "name": "register_lead", "input": {}}],
        )
        text_response = _text_ai_response("Un asesor te contactara.")
        _setup_normal_flow(mocks, contact=_default_contact(status="contacted"))
        mocks["claude"].send_message.side_effect = [
            lead_tool, text_response, Exception("Profiling failed"),
        ]
        mocks["tool_executor"].execute.return_value = {
            "success": True, "motivo": "Quiero visitar", "message": "Lead registrado",
        }
        mocks["tool_executor"].build_tool_result_message.return_value = {
            "type": "tool_result", "tool_use_id": "t1", "content": "{}",
        }

        session = AsyncMock()
        result = await orch.handle_message(_default_request(), session)

        # Pipeline should complete successfully despite profiling failure
        assert result is not None
        assert result.is_lead is True
        assert "asesor" in result.text.lower()

    # -----------------------------------------------------------------------
    # Fase 12: lead_profile_failed event instrumentation
    # -----------------------------------------------------------------------

    def _setup_lead_flow_with_profile_response(self, mocks, profile_text: str):
        """Configure mocks for a lead flow where profiling returns *profile_text*."""
        lead_tool = _tool_use_ai_response(
            tool_calls=[ToolCall(id="t1", name="register_lead", input={"motivo": "Quiero visitar"})],
            raw_content=[{"type": "tool_use", "id": "t1", "name": "register_lead", "input": {}}],
        )
        text_response = _text_ai_response("Un asesor te contactara.")
        summarizer_response = _text_ai_response(profile_text)
        _setup_normal_flow(mocks, contact=_default_contact(status="contacted"))
        mocks["claude"].send_message.side_effect = [lead_tool, text_response, summarizer_response]
        mocks["tool_executor"].execute.return_value = {
            "success": True, "motivo": "Quiero visitar", "message": "Lead registrado",
        }
        mocks["tool_executor"].build_tool_result_message.return_value = {
            "type": "tool_result", "tool_use_id": "t1", "content": "{}",
        }

    @pytest.mark.asyncio
    async def test_lead_profile_json_decode_error_writes_failed_event(self):
        """JSONDecodeError in profiling → lead_profile_failed event inserted, no TG alert."""
        orch, mocks = _make_orchestrator()
        self._setup_lead_flow_with_profile_response(mocks, "not a json")

        session = AsyncMock()
        result = await orch.handle_message(_default_request(), session)

        # Main pipeline is still successful
        assert result is not None
        assert result.is_lead is True

        # Find the lead_profile_failed INSERT
        sql_texts = _extract_sql_texts(session)
        failed_inserts = [
            (sql, call_args[0][1] if len(call_args[0]) > 1 else call_args[1])
            for sql, call_args in zip(sql_texts, session.execute.call_args_list)
            if "lead_profile_failed" in sql
        ]
        assert failed_inserts, (
            f"Expected INSERT with lead_profile_failed, found sql_texts={sql_texts}"
        )
        _sql, params = failed_inserts[0]
        assert params["id"] == 1, f"Expected contact_id=1, got {params['id']}"
        meta = json.loads(params["meta"])
        assert "reason" in meta, f"Expected 'reason' in meta, got {meta}"
        assert "profile_text_snippet" in meta, f"Expected 'profile_text_snippet' in meta, got {meta}"
        assert "not a json" in meta["profile_text_snippet"], (
            f"Expected raw text in snippet, got {meta['profile_text_snippet']}"
        )

        # No TG admin notification
        mocks["conversation_manager"].resolve_contact.assert_awaited()  # sanity
        # AdminNotifier is imported lazily inside the except block — verify
        # it is NOT called by checking no outgoing notify_new_lead on notifier.
        # Since notify_new_lead is called for a real lead registered event,
        # the best signal is that the test completes without raising and
        # we have NOT monkeypatched a notifier that would raise.

    @pytest.mark.asyncio
    async def test_lead_profile_empty_response_writes_failed_event(self):
        """Empty profiling response → lead_profile_failed event inserted."""
        orch, mocks = _make_orchestrator()
        self._setup_lead_flow_with_profile_response(mocks, "")

        session = AsyncMock()
        result = await orch.handle_message(_default_request(), session)

        assert result is not None
        assert result.is_lead is True

        sql_texts = _extract_sql_texts(session)
        failed_inserts = [sql for sql in sql_texts if "lead_profile_failed" in sql]
        assert failed_inserts, (
            f"Expected INSERT with lead_profile_failed for empty response, got {sql_texts}"
        )

        # Verify metadata is present in the params
        for call_args in session.execute.call_args_list:
            sql_text = getattr(call_args[0][0], "text", str(call_args[0][0])) if call_args[0] else ""
            if "lead_profile_failed" in sql_text:
                params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1]
                meta = json.loads(params["meta"])
                assert "reason" in meta
                assert "profile_text_snippet" in meta
                return
        pytest.fail("lead_profile_failed INSERT not found in execute calls")

    @pytest.mark.asyncio
    async def test_lead_profile_non_dict_writes_failed_event(self):
        """Profiling returns valid JSON but a list → lead_profile_failed with 'not a dict' reason."""
        orch, mocks = _make_orchestrator()
        self._setup_lead_flow_with_profile_response(mocks, "[]")

        session = AsyncMock()
        result = await orch.handle_message(_default_request(), session)

        assert result is not None
        assert result.is_lead is True

        for call_args in session.execute.call_args_list:
            sql_text = getattr(call_args[0][0], "text", str(call_args[0][0])) if call_args[0] else ""
            if "lead_profile_failed" in sql_text:
                params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1]
                meta = json.loads(params["meta"])
                assert "not a dict" in meta["reason"], (
                    f"Expected 'not a dict' in reason, got: {meta['reason']}"
                )
                return
        pytest.fail("lead_profile_failed INSERT not found for non-dict response")

    @pytest.mark.asyncio
    async def test_lead_profile_success_does_not_write_failed_event(self):
        """Successful profiling → preferences UPDATE fires, no lead_profile_failed event."""
        orch, mocks = _make_orchestrator()
        valid_profile = '{"perfil": "familia", "zona": "Asuncion"}'
        self._setup_lead_flow_with_profile_response(mocks, valid_profile)

        session = AsyncMock()
        await orch.handle_message(_default_request(), session)

        sql_texts = _extract_sql_texts(session)

        # Preferences UPDATE should be present
        prefs_update = any(
            "UPDATE contacts" in sql and "preferences" in sql
            for sql in sql_texts
        )
        assert prefs_update, f"Expected UPDATE contacts SET preferences, got: {sql_texts}"

        # No lead_profile_failed event should be inserted
        failed_event = any("lead_profile_failed" in sql for sql in sql_texts)
        assert not failed_event, (
            f"Expected NO lead_profile_failed event on success, but found one. sql_texts={sql_texts}"
        )


# ===========================================================================
# TestOrchestratorVerMas
# ===========================================================================

def _ver_mas_request():
    """Build a BotRequest with ver_mas callback."""
    return BotRequest(
        platform="telegram", chat_id="12345", user_id="12345",
        user_name="Test User", text="ver_mas",
        external_id="msg_vm_001", callback_data="ver_mas",
    )


def _setup_ver_mas_flow(mocks, pending_ids=None, search_result=None, total_found=10):
    """Configure mocks for a ver_mas pagination flow."""
    ctx = ConversationState(
        resultados_pendientes=pending_ids or [101, 102, 103, 104],
        etapa="mostrando_resultados",
        total_found=total_found,
        shown_properties=[1, 2],
        search_shown_count=2,
    )
    _setup_normal_flow(mocks, contact=_default_contact(status="contacted"))
    mocks["conversation_manager"].get_search_context.return_value = ctx
    mocks["search_service"].get_by_ids.return_value = (
        search_result or _make_search_result(2)
    )
    return ctx


class TestOrchestratorVerMas:
    """Tests for the ver_mas pagination shortcut."""

    @pytest.mark.asyncio
    async def test_ver_mas_shortcut_pops_from_pending(self):
        """ver_mas pops next 2 IDs from pending, Claude NOT called."""
        orch, mocks = _make_orchestrator()
        ctx = _setup_ver_mas_flow(mocks, pending_ids=[101, 102, 103, 104])

        result = await orch.handle_message(_ver_mas_request(), AsyncMock())

        assert result is not None
        assert result.intent == "paginacion"
        assert len(result.properties) == 2
        # Claude should NOT have been called
        mocks["claude"].send_message.assert_not_called()
        # get_by_ids should have been called with the first 2 pending IDs
        mocks["search_service"].get_by_ids.assert_awaited_once()
        call_args = mocks["search_service"].get_by_ids.call_args
        assert call_args[0][0] == [101, 102]
        # search_context should be updated
        assert ctx.resultados_pendientes == [103, 104]

    @pytest.mark.asyncio
    async def test_ver_mas_falls_through_when_no_pending(self):
        """Empty resultados_pendientes = normal AI pipeline runs."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(resultados_pendientes=[], etapa="mostrando_resultados")
        _setup_normal_flow(mocks)
        mocks["conversation_manager"].get_search_context.return_value = ctx
        mocks["claude"].send_message.return_value = _text_ai_response("Normal response")

        request = BotRequest(
            platform="telegram", chat_id="12345", user_id="12345",
            user_name="Test User", text="ver_mas",
            external_id="msg_vm_002", callback_data="ver_mas",
        )
        result = await orch.handle_message(request, AsyncMock())

        assert result is not None
        # Claude SHOULD have been called (fell through to normal pipeline)
        mocks["claude"].send_message.assert_called()
        # get_by_ids should NOT have been called (no shortcut)
        mocks["search_service"].get_by_ids.assert_not_called()

    @pytest.mark.asyncio
    async def test_ver_mas_saves_outbound_message(self):
        """ver_mas saves outbound message with intent=paginacion."""
        orch, mocks = _make_orchestrator()
        _setup_ver_mas_flow(mocks)

        await orch.handle_message(_ver_mas_request(), AsyncMock())

        mocks["conversation_manager"].save_outbound_message.assert_awaited_once()
        call_args = mocks["conversation_manager"].save_outbound_message.call_args
        # positional: session, conversation_id, contact_id, text, intent
        # search_shown_count starts at 2 (initial page) + 2 (this page) = 4
        assert "(4 de 10):" in call_args[0][3]
        assert call_args[0][4] == "paginacion"
        # keyword: properties_shown should match shown IDs
        assert call_args[1].get("properties_shown") == [100, 101]

    @pytest.mark.asyncio
    async def test_ver_mas_handles_deactivated_properties(self):
        """When get_by_ids returns empty, text-only fallback response."""
        orch, mocks = _make_orchestrator()
        empty_result = SearchResult(properties=[], total_found=0)
        _setup_ver_mas_flow(mocks, pending_ids=[999, 998], search_result=empty_result)

        result = await orch.handle_message(_ver_mas_request(), AsyncMock())

        assert result is not None
        assert result.intent == "paginacion"
        assert "No encontre" in result.text
        assert len(result.properties) == 0

    @pytest.mark.asyncio
    async def test_ver_mas_counter_uses_search_shown_count(self):
        """Counter uses search_shown_count (per-search), not cumulative shown_properties."""
        orch, mocks = _make_orchestrator()
        # Simulate: user paginó búsqueda anterior (45 props shown), nueva búsqueda con 10 resultados
        ctx = ConversationState(
            resultados_pendientes=[201, 202, 203, 204],
            etapa="mostrando_resultados",
            total_found=10,
            shown_properties=list(range(1, 48)),  # 47 from previous + current
            search_shown_count=2,  # Only 2 shown from CURRENT search
        )
        _setup_normal_flow(mocks, contact=_default_contact(status="contacted"))
        mocks["conversation_manager"].get_search_context.return_value = ctx
        mocks["search_service"].get_by_ids.return_value = _make_search_result(2)

        result = await orch.handle_message(_ver_mas_request(), AsyncMock())

        assert result is not None
        # Should be "4 de 10", NOT "49 de 10"
        assert "(4 de 10):" in result.text
        assert ctx.search_shown_count == 4


# ===========================================================================
# TestOrchestratorTextPagination
# ===========================================================================

class TestOrchestratorTextPagination:
    """Tests for free-text pagination detection (Step 5b1)."""

    @pytest.mark.asyncio
    async def test_text_pagination_triggers_ver_mas(self):
        """'Muéstrame las 14 opciones' with pending → pagination shortcut."""
        orch, mocks = _make_orchestrator()
        _setup_ver_mas_flow(mocks, pending_ids=[101, 102, 103])

        request = BotRequest(
            platform="whatsapp", chat_id="+5959999", user_id="+5959999",
            user_name="Gustavo", text="Muéstrame las 14 opciones",
            external_id="msg_tp_001",
        )
        result = await orch.handle_message(request, AsyncMock())

        assert result is not None
        assert result.intent == "paginacion"
        mocks["claude"].send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_text_pagination_ver_mas_variants(self):
        """Various free-text pagination phrases trigger shortcut."""
        phrases = [
            "ver más", "más opciones", "muéstrame más",
            "las demás", "las que faltan", "siguientes",
            "dame más", "ver todas", "muéstrame las 14 opciones",
            "las otras", "el resto", "próximas",
        ]
        for phrase in phrases:
            orch, mocks = _make_orchestrator()
            _setup_ver_mas_flow(mocks, pending_ids=[201, 202])

            request = BotRequest(
                platform="whatsapp", chat_id="+5959999", user_id="+5959999",
                user_name="Test", text=phrase, external_id="msg_tp_v",
            )
            result = await orch.handle_message(request, AsyncMock())

            assert result is not None, f"No result for: {phrase!r}"
            assert result.intent == "paginacion", (
                f"Expected paginacion for {phrase!r}, got {result.intent}"
            )
            mocks["claude"].send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_text_pagination_no_pending_falls_through(self):
        """Pagination text with NO pending results → normal AI pipeline."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(
            resultados_pendientes=[], etapa="mostrando_resultados",
        )
        _setup_normal_flow(mocks)
        mocks["conversation_manager"].get_search_context.return_value = ctx
        mocks["claude"].send_message.return_value = _text_ai_response(
            "Ok busco para vos",
        )

        request = BotRequest(
            platform="whatsapp", chat_id="+5959999", user_id="+5959999",
            user_name="Test", text="ver más opciones",
            external_id="msg_tp_np",
        )
        result = await orch.handle_message(request, AsyncMock())

        assert result is not None
        mocks["claude"].send_message.assert_called()

    def test_is_pagination_text_no_false_positive(self):
        """Normal search queries should NOT trigger pagination."""
        from app.bot.handlers.intent_detector import is_pagination_text

        non_pagination = [
            "Busco casa en Lambaré",
            "departamento en zona céntrica",
            "Quiero alquilar",
            "500 usd",
            "Hola qué tal",
            "Amenidades",
        ]
        for phrase in non_pagination:
            assert not is_pagination_text(phrase), (
                f"False positive for: {phrase!r}"
            )


# ===========================================================================
# TestOrchestratorIntentDetection
# ===========================================================================

class TestOrchestratorIntentDetection:
    """Tests for detect_intent_from_text heuristics (handlers.intent_detector)."""

    def test_detect_operacion_intent(self):
        """Asks about venta/alquiler → busqueda_incompleta_operacion."""
        from app.bot.handlers.intent_detector import detect_intent_from_text
        assert detect_intent_from_text(
            "¿Buscás para comprar o alquilar?"
        ) == "busqueda_incompleta_operacion"
        assert detect_intent_from_text(
            "¿Qué operación te interesa: venta o alquiler?"
        ) == "busqueda_incompleta_operacion"

    def test_detect_zona_intent(self):
        """Asks about location → busqueda_incompleta_zona."""
        from app.bot.handlers.intent_detector import detect_intent_from_text
        assert detect_intent_from_text(
            "¿En qué zona estás buscando?"
        ) == "busqueda_incompleta_zona"
        assert detect_intent_from_text(
            "¿En qué ciudad preferís?"
        ) == "busqueda_incompleta_zona"

    def test_detect_saludo_still_works(self):
        from app.bot.handlers.intent_detector import detect_intent_from_text
        assert detect_intent_from_text("Hola! Bienvenido") == "saludo"

    def test_detect_lead_still_works(self):
        from app.bot.handlers.intent_detector import detect_intent_from_text
        assert detect_intent_from_text("Contactar con un asesor") == "lead"

    def test_detect_conversacion_fallback(self):
        from app.bot.handlers.intent_detector import detect_intent_from_text
        assert detect_intent_from_text("Ok, entendido") == "conversacion"


# ===========================================================================
# TestOrchestratorEventTracking
# ===========================================================================

def _setup_search_tool_flow(mocks, contact=None):
    """Configure mocks for a search tool flow that produces events."""
    _setup_normal_flow(mocks, contact=contact or _default_contact(status="contacted"))

    search_tool = _tool_use_ai_response(
        tool_calls=[ToolCall(id="t1", name="search_properties", input={"ciudad": "asuncion"})],
        raw_content=[{"type": "tool_use", "id": "t1", "name": "search_properties", "input": {}}],
    )
    text_response = _text_ai_response("Encontre casas en Asuncion.")
    mocks["claude"].send_message.side_effect = [search_tool, text_response]
    mocks["tool_executor"].execute.return_value = {
        "properties": [{"id": 100}, {"id": 101}],
        "total_found": 5,
        "all_ids": [100, 101, 102, 103, 104],
    }
    mocks["tool_executor"].build_tool_result_message.return_value = {
        "type": "tool_result", "tool_use_id": "t1", "content": "{}",
    }


def _setup_detail_tool_flow(mocks, contact=None):
    """Configure mocks for a detail tool flow that produces events."""
    _setup_normal_flow(mocks, contact=contact or _default_contact(status="contacted"))

    detail_tool = _tool_use_ai_response(
        tool_calls=[ToolCall(id="t1", name="get_property_detail", input={"referencia": "100"})],
        raw_content=[{"type": "tool_use", "id": "t1", "name": "get_property_detail", "input": {}}],
    )
    text_response = _text_ai_response("Aca te muestro los detalles.")
    mocks["claude"].send_message.side_effect = [detail_tool, text_response]
    mocks["tool_executor"].execute.return_value = {
        "id": 100, "title": "Casa en Asuncion", "city": "Asuncion",
    }
    mocks["tool_executor"].build_tool_result_message.return_value = {
        "type": "tool_result", "tool_use_id": "t1", "content": "{}",
    }


class TestOrchestratorEventTracking:
    """Tests for search and detail_view event recording in lead_events."""

    @pytest.mark.asyncio
    async def test_search_event_recorded(self):
        """search_properties execution creates lead_event with event_type='search'."""
        orch, mocks = _make_orchestrator()
        _setup_search_tool_flow(mocks)

        session = AsyncMock()
        await orch.handle_message(_default_request(), session)

        # Find the event INSERT and verify params
        for call_args in session.execute.call_args_list:
            sql_text = getattr(call_args[0][0], "text", str(call_args[0][0])) if call_args[0] else ""
            if "lead_events" in sql_text and ":etype" in sql_text:
                params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1]
                if params.get("etype") == "search":
                    meta = json.loads(params["meta"])
                    assert "filters" in meta
                    assert meta["total_found"] == 5
                    assert meta["shown_ids"] == [100, 101]
                    return
        pytest.fail("search event INSERT not found in session.execute calls")

    @pytest.mark.asyncio
    async def test_detail_view_event_recorded(self):
        """get_property_detail execution creates lead_event with event_type='detail_view'."""
        orch, mocks = _make_orchestrator()
        _setup_detail_tool_flow(mocks)

        session = AsyncMock()
        await orch.handle_message(_default_request(), session)

        # Find the event INSERT and verify params
        for call_args in session.execute.call_args_list:
            sql_text = getattr(call_args[0][0], "text", str(call_args[0][0])) if call_args[0] else ""
            if "lead_events" in sql_text and ":etype" in sql_text:
                params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1]
                if params.get("etype") == "detail_view":
                    meta = json.loads(params["meta"])
                    assert meta["property_id"] == 100
                    return
        pytest.fail("detail_view event INSERT not found in session.execute calls")

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_multiple_events(self):
        """search + detail in sequence produce 2 separate event INSERTs."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks, contact=_default_contact(status="contacted"))

        # Two tool calls in one response: search + detail
        both_tools = _tool_use_ai_response(
            tool_calls=[
                ToolCall(id="t1", name="search_properties", input={"ciudad": "asuncion"}),
                ToolCall(id="t2", name="get_property_detail", input={"referencia": "100"}),
            ],
            raw_content=[
                {"type": "tool_use", "id": "t1", "name": "search_properties", "input": {}},
                {"type": "tool_use", "id": "t2", "name": "get_property_detail", "input": {}},
            ],
        )
        text_response = _text_ai_response("Resultado con detalle.")
        mocks["claude"].send_message.side_effect = [both_tools, text_response]

        # Return different results per tool call
        call_count = 0
        async def _execute_side_effect(tc, session, search_context=None):
            nonlocal call_count
            call_count += 1
            if tc.name == "search_properties":
                return {
                    "properties": [{"id": 200}, {"id": 201}],
                    "total_found": 3,
                    "all_ids": [200, 201, 202],
                }
            elif tc.name == "get_property_detail":
                return {"id": 200, "title": "Casa detalle"}
            return {}

        mocks["tool_executor"].execute = AsyncMock(side_effect=_execute_side_effect)
        mocks["tool_executor"].build_tool_result_message.return_value = {
            "type": "tool_result", "tool_use_id": "t1", "content": "{}",
        }

        session = AsyncMock()
        await orch.handle_message(_default_request(), session)

        # Count event INSERTs
        event_types_recorded = []
        for call_args in session.execute.call_args_list:
            sql_text = getattr(call_args[0][0], "text", str(call_args[0][0])) if call_args[0] else ""
            if "lead_events" in sql_text and ":etype" in sql_text:
                params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1]
                event_types_recorded.append(params.get("etype"))

        assert "search" in event_types_recorded, (
            f"Expected 'search' event, got: {event_types_recorded}"
        )
        assert "detail_view" in event_types_recorded, (
            f"Expected 'detail_view' event, got: {event_types_recorded}"
        )

    @pytest.mark.asyncio
    async def test_event_recording_failure_is_non_fatal(self):
        """Exception during event INSERT does NOT break the pipeline."""
        orch, mocks = _make_orchestrator()
        _setup_search_tool_flow(mocks)

        session = AsyncMock()
        # Make session.execute raise on the event INSERT but succeed for others
        original_execute = session.execute
        call_idx = 0

        async def _execute_with_event_failure(*args, **kwargs):
            nonlocal call_idx
            call_idx += 1
            sql_text = getattr(args[0], "text", str(args[0])) if args else ""
            if "lead_events" in sql_text and ":etype" in sql_text:
                raise RuntimeError("DB write failed")
            return await original_execute(*args, **kwargs)

        session.execute = AsyncMock(side_effect=_execute_with_event_failure)

        result = await orch.handle_message(_default_request(), session)

        # Pipeline should complete successfully despite event recording failure
        assert result is not None
        assert "Encontre" in result.text


# ===========================================================================
# TestSeguirBuscandoResetSearch
# ===========================================================================

class TestSeguirBuscandoResetSearch:
    """Tests for seguir_buscando callback resetting search_context (FIX 1)."""

    def test_seguir_buscando_in_reset_callbacks(self):
        """'seguir_buscando' must be in _RESET_SEARCH_CALLBACKS."""
        from app.bot.handlers.dispatcher import _RESET_SEARCH_CALLBACKS
        assert "seguir_buscando" in _RESET_SEARCH_CALLBACKS

    def test_seguir_buscando_not_in_callback_translations(self):
        """'seguir_buscando' must NOT be in _CALLBACK_TRANSLATIONS."""
        from app.bot.handlers.callback_resolver import _CALLBACK_TRANSLATIONS
        assert "seguir_buscando" not in _CALLBACK_TRANSLATIONS

    @pytest.mark.asyncio
    async def test_seguir_buscando_resets_context_and_calls_new_search(self):
        """seguir_buscando callback resets search_context and calls _handle_new_search."""
        orch, mocks = _make_orchestrator()

        # Set up a pre-existing search context with filters and pending results
        ctx = ConversationState(
            etapa="mostrando_resultados",
            filtros={"operacion": "venta", "ciudad": "asuncion"},
            resultados_pendientes=[201, 202, 203],
            shown_properties=[100, 101],
            current_page_ids=[100, 101],
            search_shown_count=2,
        )
        _setup_normal_flow(mocks, contact=_default_contact(status="contacted"))
        mocks["conversation_manager"].get_search_context.return_value = ctx

        request = BotRequest(
            platform="whatsapp", chat_id="+5959999", user_id="+5959999",
            user_name="Test User", text="seguir_buscando",
            external_id="msg_sb_001", callback_data="seguir_buscando",
        )

        result = await orch.handle_message(request, AsyncMock())

        # Should produce a busqueda_incompleta response (from _handle_new_search)
        assert result is not None
        assert result.intent == "busqueda_incompleta"

        # Claude should NOT have been called (shortcut path)
        mocks["claude"].send_message.assert_not_called()

        # search_context should have been reset
        assert ctx.etapa == "inicio"
        assert ctx.filtros == {}
        assert ctx.resultados_pendientes == []

        # update_search_context should have been called with the reset context
        mocks["conversation_manager"].update_search_context.assert_awaited_once()

        # save_outbound_message should have been called (from _handle_new_search)
        mocks["conversation_manager"].save_outbound_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_new_search_shortcut_advances_new_to_bot_replied(self):
        """_handle_new_search auto-advances status='new' to 'bot_replied', never 'contacted'."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks, contact=_default_contact(status="new"))

        request = BotRequest(
            platform="whatsapp", chat_id="+5959999", user_id="+5959999",
            user_name="Test User", text="seguir_buscando",
            external_id="msg_nb_001", callback_data="seguir_buscando",
        )

        session = AsyncMock()
        result = await orch.handle_message(request, session)

        assert result is not None
        assert result.intent == "busqueda_incompleta"

        execute_calls = session.execute.call_args_list
        sql_texts = [
            getattr(c[0][0], "text", str(c[0][0])) if c[0] else ""
            for c in execute_calls
        ]

        # Bug guard: the old wrong value must never appear
        contacted_found = any("status = 'contacted'" in sql for sql in sql_texts)
        assert not contacted_found, (
            f"Found illegal 'contacted' status in SQL — CHECK constraint violation: {sql_texts}"
        )

        # Fix assertion: must update to 'bot_replied'
        bot_replied_found = any("status = 'bot_replied'" in sql for sql in sql_texts)
        assert bot_replied_found, (
            f"Expected status = 'bot_replied' in execute calls, got: {sql_texts}"
        )


# ===========================================================================
# TestContextualPaginationCounter (FIX 2)
# ===========================================================================

class TestContextualPaginationCounter:
    """Tests for contextual pagination intro built from search_context.filtros."""

    @pytest.mark.asyncio
    async def test_ver_mas_with_full_context(self):
        """filtros with operacion+tipo+ciudad produce contextual intro."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(
            resultados_pendientes=[201, 202, 203, 204],
            etapa="mostrando_resultados",
            total_found=10,
            shown_properties=[1, 2],
            search_shown_count=2,
            filtros={"operacion": "alquiler", "tipo": "departamento", "ciudad": "Lambare"},
        )
        _setup_normal_flow(mocks, contact=_default_contact(status="contacted"))
        mocks["conversation_manager"].get_search_context.return_value = ctx
        mocks["search_service"].get_by_ids.return_value = _make_search_result(2)

        result = await orch.handle_message(_ver_mas_request(), AsyncMock())

        assert result is not None
        assert "departamentos en alquiler en Lambare" in result.text
        assert "(4 de 10):" in result.text
        assert result.text.startswith("Te muestro")

    @pytest.mark.asyncio
    async def test_ver_mas_without_zona(self):
        """filtros with operacion+tipo but no ciudad/barrio."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(
            resultados_pendientes=[201, 202, 203, 204],
            etapa="mostrando_resultados",
            total_found=8,
            shown_properties=[1, 2],
            search_shown_count=2,
            filtros={"operacion": "venta", "tipo": "casa"},
        )
        _setup_normal_flow(mocks, contact=_default_contact(status="contacted"))
        mocks["conversation_manager"].get_search_context.return_value = ctx
        mocks["search_service"].get_by_ids.return_value = _make_search_result(2)

        result = await orch.handle_message(_ver_mas_request(), AsyncMock())

        assert result is not None
        assert "casas en venta" in result.text
        assert "(4 de 8):" in result.text

    @pytest.mark.asyncio
    async def test_ver_mas_empty_filtros(self):
        """Empty filtros fall back to 'opciones'."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(
            resultados_pendientes=[201, 202, 203, 204],
            etapa="mostrando_resultados",
            total_found=6,
            shown_properties=[1, 2],
            search_shown_count=2,
            filtros={},
        )
        _setup_normal_flow(mocks, contact=_default_contact(status="contacted"))
        mocks["conversation_manager"].get_search_context.return_value = ctx
        mocks["search_service"].get_by_ids.return_value = _make_search_result(2)

        result = await orch.handle_message(_ver_mas_request(), AsyncMock())

        assert result is not None
        assert "opciones" in result.text
        assert "(4 de 6):" in result.text

    @pytest.mark.asyncio
    async def test_ver_mas_last_page_context(self):
        """Last page (no remaining) uses 'Estas son las ultimas' prefix."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(
            resultados_pendientes=[201, 202],  # exactly 2 → remaining will be []
            etapa="mostrando_resultados",
            total_found=6,
            shown_properties=[1, 2, 3, 4],
            search_shown_count=4,
            filtros={"operacion": "venta", "tipo": "terreno", "barrio": "San Bernardino"},
        )
        _setup_normal_flow(mocks, contact=_default_contact(status="contacted"))
        mocks["conversation_manager"].get_search_context.return_value = ctx
        mocks["search_service"].get_by_ids.return_value = _make_search_result(2)

        result = await orch.handle_message(_ver_mas_request(), AsyncMock())

        assert result is not None
        assert result.text.startswith("Estas son las")
        assert "terrenos en venta en San Bernardino" in result.text
        assert "(6 de 6):" in result.text


# ===========================================================================
# TestOrchestratorSaludoReset — FIX 1: saludo resets search_context
# ===========================================================================

class TestOrchestratorSaludoReset:
    """Tests that a saludo intent resets stale search_context."""

    @pytest.mark.asyncio
    async def test_saludo_resets_search_context(self):
        """When Claude returns a saludo with no pending results, search_context is reset.

        FIX 1 (updated by F-06): After intent == 'saludo' AND resultados_pendientes
        is empty, stale filtros, current_page_ids, and shown_properties are cleared
        so they don't bleed into the next search.

        Note: If resultados_pendientes is non-empty (active search), the reset is
        skipped — see TestF06SaludoNoResetActiveSearch for those cases.
        """
        orch, mocks = _make_orchestrator()

        # Pre-populate search_context with stale data from a COMPLETED search
        # (no pending results — user already saw all results)
        stale_ctx = ConversationState(
            etapa="mostrando_resultados",
            filtros={"operacion": "venta", "tipo": "casa", "barrio": "Villa Morra"},
            resultados_pendientes=[],  # no pending — reset is safe
            current_page_ids=[100, 101],
            shown_properties=[100, 101],
            search_shown_count=2,
            total_found=2,
        )
        _setup_normal_flow(mocks)
        mocks["conversation_manager"].get_search_context.return_value = stale_ctx

        # Claude responds with a greeting (text-only, no tool calls)
        mocks["claude"].send_message.return_value = _text_ai_response(
            "Hola! Bienvenido a Onnix SA. En que puedo ayudarte?",
        )

        request = BotRequest(
            platform="whatsapp", chat_id="+595981999999",
            user_id="+595981999999", user_name="Test User",
            text="Hola", external_id="msg_saludo_001",
        )
        await orch.handle_message(request, AsyncMock())

        # update_search_context should have been called with reset values
        mocks["conversation_manager"].update_search_context.assert_awaited()
        call_args = mocks["conversation_manager"].update_search_context.call_args
        # The search_context passed should be the third positional arg (session, conv_id, ctx)
        updated_ctx = call_args[0][2] if len(call_args[0]) > 2 else call_args[1].get("search_context", call_args[0][-1])

        assert updated_ctx.etapa == "inicio"
        assert updated_ctx.filtros == {}
        assert updated_ctx.resultados_pendientes == []
        assert updated_ctx.current_page_ids == []
        assert updated_ctx.shown_properties == []
        assert updated_ctx.search_shown_count == 0
        assert updated_ctx.total_found == 0

    @pytest.mark.asyncio
    async def test_saludo_intent_detected_from_hola_response(self):
        """Claude text containing 'Hola' is classified as saludo intent."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)
        mocks["claude"].send_message.return_value = _text_ai_response(
            "Hola! Soy el asistente inmobiliario de Onnix SA.",
        )

        result = await orch.handle_message(_default_request(), AsyncMock())

        assert result is not None
        assert result.intent == "saludo"

    @pytest.mark.asyncio
    async def test_saludo_intent_detected_from_bienvenido_response(self):
        """Claude text containing 'bienvenido' is classified as saludo intent."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)
        mocks["claude"].send_message.return_value = _text_ai_response(
            "Bienvenido! Soy tu asistente de bienes raices.",
        )

        result = await orch.handle_message(_default_request(), AsyncMock())

        assert result is not None
        assert result.intent == "saludo"

    @pytest.mark.asyncio
    async def test_non_saludo_does_not_reset_context(self):
        """A conversacion response does NOT reset search_context."""
        orch, mocks = _make_orchestrator()
        stale_ctx = ConversationState(
            etapa="mostrando_resultados",
            filtros={"operacion": "venta", "tipo": "casa"},
            resultados_pendientes=[201, 202],
            current_page_ids=[100, 101],
            shown_properties=[100, 101],
        )
        _setup_normal_flow(mocks)
        mocks["conversation_manager"].get_search_context.return_value = stale_ctx

        # Claude responds with generic conversation (no saludo keywords)
        mocks["claude"].send_message.return_value = _text_ai_response(
            "Entiendo, puedo ayudarte con eso.",
        )

        await orch.handle_message(_default_request(), AsyncMock())

        # update_search_context should NOT have been called for saludo reset
        # (it might be called for other reasons, but the context should retain filtros)
        # The key test: if update_search_context was called, filtros should NOT be {}
        if mocks["conversation_manager"].update_search_context.called:
            call_args = mocks["conversation_manager"].update_search_context.call_args
            updated_ctx = call_args[0][2] if len(call_args[0]) > 2 else call_args[0][-1]
            # If it was called for some other reason, filtros should still have data
            # unless properties were collected (which they weren't in this text-only case)
            assert updated_ctx.filtros == {"operacion": "venta", "tipo": "casa"}


# ===========================================================================
# TestCallbackTranslationBuyRent — FIX 5: BTN_COMPRAR / BTN_ALQUILAR
# ===========================================================================

# TestCallbackTranslationBuyRent removed in M4 Task 1.1 — BTN_COMPRAR and
# BTN_ALQUILAR deleted from _CALLBACK_TRANSLATIONS (0 uses in 60 days).
# SEARCH_COMPRA and SEARCH_ALQUILER remain active; see
# panel/tests/bot/test_callback_translations.py for drift guard.


# ===========================================================================
# TestOrchestratorVerSimilares — FIX 2: ver_similares direct shortcut
# ===========================================================================

def _ver_similares_request():
    """Build a BotRequest with ver_similares callback."""
    return BotRequest(
        platform="telegram", chat_id="12345", user_id="12345",
        user_name="Test User", text="ver_similares",
        external_id="msg_vs_001", callback_data="ver_similares",
    )


def _setup_ver_similares_flow(mocks, filtros=None, last_detalle_id=None, shown_ids=None, search_result=None, total_found=5):
    """Configure mocks for a ver_similares shortcut flow."""
    ctx = ConversationState(
        etapa="viendo_detalle",
        filtros=filtros or {"tipo": "casa", "ciudad": "Luque", "operacion": "alquiler", "precio_max": 600},
        last_detalle_id=last_detalle_id or 755934,
        shown_properties=shown_ids or [755934],
        total_found=total_found,
    )
    _setup_normal_flow(mocks)
    mocks["conversation_manager"].get_search_context.return_value = ctx
    mocks["search_service"].search_properties.return_value = (
        search_result or _make_search_result(3)
    )
    return ctx


class TestOrchestratorVerSimilares:
    """Tests for ver_similares callback — now handled by Claude, not a shortcut.

    The _handle_ver_similares shortcut was removed in GRUPO 5.  Both
    'ver_similares' and 'VER_SIMILARES' callbacks are translated to natural
    language and forwarded to Claude so it can decide how to search.
    """

    @pytest.mark.asyncio
    async def test_ver_similares_calls_claude(self):
        """ver_similares with filtros translates callback and calls Claude."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(
            filtros={"tipo": "casa", "ciudad": "Luque", "operacion": "alquiler"},
            last_detalle_id=755934,
            shown_properties=[755934],
        )
        _setup_normal_flow(mocks)
        mocks["conversation_manager"].get_search_context.return_value = ctx
        mocks["claude"].send_message.return_value = _text_ai_response(
            "Encontre propiedades similares en Luque."
        )

        result = await orch.handle_message(_ver_similares_request(), AsyncMock())

        assert result is not None
        # Claude MUST be called (no shortcut)
        mocks["claude"].send_message.assert_called()
        # Direct search bypass must NOT happen
        mocks["search_service"].search_properties.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ver_similares_no_filtros_calls_claude(self):
        """ver_similares callback without filtros also passes to Claude."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(etapa="viendo_detalle", filtros={}, last_detalle_id=755934)
        _setup_normal_flow(mocks)
        mocks["conversation_manager"].get_search_context.return_value = ctx
        mocks["claude"].send_message.return_value = _text_ai_response("Buscando similares...")

        result = await orch.handle_message(_ver_similares_request(), AsyncMock())

        mocks["claude"].send_message.assert_called()
        mocks["search_service"].search_properties.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ver_similares_translated_text_reaches_claude(self):
        """ver_similares callback is translated before being sent to Claude."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(
            filtros={"tipo": "departamento", "operacion": "alquiler"},
        )
        _setup_normal_flow(mocks)
        mocks["conversation_manager"].get_search_context.return_value = ctx
        mocks["claude"].send_message.return_value = _text_ai_response("Buscando...")

        await orch.handle_message(_ver_similares_request(), AsyncMock())

        # The translated text must reach Claude's messages
        call_kwargs = mocks["claude"].send_message.call_args
        messages = call_kwargs[1].get("messages") or call_kwargs[0][1]
        last_user_msg = next(
            (m for m in reversed(messages) if m["role"] == "user"), None
        )
        assert last_user_msg is not None
        assert "similares" in last_user_msg["content"].lower() or \
               "similar" in last_user_msg["content"].lower(), (
            f"Expected translated text with 'similar' in last user message, got: {last_user_msg['content']!r}"
        )

    # test_btn_comprar_translated_by_orchestrator and test_btn_alquilar_translated_by_orchestrator
    # removed in M4 Task 1.1 — BTN_COMPRAR / BTN_ALQUILAR no longer in _CALLBACK_TRANSLATIONS.

    def test_search_compra_still_works(self):
        """SEARCH_COMPRA (existing) still maps correctly alongside BTN_COMPRAR."""
        from app.bot.handlers.callback_resolver import _CALLBACK_TRANSLATIONS
        assert _CALLBACK_TRANSLATIONS["SEARCH_COMPRA"] == "Quiero comprar una propiedad"

    def test_search_alquiler_still_works(self):
        """SEARCH_ALQUILER (existing) still maps correctly alongside BTN_ALQUILAR."""
        from app.bot.handlers.callback_resolver import _CALLBACK_TRANSLATIONS
        assert _CALLBACK_TRANSLATIONS["SEARCH_ALQUILER"] == "Quiero alquilar una propiedad"

    # test_ver_similares_uppercase_in_translations removed in M4 Task 1.1 —
    # VER_SIMILARES no longer in _CALLBACK_TRANSLATIONS. Callback falls back to
    # raw text which Claude still processes.

    @pytest.mark.asyncio
    async def test_ver_similares_uppercase_calls_claude(self):
        """VER_SIMILARES (uppercase, from IC v20 template) translates and calls Claude."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(
            filtros={"tipo": "casa", "operacion": "venta"},
            last_detalle_id=755934,
        )
        _setup_normal_flow(mocks)
        mocks["conversation_manager"].get_search_context.return_value = ctx
        mocks["claude"].send_message.return_value = _text_ai_response(
            "Te muestro propiedades similares."
        )

        req = BotRequest(
            platform="whatsapp", chat_id="+595981000001", user_id="+595981000001",
            user_name="Test", text="VER_SIMILARES",
            external_id="msg_vs_upper_001", callback_data="VER_SIMILARES",
        )
        result = await orch.handle_message(req, AsyncMock())

        assert result is not None
        # Claude MUST be called (no shortcut)
        mocks["claude"].send_message.assert_called()


# ===========================================================================
# TestBotInteractionLog
# ===========================================================================

class TestBotInteractionLog:
    """Tests for bot_interaction event logging on conversational messages."""

    @pytest.mark.asyncio
    async def test_conversational_message_logs_bot_interaction(self):
        """A greeting with no tool calls creates a bot_interaction lead_event."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)
        mocks["claude"].send_message.return_value = _text_ai_response("Hola! En que te puedo ayudar?")

        session = AsyncMock()
        await orch.handle_message(_default_request(), session)

        execute_calls = session.execute.call_args_list
        sql_texts = [
            getattr(c[0][0], "text", str(c[0][0])) if c[0] else ""
            for c in execute_calls
        ]
        found = any(
            "lead_events" in sql and "bot_interaction" in sql
            for sql in sql_texts
        )
        assert found, (
            f"Expected INSERT INTO lead_events with bot_interaction, got: {sql_texts}"
        )

    @pytest.mark.asyncio
    async def test_search_message_does_not_double_log_bot_interaction(self):
        """When events_to_record is non-empty (search), no extra bot_interaction is added."""
        orch, mocks = _make_orchestrator()
        _setup_search_tool_flow(mocks)

        session = AsyncMock()
        await orch.handle_message(_default_request(), session)

        execute_calls = session.execute.call_args_list
        sql_texts = [
            getattr(c[0][0], "text", str(c[0][0])) if c[0] else ""
            for c in execute_calls
        ]
        bot_interaction_calls = [
            sql for sql in sql_texts
            if "lead_events" in sql and "bot_interaction" in sql
        ]
        assert len(bot_interaction_calls) == 0, (
            f"Search flow should NOT add bot_interaction event, got: {bot_interaction_calls}"
        )

    @pytest.mark.asyncio
    async def test_lead_flow_does_not_log_bot_interaction(self):
        """When is_lead=True, no extra bot_interaction is added."""
        orch, mocks = _make_orchestrator()
        _setup_lead_flow(mocks)

        session = AsyncMock()
        await orch.handle_message(_default_request(), session)

        execute_calls = session.execute.call_args_list
        sql_texts = [
            getattr(c[0][0], "text", str(c[0][0])) if c[0] else ""
            for c in execute_calls
        ]
        bot_interaction_calls = [
            sql for sql in sql_texts
            if "lead_events" in sql and "bot_interaction" in sql
        ]
        assert len(bot_interaction_calls) == 0, (
            f"Lead flow should NOT add bot_interaction event, got: {bot_interaction_calls}"
        )

    @pytest.mark.asyncio
    async def test_opt_out_does_not_log_bot_interaction(self):
        """When is_opt_out=True, no extra bot_interaction is added."""
        orch, mocks = _make_orchestrator()
        _setup_opt_out_flow(mocks)

        session = AsyncMock()
        await orch.handle_message(_default_request(), session)

        execute_calls = session.execute.call_args_list
        sql_texts = [
            getattr(c[0][0], "text", str(c[0][0])) if c[0] else ""
            for c in execute_calls
        ]
        bot_interaction_calls = [
            sql for sql in sql_texts
            if "lead_events" in sql and "bot_interaction" in sql
        ]
        assert len(bot_interaction_calls) == 0, (
            f"Opt-out flow should NOT add bot_interaction event, got: {bot_interaction_calls}"
        )


# ===========================================================================
# TestExtractPropertyUrlInfo
# ===========================================================================

from app.bot.handlers.url_detection import extract_property_url_info  # noqa: E402


class TestExtractPropertyUrlInfo:
    """Unit tests for the URL-to-property-ID extractor (no DB, no I/O)."""

    # --- InfoCasas -----------------------------------------------------------

    def test_infocasas_full_url(self):
        """Standard InfoCasas URL with slug and numeric ID."""
        result = extract_property_url_info(
            "www.infocasas.com.py/amplio-terreno-en-esquina-zona-eje-corporativo/189190235"
        )
        assert result == {"source": "infocasas", "property_id": "189190235"}

    def test_infocasas_https_url(self):
        """InfoCasas URL with https:// prefix."""
        result = extract_property_url_info(
            "https://www.infocasas.com.py/casa-en-venta-lambare/123456789"
        )
        assert result == {"source": "infocasas", "property_id": "123456789"}

    def test_infocasas_url_embedded_in_sentence(self):
        """URL appears mid-sentence without surrounding spaces."""
        result = extract_property_url_info(
            "Quiero saber sobre esta propiedad: infocasas.com.py/terreno/987654321 gracias"
        )
        assert result == {"source": "infocasas", "property_id": "987654321"}

    def test_infocasas_short_number_not_matched(self):
        """IDs shorter than 5 digits are not matched (avoids false positives)."""
        result = extract_property_url_info(
            "infocasas.com.py/algo/1234"
        )
        assert result is None

    # --- Onnix -----------------------------------------------------------

    def test_onnix_url_plain(self):
        """Onnix URL: numeric ID only."""
        result = extract_property_url_info(
            "https://onnix.com.py/propiedad/39711"
        )
        assert result == {"source": "onnixpy", "property_id": "39711"}

    def test_onnix_url_with_slug_suffix(self):
        """Onnix URL: numeric ID followed by underscore + slug."""
        result = extract_property_url_info(
            "onnix.com.py/propiedad/39711_terreno-lambare-sur"
        )
        assert result == {"source": "onnixpy", "property_id": "39711"}

    def test_onnix_url_in_sentence(self):
        """URL embedded in a longer message."""
        result = extract_property_url_info(
            "Mira este terreno onnix.com.py/propiedad/55000 que vi ayer"
        )
        assert result == {"source": "onnixpy", "property_id": "55000"}

    # --- Remax ----------------------------------------------------------------
    # Incidente: el dueño compartió un link de remax.com.py de una prop que SÍ
    # está en DB (source='remax', external_id='{num}-{num}') y el bot no la
    # identificó. La URL real del incidente:
    _REMAX_INCIDENT_URL = (
        "https://www.remax.com.py/es-py/propiedades/departamento/venta/mburucuya/"
        "av-santisima-trinidad-asuncion-001521-https-sharegoogle-ifelzcqebuurqwgyd/"
        "143014103-209"
    )

    def test_remax_incident_url(self):
        """The real incident URL extracts source='remax' + external_id '{num}-{num}'."""
        result = extract_property_url_info(self._REMAX_INCIDENT_URL)
        assert result == {"source": "remax", "property_id": "143014103-209"}

    def test_remax_url_with_querystring(self):
        """Remax URL with a trailing querystring still extracts the external_id."""
        result = extract_property_url_info(
            self._REMAX_INCIDENT_URL + "?utm_source=whatsapp&utm_medium=share"
        )
        assert result == {"source": "remax", "property_id": "143014103-209"}

    def test_remax_url_with_trailing_slash(self):
        """Remax URL with a trailing slash still extracts the external_id."""
        result = extract_property_url_info(self._REMAX_INCIDENT_URL + "/")
        assert result == {"source": "remax", "property_id": "143014103-209"}

    def test_remax_url_in_sentence(self):
        """Remax URL embedded mid-sentence is detected."""
        result = extract_property_url_info(
            f"Hola, tienen esta propiedad? {self._REMAX_INCIDENT_URL} gracias"
        )
        assert result == {"source": "remax", "property_id": "143014103-209"}

    def test_remax_url_without_trailing_id_not_matched(self):
        """Remax URL that does not end in the {num}-{num} external_id returns None.

        Slug segments with digits (001521-https) must NOT be mistaken for the id.
        """
        result = extract_property_url_info(
            "https://www.remax.com.py/es-py/propiedades/departamento/venta/mburucuya/"
            "av-santisima-trinidad-asuncion-001521-https-sharegoogle-ifelzcqebuurqwgyd/"
        )
        assert result is None

    def test_remax_short_number_not_matched(self):
        """Trailing ids with fewer than 5 leading digits are not matched."""
        result = extract_property_url_info("remax.com.py/algo/1234-5")
        assert result is None

    def test_remax_domain_only_not_matched(self):
        """Bare remax domain without a property path returns None."""
        result = extract_property_url_info("mira www.remax.com.py que tiene de todo")
        assert result is None

    # --- No URL --------------------------------------------------------------

    def test_no_url_plain_text(self):
        """Plain search message returns None."""
        result = extract_property_url_info("quiero un terreno en Luque")
        assert result is None

    def test_no_url_other_website(self):
        """Unrelated domain is not matched."""
        result = extract_property_url_info(
            "vi algo en clasificados.com.py/propiedad/12345"
        )
        assert result is None

    def test_empty_string(self):
        """Empty message returns None."""
        result = extract_property_url_info("")
        assert result is None

    def test_infocasas_priority_over_onnix(self):
        """When both appear, InfoCasas match is returned first."""
        result = extract_property_url_info(
            "infocasas.com.py/casa/123456789 y onnix.com.py/propiedad/39711"
        )
        assert result == {"source": "infocasas", "property_id": "123456789"}


# ===========================================================================
# TestLookupUrlProperty
# ===========================================================================

class TestLookupUrlProperty:
    """Unit tests for handlers.url_detection.lookup_url_property (M4 Task 3.5).

    Since fix/bot-url-property-lookup the function returns a tuple
    ``(context_note, db_property_id)`` so the orchestrator can propagate the
    URL-resolved property to search_context / lead persistence.
    """

    @pytest.mark.asyncio
    async def test_infocasas_found(self):
        """Found InfoCasas property returns context note with its data including type and operation."""
        from app.bot.handlers.url_detection import lookup_url_property
        session = AsyncMock()

        ic_prop = MagicMock()
        ic_prop.title = "Terreno esquina zona corporativa"
        ic_prop.price_sale = 120000
        ic_prop.price_rent = None
        ic_prop.is_active = True
        ic_prop.city = "Asuncion"
        ic_prop.neighborhood = "Eje Corporativo"
        ic_prop.operation = "venta"
        ic_prop.property_type = "terreno"
        ic_prop.total_area_m2 = 500
        ic_prop.property_id = 777  # cross-ref a properties.id

        with patch(
            "app.bot.handlers.url_detection.PropertyRepository.get_ic_by_infocasas_id",
            new=AsyncMock(return_value=ic_prop),
        ):
            result, db_prop_id = await lookup_url_property(
                {"source": "infocasas", "property_id": "189190235"}, session
            )

        assert db_prop_id == 777
        assert "Sistema:" in result
        assert "189190235" in result
        assert "Terreno esquina zona corporativa" in result
        assert "Disponible" in result
        assert "Asuncion" in result
        assert "Eje Corporativo" in result
        assert "venta" in result
        assert "terreno" in result
        assert "500" in result

    @pytest.mark.asyncio
    async def test_infocasas_not_found(self):
        """Missing InfoCasas property returns 'not found' context note."""
        from app.bot.handlers.url_detection import lookup_url_property
        session = AsyncMock()

        with patch(
            "app.bot.handlers.url_detection.PropertyRepository.get_ic_by_infocasas_id",
            new=AsyncMock(return_value=None),
        ):
            result, db_prop_id = await lookup_url_property(
                {"source": "infocasas", "property_id": "000000001"}, session
            )

        assert db_prop_id is None
        assert "no se encontró" in result
        assert "000000001" in result
        assert "InfoCasas" in result

    @pytest.mark.asyncio
    async def test_onnix_found(self):
        """Found Onnix property returns context note with its data."""
        from app.bot.handlers.url_detection import lookup_url_property
        session = AsyncMock()

        prop = MagicMock()
        prop.id = 4242
        prop.title = "Casa en Lambare"
        prop.price_usd = 85000
        prop.is_active = True
        prop.city = "Lambare"
        prop.neighborhood = "San Jose"

        with patch(
            "app.bot.handlers.url_detection.PropertyRepository.get_by_source_external_id",
            new=AsyncMock(return_value=prop),
        ):
            result, db_prop_id = await lookup_url_property(
                {"source": "onnixpy", "property_id": "39711"}, session
            )

        assert db_prop_id == 4242
        assert "Sistema:" in result
        assert "39711" in result
        assert "Casa en Lambare" in result
        assert "Disponible" in result
        assert "Lambare" in result

    @pytest.mark.asyncio
    async def test_onnix_not_found(self):
        """Missing Onnix property returns 'not found' context note."""
        from app.bot.handlers.url_detection import lookup_url_property
        session = AsyncMock()

        with patch(
            "app.bot.handlers.url_detection.PropertyRepository.get_by_source_external_id",
            new=AsyncMock(return_value=None),
        ):
            result, db_prop_id = await lookup_url_property(
                {"source": "onnixpy", "property_id": "99999"}, session
            )

        assert db_prop_id is None
        assert "no se encontró" in result
        assert "99999" in result
        assert "Onnix" in result

    @pytest.mark.asyncio
    async def test_remax_found(self):
        """Found Remax property returns context note via get_by_source_external_id."""
        from app.bot.handlers.url_detection import lookup_url_property
        session = AsyncMock()

        prop = MagicMock()
        prop.id = 1997156
        prop.title = "Departamento en Mburucuyá"
        prop.price_usd = 209000
        prop.is_active = True
        prop.city = "Asunción"
        prop.neighborhood = "Mburucuyá"

        with patch(
            "app.bot.handlers.url_detection.PropertyRepository.get_by_source_external_id",
            new=AsyncMock(return_value=prop),
        ) as repo_mock:
            result, db_prop_id = await lookup_url_property(
                {"source": "remax", "property_id": "143014103-209"}, session
            )

        repo_mock.assert_awaited_once_with(session, "remax", "143014103-209")
        assert db_prop_id == 1997156
        assert "Sistema:" in result
        assert "143014103-209" in result
        assert "Departamento en Mburucuyá" in result
        assert "Disponible" in result
        assert "Asunción" in result
        assert "Remax" in result

    @pytest.mark.asyncio
    async def test_remax_not_found(self):
        """Missing Remax property returns 'not found' context note with Remax label."""
        from app.bot.handlers.url_detection import lookup_url_property
        session = AsyncMock()

        with patch(
            "app.bot.handlers.url_detection.PropertyRepository.get_by_source_external_id",
            new=AsyncMock(return_value=None),
        ):
            result, db_prop_id = await lookup_url_property(
                {"source": "remax", "property_id": "143014103-209"}, session
            )

        assert db_prop_id is None
        assert "no se encontró" in result
        assert "143014103-209" in result
        assert "Remax" in result

    @pytest.mark.asyncio
    async def test_lookup_db_error_returns_not_found_note(self):
        """DB exceptions are swallowed — returns 'not found' note, never raises."""
        from app.bot.handlers.url_detection import lookup_url_property
        session = AsyncMock()

        with patch(
            "app.bot.handlers.url_detection.PropertyRepository.get_ic_by_infocasas_id",
            new=AsyncMock(side_effect=RuntimeError("DB connection lost")),
        ):
            result, db_prop_id = await lookup_url_property(
                {"source": "infocasas", "property_id": "189190235"}, session
            )

        # Must not raise; must still return a meaningful note and no DB id
        assert db_prop_id is None
        assert "Sistema:" in result or "no se encontró" in result


# ===========================================================================
# TestUrlPropertyLeadLink  (fix/bot-url-property-lookup)
# ===========================================================================

class TestUrlPropertyLeadLink:
    """Incidente remax: el lead debe linkear la prop identificada por URL.

    El dueño compartió un link de remax de una prop que SÍ está en DB; el bot
    no la identificó y el lead quedó linkeado a OTRA propiedad — el residuo
    ``last_detalle_id`` del search_context de búsquedas viejas. Fix: Step 5d
    pisa ``last_detalle_id`` con la prop resuelta por URL y la persiste de
    inmediato (mismo mecanismo que el preload del flujo directo IC), así
    ``persist_lead_outcome`` linkea la prop correcta en este turno o en
    turnos posteriores.
    """

    _REMAX_URL = (
        "https://www.remax.com.py/es-py/propiedades/departamento/venta/mburucuya/"
        "av-santisima-trinidad-asuncion-001521-https-sharegoogle-ifelzcqebuurqwgyd/"
        "143014103-209"
    )

    def _url_request(self):
        return BotRequest(
            platform="telegram", chat_id="12345", user_id="12345",
            user_name="Test User",
            text=f"Hola, me interesa esta propiedad {self._REMAX_URL}",
            external_id="msg_url_001",
        )

    def _remax_prop(self, prop_id=1997156):
        prop = MagicMock()
        prop.id = prop_id
        prop.title = "Departamento en Mburucuyá"
        prop.price_usd = 209000
        prop.is_active = True
        prop.city = "Asunción"
        prop.neighborhood = "Mburucuyá"
        return prop

    _REPO_PATCH = (
        "app.bot.handlers.url_detection.PropertyRepository.get_by_source_external_id"
    )

    @pytest.mark.asyncio
    async def test_incident_url_property_overrides_stale_last_detalle_id(self):
        """Caso del incidente: residuo last_detalle_id=13058 + URL remax de la
        prop 1997156 → el lead se registra con property_id=1997156, NO 13058."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(last_detalle_id=13058)  # residuo de búsquedas viejas
        _setup_lead_flow(mocks, search_context=ctx)

        session = AsyncMock()
        with patch(
            self._REPO_PATCH, new=AsyncMock(return_value=self._remax_prop()),
        ):
            await orch.handle_message(self._url_request(), session)

        # search_context queda apuntando a la prop de la URL
        assert ctx.last_detalle_id == 1997156

        # lead_events metadata lleva la prop resuelta por URL, no el residuo
        for call_args in session.execute.call_args_list:
            sql_text = getattr(call_args[0][0], "text", str(call_args[0][0])) if call_args[0] else ""
            if "lead_events" in sql_text and "lead_registered" in sql_text:
                params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1]
                metadata_str = params.get("metadata", "{}")
                assert "1997156" in metadata_str, (
                    f"Expected URL property_id=1997156 in metadata, got: {metadata_str}"
                )
                assert "13058" not in metadata_str, (
                    f"Stale last_detalle_id=13058 leaked into metadata: {metadata_str}"
                )
                break
        else:
            pytest.fail("INSERT INTO lead_events with lead_registered not found")

        # contacts.property_id se linkea con la prop de la URL
        for call_args in session.execute.call_args_list:
            sql_text = getattr(call_args[0][0], "text", str(call_args[0][0])) if call_args[0] else ""
            if "UPDATE contacts" in sql_text and "property_id" in sql_text:
                params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1]
                assert params.get("prop_id") == 1997156, (
                    f"Expected contacts.property_id=1997156, got: {params}"
                )
                break
        else:
            pytest.fail("UPDATE contacts SET property_id not found")

    @pytest.mark.asyncio
    async def test_url_detection_persists_last_detalle_id_without_lead(self):
        """Turno con URL pero sin lead: last_detalle_id se actualiza y se
        persiste de inmediato (sobrevive a turnos posteriores — el register_lead
        puede llegar varios mensajes después)."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(last_detalle_id=13058)
        _setup_normal_flow(mocks)
        mocks["conversation_manager"].get_search_context.return_value = ctx
        mocks["claude"].send_message.return_value = _text_ai_response(
            "Sí, tenemos esa propiedad disponible. Avisame si querés más detalles."
        )

        session = AsyncMock()
        with patch(
            self._REPO_PATCH, new=AsyncMock(return_value=self._remax_prop()),
        ):
            await orch.handle_message(self._url_request(), session)

        assert ctx.last_detalle_id == 1997156
        mocks["conversation_manager"].update_search_context.assert_awaited()
        saved_state = mocks["conversation_manager"].update_search_context.call_args[0][2]
        assert saved_state.last_detalle_id == 1997156

    @pytest.mark.asyncio
    async def test_url_not_in_db_keeps_residual_last_detalle_id(self):
        """URL detectada pero la prop NO está en DB: no se pisa el residuo
        ni se fuerza un update_search_context."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(last_detalle_id=13058)
        _setup_normal_flow(mocks)
        mocks["conversation_manager"].get_search_context.return_value = ctx
        mocks["claude"].send_message.return_value = _text_ai_response(
            "Entendido, avisame si necesitás algo más."
        )

        session = AsyncMock()
        with patch(self._REPO_PATCH, new=AsyncMock(return_value=None)):
            await orch.handle_message(self._url_request(), session)

        assert ctx.last_detalle_id == 13058
        mocks["conversation_manager"].update_search_context.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_lead_without_url_still_uses_last_detalle_id(self):
        """Sin URL en el mensaje, el lead conserva el comportamiento actual:
        property_id = search_context.last_detalle_id."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(last_detalle_id=13058)
        _setup_lead_flow(mocks, search_context=ctx)

        session = AsyncMock()
        await orch.handle_message(_default_request(), session)

        assert ctx.last_detalle_id == 13058
        for call_args in session.execute.call_args_list:
            sql_text = getattr(call_args[0][0], "text", str(call_args[0][0])) if call_args[0] else ""
            if "lead_events" in sql_text and "lead_registered" in sql_text:
                params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1]
                assert "13058" in params.get("metadata", "{}")
                break
        else:
            pytest.fail("INSERT INTO lead_events with lead_registered not found")


# ===========================================================================
# TestStep9BusquedaIncompletaEtapaPersistence  (Bug 6)
# ===========================================================================

class TestStep9BusquedaIncompletaEtapaPersistence:
    """Bug 6: conversational busqueda_incompleta turns must persist etapa.

    When Claude returns a text-only response asking the user for missing search
    parameters (busqueda_incompleta_operacion or busqueda_incompleta_zona),
    Step 9 must still call update_search_context so that the next turn's
    system prompt shows Estado: busqueda_incompleta — preventing Claude from
    restarting data-gathering from scratch.
    """

    @pytest.mark.asyncio
    async def test_busqueda_incompleta_operacion_persists_etapa(self):
        """When Claude asks about operation type, etapa must be saved as 'busqueda_incompleta'."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)
        # Claude asks "¿comprar o alquilar?" — no tool call, text response
        # _detect_intent_from_text will return "busqueda_incompleta_operacion"
        mocks["claude"].send_message.return_value = _text_ai_response(
            "¿Qué operación te interesa: comprar o alquilar?"
        )

        session = AsyncMock()
        await orch.handle_message(_default_request(), session)

        # update_search_context MUST be called (Bug 6 fix)
        mocks["conversation_manager"].update_search_context.assert_awaited()
        call_args = mocks["conversation_manager"].update_search_context.call_args
        saved_state = call_args[0][2]
        assert saved_state.etapa == "busqueda_incompleta"

    @pytest.mark.asyncio
    async def test_busqueda_incompleta_zona_persists_etapa(self):
        """When Claude asks about location, etapa must be saved as 'busqueda_incompleta'."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)
        # Claude asks about zone — _detect_intent_from_text returns "busqueda_incompleta_zona"
        mocks["claude"].send_message.return_value = _text_ai_response(
            "¿En qué zona estás buscando?"
        )

        session = AsyncMock()
        await orch.handle_message(_default_request(), session)

        mocks["conversation_manager"].update_search_context.assert_awaited()
        call_args = mocks["conversation_manager"].update_search_context.call_args
        saved_state = call_args[0][2]
        assert saved_state.etapa == "busqueda_incompleta"

    @pytest.mark.asyncio
    async def test_pure_conversacion_does_not_update_search_context(self):
        """Generic conversational response must NOT trigger update_search_context.

        We only persist etapa for busqueda_incompleta* intents — not for
        every single conversational exchange (e.g. the user asks a general
        question mid-search).
        """
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)
        mocks["claude"].send_message.return_value = _text_ai_response(
            "Entendido, avisame si necesitás algo más."
        )

        session = AsyncMock()
        await orch.handle_message(_default_request(), session)

        # 'conversacion' intent must NOT call update_search_context
        mocks["conversation_manager"].update_search_context.assert_not_awaited()


# ===========================================================================
# TestNoResultsText — GRUPO 4: intelligent price feedback (3 tests)
# ===========================================================================


class TestNoResultsText:
    """Unit tests for handlers._utils.no_results_text (M4 Task 3.6).

    Tests the three branches:
    1. min_price found  → contextual message with tipo + zona + min price
    2. level >= 3 relaxation exhausted → "zonas cercanas" message
    3. generic fallback → "ajustar la zona o el presupuesto" message
    """

    def _make_result(
        self,
        min_price: float | None = None,
        degradation_level: int | None = None,
    ):
        """Build a mock SearchResult with optional degradation."""
        from app.bot.search.relaxation import DegradationInfo
        from app.bot.search.search_service import SearchResult

        if degradation_level is not None:
            deg = DegradationInfo(
                level=degradation_level,
                description="test",
                min_price_in_zone=min_price,
            )
        else:
            deg = None

        return SearchResult(properties=[], total_found=0, degradation=deg)

    def _make_filters(
        self,
        tipo: str | None = None,
        barrio: str | None = None,
        ciudad: str | None = None,
        precio_max: float | None = None,
    ):
        from app.bot.search.sql_filters import SearchFilters
        return SearchFilters(
            operacion="venta",
            tipo=tipo,
            barrio=barrio,
            ciudad=ciudad,
            precio_max=precio_max,
        )

    def test_no_results_text_with_min_price(self):
        """When min_price_in_zone is available, message includes tipo, zona, and price.

        Given: filters with tipo='casa', ciudad='Villa Morra', precio_max=10000
               degradation with min_price_in_zone=80000
        Expect: message mentions 'casa', 'Villa Morra', and '80.000'
        """
        from app.bot.handlers._utils import no_results_text
        result = self._make_result(min_price=80000.0, degradation_level=4)
        filters = self._make_filters(tipo="casa", ciudad="Villa Morra", precio_max=10000)

        text = no_results_text(result, filters)

        assert "casa" in text.lower(), f"Expected 'casa' in: {text}"
        assert "Villa Morra" in text or "villa morra" in text.lower(), f"Expected zona in: {text}"
        assert "80" in text, f"Expected price digits in: {text}"
        # Must NOT mention 'propiedad' generic when tipo is specified
        assert "propiedades disponibles" not in text

    def test_no_results_text_level3_degradation(self):
        """When relaxation reached level >= 3 with no min_price, message mentions nearby zones.

        Given: filters with tipo='departamento', ciudad='Luque', no precio_max
               degradation level=3, min_price_in_zone=None
        Expect: message mentions tipo, zona and offers nearby zone search
        """
        from app.bot.handlers._utils import no_results_text
        result = self._make_result(min_price=None, degradation_level=3)
        filters = self._make_filters(tipo="departamento", ciudad="Luque")

        text = no_results_text(result, filters)

        assert "departamento" in text.lower(), f"Expected tipo in: {text}"
        assert "Luque" in text or "luque" in text.lower(), f"Expected zona in: {text}"
        # Must suggest nearby zones or alternative filters
        assert any(
            kw in text.lower()
            for kw in ("cerc", "zona", "filtro", "preferis", "preferís")
        ), f"Expected zone/filter suggestion in: {text}"

    def test_no_results_text_generic_fallback(self):
        """When no degradation available, generic fallback mentions tipo and invites adjustment.

        Given: filters with tipo='terreno', ciudad='Fernando de la Mora', no precio_max
               no degradation object
        Expect: message mentions tipo and invites the user to adjust filters
        """
        from app.bot.handlers._utils import no_results_text
        result = self._make_result(min_price=None, degradation_level=None)
        filters = self._make_filters(tipo="terreno", ciudad="Fernando de la Mora")

        text = no_results_text(result, filters)

        assert "terreno" in text.lower(), f"Expected tipo in: {text}"
        # Must NOT use the old generic "propiedades disponibles" without tipo
        assert "propiedades disponibles" not in text
        # Must invite adjustment
        assert any(
            kw in text.lower()
            for kw in ("ajust", "zona", "presupuesto", "preferis", "preferís", "filtro")
        ), f"Expected filter adjustment invitation in: {text}"


# ===========================================================================
# TestRelaxationPreservesFilters — GRUPO 4: non-relaxed filters are kept
# ===========================================================================


class TestRelaxationPreservesFilters:
    """Verify that relaxation levels do not drop non-targeted filters.

    Level 1 (price) must keep: tipo, operacion, ciudad, barrio.
    Level 2 (dormitorios) must keep: tipo, operacion, precio_max, ciudad.
    Level 3 (zone) must keep: tipo, operacion, precio_max.
    """

    def setup_method(self):
        from app.bot.search.relaxation import FilterRelaxation
        from app.bot.search.sql_filters import SQLFilterBuilder
        from app.bot.search.geo_resolver import GeoResolver

        self.builder = SQLFilterBuilder()
        self.geo_resolver = GeoResolver()
        self.relaxation = FilterRelaxation(self.builder, self.geo_resolver)

    def test_level1_price_keeps_tipo(self):
        """Level 1 price relaxation preserves tipo filter."""
        from app.bot.search.sql_filters import SearchFilters

        filters = SearchFilters(
            operacion="venta",
            tipo="casa",
            ciudad="asuncion",
            precio_max=100000,
        )
        result = self.relaxation._relax_price(filters, None)
        assert result is not None
        new_filters, _ = result
        assert new_filters.tipo == "casa", (
            f"Level 1 must NOT drop tipo, got: {new_filters.tipo}"
        )

    def test_level1_price_keeps_operacion(self):
        """Level 1 price relaxation preserves operacion filter."""
        from app.bot.search.sql_filters import SearchFilters

        filters = SearchFilters(
            operacion="alquiler",
            tipo="departamento",
            precio_max=500,
        )
        result = self.relaxation._relax_price(filters, None)
        assert result is not None
        new_filters, _ = result
        assert new_filters.operacion == "alquiler", (
            f"Level 1 must NOT drop operacion, got: {new_filters.operacion}"
        )

    def test_level2_dormitorios_keeps_tipo(self):
        """Level 2 dormitorios relaxation preserves tipo filter."""
        from app.bot.search.sql_filters import SearchFilters

        filters = SearchFilters(
            operacion="venta",
            tipo="casa",
            dormitorios_min=4,
            precio_max=200000,
        )
        result = self.relaxation._relax_dormitorios(filters, None)
        assert result is not None
        new_filters, _ = result
        assert new_filters.tipo == "casa", (
            f"Level 2 must NOT drop tipo, got: {new_filters.tipo}"
        )

    def test_level2_dormitorios_keeps_precio_max(self):
        """Level 2 dormitorios relaxation preserves precio_max."""
        from app.bot.search.sql_filters import SearchFilters

        filters = SearchFilters(
            operacion="venta",
            tipo="casa",
            dormitorios_min=4,
            precio_max=260000,  # already relaxed by level 1
        )
        result = self.relaxation._relax_dormitorios(filters, None)
        assert result is not None
        new_filters, _ = result
        assert new_filters.precio_max == pytest.approx(260000), (
            f"Level 2 must NOT drop precio_max, got: {new_filters.precio_max}"
        )

    def test_level3_zone_keeps_tipo(self):
        """Level 3 zone relaxation preserves tipo filter."""
        from app.bot.search.sql_filters import SearchFilters
        from app.bot.search.geo_resolver import GeoLocation, ResolvedGeo

        filters = SearchFilters(
            operacion="venta",
            tipo="casa",
            barrio="villa morra",
            ciudad="asuncion",
            precio_max=260000,
        )
        geo = ResolvedGeo(
            canonical_city="asuncion",
            city_locations=[GeoLocation("asuncion", 0)],
            barrio_locations=[GeoLocation("villa morra", 0)],
        )
        result = self.relaxation._relax_zone(filters, geo)
        assert result is not None
        new_filters, _ = result
        assert new_filters.tipo == "casa", (
            f"Level 3 must NOT drop tipo, got: {new_filters.tipo}"
        )

    def test_level3_zone_keeps_operacion(self):
        """Level 3 zone relaxation preserves operacion filter."""
        from app.bot.search.sql_filters import SearchFilters
        from app.bot.search.geo_resolver import GeoLocation, ResolvedGeo

        filters = SearchFilters(
            operacion="alquiler",
            tipo="departamento",
            barrio="recoleta",
            ciudad="asuncion",
        )
        geo = ResolvedGeo(
            canonical_city="asuncion",
            city_locations=[GeoLocation("asuncion", 0)],
            barrio_locations=[GeoLocation("recoleta", 0)],
        )
        result = self.relaxation._relax_zone(filters, geo)
        assert result is not None
        new_filters, _ = result
        assert new_filters.operacion == "alquiler", (
            f"Level 3 must NOT drop operacion, got: {new_filters.operacion}"
        )


# ===========================================================================
# GRUPO 5 — F-06: saludo does NOT reset active search, F-07: ahora_no text,
#            ver_similares goes to Claude (not shortcut)
# ===========================================================================


class TestF06SaludoNoResetActiveSearch:
    """F-06: saludo intent must NOT reset search_context when there are pending results.

    Before this fix, any saludo always wiped filtros + resultados_pendientes.
    After the fix, the reset only happens when resultados_pendientes == [].
    """

    @pytest.mark.asyncio
    async def test_saludo_with_pending_preserves_filtros(self):
        """F-06: saludo with active resultados_pendientes -> filtros are preserved."""
        orch, mocks = _make_orchestrator()

        ctx = ConversationState(
            etapa="mostrando_resultados",
            filtros={"operacion": "venta", "tipo": "casa", "ciudad": "Luque"},
            resultados_pendientes=[201, 202, 203],
            current_page_ids=[100, 101],
            shown_properties=[100, 101],
            search_shown_count=2,
            total_found=5,
        )
        _setup_normal_flow(mocks)
        mocks["conversation_manager"].get_search_context.return_value = ctx

        mocks["claude"].send_message.return_value = _text_ai_response(
            "Hola! Bienvenido a Onnix SA.",
        )

        request = BotRequest(
            platform="whatsapp", chat_id="+595981000001", user_id="+595981000001",
            user_name="Test User", text="Hola",
            external_id="msg_f06_001",
        )
        await orch.handle_message(request, AsyncMock())

        # update_search_context must NOT have been called for saludo reset
        # (pending results are active -- the user may still be paginating)
        if mocks["conversation_manager"].update_search_context.called:
            call_args = mocks["conversation_manager"].update_search_context.call_args
            updated_ctx = call_args[0][2]
            # filtros must NOT be wiped
            assert updated_ctx.filtros != {}, (
                "F-06: filtros must be preserved when resultados_pendientes is non-empty"
            )
            # resultados_pendientes must NOT be wiped
            assert updated_ctx.resultados_pendientes != [], (
                "F-06: resultados_pendientes must be preserved on saludo with active search"
            )

    @pytest.mark.asyncio
    async def test_saludo_with_pending_intent_is_saludo(self):
        """F-06: when pending results exist, saludo text still returns saludo intent."""
        orch, mocks = _make_orchestrator()

        ctx = ConversationState(
            resultados_pendientes=[201, 202],
            filtros={"operacion": "alquiler", "ciudad": "Asuncion"},
        )
        _setup_normal_flow(mocks)
        mocks["conversation_manager"].get_search_context.return_value = ctx

        mocks["claude"].send_message.return_value = _text_ai_response(
            "Hola! En que te puedo ayudar?",
        )

        request = BotRequest(
            platform="whatsapp", chat_id="+595981000002", user_id="+595981000002",
            user_name="Test", text="hola",
            external_id="msg_f06_002",
        )
        result = await orch.handle_message(request, AsyncMock())

        assert result is not None
        assert result.intent == "saludo"

    @pytest.mark.asyncio
    async def test_saludo_without_pending_resets_fully(self):
        """F-06: saludo with empty resultados_pendientes still resets the context."""
        orch, mocks = _make_orchestrator()

        ctx = ConversationState(
            etapa="mostrando_resultados",
            filtros={"operacion": "venta", "tipo": "casa"},
            resultados_pendientes=[],  # no pending
            current_page_ids=[100, 101],
            shown_properties=[100, 101],
            search_shown_count=2,
            total_found=2,
        )
        _setup_normal_flow(mocks)
        mocks["conversation_manager"].get_search_context.return_value = ctx

        mocks["claude"].send_message.return_value = _text_ai_response(
            "Hola! Bienvenido de nuevo.",
        )

        request = BotRequest(
            platform="whatsapp", chat_id="+595981000003", user_id="+595981000003",
            user_name="Test", text="Hola",
            external_id="msg_f06_003",
        )
        await orch.handle_message(request, AsyncMock())

        # When no pending results, reset MUST happen
        mocks["conversation_manager"].update_search_context.assert_awaited()
        call_args = mocks["conversation_manager"].update_search_context.call_args
        updated_ctx = call_args[0][2]
        assert updated_ctx.etapa == "inicio", (
            "F-06: etapa must be reset to 'inicio' when no pending results"
        )
        assert updated_ctx.filtros == {}, (
            "F-06: filtros must be reset when no pending results"
        )
        assert updated_ctx.resultados_pendientes == [], (
            "F-06: resultados_pendientes must be reset when already empty"
        )

    @pytest.mark.asyncio
    async def test_saludo_without_filtros_resets(self):
        """F-06: saludo with empty filtros (fresh session) resets to inicio."""
        orch, mocks = _make_orchestrator()

        ctx = ConversationState()  # defaults: filtros={}, resultados_pendientes=[]
        _setup_normal_flow(mocks)
        mocks["conversation_manager"].get_search_context.return_value = ctx

        mocks["claude"].send_message.return_value = _text_ai_response(
            "Hola! En que te puedo ayudar?",
        )

        request = BotRequest(
            platform="telegram", chat_id="12345", user_id="12345",
            user_name="Test", text="Hola",
            external_id="msg_f06_004",
        )
        await orch.handle_message(request, AsyncMock())

        mocks["conversation_manager"].update_search_context.assert_awaited()
        call_args = mocks["conversation_manager"].update_search_context.call_args
        updated_ctx = call_args[0][2]
        assert updated_ctx.etapa == "inicio"
        assert updated_ctx.filtros == {}


class TestF07AhoraNoText:
    """F-07: AHORA_NO_REENVIADO must send the correct closure text."""

    @pytest.mark.asyncio
    async def test_ahora_no_sends_correct_text(self):
        """F-07: AHORA_NO_REENVIADO returns the expected closure message."""
        orch, mocks = _make_orchestrator()

        ctx = ConversationState()
        _setup_normal_flow(mocks, contact=_default_contact(status="new"))
        mocks["conversation_manager"].get_search_context.return_value = ctx

        request = BotRequest(
            platform="whatsapp", chat_id="+595981000010", user_id="+595981000010",
            user_name="Test", text="AHORA_NO_REENVIADO",
            external_id="msg_f07_001", callback_data="AHORA_NO_REENVIADO",
        )

        with patch("app.repositories.contact_repo.ContactRepository") as mock_cr, \
             patch("app.repositories.lead_event_repo.LeadEventRepository") as mock_ler:
            mock_cr.update_status = AsyncMock()
            mock_ler.create = AsyncMock()
            result = await orch.handle_message(request, AsyncMock())

        assert result is not None
        assert result.intent == "conversacion"
        # F-07: must include "Entendido" and "escribinos"
        assert "Entendido" in result.text, (
            f"F-07: expected 'Entendido' in response text, got: {result.text!r}"
        )
        assert "escribinos" in result.text.lower() or "escribinos" in result.text, (
            f"F-07: expected 'escribinos' in response text, got: {result.text!r}"
        )

    @pytest.mark.asyncio
    async def test_ahora_no_saves_outbound_message(self):
        """F-07: AHORA_NO_REENVIADO must save the outbound message."""
        orch, mocks = _make_orchestrator()

        ctx = ConversationState()
        _setup_normal_flow(mocks, contact=_default_contact(status="new"))
        mocks["conversation_manager"].get_search_context.return_value = ctx

        request = BotRequest(
            platform="whatsapp", chat_id="+595981000011", user_id="+595981000011",
            user_name="Test", text="AHORA_NO_REENVIADO",
            external_id="msg_f07_002", callback_data="AHORA_NO_REENVIADO",
        )

        with patch("app.repositories.contact_repo.ContactRepository") as mock_cr, \
             patch("app.repositories.lead_event_repo.LeadEventRepository") as mock_ler:
            mock_cr.update_status = AsyncMock()
            mock_ler.create = AsyncMock()
            await orch.handle_message(request, AsyncMock())

        mocks["conversation_manager"].save_outbound_message.assert_awaited_once()


class TestVerSimilaresGoesToClaude:
    """GRUPO 5: ver_similares callback must go to Claude, not a direct shortcut.

    The _handle_ver_similares shortcut is removed. Both 'ver_similares' and
    'VER_SIMILARES' callbacks must be translated to natural language and sent
    to Claude for processing.
    """

    @pytest.mark.asyncio
    async def test_ver_similares_lowercase_calls_claude(self):
        """ver_similares callback (lowercase) translates to text and calls Claude."""
        orch, mocks = _make_orchestrator()

        ctx = ConversationState(
            filtros={"operacion": "venta", "tipo": "casa", "ciudad": "Luque"},
            last_detalle_id=755934,
            shown_properties=[755934],
        )
        _setup_normal_flow(mocks)
        mocks["conversation_manager"].get_search_context.return_value = ctx
        mocks["claude"].send_message.return_value = _text_ai_response(
            "Encontre propiedades similares para vos."
        )

        request = BotRequest(
            platform="whatsapp", chat_id="+595981000020", user_id="+595981000020",
            user_name="Test", text="ver_similares",
            external_id="msg_vs2_001", callback_data="ver_similares",
        )
        result = await orch.handle_message(request, AsyncMock())

        assert result is not None
        # Claude MUST have been called (no shortcut)
        mocks["claude"].send_message.assert_called()
        # search_service.search_properties must NOT have been called directly
        mocks["search_service"].search_properties.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ver_similares_uppercase_calls_claude(self):
        """VER_SIMILARES callback (uppercase) translates to text and calls Claude."""
        orch, mocks = _make_orchestrator()

        ctx = ConversationState(
            filtros={"operacion": "alquiler", "tipo": "departamento"},
            last_detalle_id=12345,
        )
        _setup_normal_flow(mocks)
        mocks["conversation_manager"].get_search_context.return_value = ctx
        mocks["claude"].send_message.return_value = _text_ai_response(
            "Te muestro propiedades similares."
        )

        request = BotRequest(
            platform="whatsapp", chat_id="+595981000021", user_id="+595981000021",
            user_name="Test", text="VER_SIMILARES",
            external_id="msg_vs2_002", callback_data="VER_SIMILARES",
        )
        result = await orch.handle_message(request, AsyncMock())

        assert result is not None
        mocks["claude"].send_message.assert_called()
        mocks["search_service"].search_properties.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ver_similares_no_filtros_calls_claude(self):
        """ver_similares with empty filtros also calls Claude (no shortcut at all)."""
        orch, mocks = _make_orchestrator()

        ctx = ConversationState(filtros={}, last_detalle_id=755934)
        _setup_normal_flow(mocks)
        mocks["conversation_manager"].get_search_context.return_value = ctx
        mocks["claude"].send_message.return_value = _text_ai_response(
            "Buscando propiedades similares..."
        )

        request = BotRequest(
            platform="whatsapp", chat_id="+595981000022", user_id="+595981000022",
            user_name="Test", text="ver_similares",
            external_id="msg_vs2_003", callback_data="ver_similares",
        )
        result = await orch.handle_message(request, AsyncMock())

        assert result is not None
        mocks["claude"].send_message.assert_called()

    # test_ver_similares_still_in_callback_translations removed in M4 Task 1.1 —
    # both callbacks deleted from _CALLBACK_TRANSLATIONS.


class TestWizardFieldsRemoved:
    """GRUPO 5: wizard_step and wizard_filtros must be removed from ConversationState."""

    def test_wizard_step_not_in_conversation_state(self):
        """ConversationState must NOT have a wizard_step field."""
        from app.bot.core.types import ConversationState
        state = ConversationState()
        assert not hasattr(state, "wizard_step"), (
            "wizard_step must be removed from ConversationState -- it was dead code"
        )

    def test_wizard_filtros_not_in_conversation_state(self):
        """ConversationState must NOT have a wizard_filtros field."""
        from app.bot.core.types import ConversationState
        state = ConversationState()
        assert not hasattr(state, "wizard_filtros"), (
            "wizard_filtros must be removed from ConversationState -- it was dead code"
        )

    def test_from_jsonb_ignores_wizard_keys(self):
        """from_jsonb with wizard_step/wizard_filtros in JSONB silently ignores them."""
        from app.bot.core.types import ConversationState
        old_jsonb = {
            "etapa": "mostrando_resultados",
            "filtros": {"operacion": "venta"},
            "wizard_step": "tipo",
            "wizard_filtros": {"operacion": "venta"},
            "resultados_pendientes": [100, 101],
        }
        state = ConversationState.from_jsonb(old_jsonb)
        assert state.etapa == "mostrando_resultados"
        assert state.filtros == {"operacion": "venta"}
        assert state.resultados_pendientes == [100, 101]

    def test_to_jsonb_does_not_include_wizard_keys(self):
        """to_jsonb must NOT include wizard_step or wizard_filtros keys."""
        from app.bot.core.types import ConversationState
        state = ConversationState(etapa="inicio", filtros={})
        data = state.to_jsonb()
        assert "wizard_step" not in data, (
            "wizard_step must not appear in JSONB serialization"
        )
        assert "wizard_filtros" not in data, (
            "wizard_filtros must not appear in JSONB serialization"
        )


# ===========================================================================
# TestFilterMergeOnRefinement (B3)
# ===========================================================================

class TestFilterMergeOnRefinement:
    """B3: search filters merge instead of overwrite on refinement."""

    @pytest.mark.asyncio
    async def test_refinement_preserves_previous_filters(self):
        """When Claude calls search_properties with only new filter,
        previous filters (tipo, operacion, dormitorios_max) survive."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)

        # Pre-populate search_context with existing filters
        existing_ctx = ConversationState(
            filtros={
                "operacion": "venta",
                "tipo": "casa",
                "dormitorios_max": 2,
            }
        )
        mocks["conversation_manager"].get_search_context.return_value = existing_ctx

        # Claude refines with only new ciudad
        tool_response = _tool_use_ai_response(
            tool_calls=[ToolCall(
                id="t1",
                name="search_properties",
                input={"ciudad": "Fernando de la Mora"},
            )],
        )
        text_response = _text_ai_response("Casas en Fernando de la Mora")
        mocks["claude"].send_message.side_effect = [tool_response, text_response]
        mocks["tool_executor"].execute.return_value = {
            "properties": [{"id": 200}],
            "total_found": 5,
            "all_ids": [200, 201, 202],
        }
        mocks["tool_executor"].build_tool_result_message.return_value = {
            "type": "tool_result", "tool_use_id": "t1", "content": "{}",
        }

        await orch.handle_message(_default_request(), AsyncMock())

        # After search: filtros must contain merged result
        assert existing_ctx.filtros.get("operacion") == "venta", \
            "operacion lost during refinement (B3)"
        assert existing_ctx.filtros.get("tipo") == "casa", \
            "tipo lost during refinement (B3)"
        assert existing_ctx.filtros.get("dormitorios_max") == 2, \
            "dormitorios_max lost during refinement (B3)"
        assert existing_ctx.filtros.get("ciudad") == "Fernando de la Mora", \
            "new ciudad not applied during refinement (B3)"

    @pytest.mark.asyncio
    async def test_new_value_overwrites_old_value_on_merge(self):
        """When Claude passes a filter that already exists, new value wins."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)

        existing_ctx = ConversationState(
            filtros={"ciudad": "Asuncion", "tipo": "casa"}
        )
        mocks["conversation_manager"].get_search_context.return_value = existing_ctx

        tool_response = _tool_use_ai_response(
            tool_calls=[ToolCall(
                id="t1",
                name="search_properties",
                input={"ciudad": "Lambare", "tipo": "casa"},
            )],
        )
        text_response = _text_ai_response("Casas en Lambare")
        mocks["claude"].send_message.side_effect = [tool_response, text_response]
        mocks["tool_executor"].execute.return_value = {
            "properties": [],
            "total_found": 0,
            "all_ids": [],
        }
        mocks["tool_executor"].build_tool_result_message.return_value = {
            "type": "tool_result", "tool_use_id": "t1", "content": "{}",
        }

        await orch.handle_message(_default_request(), AsyncMock())

        assert existing_ctx.filtros.get("ciudad") == "Lambare", \
            "New ciudad should overwrite old on merge"
        assert existing_ctx.filtros.get("tipo") == "casa"


# ===========================================================================
# TestLoadIcFiltrosForContact
# ===========================================================================

from app.repositories.contact_repo import ContactRepository  # noqa: E402
from app.repositories.property_repo import PropertyRepository  # noqa: E402


class TestLoadIcFiltrosForContact:
    """Unit tests for handlers.detail_ic.load_ic_filtros_for_contact.

    Verifies that the returned filtros dict does NOT contain the plain
    'dormitorios' key (which is dead data — neither the prompt builder
    nor SearchFilters read it).
    """

    @pytest.mark.asyncio
    async def test_returned_filtros_has_no_plain_dormitorios_key(self):
        """load_ic_filtros_for_contact must not write 'dormitorios' into filtros."""
        from app.bot.handlers.detail_ic import load_ic_filtros_for_contact

        mock_contact = MagicMock()
        mock_contact.infocasas_ref = "IC-REF-001"

        mock_ic_prop = MagicMock()
        mock_ic_prop.property_type = "casa"
        mock_ic_prop.city = "Lambare"
        mock_ic_prop.neighborhood = "Centro"
        mock_ic_prop.operation = "venta"
        mock_ic_prop.bedrooms = 3
        mock_ic_prop.price_sale = "200000"
        mock_ic_prop.price_rent = None
        mock_ic_prop.currency_sale = "USD"
        mock_ic_prop.currency_rent = None

        mock_session = AsyncMock()

        with (
            patch.object(ContactRepository, "get_by_id", new=AsyncMock(return_value=mock_contact)),
            patch.object(PropertyRepository, "get_ic_by_ref", new=AsyncMock(return_value=mock_ic_prop)),
        ):
            result = await load_ic_filtros_for_contact(
                contact_id=42,
                session=mock_session,
            )

        assert isinstance(result, dict)
        assert "dormitorios" not in result, (
            "Plain 'dormitorios' key must not appear in filtros — "
            "it is dead data (neither prompt nor SearchFilters reads it)"
        )
        # Sanity: other keys are present
        assert "tipo" in result
        assert "ciudad" in result
        assert "operacion" in result


# ===========================================================================
# TestReactivateFromAgentReplied
# ===========================================================================


class TestReactivateFromAgentReplied:
    """Tests for reactivate_from_agent_replied: agent_user_id propagation."""

    @pytest.mark.asyncio
    async def test_reactivate_writes_real_agent_user_id_in_metadata(self):
        """reactivate_from_agent_replied must pass contact.agent_user_id (not None) to lead_event_repo.create."""
        from app.bot.state.bot_gate import reactivate_from_agent_replied

        contact = ContactInfo(
            id=77, name="Agent Lead", status="agent_replied",
            platform="whatsapp", agent_user_id=123,
        )
        conversation = ConversationInfo(
            id=10, contact_id=77, platform="whatsapp", chat_id="+595981000077",
        )
        session = AsyncMock()
        # baja_at IS NULL — race #3 guard lets reactivation proceed.
        session.execute.return_value = MagicMock(scalar=MagicMock(return_value=None))

        with (
            patch(
                "app.bot.state.bot_gate.lead_event_repo.create",
                new=AsyncMock(return_value=None),
            ) as mock_create,
            patch(
                "app.bot.state.bot_gate.get_admin_notifier",
                return_value=AsyncMock(notify=AsyncMock()),
            ),
        ):
            await reactivate_from_agent_replied(session, contact, conversation)

        mock_create.assert_awaited_once()
        _, kwargs = mock_create.call_args
        metadata = kwargs.get("metadata") or mock_create.call_args[0][5] if mock_create.call_args[0] else {}
        # Robust extraction: try kwargs first, then positional
        call_args = mock_create.call_args
        metadata = call_args.kwargs.get("metadata") if call_args.kwargs else None
        if metadata is None and call_args.args:
            # positional: create(db, contact_id, event_type, old_status, new_status, triggered_by, metadata)
            metadata = call_args.args[6] if len(call_args.args) > 6 else None
        assert metadata is not None, "metadata was not passed to lead_event_repo.create"
        assert metadata.get("agent_user_id") == 123, (
            f"Expected agent_user_id=123, got: {metadata.get('agent_user_id')}"
        )

    @pytest.mark.asyncio
    async def test_reactivate_via_handle_message_writes_real_agent_user_id(self):
        """handle_message with agent_replied contact calls _reactivate with real agent_user_id."""
        orch, mocks = _make_orchestrator()

        agent_contact = ContactInfo(
            id=88, name="Agent User", status="agent_replied",
            platform="telegram", source_id="88888", agent_user_id=99,
        )
        _setup_normal_flow(mocks, contact=agent_contact)
        # After reactivation, status becomes bot_replied — need Claude to return something
        mocks["claude"].send_message.return_value = _text_ai_response("Hola de nuevo!")
        mocks["response_builder"].build.return_value = MagicMock(messages=[])

        session = AsyncMock()
        # baja_at IS NULL — race #3 guard lets reactivation proceed.
        session.execute.return_value = MagicMock(scalar=MagicMock(return_value=None))

        with (
            patch(
                "app.repositories.lead_event_repo.lead_event_repo.create",
                new=AsyncMock(return_value=None),
            ) as mock_create,
            patch(
                "app.bot.services.admin_notifier.get_admin_notifier",
                return_value=AsyncMock(notify=AsyncMock()),
            ),
        ):
            await orch.handle_message(_default_request(), session)

        # Find the call with event_type='client_responded_to_agent'
        reactivate_call = None
        for c in mock_create.call_args_list:
            if c.kwargs.get("event_type") == "client_responded_to_agent":
                reactivate_call = c
                break
            # Also check positional
            if len(c.args) >= 3 and c.args[2] == "client_responded_to_agent":
                reactivate_call = c
                break

        assert reactivate_call is not None, (
            "lead_event_repo.create was not called with event_type='client_responded_to_agent'"
        )
        metadata = reactivate_call.kwargs.get("metadata")
        if metadata is None and reactivate_call.args:
            metadata = reactivate_call.args[6] if len(reactivate_call.args) > 6 else None
        assert metadata is not None, "metadata was not passed"
        assert metadata.get("agent_user_id") == 99, (
            f"Expected agent_user_id=99, got: {metadata.get('agent_user_id')}"
        )


# ===========================================================================
# Fase 13: Dual-fail fallback — both Claude and Gemini fail
# ===========================================================================

class TestOrchestratorDualFail:
    """Fase 13: When both Claude and Gemini fail, handle_message must return
    a BotResponse (intent='ai_dual_fail') instead of raising, fire admin
    notifier, and save the outbound message.
    """

    def _setup_dual_fail(self, mocks):
        """Configure mocks so Claude raises API error and Gemini raises.

        Uses APIConnectionError (real Anthropic SDK exception) para que
        el circuit breaker dispare fallback a Gemini. Post M4 Task 2.2,
        exceptions genéricas (RuntimeError) NO disparan fallback — propagan.
        """
        _setup_normal_flow(mocks)
        mocks["circuit_breaker"].is_open = False
        mocks["claude"].send_message.side_effect = APIConnectionError(request=MagicMock())
        mocks["gemini"].send_message.side_effect = RuntimeError("Gemini API down")

    @pytest.mark.asyncio
    async def test_dual_fail_returns_fallback_response_not_raises(self):
        """Both providers fail → returns BotResponse with intent='ai_dual_fail'
        and text containing the user-facing fallback keywords. Must NOT raise."""
        orch, mocks = _make_orchestrator()
        self._setup_dual_fail(mocks)

        with patch(
            "app.bot.ai.ai_dispatch.get_ai_dual_fail_text",
            new=AsyncMock(return_value=(
                "Perdón, estoy teniendo un problema técnico. "
                "Intentá de nuevo en unos minutos. "
                "Si es urgente escribí ASESOR y te contactamos."
            )),
        ):
            result = await orch.handle_message(_default_request(), AsyncMock())

        assert result is not None, "handle_message must not return None on dual fail"
        assert result.intent == "ai_dual_fail", (
            f"Expected intent='ai_dual_fail', got: {result.intent!r}"
        )
        assert any(kw in result.text for kw in _DUAL_FAIL_KEYWORDS), (
            f"Fallback text must contain one of {_DUAL_FAIL_KEYWORDS!r}, got: {result.text!r}"
        )

    @pytest.mark.asyncio
    async def test_dual_fail_fires_admin_notifier(self):
        """Both providers fail → AdminNotifier.notify called with 'AMBOS providers AI'."""
        orch, mocks = _make_orchestrator()
        self._setup_dual_fail(mocks)

        mock_notifier = AsyncMock()
        mock_notifier.notify = AsyncMock(return_value=True)

        # The dual-fail block uses a lazy `from app.bot.services.admin_notifier import
        # get_admin_notifier` inside the except, so we must patch at the source module.
        with patch(
            "app.bot.ai.ai_dispatch.get_ai_dual_fail_text",
            new=AsyncMock(return_value="Perdón, problema técnico."),
        ), patch(
            "app.bot.services.admin_notifier.get_admin_notifier",
            return_value=mock_notifier,
        ):
            await orch.handle_message(_default_request(), AsyncMock())

        mock_notifier.notify.assert_awaited_once()
        call_args = mock_notifier.notify.call_args
        notify_msg = call_args[0][0] if call_args[0] else call_args[1].get("message", "")
        assert "AMBOS providers AI" in notify_msg, (
            f"Admin notification must mention 'AMBOS providers AI', got: {notify_msg!r}"
        )

    @pytest.mark.asyncio
    async def test_dual_fail_saves_outbound_message(self):
        """Both providers fail → save_outbound_message called with fallback text
        and intent='ai_dual_fail' so the message is persisted to DB."""
        orch, mocks = _make_orchestrator()
        self._setup_dual_fail(mocks)

        fallback_text = (
            "Perdón, estoy teniendo un problema técnico. "
            "Intentá de nuevo en unos minutos. "
            "Si es urgente escribí ASESOR y te contactamos."
        )

        with patch(
            "app.bot.ai.ai_dispatch.get_ai_dual_fail_text",
            new=AsyncMock(return_value=fallback_text),
        ):
            await orch.handle_message(_default_request(), AsyncMock())

        mocks["conversation_manager"].save_outbound_message.assert_awaited_once()
        save_call = mocks["conversation_manager"].save_outbound_message.call_args
        # save_outbound_message(session, conversation_id, contact_id, text, intent, ...)
        # Check that the text argument matches the fallback text
        positional = save_call[0] if save_call[0] else []
        keyword = save_call[1] if save_call[1] else {}
        saved_text = positional[3] if len(positional) > 3 else keyword.get("text", "")
        saved_intent = positional[4] if len(positional) > 4 else keyword.get("intent", "")
        assert any(kw in saved_text for kw in _DUAL_FAIL_KEYWORDS), (
            f"Saved text must contain fallback keywords, got: {saved_text!r}"
        )
        assert saved_intent == "ai_dual_fail", (
            f"Saved intent must be 'ai_dual_fail', got: {saved_intent!r}"
        )


# ===========================================================================
# TestOrchestratorModeWiring (M6.3 Plan 123-02 — BOT-03/BOT-04 + D-2)
# ===========================================================================

class TestOrchestratorModeWiring:
    """Integration: per-turn mode resolution drives the tool set at the AI call.

    Patches run_ai_with_fallback to capture the tools kwarg the orchestrator
    passes, then asserts mode -> tool-set wiring and D-2 (Telegram = busqueda).
    """

    def _wa_request(self):
        return BotRequest(
            platform="whatsapp", chat_id="595981000000",
            user_id="595981000000", user_name="Tester",
            text="hola", external_id="msg_wa",
        )

    def _wa_contact(self):
        return ContactInfo(
            id=1, name="Tester", status="new", is_baja=False,
            platform="whatsapp", phone="595981000000",
        )

    def _wa_conversation(self):
        return ConversationInfo(
            id=10, contact_id=1, platform="whatsapp",
            chat_id="595981000000", is_bot_active=True,
        )

    @pytest.mark.asyncio
    async def test_whatsapp_default_busqueda_gets_all_six_tools(self):
        """WhatsApp + bot_default_mode='busqueda' -> all 6 tools at AI call."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(
            mocks, contact=self._wa_contact(),
            conversation=self._wa_conversation(),
        )

        captured = {}

        async def _fake_ai(*args, **kwargs):
            captured["tools"] = kwargs.get("tools")
            return BotResponse(text="ok", intent="conversacion")

        with patch(
            "app.bot.core.orchestrator.run_ai_with_fallback", new=_fake_ai,
        ), patch(
            "app.bot.core.orchestrator.bot_setting_repo.get_value",
            new=AsyncMock(return_value="busqueda"),
        ):
            await orch.handle_message(self._wa_request(), AsyncMock())

        names = {t["name"] for t in captured["tools"]}
        assert "search_properties" in names
        assert "agendar_visita" in names
        assert len(captured["tools"]) == 6

    @pytest.mark.asyncio
    async def test_whatsapp_recepcionista_excludes_search(self):
        """WhatsApp + bot_default_mode='recepcionista' -> 5 tools, no search."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(
            mocks, contact=self._wa_contact(),
            conversation=self._wa_conversation(),
        )

        captured = {}

        async def _fake_ai(*args, **kwargs):
            captured["tools"] = kwargs.get("tools")
            return BotResponse(text="ok", intent="conversacion")

        with patch(
            "app.bot.core.orchestrator.run_ai_with_fallback", new=_fake_ai,
        ), patch(
            "app.bot.core.orchestrator.bot_setting_repo.get_value",
            new=AsyncMock(return_value="recepcionista"),
        ):
            await orch.handle_message(self._wa_request(), AsyncMock())

        names = {t["name"] for t in captured["tools"]}
        assert "search_properties" not in names
        assert "agendar_visita" in names
        assert len(captured["tools"]) == 5

    @pytest.mark.asyncio
    async def test_telegram_always_busqueda_zero_regression(self):
        """D-2: Telegram resolves to busqueda (all 6 tools), never reads default."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks)  # default contact/conversation are telegram

        captured = {}

        async def _fake_ai(*args, **kwargs):
            captured["tools"] = kwargs.get("tools")
            return BotResponse(text="ok", intent="conversacion")

        get_value = AsyncMock(return_value="recepcionista")
        with patch(
            "app.bot.core.orchestrator.run_ai_with_fallback", new=_fake_ai,
        ), patch(
            "app.bot.core.orchestrator.bot_setting_repo.get_value", new=get_value,
        ):
            await orch.handle_message(_default_request(), AsyncMock())

        names = {t["name"] for t in captured["tools"]}
        assert "search_properties" in names
        assert len(captured["tools"]) == 6
        # D-2: channel gate short-circuits before any default DB read.
        get_value.assert_not_awaited()


# ===========================================================================
# TestOrchestratorNameAttemptsDirective (M6.3.1 Plan 124.4 — POLISH-02 path-b)
# ===========================================================================

class TestOrchestratorNameAttemptsDirective:
    """Integration: the deterministic name-ask directive (name_gate) is injected
    into the dynamic url_context channel ONLY in recepcionista mode for an
    unnamed contact, and NEVER in busqueda mode or for a named contact.

    Patches run_ai_with_fallback to capture the url_context kwarg the
    orchestrator passes (same seam as TestOrchestratorModeWiring), then asserts
    on the merged directive text.
    """

    # Two real recepcionista name-ask phrasings (grounded in prompts.py few-shots).
    _ASK_1 = "Genial. ¿Con quién tengo el gusto?"
    _ASK_2 = "Antes de seguir, ¿tu nombre para que el asesor te ubique?"

    def _two_ask_history(self):
        """history with exactly 2 prior BOT name-asks interleaved with user turns."""
        return [
            HistoryMessage(direction="inbound", sender_type="contact", body="Hola, busco depto"),
            HistoryMessage(direction="outbound", sender_type="bot", body=self._ASK_1),
            HistoryMessage(direction="inbound", sender_type="contact", body="En Asuncion"),
            HistoryMessage(direction="outbound", sender_type="bot", body=self._ASK_2),
            HistoryMessage(direction="inbound", sender_type="contact", body="Para alquilar"),
        ]

    def _wa_request(self):
        return BotRequest(
            platform="whatsapp", chat_id="595981000099",
            user_id="595981000099", user_name="",
            text="quiero ver opciones", external_id="msg_wa_namegate",
        )

    def _tg_request(self):
        return BotRequest(
            platform="telegram", chat_id="595981000099",
            user_id="595981000099", user_name="",
            text="quiero ver opciones", external_id="msg_tg_namegate",
        )

    def _wa_contact(self, name=""):
        return ContactInfo(
            id=1, name=name, status="bot_replied", is_baja=False,
            platform="whatsapp", phone="595981000099",
        )

    def _wa_conversation(self):
        return ConversationInfo(
            id=10, contact_id=1, platform="whatsapp",
            chat_id="595981000099", is_bot_active=True,
        )

    def _tg_conversation(self):
        return ConversationInfo(
            id=10, contact_id=1, platform="telegram",
            chat_id="595981000099", is_bot_active=True,
        )

    async def _run_capture(self, orch, request, default_mode="recepcionista"):
        """Drive handle_message with run_ai_with_fallback patched; return the
        captured url_context kwarg (or "" if not passed)."""
        captured = {}

        async def _fake_ai(*args, **kwargs):
            captured["url_context"] = kwargs.get("url_context", "")
            return BotResponse(text="ok", intent="conversacion")

        with patch(
            "app.bot.core.orchestrator.run_ai_with_fallback", new=_fake_ai,
        ), patch(
            "app.bot.core.orchestrator.bot_setting_repo.get_value",
            new=AsyncMock(return_value=default_mode),
        ):
            await orch.handle_message(request, AsyncMock())
        return captured.get("url_context", "")

    @pytest.mark.asyncio
    async def test_recepcionista_unnamed_two_asks_injects_directive(self):
        """Test A: recepcionista + unnamed contact + >=2 prior name-asks ->
        the HARD directive (count + register_lead + captura parcial) reaches
        url_context, so it lands in the dynamic block 1 of build_dynamic_prompt."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(
            mocks, contact=self._wa_contact(name=""),
            conversation=self._wa_conversation(),
            history=self._two_ask_history(),
        )
        # build_origin_context is an AsyncMock by default — return "" so the
        # only dynamic injection is the name-gate directive.
        mocks["conversation_manager"].build_origin_context.return_value = ""

        url_context = await self._run_capture(orch, self._wa_request())

        assert "attempts_without_name=2" in url_context
        assert "register_lead" in url_context
        assert "captura parcial" in url_context

    @pytest.mark.asyncio
    async def test_busqueda_mode_never_injects_directive(self):
        """Test B: busqueda mode (telegram -> _resolve_mode returns busqueda)
        NEVER computes/injects the directive. Proves zero buscador change."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(
            mocks, contact=self._wa_contact(name=""),
            conversation=self._tg_conversation(),
            history=self._two_ask_history(),
        )
        mocks["conversation_manager"].build_origin_context.return_value = ""

        # Telegram resolves to busqueda inside _resolve_mode regardless of default.
        url_context = await self._run_capture(orch, self._tg_request())

        assert "attempts_without_name" not in url_context
        assert "captura parcial" not in url_context

    @pytest.mark.asyncio
    async def test_recepcionista_named_contact_no_directive(self):
        """Test C: recepcionista + named contact -> directive NOT injected
        (name-empty gate)."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(
            mocks, contact=self._wa_contact(name="Juan"),
            conversation=self._wa_conversation(),
            history=self._two_ask_history(),
        )
        mocks["conversation_manager"].build_origin_context.return_value = ""

        url_context = await self._run_capture(orch, self._wa_request())

        assert "attempts_without_name" not in url_context
        assert "captura parcial" not in url_context

    @pytest.mark.asyncio
    async def test_recepcionista_unnamed_zero_asks_no_directive(self):
        """Test D: recepcionista + unnamed + 0 prior asks -> empty section, no
        injection noise (url_context stays free of directive tokens)."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(
            mocks, contact=self._wa_contact(name=""),
            conversation=self._wa_conversation(),
            history=[],  # no prior bot name-asks
        )
        mocks["conversation_manager"].build_origin_context.return_value = ""

        url_context = await self._run_capture(orch, self._wa_request())

        assert "attempts_without_name" not in url_context
        assert "captura parcial" not in url_context


# ===========================================================================
# TestOrchestratorForcedDerivation (M6.3.1 iter-3 — deterministic forced
# derivation in CODE)
# ===========================================================================

class TestOrchestratorForcedDerivation:
    """The derivation guarantee moves from the LLM to the orchestrator.

    Root cause 124.4: Haiku does not reliably honor the HARD name_gate
    directive (conv 168) and the name-ask signal never reaches the threshold
    in the criteria-loop shape (conv 206); the Gemini fallback runs no tools
    at all. Post-AI safety net: when a recepcionista turn for an unnamed
    contact ends without register_lead and forced_derivation_due(history) is
    True, the orchestrator registers the lead itself (is_lead=True +
    deterministic motivo) and the existing Step 8c + POLISH-05 pipeline
    persists status/events and appends LEAD-{contact.id}.
    """

    _ASK_1 = "Hola! Soy Onnix. ¿Con quién tengo el gusto y qué estás buscando?"

    def _criteria_loop_history(self):
        """Conv-206 shape: 1 name-ask then a criteria loop — 3 bot turns total."""
        return [
            HistoryMessage(direction="inbound", sender_type="contact", body="Hol"),
            HistoryMessage(direction="outbound", sender_type="bot", body=self._ASK_1),
            HistoryMessage(direction="inbound", sender_type="contact", body="Buscar propiedad"),
            HistoryMessage(direction="outbound", sender_type="bot",
                           body="¿Qué tipo de propiedad, zona y presupuesto?"),
            HistoryMessage(direction="inbound", sender_type="contact",
                           body="Venta, J Augusto Saldívar"),
            HistoryMessage(direction="outbound", sender_type="bot",
                           body="¿Qué tipo de propiedad te interesa y cuál es tu presupuesto?"),
        ]

    def _short_history(self):
        """Below both thresholds: 2 bot turns, 1 name-ask."""
        return [
            HistoryMessage(direction="outbound", sender_type="bot", body=self._ASK_1),
            HistoryMessage(direction="inbound", sender_type="contact", body="hola"),
            HistoryMessage(direction="outbound", sender_type="bot", body="¿Qué estás buscando?"),
        ]

    def _wa_request(self):
        return BotRequest(
            platform="whatsapp", chat_id="595981000099",
            user_id="595981000099", user_name="",
            text="Más información", external_id="msg_wa_forced",
        )

    def _tg_request(self):
        return BotRequest(
            platform="telegram", chat_id="595981000099",
            user_id="595981000099", user_name="",
            text="Más información", external_id="msg_tg_forced",
        )

    def _wa_contact(self, name=""):
        return ContactInfo(
            id=1, name=name, status="bot_replied", is_baja=False,
            platform="whatsapp", phone="595981000099",
        )

    def _wa_conversation(self):
        return ConversationInfo(
            id=10, contact_id=1, platform="whatsapp",
            chat_id="595981000099", is_bot_active=True,
        )

    def _tg_conversation(self):
        return ConversationInfo(
            id=10, contact_id=1, platform="telegram",
            chat_id="595981000099", is_bot_active=True,
        )

    @staticmethod
    def _text_outcome(is_lead=False, lead_motivo=""):
        from app.bot.ai.ai_dispatch import AIOutcome
        from app.bot.ai.types import AIResponse

        ai_response = AIResponse(
            text="Para ayudarte mejor, ¿qué tipo de propiedad y presupuesto?",
            tool_calls=[], model="claude-haiku",
            input_tokens=10, output_tokens=10,
            stop_reason="end_turn", raw_content=[],
        )
        return AIOutcome(
            ai_response=ai_response, properties_collected=[],
            all_ids_collected=[], is_lead=is_lead, is_detail=False,
            is_opt_out=False, lead_motivo=lead_motivo,
            events_to_record=[], tool_iterations=0, fallback_used=False,
        )

    async def _run(self, orch, request, outcome):
        persist = AsyncMock()
        with patch(
            "app.bot.core.orchestrator.run_ai_with_fallback",
            new=AsyncMock(return_value=outcome),
        ), patch(
            "app.bot.core.orchestrator.persist_lead_outcome", new=persist,
        ), patch(
            "app.bot.core.orchestrator.bot_setting_repo.get_value",
            new=AsyncMock(return_value="recepcionista"),
        ):
            result = await orch.handle_message(request, AsyncMock())
        return result, persist

    @pytest.mark.asyncio
    async def test_force_fires_on_criteria_loop_shape(self):
        """Test E (conv-206 shape): recepcionista + unnamed + 3 bot turns and
        no organic register_lead -> the code forces the derivation."""
        from app.bot.core.name_gate import FORCED_DERIVATION_NOTE

        orch, mocks = _make_orchestrator()
        _setup_normal_flow(
            mocks, contact=self._wa_contact(name=""),
            conversation=self._wa_conversation(),
            history=self._criteria_loop_history(),
        )
        mocks["conversation_manager"].build_origin_context.return_value = ""
        state = mocks["conversation_manager"].get_search_context.return_value

        result, persist = await self._run(orch, self._wa_request(), self._text_outcome())

        assert result.is_lead is True
        assert result.intent == "lead"
        assert "LEAD-1" in result.text
        assert FORCED_DERIVATION_NOTE in result.text
        persist.assert_awaited_once()
        motivo = persist.await_args.args[5]
        assert "derivación automática" in motivo.lower() or "derivacion automatica" in motivo.lower()
        assert state.lead_registrado is True

    @pytest.mark.asyncio
    async def test_force_never_fires_in_busqueda_mode(self):
        """Test F: busqueda (telegram) never forces — zero buscador change."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(
            mocks, contact=self._wa_contact(name=""),
            conversation=self._tg_conversation(),
            history=self._criteria_loop_history(),
        )
        mocks["conversation_manager"].build_origin_context.return_value = ""

        result, persist = await self._run(orch, self._tg_request(), self._text_outcome())

        assert result.is_lead is False
        persist.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_force_not_fired_for_named_contact(self):
        """Test G: named contact -> no force (name-empty gate)."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(
            mocks, contact=self._wa_contact(name="Juan"),
            conversation=self._wa_conversation(),
            history=self._criteria_loop_history(),
        )
        mocks["conversation_manager"].build_origin_context.return_value = ""

        result, persist = await self._run(orch, self._wa_request(), self._text_outcome())

        assert result.is_lead is False
        persist.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_force_not_fired_when_lead_already_registered(self):
        """Test H: search_context.lead_registrado=True -> no re-fire."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(
            mocks, contact=self._wa_contact(name=""),
            conversation=self._wa_conversation(),
            history=self._criteria_loop_history(),
        )
        mocks["conversation_manager"].build_origin_context.return_value = ""
        mocks["conversation_manager"].get_search_context.return_value.lead_registrado = True

        result, persist = await self._run(orch, self._wa_request(), self._text_outcome())

        assert result.is_lead is False
        persist.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_force_not_fired_below_threshold(self):
        """Test I: 2 bot turns / 1 ask -> below both thresholds, no force."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(
            mocks, contact=self._wa_contact(name=""),
            conversation=self._wa_conversation(),
            history=self._short_history(),
        )
        mocks["conversation_manager"].build_origin_context.return_value = ""

        result, persist = await self._run(orch, self._wa_request(), self._text_outcome())

        assert result.is_lead is False
        persist.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_organic_lead_not_overwritten_by_force(self):
        """Test J: when Claude registers the lead organically the force must
        not fire — the organic motivo is preserved and no note is appended."""
        from app.bot.core.name_gate import FORCED_DERIVATION_NOTE

        orch, mocks = _make_orchestrator()
        _setup_normal_flow(
            mocks, contact=self._wa_contact(name=""),
            conversation=self._wa_conversation(),
            history=self._criteria_loop_history(),
        )
        mocks["conversation_manager"].build_origin_context.return_value = ""

        result, persist = await self._run(
            orch, self._wa_request(),
            self._text_outcome(is_lead=True, lead_motivo="cliente pidió asesor"),
        )

        assert result.is_lead is True
        persist.assert_awaited_once()
        assert persist.await_args.args[5] == "cliente pidió asesor"
        assert FORCED_DERIVATION_NOTE not in result.text
        assert "LEAD-1" in result.text
