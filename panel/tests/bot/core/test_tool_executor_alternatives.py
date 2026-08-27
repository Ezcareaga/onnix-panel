"""Tests for ToolExecutor AlternativesBuilder integration (M5 Fase E).

Tests cover:
1. test_flag_off_preserves_current_behavior   — flag OFF → legacy shape (no alternatives key)
2. test_flag_on_lt_2_filters_no_alternatives  — flag ON but < 2 active filters → legacy shape
3. test_flag_on_with_2_filters_and_hits       — flag ON + 2 filters + zero-results + alts → new shape
4. test_flag_on_with_alternatives_persists_to_state — state.pending_alternatives populated
5. test_flag_on_no_alternatives_falls_back    — flag ON + builder returns [] → legacy shape
"""
from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bot.ai.types import ToolCall
from app.bot.core.tool_executor import ToolExecutor
from app.bot.core.types import ConversationState
from app.bot.search.alternatives import Alternative, AlternativesResult
from app.bot.search.search_service import SearchResult


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _zero_result() -> SearchResult:
    """SearchResult with 0 properties found."""
    return SearchResult(properties=[], total_found=0)


def _nonzero_result(n: int = 1) -> SearchResult:
    """SearchResult with n properties found."""
    props = [
        {
            "id": 100 + i,
            "title": f"Prop {i}",
            "city": "Asuncion",
            "operation": "venta",
            "property_type": "casa",
            "price_usd": 100000,
            "bedrooms": 3,
            "bathrooms": 2,
            "total_area_m2": 120,
            "source": "onnix",
            "external_id": f"ext_{100 + i}",
            "local_image_count": 1,
        }
        for i in range(n)
    ]
    return SearchResult(properties=props, total_found=n)


def _make_alternative(alt_id: str = "zona_vecina:lambare") -> Alternative:
    return Alternative(
        id=alt_id,
        label="En Lambaré hay 5",
        count=5,
        filters={"ciudad": "Lambare", "tipo": "casa", "operacion": "venta"},
        reason="zona vecina",
        callback_payload=f"ALT:{alt_id}",
    )


def _make_executor(
    search_returns: SearchResult,
    flag_value: bool,
    alternatives_result: AlternativesResult | None = None,
) -> ToolExecutor:
    """Build a ToolExecutor with mocked dependencies."""
    search_service = AsyncMock()
    search_service.search_properties.return_value = search_returns
    # Expose _geo_resolver.resolve() for the Fase E integration
    geo_mock = MagicMock()
    geo_mock.resolve.return_value = MagicMock()  # ResolvedGeo stub
    search_service._geo_resolver = geo_mock

    bot_settings_repo = AsyncMock()
    bot_settings_repo.get_bool.return_value = flag_value

    alternatives_builder = AsyncMock()
    if alternatives_result is not None:
        alternatives_builder.build.return_value = alternatives_result
    else:
        alternatives_builder.build.return_value = AlternativesResult(alternatives=[])

    return ToolExecutor(
        search_service=search_service,
        alternatives_builder=alternatives_builder,
        bot_settings_repo=bot_settings_repo,
    )


def _make_tool_call(extra: dict | None = None) -> ToolCall:
    """Build a search_properties ToolCall with 2 active filters by default."""
    base = {"tipo": "casa", "ciudad": "Asuncion", "operacion": "venta"}
    if extra:
        base.update(extra)
    return ToolCall(
        id="toolu_e2e_01",
        name="search_properties",
        input=base,
    )


# ---------------------------------------------------------------------------
# 1. Flag OFF — legacy shape preserved
# ---------------------------------------------------------------------------

class TestFlagOff:
    @pytest.mark.asyncio
    async def test_flag_off_preserves_current_behavior(self):
        """When flag is OFF, zero-results returns legacy shape without alternatives key."""
        executor = _make_executor(
            search_returns=_zero_result(),
            flag_value=False,
        )
        session = AsyncMock()
        ctx = ConversationState()

        result = await executor.execute(
            _make_tool_call(), session, search_context=ctx,
        )

        assert result["total_found"] == 0
        assert result["properties"] == []
        # Legacy shape must NOT contain alternatives key
        assert "alternatives" not in result


# ---------------------------------------------------------------------------
# 2. Flag ON but < 2 active filters
# ---------------------------------------------------------------------------

class TestFlagOnFewFilters:
    @pytest.mark.asyncio
    async def test_flag_on_lt_2_filters_no_alternatives(self):
        """Flag ON but only 1 active filter → legacy shape (no alternatives)."""
        executor = _make_executor(
            search_returns=_zero_result(),
            flag_value=True,
            alternatives_result=AlternativesResult(alternatives=[_make_alternative()]),
        )
        session = AsyncMock()
        ctx = ConversationState()

        # Only tipo is set — operacion & moneda are excluded from count
        tc = ToolCall(
            id="toolu_few_01",
            name="search_properties",
            input={"tipo": "casa", "operacion": "venta"},  # tipo=1 active, operacion excluded
        )
        result = await executor.execute(tc, session, search_context=ctx)

        assert "alternatives" not in result
        # AlternativesBuilder.build must NOT be called (short-circuit at filter count)
        executor._alternatives_builder.build.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Flag ON + 2 filters + zero-results + alternatives available
# ---------------------------------------------------------------------------

class TestFlagOnWithHits:
    @pytest.mark.asyncio
    async def test_flag_on_with_2_filters_and_hits(self):
        """Flag ON + >=2 active filters + zero-results + builder returns alts → new shape."""
        alt = _make_alternative()
        executor = _make_executor(
            search_returns=_zero_result(),
            flag_value=True,
            alternatives_result=AlternativesResult(alternatives=[alt]),
        )
        session = AsyncMock()
        ctx = ConversationState()

        result = await executor.execute(
            _make_tool_call(), session, search_context=ctx,
        )

        assert result["total_found"] == 0
        assert result["properties"] == []
        assert result["all_ids"] == []
        assert "alternatives" in result
        assert len(result["alternatives"]) == 1
        assert result["alternatives"][0]["id"] == alt.id

    @pytest.mark.asyncio
    async def test_flag_on_non_zero_results_no_alternatives_called(self):
        """When results ARE found, AlternativesBuilder is never called."""
        executor = _make_executor(
            search_returns=_nonzero_result(3),
            flag_value=True,
        )
        session = AsyncMock()
        ctx = ConversationState()

        result = await executor.execute(
            _make_tool_call(), session, search_context=ctx,
        )

        assert result["total_found"] == 3
        assert "alternatives" not in result
        executor._alternatives_builder.build.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Flag ON + alternatives → state.pending_alternatives populated
# ---------------------------------------------------------------------------

class TestFlagOnPersistsToState:
    @pytest.mark.asyncio
    async def test_flag_on_with_alternatives_persists_to_state(self):
        """After call, search_context.pending_alternatives is populated and age==0."""
        alt = _make_alternative()
        executor = _make_executor(
            search_returns=_zero_result(),
            flag_value=True,
            alternatives_result=AlternativesResult(alternatives=[alt]),
        )
        session = AsyncMock()
        ctx = ConversationState()
        # Simulate stale age from previous turn
        ctx.pending_alternatives_age = 1

        await executor.execute(_make_tool_call(), session, search_context=ctx)

        assert len(ctx.pending_alternatives) == 1
        assert ctx.pending_alternatives[0]["id"] == alt.id
        assert ctx.pending_alternatives_age == 0


# ---------------------------------------------------------------------------
# 5. Flag ON but builder returns empty list → fall back to legacy
# ---------------------------------------------------------------------------

class TestFlagOnNoAlternativesFallsBack:
    @pytest.mark.asyncio
    async def test_flag_on_no_alternatives_falls_back(self):
        """Flag ON + 2 filters + builder returns [] → legacy fallback shape."""
        executor = _make_executor(
            search_returns=_zero_result(),
            flag_value=True,
            alternatives_result=AlternativesResult(alternatives=[]),
        )
        session = AsyncMock()
        ctx = ConversationState()

        result = await executor.execute(
            _make_tool_call(), session, search_context=ctx,
        )

        # Must fall through to legacy shape (no alternatives key)
        assert "alternatives" not in result
        assert result["total_found"] == 0
