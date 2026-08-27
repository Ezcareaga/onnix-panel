"""Tests for AlternativesBuilder — zero-results alternatives service.

Tests cover:
- Activation rules: less than 2 active filters → empty result (3 tests)
- Zona vecina (barrio expansion): neighbors with count >= 3 included (3 tests)
- Zona vecina (city expansion): city neighbors when no barrio (2 tests)
- Presupuesto relajado: +20% hit, +30% fallback, skip when no precio_max (3 tests)
- Tipo similar: mapping hit, no-mapping skip (2 tests)
- Cap / priority / payload constraints (3 tests)
- Sanity: returned counts are real and positive (1 test)
"""
import os


import pytest

from app.bot.search.alternatives import AlternativesBuilder, AlternativesResult
from app.bot.search.geo_resolver import GeoResolver
from app.bot.search.search_service import SearchService
from app.bot.search.sql_filters import SearchFilters

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Re-export db_session from bot conftest — it is in the conftest for bot/
# No import needed; pytest collects conftest.py automatically.


@pytest.fixture(scope="module")
def geo() -> GeoResolver:
    """Real GeoResolver loaded from data/geografia."""
    return GeoResolver()


@pytest.fixture(scope="module")
def search_service() -> SearchService:
    """SearchService without vector search (no Gemini needed for count tests)."""
    return SearchService(gemini_client=None)


@pytest.fixture(scope="module")
def builder(search_service: SearchService, geo: GeoResolver) -> AlternativesBuilder:
    """AlternativesBuilder wired to real SearchService and GeoResolver."""
    return AlternativesBuilder(search_service, geo)


# ---------------------------------------------------------------------------
# TestActivationRules — empty result when < 2 active filters
# ---------------------------------------------------------------------------


class TestActivationRules:
    """AlternativesBuilder returns [] when activation threshold not met."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_lt_2_active_filters(
        self, builder: AlternativesBuilder, db_session, geo: GeoResolver
    ):
        """Only tipo active (1 filter) → returns empty alternatives."""
        filters = SearchFilters(tipo="departamento")
        resolved = geo.resolve(city=None, barrios=None)
        result = await builder.build(db_session, filters, resolved)
        assert isinstance(result, AlternativesResult)
        assert result.alternatives == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_filters(
        self, builder: AlternativesBuilder, db_session, geo: GeoResolver
    ):
        """No filters at all → returns empty alternatives."""
        filters = SearchFilters()
        resolved = geo.resolve(city=None, barrios=None)
        result = await builder.build(db_session, filters, resolved)
        assert result.alternatives == []

    @pytest.mark.asyncio
    async def test_excludes_operacion_moneda_from_filter_count(
        self, builder: AlternativesBuilder, db_session, geo: GeoResolver
    ):
        """operacion + moneda do not count toward active filter threshold."""
        filters = SearchFilters(operacion="venta", moneda="usd")
        resolved = geo.resolve(city=None, barrios=None)
        result = await builder.build(db_session, filters, resolved)
        assert result.alternatives == []


# ---------------------------------------------------------------------------
# TestZonaVecinaBarrio — barrio-level neighbor expansion
# ---------------------------------------------------------------------------


class TestZonaVecinaBarrio:
    """Zona vecina alternatives when a barrio is present in filters."""

    @pytest.mark.asyncio
    async def test_zona_vecina_barrio_returns_neighbors(
        self, builder: AlternativesBuilder, db_session, geo: GeoResolver
    ):
        """Villa Morra + departamento → returns neighbor barrios with count >= 3."""
        filters = SearchFilters(
            tipo="departamento",
            ciudad="asuncion",
            barrio="villa morra",
        )
        # Villa Morra has neighbors: bella vista, recoleta, etc.
        resolved = geo.resolve(city="asuncion", barrios=["villa morra"])
        result = await builder.build(db_session, filters, resolved)
        # Should produce some zona vecina alternatives (Recoleta/Bella Vista have 200+ deptos)
        zona_alts = [a for a in result.alternatives if a.id.startswith("zona_vecina:")]
        assert len(zona_alts) >= 1
        for alt in zona_alts:
            assert alt.count >= 3
            assert "zona vecina" in alt.reason

    @pytest.mark.asyncio
    async def test_zona_vecina_filters_out_low_counts(
        self, builder: AlternativesBuilder, db_session, geo: GeoResolver
    ):
        """Neighbor barrio 'bella vista' (1 depto <= 35000 USD) must not appear in alternatives.

        Data: Recoleta depto precio_max=35000 → 0 results.
        Bella Vista (neighbor of Recoleta) has exactly 1 depto at that price — below _MIN_COUNT=3.
        The alternative for bella vista must be absent from the result.
        """
        filters = SearchFilters(
            tipo="departamento",
            ciudad="asuncion",
            barrio="recoleta",
            precio_max=35000.0,
        )
        resolved = geo.resolve(city="asuncion", barrios=["recoleta"])
        result = await builder.build(db_session, filters, resolved)
        zona_ids = [a.id for a in result.alternatives if a.id.startswith("zona_vecina:")]
        assert "zona_vecina:bella vista" not in zona_ids, (
            "bella vista has only 1 depto at this price — should be filtered out (< _MIN_COUNT=3)"
        )

    @pytest.mark.asyncio
    async def test_zona_vecina_max_two_barrio_alternatives(
        self, builder: AlternativesBuilder, db_session, geo: GeoResolver
    ):
        """At most 2 zona_vecina alternatives are returned for barrio expansion."""
        filters = SearchFilters(
            tipo="departamento",
            ciudad="asuncion",
            barrio="villa morra",
        )
        resolved = geo.resolve(city="asuncion", barrios=["villa morra"])
        result = await builder.build(db_session, filters, resolved)
        zona_alts = [a for a in result.alternatives if a.id.startswith("zona_vecina:")]
        assert len(zona_alts) <= 2


# ---------------------------------------------------------------------------
# TestZonaVecinaCity — city-level neighbor expansion
# ---------------------------------------------------------------------------


class TestZonaVecinaCity:
    """Zona vecina alternatives when no barrio but ciudad is present."""

    @pytest.mark.asyncio
    async def test_zona_vecina_city_expansion_when_no_barrio(
        self, builder: AlternativesBuilder, db_session, geo: GeoResolver
    ):
        """ciudad=asuncion without barrio → returns neighboring cities with count >= 3."""
        filters = SearchFilters(
            tipo="departamento",
            ciudad="asuncion",
            precio_max=500000.0,  # permissive — all neighbors should have results
        )
        resolved = geo.resolve(city="asuncion", barrios=None)
        result = await builder.build(db_session, filters, resolved)
        # Asuncion neighbors: lambare, luque, fernando de la mora, etc.
        zona_alts = [a for a in result.alternatives if a.id.startswith("zona_vecina:")]
        assert len(zona_alts) >= 1
        for alt in zona_alts:
            assert alt.count >= 3

    @pytest.mark.asyncio
    async def test_zona_vecina_city_no_alternatives_when_missing_geo(
        self, builder: AlternativesBuilder, db_session, geo: GeoResolver
    ):
        """No barrio, no ciudad → zona vecina produces no alternatives."""
        filters = SearchFilters(
            tipo="departamento",
            precio_max=200000.0,
        )
        resolved = geo.resolve(city=None, barrios=None)
        result = await builder.build(db_session, filters, resolved)
        zona_alts = [a for a in result.alternatives if a.id.startswith("zona_vecina:")]
        assert zona_alts == []


# ---------------------------------------------------------------------------
# TestPresupuestoRelajado — budget relaxation
# ---------------------------------------------------------------------------


class TestPresupuestoRelajado:
    """Presupuesto alternatives at +20% or +30%."""

    @pytest.mark.asyncio
    async def test_presupuesto_relajado_20pct_hit(
        self, builder: AlternativesBuilder, db_session, geo: GeoResolver
    ):
        """At +20% budget there are >=3 results → includes presupuesto_20pct.

        Data: operacion=venta, tipo=departamento, ciudad=asuncion (city-wide, no barrio),
        precio_max=35000 → few/zero results at base; at *1.2=42000 → 62 results (2026-06-06).

        WHY city-wide window: the previous single-barrio scenario (recoleta, precio_max=40000,
        ~8 deptos ≤48k) was invalidated by the 2026-06-06 dev refresh from prod (STAB-09) —
        after the refresh Recoleta had only 2 matching deptos ≤48k (below _MIN_COUNT=3), causing
        the builder to correctly fall through to presupuesto_30pct.  A city-wide Asunción window
        (1,382 active non-duplicate deptos venta total; 62 ≤42,000 USD) has ample margin and
        will remain stable across future dev refreshes.
        """
        filters = SearchFilters(
            operacion="venta",
            tipo="departamento",
            ciudad="asuncion",
            precio_max=35000.0,
        )
        resolved = geo.resolve(city="asuncion", barrios=None)
        result = await builder.build(db_session, filters, resolved)
        presup_alts = [a for a in result.alternatives if "presupuesto" in a.id]
        assert len(presup_alts) == 1
        assert presup_alts[0].id == "presupuesto_20pct"
        assert presup_alts[0].count >= 3

    @pytest.mark.asyncio
    async def test_presupuesto_relajado_fallsback_to_30pct(
        self, builder: AlternativesBuilder, db_session, geo: GeoResolver
    ):
        """At +20% budget count < 3 but +30% gives >= 3 → includes presupuesto_30pct.

        Data: operacion=venta, tipo=departamento, ciudad=asuncion, barrio=los laureles,
        precio_max=60000 → 0 results; at *1.2=72000 → 2 results; at *1.3=78000 → 4 results.
        """
        filters = SearchFilters(
            operacion="venta",
            tipo="departamento",
            ciudad="asuncion",
            barrio="los laureles",
            precio_max=60000.0,
        )
        resolved = geo.resolve(city="asuncion", barrios=["los laureles"])
        result = await builder.build(db_session, filters, resolved)
        presup_alts = [a for a in result.alternatives if "presupuesto" in a.id]
        assert len(presup_alts) == 1
        assert presup_alts[0].id == "presupuesto_30pct"
        assert presup_alts[0].count >= 3

    @pytest.mark.asyncio
    async def test_presupuesto_relajado_skipped_when_no_precio_max(
        self, builder: AlternativesBuilder, db_session, geo: GeoResolver
    ):
        """No precio_max in filters → no presupuesto alternative generated."""
        filters = SearchFilters(
            tipo="departamento",
            ciudad="asuncion",
        )
        resolved = geo.resolve(city="asuncion", barrios=None)
        result = await builder.build(db_session, filters, resolved)
        presup_alts = [a for a in result.alternatives if "presupuesto" in a.id]
        assert presup_alts == []


# ---------------------------------------------------------------------------
# TestTipoSimilar — property type mapping alternatives
# ---------------------------------------------------------------------------


class TestTipoSimilar:
    """Tipo similar alternatives using fixed mapping."""

    @pytest.mark.asyncio
    async def test_tipo_similar_casa_duplex(
        self, builder: AlternativesBuilder, db_session, geo: GeoResolver
    ):
        """tipo=casa → proposes tipo_duplex if count >= 3."""
        # Duplex Asuncion: 33 total — well above threshold
        filters = SearchFilters(
            tipo="casa",
            ciudad="asuncion",
        )
        resolved = geo.resolve(city="asuncion", barrios=None)
        result = await builder.build(db_session, filters, resolved)
        tipo_alts = [a for a in result.alternatives if a.id.startswith("tipo_")]
        assert len(tipo_alts) == 1
        assert tipo_alts[0].id == "tipo_duplex"
        assert tipo_alts[0].count >= 3

    @pytest.mark.asyncio
    async def test_tipo_similar_no_mapping(
        self, builder: AlternativesBuilder, db_session, geo: GeoResolver
    ):
        """tipo=terreno has no mapping → no tipo alternative generated."""
        filters = SearchFilters(
            tipo="terreno",
            ciudad="asuncion",
        )
        resolved = geo.resolve(city="asuncion", barrios=None)
        result = await builder.build(db_session, filters, resolved)
        tipo_alts = [a for a in result.alternatives if a.id.startswith("tipo_")]
        assert tipo_alts == []


# ---------------------------------------------------------------------------
# TestCapAndPriority — max 3 alternatives, priority order, payload length
# ---------------------------------------------------------------------------


class TestCapAndPriority:
    """Cap at 3 alternatives, priority order, and payload constraints."""

    @pytest.mark.asyncio
    async def test_max_3_alternatives(
        self, builder: AlternativesBuilder, db_session, geo: GeoResolver
    ):
        """Result never exceeds 3 alternatives regardless of how many pass threshold."""
        # Villa Morra departamento with permissive price — many neighbors pass
        filters = SearchFilters(
            tipo="departamento",
            ciudad="asuncion",
            barrio="villa morra",
            precio_max=500000.0,
        )
        resolved = geo.resolve(city="asuncion", barrios=["villa morra"])
        result = await builder.build(db_session, filters, resolved)
        assert len(result.alternatives) <= 3

    @pytest.mark.asyncio
    async def test_priority_order(
        self, builder: AlternativesBuilder, db_session, geo: GeoResolver
    ):
        """Zona vecina alternatives appear before presupuesto and tipo alternatives."""
        filters = SearchFilters(
            tipo="casa",
            ciudad="asuncion",
            barrio="villa morra",
            precio_max=500000.0,
        )
        resolved = geo.resolve(city="asuncion", barrios=["villa morra"])
        result = await builder.build(db_session, filters, resolved)
        alts = result.alternatives
        if len(alts) < 2:
            pytest.skip("Not enough alternatives to verify ordering")
        # Find positions of each category
        positions: dict[str, list[int]] = {"zona_vecina": [], "presupuesto": [], "tipo": []}
        for i, alt in enumerate(alts):
            if alt.id.startswith("zona_vecina:"):
                positions["zona_vecina"].append(i)
            elif "presupuesto" in alt.id:
                positions["presupuesto"].append(i)
            elif alt.id.startswith("tipo_"):
                positions["tipo"].append(i)
        # If zona_vecina and presupuesto both appear, zona first
        if positions["zona_vecina"] and positions["presupuesto"]:
            assert min(positions["zona_vecina"]) < min(positions["presupuesto"])
        # If presupuesto and tipo both appear, presupuesto first
        if positions["presupuesto"] and positions["tipo"]:
            assert min(positions["presupuesto"]) < min(positions["tipo"])

    @pytest.mark.asyncio
    async def test_callback_payload_leq_50_chars(
        self, builder: AlternativesBuilder, db_session, geo: GeoResolver
    ):
        """Every alternative's callback_payload is at most 50 chars (Twilio limit)."""
        filters = SearchFilters(
            tipo="departamento",
            ciudad="asuncion",
            barrio="villa morra",
            precio_max=500000.0,
        )
        resolved = geo.resolve(city="asuncion", barrios=["villa morra"])
        result = await builder.build(db_session, filters, resolved)
        for alt in result.alternatives:
            assert len(alt.callback_payload) <= 50, (
                f"Payload too long ({len(alt.callback_payload)} chars): {alt.callback_payload}"
            )


# ---------------------------------------------------------------------------
# TestSanity — counts are real and positive
# ---------------------------------------------------------------------------


class TestSanity:
    """Returned counts reflect real DB queries and are positive."""

    @pytest.mark.asyncio
    async def test_alternatives_have_real_counts(
        self, builder: AlternativesBuilder, db_session, geo: GeoResolver
    ):
        """All included alternatives have count > 0 (they passed threshold >= 3)."""
        filters = SearchFilters(
            tipo="departamento",
            ciudad="asuncion",
            barrio="villa morra",
            precio_max=500000.0,
        )
        resolved = geo.resolve(city="asuncion", barrios=["villa morra"])
        result = await builder.build(db_session, filters, resolved)
        for alt in result.alternatives:
            assert alt.count >= 3, f"Alternative {alt.id} has count={alt.count} < 3"
            assert alt.filters  # filters dict is populated
            assert alt.label    # label string is not empty
            assert alt.reason   # reason string is not empty
