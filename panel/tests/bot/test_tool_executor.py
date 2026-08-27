"""Tests for ToolExecutor tipo-inheritance (Fix B / Defense-2).

Verifies that _execute_search inherits ``tipo`` from search_context.filtros
when Claude omits it from the tool call input, while ensuring that an
explicit ``tipo`` in input_data always wins.

These are pure-unit tests — no DB, no real SearchService.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.bot.ai.types import ToolCall
from app.bot.core.tool_executor import ToolExecutor
from app.bot.core.types import ConversationState
from app.bot.search.relaxation import DegradationInfo
from app.bot.search.search_service import SearchResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_search_result(count: int = 1) -> SearchResult:
    props = [
        {
            "id": 200 + i,
            "title": f"Prop {i}",
            "city": "Asuncion",
            "operation": "venta",
            "property_type": "casa",
            "price_usd": 120000,
            "bedrooms": 3,
            "bathrooms": 2,
            "total_area_m2": 150,
            "source": "onnix",
            "external_id": f"ext_{200 + i}",
            "local_image_count": 2,
        }
        for i in range(count)
    ]
    return SearchResult(properties=props, total_found=count)


def _make_executor() -> tuple[ToolExecutor, AsyncMock]:
    search_service = AsyncMock()
    executor = ToolExecutor(search_service)
    return executor, search_service


# ===========================================================================
# TestTipoInheritance
# ===========================================================================


class TestTipoInheritance:
    """Fix B: _execute_search inherits tipo from search_context.filtros."""

    @pytest.mark.asyncio
    async def test_tipo_inherited_when_claude_omits_it(self):
        """When Claude sends no tipo, _execute_search uses the tipo from filtros.

        RED test until Fix B is applied.
        """
        executor, search_svc = _make_executor()
        search_svc.search_properties.return_value = _make_search_result(1)

        # Context has tipo=casa established in a previous turn
        ctx = ConversationState(
            filtros={"tipo": "casa", "ciudad": "Asuncion", "operacion": "venta"}
        )

        # Claude refines by city but forgets to echo tipo
        tc = ToolCall(
            id="toolu_inherit_01",
            name="search_properties",
            input={"ciudad": "Lambare"},  # no tipo
        )
        session = AsyncMock()

        await executor.execute(tc, session, search_context=ctx)

        call_args = search_svc.search_properties.call_args
        filters = call_args[0][0]
        assert filters.tipo == "casa", (
            f"Expected filters.tipo='casa' (inherited from context), got {filters.tipo!r}"
        )

    @pytest.mark.asyncio
    async def test_claude_tipo_wins_over_inherited(self):
        """When Claude explicitly passes a tipo, it overrides the context value.

        This is a guardrail — it must pass both BEFORE and AFTER Fix B.
        Ensures we never clobber a legitimate type switch.
        """
        executor, search_svc = _make_executor()
        search_svc.search_properties.return_value = _make_search_result(1)

        # Context has tipo=casa but the user now wants a departamento
        ctx = ConversationState(
            filtros={"tipo": "casa", "ciudad": "Asuncion", "operacion": "venta"}
        )

        tc = ToolCall(
            id="toolu_inherit_02",
            name="search_properties",
            input={"ciudad": "Asuncion", "tipo": "departamento"},  # explicit switch
        )
        session = AsyncMock()

        await executor.execute(tc, session, search_context=ctx)

        call_args = search_svc.search_properties.call_args
        filters = call_args[0][0]
        assert filters.tipo == "departamento", (
            f"Expected filters.tipo='departamento' (Claude's explicit value), got {filters.tipo!r}"
        )


# ===========================================================================
# TestRelaxedFiltersInResult — FIX 3, Bug 2026-04-25
# ===========================================================================


class TestRelaxedFiltersInResult:
    """When SearchResult carries a DegradationInfo with relaxed_filters,
    the dict returned by ToolExecutor.execute must propagate those into
    'relaxed_filters' and 'degradation_level' so Claude can inform the user.

    When there is no degradation, those keys must NOT be present (no noise).
    """

    @pytest.mark.asyncio
    async def test_result_dict_includes_relaxed_filters_when_degraded(self):
        """Bug 2026-04-25: bot dijo "7 departamentos de 2 dormitorios"
        cuando en realidad la relajación había eliminado el filtro.
        El dict devuelto a Claude debe incluir relaxed_filters y degradation_level.
        """
        executor, search_svc = _make_executor()
        # Fake SearchResult with degradation populated
        sr = _make_search_result(2)
        sr.degradation = DegradationInfo(
            level=2,
            description="Sin filtro de dormitorios",
            relaxed_filters=[
                "presupuesto ampliado a 5200000 Gs",
                "filtro de dormitorios mínimos eliminado",
            ],
        )
        search_svc.search_properties.return_value = sr

        tc = ToolCall(
            id="toolu_relax_01",
            name="search_properties",
            input={"operacion": "alquiler", "tipo": "departamento", "barrio": "Villa Morra"},
        )
        result = await executor.execute(tc, AsyncMock())

        assert result.get("degradation_level") == 2, (
            f"result_dict must include degradation_level=2, got {result.get('degradation_level')!r}"
        )
        relaxed = result.get("relaxed_filters")
        assert isinstance(relaxed, list) and len(relaxed) == 2, (
            f"result_dict must include relaxed_filters list of length 2, got {relaxed!r}"
        )
        assert "presupuesto" in " ".join(relaxed).lower()
        assert "dormitorios" in " ".join(relaxed).lower()

    @pytest.mark.asyncio
    async def test_result_dict_omits_relaxed_filters_when_no_degradation(self):
        """Sin relajación, no debe haber ruido sobre filtros relajados."""
        executor, search_svc = _make_executor()
        sr = _make_search_result(3)
        # Note: degradation is None by default
        assert sr.degradation is None
        search_svc.search_properties.return_value = sr

        tc = ToolCall(
            id="toolu_relax_02",
            name="search_properties",
            input={"operacion": "venta", "tipo": "casa", "ciudad": "Asuncion"},
        )
        result = await executor.execute(tc, AsyncMock())

        assert "degradation_level" not in result, (
            f"result_dict must NOT include degradation_level when no degradation, got {result!r}"
        )
        assert "relaxed_filters" not in result, (
            f"result_dict must NOT include relaxed_filters when no degradation, got {result!r}"
        )
