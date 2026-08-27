"""Tests for handlers._utils.no_results_text helper (M4 Task 3.6).

Verifies the no-results UX improvement: when a budget filter (precio_max) was
applied and the relaxation pipeline found a minimum available price in zone,
the message tells the user that price and invites them to search from it.

Covers:
- Min price present + GS budget → shows price in USD (min_price_in_zone is always USD)
- Min price present + USD budget → shows "USD X.XXX"
- No degradation (degradation=None) → generic message
- Min price present but no budget filter → generic message (budget not the constraint)
- No degradation and no budget → generic message
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bot.core.orchestrator import Orchestrator
from app.bot.handlers._utils import no_results_text
from app.bot.search.relaxation import DegradationInfo
from app.bot.search.search_service import SearchResult
from app.bot.search.sql_filters import SearchFilters


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_orchestrator() -> Orchestrator:
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

    return Orchestrator(
        claude=claude,
        gemini=gemini,
        circuit_breaker=circuit_breaker,
        search_service=search_service,
        conversation_manager=conversation_manager,
        response_builder=response_builder,
        tool_executor=tool_executor,
    )


def _degradation_with_min_price(min_price: float) -> DegradationInfo:
    """Build a DegradationInfo at level=4 with min_price_in_zone set."""
    return DegradationInfo(
        level=4,
        description="No hay propiedades a ese presupuesto",
        min_price_in_zone=min_price,
    )


def _result_with_min_price(min_price: float) -> SearchResult:
    """SearchResult with no properties and degradation carrying a min price."""
    return SearchResult(
        properties=[],
        total_found=0,
        degradation=_degradation_with_min_price(min_price),
    )


def _result_no_degradation() -> SearchResult:
    """SearchResult with no properties and no degradation info."""
    return SearchResult(properties=[], total_found=0, degradation=None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNoResultsMinPrice:
    """Orchestrator._no_results_text builds contextual no-results messages."""

    def test_no_results_with_min_price_gs(self):
        """When moneda='gs' and min_price exists, message shows price in USD.

        min_price_in_zone is always USD (queries MIN(price_usd)), so the
        displayed price is always in USD regardless of the user's moneda.
        """
        orch = _make_orchestrator()
        result = _result_with_min_price(50000.0)
        filters = SearchFilters(
            precio_max=40000.0,
            moneda="gs",
            tipo="departamento",
            ciudad="Asuncion",
        )

        text = no_results_text(result, filters)

        # Must mention the minimum price in USD format
        assert "50.000" in text or "50000" in text or "USD" in text
        # Must invite to search from that price
        assert "precio" in text.lower() or "opciones" in text.lower()
        # Must not be the generic fallback
        assert "disponibles en este momento" not in text

    def test_no_results_with_min_price_usd(self):
        """When moneda='usd' and min_price=80000, message contains 'USD 80.000' with tipo and zona."""
        orch = _make_orchestrator()
        result = _result_with_min_price(80000.0)
        filters = SearchFilters(
            precio_max=50000.0,
            moneda="usd",
            tipo="casa",
            ciudad="Luque",
        )

        text = no_results_text(result, filters)

        assert "USD 80.000" in text
        assert "Luque" in text
        assert "¿Querés que busque desde ese precio?" in text

    def test_no_results_no_min_price(self):
        """When degradation is None, the generic fallback message mentions tipo and adjustment."""
        orch = _make_orchestrator()
        result = _result_no_degradation()
        filters = SearchFilters(precio_max=50000.0, moneda="usd")

        text = no_results_text(result, filters)

        # Generic fallback: mentions tipo (propiedad) and invites filter adjustment
        assert "propiedad" in text.lower()
        assert "ajust" in text.lower() or "zona" in text.lower() or "presupuesto" in text.lower()
        # Must not use old "en este momento" phrasing
        assert "en este momento" not in text

    def test_no_results_no_budget_filter(self):
        """When precio_max is None but min_price exists (level=4), offers nearby zones.

        Budget was NOT the constraint (precio_max=None), so we don't show the price.
        Level 4 >= 3, so we offer nearby zone search instead.
        """
        orch = _make_orchestrator()
        result = _result_with_min_price(50000.0)
        filters = SearchFilters(precio_max=None, moneda="usd", tipo="casa")

        text = no_results_text(result, filters)

        # Level 4 >= 3, no budget constraint → nearby zones offer
        assert "casa" in text.lower()
        assert "cerc" in text.lower() or "zona" in text.lower() or "filtro" in text.lower()

    def test_no_results_empty_zone(self):
        """When degradation=None and precio_max=None, generic fallback mentions tipo and adjustment."""
        orch = _make_orchestrator()
        result = _result_no_degradation()
        filters = SearchFilters(precio_max=None, moneda="usd")

        text = no_results_text(result, filters)

        # No degradation → generic fallback: tipo='propiedad', invites adjustment
        assert "propiedad" in text.lower()
        assert "ajust" in text.lower() or "zona" in text.lower() or "presupuesto" in text.lower()
        assert "en este momento" not in text

    def test_no_results_uses_fallback_zona_label(self):
        """When barrio and ciudad are both None, the zona label is 'esa zona'."""
        orch = _make_orchestrator()
        result = _result_with_min_price(30000.0)
        filters = SearchFilters(
            precio_max=20000.0,
            moneda="usd",
            tipo="terreno",
            # No barrio, no ciudad
        )

        text = no_results_text(result, filters)

        assert "esa zona" in text
        assert "terrenos" in text

    def test_no_results_uses_barrio_over_ciudad(self):
        """When both barrio and ciudad are set, barrio is used in the label."""
        orch = _make_orchestrator()
        result = _result_with_min_price(45000.0)
        filters = SearchFilters(
            precio_max=30000.0,
            moneda="usd",
            tipo="departamento",
            ciudad="Asuncion",
            barrio="Villa Morra",
        )

        text = no_results_text(result, filters)

        assert "Villa Morra" in text
        # ciudad should not appear since barrio takes precedence
        assert "Asuncion" not in text

    def test_price_formatted_with_dots_not_commas(self):
        """Prices use dots as thousands separator (Spanish/Paraguayan convention)."""
        orch = _make_orchestrator()
        result = _result_with_min_price(120000.0)
        filters = SearchFilters(precio_max=80000.0, moneda="usd")

        text = no_results_text(result, filters)

        # Should use "120.000" not "120,000"
        assert "120.000" in text
        assert "120,000" not in text

    def test_degradation_min_price_none_with_budget(self):
        """When degradation level=1 and min_price_in_zone is None, generic fallback is returned.

        Level 1 < 3, so the nearby-zones branch is NOT triggered.
        No min_price → first branch skipped.
        Falls through to generic fallback.
        """
        orch = _make_orchestrator()
        # DegradationInfo at level 1 (price relaxation) — min_price_in_zone not set
        degradation = DegradationInfo(
            level=1,
            description="Ampliamos el presupuesto un 30%",
            min_price_in_zone=None,
        )
        result = SearchResult(properties=[], total_found=0, degradation=degradation)
        filters = SearchFilters(precio_max=50000.0, moneda="usd")

        text = no_results_text(result, filters)

        # Level 1 < 3 → generic fallback (tipo='propiedad', invites adjustment)
        assert "propiedad" in text.lower()
        assert "ajust" in text.lower() or "zona" in text.lower() or "presupuesto" in text.lower()
        assert "en este momento" not in text
