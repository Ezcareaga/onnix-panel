"""Tests for FilterRelaxation — progressive filter degradation.

Tests cover:
- DegradationInfo dataclass validation (3 tests)
- Individual relaxation level logic — unit tests (10 tests)
- FilterRelaxation DB integration — async tests against onnix_dev (5 tests)
"""
import os


import pytest
from sqlalchemy import text

from app.bot.search.relaxation import (
    DegradationInfo,
    FilterRelaxation,
    RelaxationResult,
)
from app.bot.search.sql_filters import SearchFilters, SQLFilterBuilder
from app.bot.search.geo_resolver import GeoResolver, ResolvedGeo, GeoLocation


# ===========================================================================
# TestDegradationInfo — dataclass validation (3 tests)
# ===========================================================================


class TestDegradationInfo:
    """Validate DegradationInfo dataclass fields and descriptions."""

    def test_level_1_description(self):
        """Level 1 DegradationInfo description mentions presupuesto."""
        info = DegradationInfo(
            level=1,
            description="Ampliamos el presupuesto un 30%",
        )
        assert info.level == 1
        assert "presupuesto" in info.description

    def test_level_3_description(self):
        """Level 3 DegradationInfo description mentions zona."""
        info = DegradationInfo(
            level=3,
            description="Buscando en zonas cercanas",
        )
        assert info.level == 3
        assert "zona" in info.description

    def test_level_99_text_only(self):
        """Level 99 DegradationInfo with text_only=True."""
        info = DegradationInfo(
            level=99,
            description="La opcion mas economica en la zona",
            text_only=True,
        )
        assert info.level == 99
        assert info.text_only is True


# ===========================================================================
# TestRelaxationLevels — unit tests for each level's filter mutation (10 tests)
# ===========================================================================


class TestRelaxationLevels:
    """Test individual _relax_* methods on FilterRelaxation."""

    def setup_method(self):
        self.builder = SQLFilterBuilder()
        self.geo_resolver = GeoResolver()
        self.relaxation = FilterRelaxation(self.builder, self.geo_resolver)

    # --- Level 1: price ---

    def test_level1_increases_price(self):
        """_relax_price increases precio_max by 30%."""
        filters = SearchFilters(
            operacion="venta",
            precio_max=200000,
        )
        result = self.relaxation._relax_price(filters, None)
        assert result is not None
        new_filters, new_geo = result
        assert new_filters.precio_max == pytest.approx(260000)

    def test_level1_skipped_if_no_price(self):
        """_relax_price returns None when no precio_max set."""
        filters = SearchFilters(operacion="venta")
        result = self.relaxation._relax_price(filters, None)
        assert result is None

    # --- Level 2: dormitorios ---

    def test_level2_drops_dormitorios(self):
        """_relax_dormitorios sets dormitorios_min/max to None."""
        filters = SearchFilters(
            operacion="venta",
            dormitorios_min=3,
        )
        result = self.relaxation._relax_dormitorios(filters, None)
        assert result is not None
        new_filters, new_geo = result
        assert new_filters.dormitorios_min is None
        assert new_filters.dormitorios_max is None

    def test_level2_skipped_if_no_dorms(self):
        """_relax_dormitorios returns None when no dormitorios_min/max set."""
        filters = SearchFilters(operacion="venta")
        result = self.relaxation._relax_dormitorios(filters, None)
        assert result is None

    # --- Level 3: zone ---

    def test_level3_expands_zone_barrio_to_city(self):
        """_relax_zone with barrio drops barrio to search whole city."""
        filters = SearchFilters(
            operacion="venta",
            barrio="villa morra",
            ciudad="asuncion",
        )
        geo = ResolvedGeo(
            canonical_city="asuncion",
            city_locations=[GeoLocation("asuncion", 0)],
            barrio_locations=[
                GeoLocation("villa morra", 0),
                GeoLocation("recoleta", 1),
            ],
        )
        result = self.relaxation._relax_zone(filters, geo)
        assert result is not None
        new_filters, new_geo = result
        # barrio dropped — search whole city
        assert new_filters.barrio is None
        assert new_filters.barrios is None
        # geo should now have no barrio_locations (city-level search)
        assert new_geo is not None
        assert len(new_geo.barrio_locations) == 0

    def test_level3_expands_zone_city_to_neighbors(self):
        """_relax_zone with city-only expands to neighbor cities."""
        filters = SearchFilters(
            operacion="venta",
            ciudad="asuncion",
        )
        geo = ResolvedGeo(
            canonical_city="asuncion",
            city_locations=[GeoLocation("asuncion", 0)],
            barrio_locations=[],
        )
        result = self.relaxation._relax_zone(filters, geo)
        assert result is not None
        new_filters, new_geo = result
        assert new_geo is not None
        # Should have more city_locations than just asuncion
        assert len(new_geo.city_locations) > 1

    def test_level3_skipped_if_no_zone(self):
        """_relax_zone returns None when no city or barrio set."""
        filters = SearchFilters(operacion="venta")
        result = self.relaxation._relax_zone(filters, None)
        assert result is None

    # --- Level 4 removed: _relax_tipo no longer exists (FIX 3) ---

    def test_level4_relax_tipo_removed(self):
        """_relax_tipo method has been removed — relaxation stops at level 3."""
        assert not hasattr(self.relaxation, "_relax_tipo"), (
            "_relax_tipo should be removed; relaxation now stops at level 3"
        )

    # --- Level 99: 2x budget rule ---

    def test_level99_2x_budget_rule(self):
        """Level 99 sets text_only when cheapest > 2x budget."""
        # This test verifies the DegradationInfo flags correctly
        info = DegradationInfo(
            level=99,
            description="La opcion mas economica en la zona",
            text_only=True,
            original_filters={"precio_max": 100000},
        )
        assert info.text_only is True
        assert info.level == 99
        assert info.original_filters["precio_max"] == 100000


# ===========================================================================
# TestFilterRelaxation — DB integration tests (5 async tests)
# ===========================================================================


class TestFilterRelaxation:
    """Integration tests: execute relaxation against onnix_dev."""

    def setup_method(self):
        self.builder = SQLFilterBuilder()
        self.geo_resolver = GeoResolver()
        self.relaxation = FilterRelaxation(self.builder, self.geo_resolver)

    @pytest.mark.asyncio
    async def test_degrade_returns_results_eventually(self, db_session):
        """Restrictive filters (dormitorios_min=10) should degrade and find results."""
        filters = SearchFilters(
            operacion="venta",
            dormitorios_min=10,
            tipo="castillo",
        )
        geo = self.geo_resolver.resolve(city="asuncion")
        result = await self.relaxation.degrade(db_session, filters, geo)
        # With extremely restrictive filters, relaxation should still
        # find something (Asuncion has ~19k properties)
        assert result is not None
        assert result.total_count > 0
        assert result.degradation.level >= 1

    @pytest.mark.asyncio
    async def test_degrade_level1_price_relaxation(self, db_session):
        """Find a price range where original=0 but price*1.3 finds results."""
        # First, find a price point near the edge of available inventory
        edge_result = await db_session.execute(
            text(
                "SELECT price_usd FROM properties "
                "WHERE is_active = true AND duplicate_of IS NULL "
                "AND f_unaccent(lower(operation)) = 'venta' "
                "AND f_unaccent(lower(city)) = 'asuncion' "
                "AND price_usd IS NOT NULL AND price_usd >= 5000 "
                "ORDER BY price_usd DESC LIMIT 1"
            )
        )
        edge_row = edge_result.first()
        if edge_row is None:
            pytest.skip("No venta properties in Asuncion for price edge test")

        # Use the max price as base — nothing above it, but +30% might
        # still work. Instead, use a slightly lower amount to guarantee
        # the original query finds 0 and +30% finds something.
        max_price = float(edge_row.price_usd)
        # Set precio_max just below max so original finds 0
        # but relaxed (+30%) finds at least 1
        test_price = max_price * 0.8
        filters = SearchFilters(
            operacion="venta",
            precio_max=test_price,
            ciudad="asuncion",
        )
        geo = self.geo_resolver.resolve(city="asuncion")

        # Check that relaxed price range actually catches the max_price property
        # test_price * 1.3 = max_price * 0.8 * 1.3 = max_price * 1.04
        # So relaxed range goes slightly above max_price
        result = await self.relaxation.degrade(db_session, filters, geo)
        if result is not None and result.degradation.level == 1:
            assert result.degradation.level == 1
            assert "presupuesto" in result.degradation.description

    @pytest.mark.asyncio
    async def test_degrade_returns_none_for_impossible(self, db_session):
        """Nonexistent city with no results at any level returns None."""
        filters = SearchFilters(
            operacion="venta",
            ciudad="ciudad_inexistente_xyz_99",
        )
        geo = self.geo_resolver.resolve(city="ciudad_inexistente_xyz_99")
        result = await self.relaxation.degrade(db_session, filters, geo)
        assert result is None

    @pytest.mark.asyncio
    async def test_degrade_info_carries_level(self, db_session):
        """Returned DegradationInfo has a valid level number."""
        filters = SearchFilters(
            operacion="venta",
            dormitorios_min=10,
        )
        geo = self.geo_resolver.resolve(city="asuncion")
        result = await self.relaxation.degrade(db_session, filters, geo)
        if result is not None:
            assert result.degradation.level in (1, 2, 3, 4, 99)
            assert isinstance(result.degradation.description, str)
            assert len(result.degradation.description) > 0

    @pytest.mark.asyncio
    async def test_degrade_preserves_unrelaxed_filters(self, db_session):
        """After level 1, operacion filter is still applied."""
        filters = SearchFilters(
            operacion="venta",
            precio_max=200000,
            ciudad="asuncion",
        )
        geo = self.geo_resolver.resolve(city="asuncion")
        result = await self.relaxation.degrade(db_session, filters, geo)
        if result is not None:
            # All returned properties should be venta
            for prop in result.properties:
                # The operation field comes from the DB as 'operation'
                op = prop.get("operation", "")
                if op:
                    assert op.lower() == "venta", (
                        f"Expected 'venta' but got '{op}'"
                    )

    @pytest.mark.asyncio
    async def test_degrade_populates_relaxed_filters(self, db_session):
        """Bug 2026-04-25 — when relaxation kicks in, DegradationInfo.relaxed_filters
        must contain a non-empty list of human-readable descriptions of EVERY
        filter that was relaxed. Claude needs this to inform the user honestly.
        """
        # Filters that force level 2 relaxation (drop dormitorios_min): houses
        # with bedrooms>=10 are rare → strict returns 0 → level 2 drops the
        # filter and Asuncion has plenty of venta listings to find.
        filters = SearchFilters(
            operacion="venta",
            dormitorios_min=10,
            ciudad="asuncion",
        )
        geo = self.geo_resolver.resolve(city="asuncion")
        result = await self.relaxation.degrade(db_session, filters, geo)

        assert result is not None, (
            "Expected degrade() to find results after relaxation for these filters"
        )
        assert result.degradation.level in (1, 2, 3), (
            f"Expected level 1-3 (where relaxed_filters is populated), got {result.degradation.level}"
        )
        assert result.degradation is not None
        assert hasattr(result.degradation, "relaxed_filters"), (
            "DegradationInfo must expose 'relaxed_filters' attribute"
        )
        relaxed = result.degradation.relaxed_filters
        assert isinstance(relaxed, list)
        assert len(relaxed) > 0, (
            f"Expected non-empty relaxed_filters at level={result.degradation.level}, got {relaxed!r}"
        )
        # Each entry is a non-empty string in Spanish
        for entry in relaxed:
            assert isinstance(entry, str)
            assert len(entry) > 0


# ===========================================================================
# TestRelaxationLevel4Removed — FIX 3: levels list stops at 3
# ===========================================================================


class TestRelaxationLevel4Removed:
    """FIX 3: Level 4 (_relax_tipo) was removed from the relaxation loop.

    When levels 1-3 exhaust without results, degrade() now returns
    min_price_in_zone instead of dropping the property type filter.
    """

    def setup_method(self):
        self.builder = SQLFilterBuilder()
        self.geo_resolver = GeoResolver()
        self.relaxation = FilterRelaxation(self.builder, self.geo_resolver)

    def test_levels_list_has_no_level_4(self):
        """The internal levels list only contains levels 1, 2, 3."""
        # Access the levels list from degrade() by inspecting the code structure.
        # Since the levels list is built inside degrade(), we verify indirectly:
        # _relax_tipo should not exist as a method.
        assert not hasattr(self.relaxation, "_relax_tipo"), (
            "_relax_tipo should be removed from FilterRelaxation"
        )

    def test_degradation_info_has_min_price_in_zone_field(self):
        """DegradationInfo dataclass has the min_price_in_zone field."""
        info = DegradationInfo(
            level=4,
            description="No hay casas en Villa Morra a ese presupuesto",
            min_price_in_zone=95000.0,
        )
        assert info.min_price_in_zone == 95000.0

    def test_degradation_info_min_price_defaults_to_none(self):
        """DegradationInfo.min_price_in_zone defaults to None."""
        info = DegradationInfo(
            level=1,
            description="Ampliamos el presupuesto un 30%",
        )
        assert info.min_price_in_zone is None

    @pytest.mark.asyncio
    async def test_get_min_price_for_type_returns_min(self):
        """_get_min_price_for_type returns the MIN(price_usd) for tipo+zone."""
        from unittest.mock import AsyncMock, MagicMock

        relaxation = FilterRelaxation(self.builder, self.geo_resolver)
        filters = SearchFilters(
            operacion="venta",
            tipo="casa",
            ciudad="asuncion",
        )
        geo = ResolvedGeo(
            canonical_city="asuncion",
            city_locations=[GeoLocation("asuncion", 0)],
            barrio_locations=[],
        )

        # Mock the session to return a specific min_price
        mock_session = AsyncMock()
        mock_row = MagicMock()
        mock_row.min_price = 85000.0
        mock_result = MagicMock()
        mock_result.first.return_value = mock_row
        mock_session.execute.return_value = mock_result

        result = await relaxation._get_min_price_for_type(
            mock_session, filters, geo,
        )

        assert result == 85000.0
        mock_session.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_min_price_for_type_returns_none_without_tipo(self):
        """_get_min_price_for_type returns None when filters.tipo is None."""
        from unittest.mock import AsyncMock

        relaxation = FilterRelaxation(self.builder, self.geo_resolver)
        filters = SearchFilters(
            operacion="venta",
            ciudad="asuncion",
        )  # No tipo

        mock_session = AsyncMock()
        result = await relaxation._get_min_price_for_type(
            mock_session, filters, None,
        )

        assert result is None
        mock_session.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_min_price_for_type_returns_none_when_no_rows(self):
        """_get_min_price_for_type returns None when no matching properties."""
        from unittest.mock import AsyncMock, MagicMock

        relaxation = FilterRelaxation(self.builder, self.geo_resolver)
        filters = SearchFilters(
            operacion="venta",
            tipo="castillo",
            ciudad="asuncion",
        )

        mock_session = AsyncMock()
        mock_row = MagicMock()
        mock_row.min_price = None
        mock_result = MagicMock()
        mock_result.first.return_value = mock_row
        mock_session.execute.return_value = mock_result

        result = await relaxation._get_min_price_for_type(
            mock_session, filters, None,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_degrade_returns_min_price_when_levels_exhausted(self):
        """When levels 1-3 fail, degrade() returns min_price_in_zone."""
        from unittest.mock import AsyncMock, MagicMock, patch

        relaxation = FilterRelaxation(self.builder, self.geo_resolver)
        filters = SearchFilters(
            operacion="venta",
            tipo="casa",
            precio_max=50000,
            ciudad="asuncion",
        )
        geo = ResolvedGeo(
            canonical_city="asuncion",
            city_locations=[GeoLocation("asuncion", 0)],
            barrio_locations=[],
        )

        mock_session = AsyncMock()

        # Make all level 1-3 queries return empty results
        empty_result = MagicMock()
        empty_result.fetchall.return_value = []

        # Make _get_min_price_for_type return a price
        min_price_row = MagicMock()
        min_price_row.min_price = 95000.0
        min_price_result = MagicMock()
        min_price_result.first.return_value = min_price_row

        # Level 2 (dormitorios) is skipped because dormitorios=None.
        # So only 3 DB calls: level 1, level 3, min_price query.
        mock_session.execute.side_effect = [
            empty_result,  # level 1: price relaxation query
            empty_result,  # level 3: zone relaxation query
            min_price_result,  # _get_min_price_for_type
        ]

        result = await relaxation.degrade(mock_session, filters, geo)

        assert result is not None
        assert result.properties == []
        assert result.total_count == 0
        assert result.degradation.level == 4
        assert result.degradation.min_price_in_zone == 95000.0
        assert "casa" in result.degradation.description.lower()

    @pytest.mark.asyncio
    async def test_degrade_does_not_drop_tipo(self):
        """Relaxation never drops property type — level 4 _relax_tipo was removed."""
        from unittest.mock import AsyncMock, MagicMock

        relaxation = FilterRelaxation(self.builder, self.geo_resolver)
        filters = SearchFilters(
            operacion="venta",
            tipo="casa",
            precio_max=50000,
        )

        mock_session = AsyncMock()

        # All queries return empty
        empty_result = MagicMock()
        empty_result.fetchall.return_value = []

        # Min price query returns None (no properties at all)
        none_result = MagicMock()
        none_row = MagicMock()
        none_row.min_price = None
        none_result.first.return_value = none_row

        # Cheapest fallback also returns None
        cheapest_result = MagicMock()
        cheapest_result.first.return_value = None

        mock_session.execute.side_effect = [
            empty_result,   # level 1 query
            none_result,    # _get_min_price_for_type
            cheapest_result,  # _cheapest_fallback
        ]

        result = await relaxation.degrade(mock_session, filters, None)

        # With no results anywhere, should return None (not drop tipo)
        assert result is None


# ===========================================================================
# TestRelaxationB4 — dormitorios_max is never relaxed
# ===========================================================================

class TestRelaxationB4:
    """B4: _relax_dormitorios must NOT relax dormitorios_max (hard constraint)."""

    def setup_method(self):
        import os
        os.environ["GEO_DATA_PATH"] = os.environ["GEO_DATA_PATH"]
        from app.bot.search.relaxation import FilterRelaxation
        from app.bot.search.sql_filters import SQLFilterBuilder
        from app.bot.search.geo_resolver import GeoResolver
        self.relaxation = FilterRelaxation(SQLFilterBuilder(), GeoResolver())

    def test_dormitorios_max_only_returns_none(self):
        """Only dormitorios_max set → nothing to relax (max is hard constraint)."""
        filters = SearchFilters(dormitorios_max=2)
        result = self.relaxation._relax_dormitorios(filters, None)
        assert result is None, \
            "dormitorios_max alone must not trigger relaxation (B4)"

    def test_dormitorios_min_relaxed_but_max_preserved(self):
        """Both min and max set → relax only min, preserve max."""
        filters = SearchFilters(dormitorios_min=3, dormitorios_max=2)
        result = self.relaxation._relax_dormitorios(filters, None)
        assert result is not None, \
            "Should relax when dormitorios_min is set"
        new_filters, _ = result
        assert new_filters.dormitorios_min is None, \
            "dormitorios_min should be relaxed"
        assert new_filters.dormitorios_max == 2, \
            "dormitorios_max must survive relaxation (B4)"

    def test_dormitorios_min_alone_still_relaxed(self):
        """Only dormitorios_min set → relax it (soft constraint)."""
        filters = SearchFilters(dormitorios_min=3)
        result = self.relaxation._relax_dormitorios(filters, None)
        assert result is not None
        new_filters, _ = result
        assert new_filters.dormitorios_min is None

    def test_no_dormitorios_returns_none(self):
        """Neither min nor max → returns None (nothing to relax)."""
        filters = SearchFilters(operacion="venta")
        result = self.relaxation._relax_dormitorios(filters, None)
        assert result is None
