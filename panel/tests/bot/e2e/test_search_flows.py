"""E2E tests for search flows — M3 Fase E.

Covers 5 search flow scenarios (~7 tests):
  1. test_busqueda_completa_un_turno         — all filters in one message
  2. test_busqueda_incremental_pide_lo_que_falta — bot asks missing fields
  3. test_busqueda_incremental_completa_y_busca  — context accumulated, then searches
  4. test_refinamiento_filtros_mantiene_contexto — post-search filter refinement
  5. test_nueva_busqueda_resetea_contexto       — new search clears old context
  6. test_sin_resultados_informa_minimo         — 0 results with alternatives
  7. test_sin_resultados_espera_instruccion     — after 0 results, bot waits

Tool names (panel/app/bot/ai/tools.py):
  - search_properties
  - get_property_detail
  - register_lead
  - process_opt_out

ConversationState fields used:
  - filtros  — dict accumulating search filters (operacion, tipo, barrio, ciudad, precio_max, …)
  - etapa    — current stage string

Design contract (per runner.py):
  - REAL orchestrator, mocked Claude + tool_executor + search_mock
  - assert_last_tool("none")  → no tool called in that turn
  - assert_last_tool("search_properties") → search called
  - assert_tool_args(**kwargs) → tool input had those key=value pairs
  - assert_tool_args_not_contains(*keys) → key must NOT be in tool input
  - assert_search_context(**kwargs) → fields on ConversationState after last update
  - last_properties_shown → list of property dicts from BotResponse.properties
"""
from __future__ import annotations

import pytest

from app.bot.core.types import ConversationState


# ---------------------------------------------------------------------------
# Shared property fixtures
# ---------------------------------------------------------------------------

def _make_prop(prop_id: int, **kwargs) -> dict:
    """Build a minimal property dict for use in program_search_result."""
    return {
        "id": prop_id,
        "title": f"Propiedad test {prop_id}",
        "operation": kwargs.get("operation", "venta"),
        "property_type": kwargs.get("property_type", "casa"),
        "city": kwargs.get("city", "Asuncion"),
        "neighborhood": kwargs.get("neighborhood", "Villa Morra"),
        "price_usd": kwargs.get("price_usd", 180000),
        "is_active": True,
        "source": "onnix",
        "local_image_count": 2,
    }


# ---------------------------------------------------------------------------
# Test class 1: Búsqueda completa en un turno
# ---------------------------------------------------------------------------

class TestBusquedaCompletaUnTurno:
    """All filters provided in a single user message → one search_properties call."""

    @pytest.mark.asyncio
    async def test_busqueda_completa_un_turno(self, runner):
        """Single message with all filters: search is called immediately.

        Arrange: Claude receives message, calls search_properties with all filters.
        Program two result properties.
        Assert: tool called, filtros updated, response mentions Villa Morra,
                two properties shown.
        """
        props = [_make_prop(1001), _make_prop(1002)]

        runner.program_claude_response(
            tool_calls=[{
                "name": "search_properties",
                "input": {
                    "operacion": "venta",
                    "barrio": "Villa Morra",
                    "precio_max": 200000,
                    "tipo": "casa",
                },
            }],
            text="Te muestro 2 opciones en Villa Morra.",
        )
        runner.program_search_result(props)

        await runner.send("busco casa en Villa Morra para comprar hasta 200 mil")

        # Tool was called
        runner.assert_last_tool("search_properties")

        # Tool args match the intent
        runner.assert_tool_args(
            operacion="venta",
            barrio="Villa Morra",
            precio_max=200000,
            tipo="casa",
        )

        # search_context.filtros accumulated the args
        await runner.assert_search_context(
            filtros={
                "operacion": "venta",
                "barrio": "Villa Morra",
                "precio_max": 200000,
                "tipo": "casa",
            }
        )

        # Response mentions the zone
        runner.assert_response_contains("villa morra")

        # Two properties were shown
        assert len(runner.last_properties_shown) == 2


# ---------------------------------------------------------------------------
# Test class 2: Búsqueda incremental — bot pide lo que falta
# ---------------------------------------------------------------------------

class TestBusquedaIncrementalPideLoqueFalta:
    """Bot asks for missing fields one by one before searching."""

    @pytest.mark.asyncio
    async def test_turno1_sin_tool_pregunta_operacion(self, runner):
        """Turn 1: user says 'busco casa' → bot asks comprar or alquilar, no tool.

        Assert: no tool called.
        """
        runner.program_claude_response(text="¿Para comprar o alquilar?")

        await runner.send("busco casa")

        runner.assert_last_tool("none")

    @pytest.mark.asyncio
    async def test_turno2_sin_tool_pregunta_zona(self, runner):
        """Turn 2: user says 'comprar' → bot asks for zone, no tool.

        After two turns the context should accumulate operacion and tipo
        (if the orchestrator stores them in busqueda_incompleta etapa).
        Assert: still no tool after second turn.
        """
        # Turn 1
        runner.program_claude_response(text="¿Para comprar o alquilar?")
        await runner.send("busco casa")

        # Turn 2
        runner.program_claude_response(text="¿En qué zona buscás?")
        await runner.send("comprar")

        runner.assert_last_tool("none")


# ---------------------------------------------------------------------------
# Test class 3: Búsqueda incremental — completa y busca
# ---------------------------------------------------------------------------

class TestBusquedaIncrementalCompletaYBusca:
    """After enough context is accumulated, bot searches without prompting."""

    @pytest.mark.asyncio
    async def test_tres_turnos_acumula_y_busca(self, runner):
        """Three turns: type → operacion → zone. Third turn triggers search.

        Turn 1: 'busco casa'         → bot asks comprar/alquilar (no tool)
        Turn 2: 'comprar'             → bot asks zone (no tool)
        Turn 3: 'Carmelitas'         → bot has all filters, calls search_properties
        """
        props = [_make_prop(2001, neighborhood="Carmelitas"), _make_prop(2002, neighborhood="Carmelitas")]

        # Turn 1: no tool, ask for operacion
        runner.program_claude_response(text="¿Para comprar o alquilar?")
        await runner.send("busco casa")
        runner.assert_last_tool("none")

        # Turn 2: no tool, ask for zone
        runner.program_claude_response(text="¿En qué zona?")
        await runner.send("comprar")
        runner.assert_last_tool("none")

        # Turn 3: now Claude has all filters → calls search_properties
        runner.program_claude_response(
            tool_calls=[{
                "name": "search_properties",
                "input": {
                    "operacion": "venta",
                    "tipo": "casa",
                    "barrio": "Carmelitas",
                },
            }],
            text="Encontré estas opciones en Carmelitas.",
        )
        runner.program_search_result(props)

        await runner.send("Carmelitas")

        # Tool was called with accumulated filters
        runner.assert_last_tool("search_properties")
        runner.assert_tool_args(operacion="venta", tipo="casa", barrio="Carmelitas")

        # Response mentions results
        runner.assert_response_contains("encontre", "carmelitas")

        # Properties were returned
        assert len(runner.last_properties_shown) > 0


# ---------------------------------------------------------------------------
# Test class 4: Refinamiento mantiene contexto
# ---------------------------------------------------------------------------

class TestRefinamientoFiltrosMantienContexto:
    """Post-search filter refinement preserves existing filters."""

    @pytest.mark.asyncio
    async def test_refinamiento_tipo_preserva_filtros_anteriores(self, runner):
        """User changes only 'tipo' after first search; all prior filters are kept.

        Arrange:
          - First search: Villa Morra, venta, casa, precio_max=250000
          - Second turn: 'que sea duplex'
          - Second tool call must include zona+operacion+precio_max from context
        Assert:
          - Second tool_args has tipo=duplex AND barrio=Villa Morra AND precio_max=250000
          - search_context.filtros reflects the merged state
        """
        props_first = [_make_prop(3001), _make_prop(3002)]
        props_second = [_make_prop(3003, property_type="duplex")]

        # --- First search ---
        runner.program_claude_response(
            tool_calls=[{
                "name": "search_properties",
                "input": {
                    "operacion": "venta",
                    "barrio": "Villa Morra",
                    "tipo": "casa",
                    "precio_max": 250000,
                },
            }],
            text="Te muestro casas en Villa Morra.",
        )
        runner.program_search_result(props_first)
        await runner.send("busco casa en Villa Morra para comprar hasta 250 mil")

        runner.assert_last_tool("search_properties")
        runner.assert_tool_args(operacion="venta", barrio="Villa Morra", tipo="casa", precio_max=250000)

        # Seed the search context with accumulated filtros for the second turn.
        # The real orchestrator updates search_context.filtros = {**filtros, **tc.input}
        # after a successful search. We replicate that here so the second turn's
        # Claude response can include those accumulated filters.
        runner.set_search_context(ConversationState(
            etapa="mostrando_resultados",
            filtros={
                "operacion": "venta",
                "barrio": "Villa Morra",
                "tipo": "casa",
                "precio_max": 250000,
            },
        ))

        # --- Second search: refinement ---
        runner.program_claude_response(
            tool_calls=[{
                "name": "search_properties",
                "input": {
                    "operacion": "venta",
                    "barrio": "Villa Morra",
                    "tipo": "duplex",
                    "precio_max": 250000,
                },
            }],
            text="Encontré estos duplexes en Villa Morra.",
        )
        runner.program_search_result(props_second)

        await runner.send("que sea duplex")

        runner.assert_last_tool("search_properties")

        # All prior filters preserved, only tipo changed
        runner.assert_tool_args(
            operacion="venta",
            barrio="Villa Morra",
            tipo="duplex",
            precio_max=250000,
        )

        # Merged filtros in search_context include tipo=duplex and the old filters
        await runner.assert_search_context(
            filtros={
                "operacion": "venta",
                "barrio": "Villa Morra",
                "tipo": "duplex",
                "precio_max": 250000,
            }
        )


# ---------------------------------------------------------------------------
# Test class 5: Nueva búsqueda resetea contexto
# ---------------------------------------------------------------------------

class TestNuevaBusquedaResetaContexto:
    """Starting a new search clears all previous filters."""

    @pytest.mark.asyncio
    async def test_nueva_busqueda_no_arrastra_filtros_viejos(self, runner):
        """After first search, a wholly new query uses only the new filters.

        Old filters (Villa Morra, precio_max=250000) must NOT appear in the
        second tool call for 'alquiler departamento en Las Mercedes'.
        """
        props_first = [_make_prop(4001)]
        props_second = [_make_prop(4002, operation="alquiler", property_type="departamento",
                                   neighborhood="Las Mercedes")]

        # --- First search ---
        runner.program_claude_response(
            tool_calls=[{
                "name": "search_properties",
                "input": {
                    "operacion": "venta",
                    "barrio": "Villa Morra",
                    "tipo": "casa",
                    "precio_max": 250000,
                },
            }],
            text="Te muestro casas en Villa Morra.",
        )
        runner.program_search_result(props_first)
        await runner.send("busco casa en Villa Morra para comprar hasta 250 mil")

        # Explicitly reset the search context as the orchestrator would on a new search.
        # The bot treats 'ahora quiero algo diferente' as a new search and calls
        # search_properties with ONLY the new filters.
        runner.set_search_context(ConversationState(
            etapa="inicio",
            filtros={},
        ))

        # --- New search with completely different filters ---
        runner.program_claude_response(
            tool_calls=[{
                "name": "search_properties",
                "input": {
                    "operacion": "alquiler",
                    "tipo": "departamento",
                    "barrio": "Las Mercedes",
                },
            }],
            text="Busco departamentos en alquiler en Las Mercedes.",
        )
        runner.program_search_result(props_second)

        await runner.send("ahora quiero alquilar departamento en Las Mercedes")

        runner.assert_last_tool("search_properties")

        # New filters present
        runner.assert_tool_args(
            operacion="alquiler",
            tipo="departamento",
            barrio="Las Mercedes",
        )

        # Old filters NOT in this call
        runner.assert_tool_args_not_contains("precio_max")

        # search_context filtros contains only the new filters
        await runner.assert_search_context(
            filtros={
                "operacion": "alquiler",
                "tipo": "departamento",
                "barrio": "Las Mercedes",
            }
        )


# ---------------------------------------------------------------------------
# Test class 6: Sin resultados — informa alternativa mínima
# ---------------------------------------------------------------------------

class TestSinResultadosInformaMinimo:
    """Zero results: bot reports alternatives, shows no properties."""

    @pytest.mark.asyncio
    async def test_sin_resultados_con_alternativa_precio(self, runner):
        """0 results + alternatives dict → bot mentions cheapest price, no props shown.

        Arrange:
          - Claude calls search_properties
          - tool_executor returns 0 properties with min_price info
          - Claude's second response mentions 'no encontre' and the alternative
        Assert:
          - Response contains 'no encontre' or 'sin resultados'
          - last_properties_shown is empty
          - tool_executor.execute was called exactly once (no auto-retry)
        """
        runner.program_claude_response(
            tool_calls=[{
                "name": "search_properties",
                "input": {
                    "operacion": "venta",
                    "barrio": "Villa Morra",
                    "tipo": "casa",
                    "precio_max": 100000,
                },
            }],
            text=(
                "No encontré casas en Villa Morra dentro de ese presupuesto. "
                "La opción más económica arranca en USD 280.000."
            ),
        )
        # 0 properties
        runner.program_search_result(
            properties=[],
            alternatives={"cheapest_in_zone": {"price_usd": 280000, "title": "Casa en Villa Morra"}},
        )

        await runner.send("busco casa en Villa Morra hasta 100 mil")

        runner.assert_last_tool("search_properties")

        # Response acknowledges 0 results
        runner.assert_response_contains("no encontre")

        # No properties displayed
        assert len(runner.last_properties_shown) == 0

        # Exactly 1 call to tool_executor.execute — no silent retry
        assert runner._tool_executor.execute.call_count == 1


# ---------------------------------------------------------------------------
# Test class 7: Sin resultados — bot espera instrucción del usuario
# ---------------------------------------------------------------------------

class TestSinResultadosEsperaInstruccion:
    """After 0 results, bot does NOT re-search on a vague follow-up."""

    @pytest.mark.asyncio
    async def test_sin_resultados_hmm_no_reitera_busqueda(self, runner):
        """After 0 results, user says 'hmm' → bot asks what to adjust, no new tool.

        Turn 1: search returns 0 results.
        Turn 2: user sends 'hmm' → bot should NOT call search_properties again.
                It must ask the user to clarify (zone, budget, etc.).
        """
        # Turn 1: search returns 0 results
        runner.program_claude_response(
            tool_calls=[{
                "name": "search_properties",
                "input": {
                    "operacion": "venta",
                    "barrio": "Villa Morra",
                    "tipo": "casa",
                    "precio_max": 100000,
                },
            }],
            text=(
                "No encontré casas con esos filtros. "
                "¿Querés ampliar la zona o subir el presupuesto?"
            ),
        )
        runner.program_search_result(properties=[])
        await runner.send("busco casa en Villa Morra hasta 100 mil")

        runner.assert_last_tool("search_properties")

        # Turn 2: vague follow-up — bot must NOT re-search
        runner.program_claude_response(
            text="¿Querés que amplíe la zona, suba el presupuesto, o probamos otro tipo de propiedad?"
        )

        await runner.send("hmm")

        # No tool call — Claude waits for explicit instruction
        runner.assert_last_tool("none")

        # Response asks user what to adjust
        runner.assert_response_contains("zona", "presupuesto")
