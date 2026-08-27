"""Tests unitarios para ``ai.tool_use_loop.run_tool_use_loop`` (M4 Task 3.14).

Red de seguridad que acompañó la extracción desde
``Orchestrator._call_claude_with_tools``. Sigue construyendo el
Orchestrator para heredar los mocks / system prompt / tools que la
nueva función recibe como parámetros explícitos.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.ai.tool_use_loop import MAX_TOOL_ITERATIONS, run_tool_use_loop
from app.bot.ai.types import AIResponse, ToolCall
from app.bot.core.orchestrator import Orchestrator
from app.bot.core.types import ConversationState


# ---------------------------------------------------------------------------
# Helpers  (copiados de test_orchestrator.py — ver TODO arriba)
# ---------------------------------------------------------------------------

def _make_orchestrator():
    """Crea un Orchestrator con todas las dependencias mockeadas."""
    claude = AsyncMock()
    gemini = AsyncMock()
    circuit_breaker = MagicMock()
    circuit_breaker.is_open = False
    search_service = AsyncMock()
    conversation_manager = AsyncMock()
    conversation_manager.check_human_cooldown = MagicMock(return_value=False)
    response_builder = MagicMock()
    tool_executor = AsyncMock()
    tool_executor.build_tool_result_message = MagicMock(
        return_value={"type": "tool_result", "tool_use_id": "t_stub", "content": "{}"}
    )

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


def _text_ai_response(text="Hola!", model="claude-haiku", stop_reason="end_turn"):
    """Construye un AIResponse de texto puro."""
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
    """Construye un AIResponse con tool_calls y stop_reason='tool_use'."""
    if tool_calls is None:
        tool_calls = [ToolCall(id="t1", name="search_properties", input={"ciudad": "asuncion"})]
    return AIResponse(
        text=text,
        tool_calls=tool_calls,
        model="claude-haiku",
        input_tokens=150,
        output_tokens=50,
        stop_reason="tool_use",
        raw_content=raw_content or [
            {"type": "tool_use", "id": "t1", "name": "search_properties", "input": {}}
        ],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path_no_tools_returns_text_with_zero_iterations():
    """Claude responde texto directamente: no hay loop, iterations=0."""
    orch, mocks = _make_orchestrator()
    mocks["claude"].send_message = AsyncMock(return_value=_text_ai_response("Bienvenido!"))

    messages = [{"role": "user", "content": "hola"}]
    ctx = ConversationState()

    ai_resp, props, all_ids, is_lead, is_detail, is_opt_out, motivo, events, iterations = (
        await run_tool_use_loop(
            mocks["claude"], mocks["tool_executor"], mocks["circuit_breaker"],
            messages, AsyncMock(), ctx,
            orch._system_prompt, orch._tools, url_context="",
        )
    )

    assert ai_resp.text == "Bienvenido!"
    assert ai_resp.stop_reason == "end_turn"
    assert props == []
    assert all_ids == []
    assert is_lead is False
    assert is_detail is False
    assert is_opt_out is False
    assert motivo == ""
    assert events == []
    assert iterations == 0


@pytest.mark.asyncio
async def test_search_tool_populates_properties_and_mutates_search_context():
    """search_properties: properties acumuladas, all_ids, search_context mutado, events."""
    orch, mocks = _make_orchestrator()

    tool_call = ToolCall(id="t1", name="search_properties", input={"ciudad": "asuncion", "operacion": "venta"})
    tool_result = {
        "properties": [{"id": 100, "title": "Casa A"}],
        "all_ids": [100, 101, 102],
        "total_found": 3,
    }

    mocks["claude"].send_message = AsyncMock(side_effect=[
        _tool_use_ai_response(tool_calls=[tool_call]),
        _text_ai_response("Encontre 3 propiedades."),
    ])
    mocks["tool_executor"].execute = AsyncMock(return_value=tool_result)

    messages = [{"role": "user", "content": "busco casa en asuncion"}]
    ctx = ConversationState()

    _, props, all_ids, is_lead, is_detail, is_opt_out, motivo, events, iterations = (
        await run_tool_use_loop(
            mocks["claude"], mocks["tool_executor"], mocks["circuit_breaker"],
            messages, AsyncMock(), ctx,
            orch._system_prompt, orch._tools, url_context="",
        )
    )

    assert props == [{"id": 100, "title": "Casa A"}]
    assert all_ids == [100, 101, 102]
    assert is_lead is False
    assert is_detail is False
    assert iterations == 1

    # search_context mutado
    assert ctx.filtros.get("ciudad") == "asuncion"
    assert ctx.total_found == 3
    assert len(ctx.busquedas_historicas) == 1

    # evento search registrado
    assert len(events) == 1
    assert events[0]["event_type"] == "search"
    assert events[0]["metadata"]["total_found"] == 3


@pytest.mark.asyncio
async def test_search_with_relaxed_filters_surfaces_to_search_context():
    """Bug 2026-04-26: cuando el sistema relaja filtros (degradation), Claude
    genera un aviso ANTES de las propiedades pero el ResponseBuilder lo
    truncaba a 150 chars. Para que la metadata llegue al ResponseBuilder, el
    loop debe surfacear ``relaxed_filters`` por search_context (transient
    attribute, mismo patrón que ``_contact_id`` / ``_conversation_id``).
    """
    orch, mocks = _make_orchestrator()

    tool_call = ToolCall(
        id="t1",
        name="search_properties",
        input={"ciudad": "asuncion", "barrio": "villa morra", "operacion": "alquiler"},
    )
    tool_result = {
        "properties": [{"id": 100, "title": "Depto"}],
        "all_ids": [100],
        "total_found": 1,
        "degradation_level": 3,
        "relaxed_filters": [
            "barrio Villa Morra eliminado, búsqueda ampliada a toda la ciudad",
        ],
    }

    mocks["claude"].send_message = AsyncMock(side_effect=[
        _tool_use_ai_response(tool_calls=[tool_call]),
        _text_ai_response("No encontré en Villa Morra. Te muestro alternativas en la ciudad."),
    ])
    mocks["tool_executor"].execute = AsyncMock(return_value=tool_result)

    messages = [{"role": "user", "content": "depto en villa morra"}]
    ctx = ConversationState()

    await run_tool_use_loop(
        mocks["claude"], mocks["tool_executor"], mocks["circuit_breaker"],
        messages, AsyncMock(), ctx,
        orch._system_prompt, orch._tools, url_context="",
    )

    assert getattr(ctx, "_last_relaxed_filters", None) == [
        "barrio Villa Morra eliminado, búsqueda ampliada a toda la ciudad"
    ]


@pytest.mark.asyncio
async def test_search_without_relaxed_filters_does_not_set_transient():
    """Si el search no relajó filtros, el atributo transient queda en None
    o no existe — evita falsos positivos en turnos posteriores.
    """
    orch, mocks = _make_orchestrator()

    tool_call = ToolCall(id="t1", name="search_properties", input={"ciudad": "asuncion"})
    tool_result = {
        "properties": [{"id": 100, "title": "Casa"}],
        "all_ids": [100],
        "total_found": 1,
    }

    mocks["claude"].send_message = AsyncMock(side_effect=[
        _tool_use_ai_response(tool_calls=[tool_call]),
        _text_ai_response("Encontre 1 propiedad."),
    ])
    mocks["tool_executor"].execute = AsyncMock(return_value=tool_result)

    ctx = ConversationState()
    await run_tool_use_loop(
        mocks["claude"], mocks["tool_executor"], mocks["circuit_breaker"],
        [{"role": "user", "content": "casa en asuncion"}], AsyncMock(), ctx,
        orch._system_prompt, orch._tools, url_context="",
    )

    # Either attribute absent or empty list — both indicate "no relajación"
    val = getattr(ctx, "_last_relaxed_filters", None)
    assert not val, f"Expected no relaxed_filters, got {val!r}"


@pytest.mark.asyncio
async def test_get_property_detail_ok_sets_is_detail_and_collects_property():
    """get_property_detail sin error: is_detail=True, propiedad en collected, evento detail_view."""
    orch, mocks = _make_orchestrator()

    tool_call = ToolCall(id="t4", name="get_property_detail", input={"referencia": "la primera"})
    tool_result = {"id": 42, "title": "Casa X", "city": "Asuncion"}

    mocks["claude"].send_message = AsyncMock(side_effect=[
        _tool_use_ai_response(tool_calls=[tool_call]),
        _text_ai_response("Aqui el detalle."),
    ])
    mocks["tool_executor"].execute = AsyncMock(return_value=tool_result)

    messages = [{"role": "user", "content": "dame detalle"}]
    ctx = ConversationState()

    _, props, all_ids, is_lead, is_detail, is_opt_out, motivo, events, iterations = (
        await run_tool_use_loop(
            mocks["claude"], mocks["tool_executor"], mocks["circuit_breaker"],
            messages, AsyncMock(), ctx,
            orch._system_prompt, orch._tools, url_context="",
        )
    )

    assert is_detail is True
    assert {"id": 42, "title": "Casa X", "city": "Asuncion"} in props
    assert all_ids == []  # detail no produce all_ids

    detail_events = [e for e in events if e["event_type"] == "detail_view"]
    assert len(detail_events) == 1
    assert detail_events[0]["metadata"]["property_id"] == 42


@pytest.mark.asyncio
async def test_get_property_detail_with_error_does_not_set_is_detail():
    """get_property_detail con error: NO agrega a properties_collected, NO setea is_detail."""
    orch, mocks = _make_orchestrator()

    tool_call = ToolCall(id="t4", name="get_property_detail", input={"referencia": "inexistente"})
    tool_result = {"error": "Propiedad no encontrada"}

    mocks["claude"].send_message = AsyncMock(side_effect=[
        _tool_use_ai_response(tool_calls=[tool_call]),
        _text_ai_response("No encontre la propiedad."),
    ])
    mocks["tool_executor"].execute = AsyncMock(return_value=tool_result)

    messages = [{"role": "user", "content": "dame detalle de prop inexistente"}]
    ctx = ConversationState()

    _, props, all_ids, is_lead, is_detail, is_opt_out, motivo, events, iterations = (
        await run_tool_use_loop(
            mocks["claude"], mocks["tool_executor"], mocks["circuit_breaker"],
            messages, AsyncMock(), ctx,
            orch._system_prompt, orch._tools, url_context="",
        )
    )

    assert is_detail is False
    assert props == []
    assert not any(e["event_type"] == "detail_view" for e in events)


@pytest.mark.asyncio
async def test_register_lead_success_sets_is_lead_and_lead_motivo():
    """register_lead con success=True: is_lead=True, lead_motivo correcto, search_context.lead_registrado."""
    orch, mocks = _make_orchestrator()

    tool_call = ToolCall(id="t2", name="register_lead", input={"motivo": "cerrar pronto"})
    tool_result = {"success": True, "motivo": "cerrar pronto"}

    mocks["claude"].send_message = AsyncMock(side_effect=[
        _tool_use_ai_response(tool_calls=[tool_call]),
        _text_ai_response("Registre tu consulta."),
    ])
    mocks["tool_executor"].execute = AsyncMock(return_value=tool_result)

    messages = [{"role": "user", "content": "quiero hablar con un asesor"}]
    ctx = ConversationState()

    _, props, all_ids, is_lead, is_detail, is_opt_out, motivo, events, iterations = (
        await run_tool_use_loop(
            mocks["claude"], mocks["tool_executor"], mocks["circuit_breaker"],
            messages, AsyncMock(), ctx,
            orch._system_prompt, orch._tools, url_context="",
        )
    )

    assert is_lead is True
    assert motivo == "cerrar pronto"
    assert ctx.lead_registrado is True


@pytest.mark.asyncio
async def test_register_lead_failure_does_not_set_is_lead():
    """register_lead con success=False: no setea is_lead."""
    orch, mocks = _make_orchestrator()

    tool_call = ToolCall(id="t2", name="register_lead", input={"motivo": "intento"})
    tool_result = {"success": False, "motivo": ""}

    mocks["claude"].send_message = AsyncMock(side_effect=[
        _tool_use_ai_response(tool_calls=[tool_call]),
        _text_ai_response("Hubo un problema."),
    ])
    mocks["tool_executor"].execute = AsyncMock(return_value=tool_result)

    messages = [{"role": "user", "content": "quiero asesor"}]
    ctx = ConversationState()

    _, props, all_ids, is_lead, is_detail, is_opt_out, motivo, events, iterations = (
        await run_tool_use_loop(
            mocks["claude"], mocks["tool_executor"], mocks["circuit_breaker"],
            messages, AsyncMock(), ctx,
            orch._system_prompt, orch._tools, url_context="",
        )
    )

    assert is_lead is False
    assert ctx.lead_registrado is False


@pytest.mark.asyncio
async def test_process_opt_out_success_sets_is_opt_out():
    """process_opt_out con success=True: is_opt_out=True."""
    orch, mocks = _make_orchestrator()

    tool_call = ToolCall(id="t3", name="process_opt_out", input={})
    tool_result = {"success": True}

    mocks["claude"].send_message = AsyncMock(side_effect=[
        _tool_use_ai_response(tool_calls=[tool_call]),
        _text_ai_response("Te damos de baja."),
    ])
    mocks["tool_executor"].execute = AsyncMock(return_value=tool_result)

    messages = [{"role": "user", "content": "no me manden mas mensajes"}]
    ctx = ConversationState()

    _, props, all_ids, is_lead, is_detail, is_opt_out, motivo, events, iterations = (
        await run_tool_use_loop(
            mocks["claude"], mocks["tool_executor"], mocks["circuit_breaker"],
            messages, AsyncMock(), ctx,
            orch._system_prompt, orch._tools, url_context="",
        )
    )

    assert is_opt_out is True
    assert is_lead is False


@pytest.mark.asyncio
async def test_multiple_iterations_accumulate_properties():
    """Dos iteraciones: search luego detail. properties_collected acumula ambos resultados."""
    orch, mocks = _make_orchestrator()

    search_tc = ToolCall(id="t1", name="search_properties", input={"ciudad": "luque"})
    detail_tc = ToolCall(id="t4", name="get_property_detail", input={"referencia": "primera"})

    search_result = {
        "properties": [{"id": 200, "title": "Casa B"}],
        "all_ids": [200, 201],
        "total_found": 2,
    }
    detail_result = {"id": 42, "title": "Casa X", "city": "Luque"}

    mocks["claude"].send_message = AsyncMock(side_effect=[
        _tool_use_ai_response(tool_calls=[search_tc]),
        _tool_use_ai_response(tool_calls=[detail_tc]),
        _text_ai_response("Aqui tenes las opciones."),
    ])
    mocks["tool_executor"].execute = AsyncMock(side_effect=[search_result, detail_result])

    messages = [{"role": "user", "content": "busca y dame detalle"}]
    ctx = ConversationState()

    _, props, all_ids, is_lead, is_detail, is_opt_out, motivo, events, iterations = (
        await run_tool_use_loop(
            mocks["claude"], mocks["tool_executor"], mocks["circuit_breaker"],
            messages, AsyncMock(), ctx,
            orch._system_prompt, orch._tools, url_context="",
        )
    )

    assert iterations == 2
    # La propiedad de search
    assert {"id": 200, "title": "Casa B"} in props
    # La propiedad de detail
    assert {"id": 42, "title": "Casa X", "city": "Luque"} in props
    assert is_detail is True
    assert all_ids == [200, 201]


@pytest.mark.asyncio
async def test_max_tool_iterations_respected():
    """Claude siempre devuelve tool_use: loop termina exactamente en MAX_TOOL_ITERATIONS=5."""
    orch, mocks = _make_orchestrator()

    tool_call = ToolCall(id="t1", name="search_properties", input={"ciudad": "asuncion"})
    tool_result = {"properties": [], "all_ids": [], "total_found": 0}

    # MAX_TOOL_ITERATIONS llamadas tool_use + 1 llamada inicial también tool_use
    # El loop corre mientras response.stop_reason == "tool_use" AND iterations < MAX_TOOL_ITERATIONS
    # Con stop_reason siempre "tool_use": entra las primeras MAX_TOOL_ITERATIONS veces (iterations: 1..5),
    # la última respuesta de Claude (la del último send_message en el loop) también es tool_use,
    # pero el while ya no se cumple (iterations == 5).
    tool_use_resp = _tool_use_ai_response(tool_calls=[tool_call])
    # Necesitamos 1 respuesta inicial + MAX_TOOL_ITERATIONS respuestas dentro del loop
    mocks["claude"].send_message = AsyncMock(
        return_value=tool_use_resp  # siempre devuelve tool_use
    )
    mocks["tool_executor"].execute = AsyncMock(return_value=tool_result)

    messages = [{"role": "user", "content": "busca"}]
    ctx = ConversationState()

    _, props, all_ids, is_lead, is_detail, is_opt_out, motivo, events, iterations = (
        await run_tool_use_loop(
            mocks["claude"], mocks["tool_executor"], mocks["circuit_breaker"],
            messages, AsyncMock(), ctx,
            orch._system_prompt, orch._tools, url_context="",
        )
    )

    assert iterations == MAX_TOOL_ITERATIONS


@pytest.mark.asyncio
async def test_busquedas_historicas_capped_at_20():
    """Si ya hay 20 búsquedas históricas y llega una nueva, mantiene solo las últimas 20."""
    orch, mocks = _make_orchestrator()

    tool_call = ToolCall(id="t1", name="search_properties", input={"ciudad": "asuncion"})
    tool_result = {"properties": [], "all_ids": [1, 2], "total_found": 2}

    mocks["claude"].send_message = AsyncMock(side_effect=[
        _tool_use_ai_response(tool_calls=[tool_call]),
        _text_ai_response("Ok."),
    ])
    mocks["tool_executor"].execute = AsyncMock(return_value=tool_result)

    messages = [{"role": "user", "content": "busca"}]
    ctx = ConversationState()
    # Prellenamos con 20 búsquedas previas
    ctx.busquedas_historicas = [{"fecha": f"2026-01-{i:02d}", "ciudad": "x"} for i in range(1, 21)]
    assert len(ctx.busquedas_historicas) == 20

    await run_tool_use_loop(
            mocks["claude"], mocks["tool_executor"], mocks["circuit_breaker"],
            messages, AsyncMock(), ctx,
            orch._system_prompt, orch._tools, url_context="",
        )

    # Debe seguir siendo 20 (se agrego 1 nueva y se descarto la mas antigua)
    assert len(ctx.busquedas_historicas) == 20
    # La nueva busqueda (ciudad asuncion) debe estar al final
    assert ctx.busquedas_historicas[-1]["ciudad"] == "asuncion"


@pytest.mark.asyncio
async def test_circuit_breaker_record_success_called_once_per_claude_call():
    """1 iteración de tool = 2 llamadas a Claude → circuit_breaker.record_success llamado 2 veces."""
    orch, mocks = _make_orchestrator()

    tool_call = ToolCall(id="t1", name="search_properties", input={"ciudad": "asuncion"})
    tool_result = {"properties": [], "all_ids": [], "total_found": 0}

    mocks["claude"].send_message = AsyncMock(side_effect=[
        _tool_use_ai_response(tool_calls=[tool_call]),
        _text_ai_response("Ok."),
    ])
    mocks["tool_executor"].execute = AsyncMock(return_value=tool_result)

    messages = [{"role": "user", "content": "busca"}]
    ctx = ConversationState()

    await run_tool_use_loop(
            mocks["claude"], mocks["tool_executor"], mocks["circuit_breaker"],
            messages, AsyncMock(), ctx,
            orch._system_prompt, orch._tools, url_context="",
        )

    # 1 llamada inicial + 1 dentro del loop = 2 en total
    assert mocks["circuit_breaker"].record_success.call_count == 2


@pytest.mark.asyncio
async def test_build_dynamic_prompt_called_once_with_search_context_and_url_context():
    """build_dynamic_prompt se llama exactamente una vez al principio con base_system_prompt + search_context + url_context.

    Post M4 Task 3.4, la función vive en app.bot.ai.prompt_builder y recibe
    el base_system_prompt explícitamente (antes era self._system_prompt).
    """
    orch, mocks = _make_orchestrator()
    mocks["claude"].send_message = AsyncMock(return_value=_text_ai_response("Hola!"))

    messages = [{"role": "user", "content": "hola"}]
    ctx = ConversationState()
    url_ctx = "URL context: https://example.com/prop/123"

    with patch(
        "app.bot.ai.tool_use_loop.build_dynamic_prompt",
        return_value="SYSTEM PROMPT",
    ) as mock_bdp:
        await run_tool_use_loop(
            mocks["claude"], mocks["tool_executor"], mocks["circuit_breaker"],
            messages, AsyncMock(), ctx,
            orch._system_prompt, orch._tools, url_context=url_ctx,
        )

    mock_bdp.assert_called_once_with(orch._system_prompt, ctx, url_context=url_ctx)


@pytest.mark.asyncio
async def test_messages_mutated_with_assistant_and_tool_result_per_iteration():
    """Después de 1 iteración, messages crece en 2 (assistant + user con tool_results)."""
    orch, mocks = _make_orchestrator()

    tool_call = ToolCall(id="t1", name="search_properties", input={"ciudad": "asuncion"})
    tool_result = {"properties": [], "all_ids": [], "total_found": 0}

    mocks["claude"].send_message = AsyncMock(side_effect=[
        _tool_use_ai_response(tool_calls=[tool_call]),
        _text_ai_response("Ok."),
    ])
    mocks["tool_executor"].execute = AsyncMock(return_value=tool_result)

    messages = [{"role": "user", "content": "busca"}]
    original_len = len(messages)

    ctx = ConversationState()
    await run_tool_use_loop(
            mocks["claude"], mocks["tool_executor"], mocks["circuit_breaker"],
            messages, AsyncMock(), ctx,
            orch._system_prompt, orch._tools, url_context="",
        )

    # 1 iteración → +2 mensajes (assistant + user/tool_results)
    assert len(messages) == original_len + 2
    assert messages[-2]["role"] == "assistant"
    assert messages[-1]["role"] == "user"


@pytest.mark.asyncio
async def test_url_context_passed_to_build_dynamic_prompt():
    """url_context no vacío se pasa correctamente a build_dynamic_prompt."""
    orch, mocks = _make_orchestrator()
    mocks["claude"].send_message = AsyncMock(return_value=_text_ai_response("Ok."))

    messages = [{"role": "user", "content": "mira esta prop"}]
    ctx = ConversationState()
    url_ctx = "El usuario compartio esta URL: https://onnix.com/prop/999"

    with patch(
        "app.bot.ai.tool_use_loop.build_dynamic_prompt",
        return_value="SYSTEM",
    ) as mock_bdp:
        await run_tool_use_loop(
            mocks["claude"], mocks["tool_executor"], mocks["circuit_breaker"],
            messages, AsyncMock(), ctx,
            orch._system_prompt, orch._tools, url_context=url_ctx,
        )

    # Verificar que url_context fue incluido en la llamada
    call_kwargs = mock_bdp.call_args
    assert call_kwargs.kwargs.get("url_context") == url_ctx or (
        len(call_kwargs.args) > 1 and call_kwargs.args[1] == url_ctx
    )
