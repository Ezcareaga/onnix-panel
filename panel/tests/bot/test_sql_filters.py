"""Tests for SQLFilterBuilder — parameterized SQL query construction.

Tests cover:
- SearchFilters Pydantic model validation (5 tests)
- SQLFilterBuilder unit tests — SQL string + params inspection (12 tests)
- SQLFilterBuilder DB integration tests — execute against onnix_dev (6 tests)
"""
import os


import pytest
import pytest_asyncio
from sqlalchemy import text

from app.bot.search.sql_filters import (
    FilteredQuery,
    SearchFilters,
    SQLFilterBuilder,
)
from app.bot.search.geo_resolver import GeoLocation, ResolvedGeo


# ===========================================================================
# TestSearchFilters — Pydantic model validation (5 tests)
# ===========================================================================


class TestSearchFilters:
    """Validate SearchFilters Pydantic model behavior."""

    def test_empty_filters(self):
        """Empty SearchFilters has all None fields, moneda defaults to 'usd'."""
        f = SearchFilters()
        assert f.operacion is None
        assert f.tipo is None
        assert f.ciudad is None
        assert f.barrio is None
        assert f.barrios is None
        assert f.precio_min is None
        assert f.precio_max is None
        assert f.dormitorios_min is None
        assert f.dormitorios_max is None
        assert f.moneda == "usd"
        assert f.descripcion_libre is None
        assert f.excluded_ids == []
        assert f.pagination_ids == []

    def test_full_filters(self):
        """SearchFilters with all fields populated validates correctly."""
        f = SearchFilters(
            operacion="venta",
            tipo="casa",
            ciudad="asuncion",
            barrio="villa morra",
            barrios=["villa morra", "recoleta"],
            precio_min=50000,
            precio_max=200000,
            dormitorios_min=3,
            moneda="usd",
            descripcion_libre="piscina y jardin",
            excluded_ids=[1, 2, 3],
            pagination_ids=[10, 20],
        )
        assert f.operacion == "venta"
        assert f.tipo == "casa"
        assert f.ciudad == "asuncion"
        assert f.barrio == "villa morra"
        assert f.barrios == ["villa morra", "recoleta"]
        assert f.precio_min == 50000
        assert f.precio_max == 200000
        assert f.dormitorios_min == 3
        assert f.moneda == "usd"
        assert f.descripcion_libre == "piscina y jardin"
        assert f.excluded_ids == [1, 2, 3]
        assert f.pagination_ids == [10, 20]

    def test_moneda_default_usd(self):
        """SearchFilters without moneda defaults to 'usd'."""
        f = SearchFilters(operacion="venta")
        assert f.moneda == "usd"

    def test_price_guardrail_awareness(self):
        """SearchFilters accepts low precio_max — guardrail applied in builder."""
        f = SearchFilters(operacion="venta", precio_max=100)
        assert f.precio_max == 100
        # The builder, not the model, enforces the 5000 USD minimum for venta

    def test_excluded_ids_list(self):
        """SearchFilters stores excluded_ids as a list."""
        f = SearchFilters(excluded_ids=[10, 20, 30])
        assert f.excluded_ids == [10, 20, 30]
        assert isinstance(f.excluded_ids, list)


# ===========================================================================
# TestSQLFilterBuilder — unit tests for query construction (12 tests)
# ===========================================================================


class TestSQLFilterBuilder:
    """Test SQL string and params generation WITHOUT executing queries."""

    def setup_method(self):
        self.builder = SQLFilterBuilder()

    def test_base_where_always_present(self):
        """build_query always includes is_active = true and duplicate_of IS NULL."""
        result = self.builder.build_query(SearchFilters())
        sql_lower = result.sql.lower()
        assert "is_active = true" in sql_lower
        assert "duplicate_of is null" in sql_lower

    def test_operacion_filter(self):
        """Operacion filter uses f_unaccent(lower()) on the operation column."""
        filters = SearchFilters(operacion="venta")
        result = self.builder.build_query(filters)
        sql_lower = result.sql.lower()
        assert "f_unaccent(lower(p.operation))" in sql_lower
        assert ":operacion" in result.sql
        assert result.params["operacion"] == "venta"

    def test_tipo_filter(self):
        """Tipo filter uses property_type_normalized FK with ILIKE fallback for NULLs."""
        filters = SearchFilters(tipo="casa")
        result = self.builder.build_query(filters)
        sql_lower = result.sql.lower()
        # Must use normalized column as primary filter
        assert "property_type_normalized" in sql_lower
        # Must include ILIKE fallback for rows not yet classified (NULL)
        assert "f_unaccent(lower(p.property_type))" in sql_lower
        assert ":tipo" in result.sql
        assert result.params["tipo_id"] == 1
        assert "casa" in result.params["tipo"]

    def test_tipo_departamento_no_overlap_with_oficina(self):
        """tipo='departamento' (FK id=2) cannot match oficina (FK id=5).

        Integer FK comparison guarantees no cross-type contamination.
        Previously the ILIKE pattern '%departamento%' was safe, but now the
        primary filter is an exact integer match which is even more precise.
        """
        filters = SearchFilters(tipo="departamento")
        result = self.builder.build_query(filters)
        # Primary filter is integer FK — departamento=2, oficina=5
        assert result.params.get("tipo_id") == 2
        # ILIKE fallback param scoped to departamento only
        tipo_param = result.params["tipo"]
        assert "departamento" in tipo_param
        assert "oficina" not in tipo_param
        # SQL contains both the normalized FK check and LIKE
        assert "property_type_normalized" in result.sql
        assert "like" in result.sql.lower()

    def test_ciudad_filter_with_geo(self):
        """ResolvedGeo with city_locations produces CTE in SQL."""
        geo = ResolvedGeo(
            canonical_city="asuncion",
            city_locations=[
                GeoLocation("asuncion", 0),
                GeoLocation("luque", 1),
                GeoLocation("lambare", 1),
            ],
            barrio_locations=[],
            landmark=None,
        )
        filters = SearchFilters(operacion="venta")
        result = self.builder.build_query(filters, geo=geo)
        sql_lower = result.sql.lower()
        assert "with target_locations as" in sql_lower
        assert "f_unaccent(lower(p.city))" in sql_lower
        # CTE params for locations
        assert ":loc_0" in result.sql
        assert result.params["loc_0"] == "asuncion"

    def test_barrio_filter_with_geo(self):
        """ResolvedGeo with barrio_locations produces CTE with neighborhood join."""
        geo = ResolvedGeo(
            canonical_city="asuncion",
            city_locations=[GeoLocation("asuncion", 0)],
            barrio_locations=[
                GeoLocation("villa morra", 0),
                GeoLocation("recoleta", 1),
            ],
            landmark=None,
        )
        filters = SearchFilters(operacion="venta")
        result = self.builder.build_query(filters, geo=geo)
        sql_lower = result.sql.lower()
        assert "with target_locations as" in sql_lower
        assert "f_unaccent(lower(p.neighborhood))" in sql_lower
        assert ":loc_0" in result.sql
        assert result.params["loc_0"] == "villa morra"

    def test_precio_max_usd(self):
        """USD price filter uses price_usd column."""
        filters = SearchFilters(precio_max=200000, moneda="usd")
        result = self.builder.build_query(filters)
        sql_lower = result.sql.lower()
        assert "price_usd" in sql_lower
        assert ":precio_max" in result.sql
        assert result.params["precio_max"] == 200000

    def test_precio_pyg(self):
        """PYG price filter uses price_pyg column."""
        filters = SearchFilters(precio_max=500000000, moneda="gs")
        result = self.builder.build_query(filters)
        sql_lower = result.sql.lower()
        assert "price_pyg" in sql_lower
        assert ":precio_max" in result.sql
        assert result.params["precio_max"] == 500000000

    def test_dormitorios_min_generates_gte(self):
        """dormitorios_min uses minimum match: bedrooms >= :dorms_min."""
        filters = SearchFilters(dormitorios_min=3)
        result = self.builder.build_query(filters)
        assert "p.bedrooms >= :dorms_min" in result.sql
        assert result.params["dorms_min"] == 3

    def test_dormitorios_max_generates_lte(self):
        """dormitorios_max uses maximum match: bedrooms <= :dorms_max."""
        filters = SearchFilters(dormitorios_max=2)
        result = self.builder.build_query(filters)
        assert "p.bedrooms <= :dorms_max" in result.sql
        assert result.params["dorms_max"] == 2

    def test_excluded_ids(self):
        """Excluded IDs produce NOT IN clause with numbered params."""
        filters = SearchFilters(excluded_ids=[10, 20])
        result = self.builder.build_query(filters)
        sql_lower = result.sql.lower()
        assert "id not in" in sql_lower
        assert ":ex_0" in result.sql
        assert ":ex_1" in result.sql
        assert result.params["ex_0"] == 10
        assert result.params["ex_1"] == 20

    def test_pagination_mode(self):
        """build_pagination_query returns WHERE id IN clause."""
        result = self.builder.build_pagination_query([1, 2, 3])
        sql_lower = result.sql.lower()
        assert "where" in sql_lower
        assert "id in" in sql_lower.replace("\n", " ")
        assert ":id_0" in result.sql
        assert ":id_1" in result.sql
        assert ":id_2" in result.sql
        assert result.params["id_0"] == 1
        assert result.params["id_1"] == 2
        assert result.params["id_2"] == 3

    def test_price_guardrail_venta_minimum(self):
        """Venta queries enforce price_usd >= 5000 guardrail."""
        filters = SearchFilters(operacion="venta")
        result = self.builder.build_query(filters)
        sql_lower = result.sql.lower()
        assert "price_usd >= 5000" in sql_lower


# ===========================================================================
# TestSQLFilterBuilderDB — integration tests against onnix_dev (6 tests)
# ===========================================================================


class TestSQLFilterBuilderDB:
    """Execute generated SQL against onnix_dev and verify results."""

    def setup_method(self):
        self.builder = SQLFilterBuilder()

    @pytest.mark.asyncio
    async def test_query_returns_results(self, db_session):
        """Build query for venta in Asuncion, execute, get results."""
        geo = ResolvedGeo(
            canonical_city="asuncion",
            city_locations=[
                GeoLocation("asuncion", 0),
                GeoLocation("luque", 1),
            ],
            barrio_locations=[],
            landmark=None,
        )
        filters = SearchFilters(operacion="venta")
        fq = self.builder.build_query(filters, geo=geo)
        result = await db_session.execute(text(fq.sql), fq.params)
        rows = result.fetchall()
        assert len(rows) > 0

    @pytest.mark.asyncio
    async def test_query_excludes_inactive(self, db_session):
        """All returned properties have is_active = true."""
        filters = SearchFilters(operacion="venta")
        fq = self.builder.build_query(filters)
        result = await db_session.execute(text(fq.sql), fq.params)
        rows = result.fetchall()
        assert len(rows) > 0
        # Verify via separate query that all returned IDs are active
        returned_ids = [row.id for row in rows]
        check = await db_session.execute(
            text(
                "SELECT id, is_active FROM properties "
                "WHERE id = ANY(:ids)"
            ),
            {"ids": returned_ids},
        )
        for r in check.fetchall():
            assert r.is_active is True

    @pytest.mark.asyncio
    async def test_query_excludes_duplicates(self, db_session):
        """All returned properties have duplicate_of IS NULL."""
        filters = SearchFilters(operacion="venta")
        fq = self.builder.build_query(filters)
        result = await db_session.execute(text(fq.sql), fq.params)
        rows = result.fetchall()
        assert len(rows) > 0
        # Verify via separate query that all returned IDs have no duplicate_of
        returned_ids = [row.id for row in rows]
        check = await db_session.execute(
            text(
                "SELECT id, duplicate_of FROM properties "
                "WHERE id = ANY(:ids)"
            ),
            {"ids": returned_ids},
        )
        for r in check.fetchall():
            assert r.duplicate_of is None

    @pytest.mark.asyncio
    async def test_pagination_returns_specific_ids(self, db_session):
        """Pagination query returns exactly the requested IDs."""
        # First, get 3 real property IDs
        pre = await db_session.execute(
            text(
                "SELECT id FROM properties "
                "WHERE is_active = true LIMIT 3"
            )
        )
        real_ids = [row.id for row in pre.fetchall()]
        assert len(real_ids) == 3

        fq = self.builder.build_pagination_query(real_ids)
        result = await db_session.execute(text(fq.sql), fq.params)
        rows = result.fetchall()
        returned_ids = {row.id for row in rows}
        assert returned_ids == set(real_ids)

    @pytest.mark.asyncio
    async def test_count_by_barrios(self, db_session):
        """Count properties in villa morra and recoleta per barrio."""
        filters = SearchFilters(operacion="venta")
        fq = self.builder.build_count_by_barrios_query(
            barrios=["villa morra", "recoleta"],
            city="asuncion",
            filters=filters,
        )
        result = await db_session.execute(text(fq.sql), fq.params)
        rows = result.fetchall()
        # Should return rows with barrio names and counts
        assert len(rows) > 0
        barrio_names = [row.barrio_name for row in rows]
        # At least one of the requested barrios should appear
        assert any(
            b in ["villa morra", "recoleta"] for b in barrio_names
        ), f"Expected villa morra or recoleta in {barrio_names}"

    @pytest.mark.asyncio
    async def test_query_limit_50(self, db_session):
        """Broad query returns at most 50 results."""
        filters = SearchFilters(operacion="venta")
        fq = self.builder.build_query(filters)
        result = await db_session.execute(text(fq.sql), fq.params)
        rows = result.fetchall()
        assert len(rows) <= 50


# ===========================================================================
# TestDormitoriosMinMax — B1: dormitorios_min/max SQL operators (5 tests)
# ===========================================================================


class TestDormitoriosMinMax:
    """B1: dormitorios_min/max generate correct SQL operators."""

    def test_dormitorios_min_only_generates_gte(self):
        f = SearchFilters(dormitorios_min=3)
        q = SQLFilterBuilder().build_query(f)
        assert "p.bedrooms >= :dorms_min" in q.sql
        assert "p.bedrooms <= " not in q.sql
        assert q.params["dorms_min"] == 3

    def test_dormitorios_max_only_generates_lte(self):
        f = SearchFilters(dormitorios_max=2)
        q = SQLFilterBuilder().build_query(f)
        assert "p.bedrooms <= :dorms_max" in q.sql
        assert "p.bedrooms >= " not in q.sql
        assert q.params["dorms_max"] == 2

    def test_dormitorios_exact_uses_both(self):
        """'2 dormitorios' exacto → min=2 AND max=2."""
        f = SearchFilters(dormitorios_min=2, dormitorios_max=2)
        q = SQLFilterBuilder().build_query(f)
        assert "p.bedrooms >= :dorms_min" in q.sql
        assert "p.bedrooms <= :dorms_max" in q.sql
        assert q.params["dorms_min"] == 2
        assert q.params["dorms_max"] == 2

    def test_dormitorios_between_range(self):
        f = SearchFilters(dormitorios_min=2, dormitorios_max=4)
        q = SQLFilterBuilder().build_query(f)
        assert "p.bedrooms >= :dorms_min" in q.sql
        assert "p.bedrooms <= :dorms_max" in q.sql

    def test_no_dormitorios_generates_no_bedrooms_clause(self):
        f = SearchFilters()
        q = SQLFilterBuilder().build_query(f)
        assert "bedrooms >= " not in q.sql
        assert "bedrooms <= " not in q.sql


# ===========================================================================
# TestBathroomsFilters — B5: bathrooms_min/max filters (3 tests)
# ===========================================================================


class TestBathroomsFilters:
    """B5: bathrooms_min/max filters."""

    def test_bathrooms_min_only(self):
        f = SearchFilters(bathrooms_min=2)
        q = SQLFilterBuilder().build_query(f)
        assert "p.bathrooms >= :baths_min" in q.sql
        assert q.params["baths_min"] == 2

    def test_bathrooms_max_only(self):
        f = SearchFilters(bathrooms_max=3)
        q = SQLFilterBuilder().build_query(f)
        assert "p.bathrooms <= :baths_max" in q.sql
        assert q.params["baths_max"] == 3

    def test_bathrooms_both(self):
        f = SearchFilters(bathrooms_min=1, bathrooms_max=2)
        q = SQLFilterBuilder().build_query(f)
        assert "p.bathrooms >= :baths_min" in q.sql
        assert "p.bathrooms <= :baths_max" in q.sql


# ===========================================================================
# TestAreaFilters — B5: area_min/max filters on total_area_m2 (3 tests)
# ===========================================================================


class TestAreaFilters:
    """B5: area_min/max filters on total_area_m2."""

    def test_area_min_only(self):
        f = SearchFilters(area_min=100.0)
        q = SQLFilterBuilder().build_query(f)
        assert "p.total_area_m2 >= :area_min" in q.sql
        assert q.params["area_min"] == 100.0

    def test_area_max_only(self):
        f = SearchFilters(area_max=200.0)
        q = SQLFilterBuilder().build_query(f)
        assert "p.total_area_m2 <= :area_max" in q.sql
        assert q.params["area_max"] == 200.0

    def test_area_both(self):
        f = SearchFilters(area_min=80.0, area_max=150.0)
        q = SQLFilterBuilder().build_query(f)
        assert "p.total_area_m2 >= :area_min" in q.sql
        assert "p.total_area_m2 <= :area_max" in q.sql


# ===========================================================================
# TestPriceGuardrailMoneda — B8: _price_guardrail respects moneda (7 tests)
# ===========================================================================


class TestPriceGuardrailMoneda:
    """B8: _price_guardrail respects moneda (gs vs usd)."""

    def test_guardrail_default_usd_venta(self):
        result = SQLFilterBuilder._price_guardrail("venta", "usd")
        assert "price_usd >= 5000" in result

    def test_guardrail_default_usd_alquiler(self):
        result = SQLFilterBuilder._price_guardrail("alquiler", "usd")
        assert "price_usd >= 50" in result
        assert "price_usd < 5000" in result

    def test_guardrail_gs_venta_uses_pyg(self):
        result = SQLFilterBuilder._price_guardrail("venta", "gs")
        assert "price_pyg" in result
        assert "price_usd" not in result

    def test_guardrail_gs_alquiler_uses_pyg(self):
        result = SQLFilterBuilder._price_guardrail("alquiler", "gs")
        assert "price_pyg" in result
        assert "price_usd" not in result

    def test_guardrail_no_operacion_usd(self):
        result = SQLFilterBuilder._price_guardrail(None, "usd")
        assert "price_usd >= 50" in result

    def test_guardrail_no_operacion_gs(self):
        result = SQLFilterBuilder._price_guardrail(None, "gs")
        assert "price_pyg" in result

    def test_build_query_passes_moneda_to_guardrail(self):
        """build_query with moneda='gs' generates GS guardrail."""
        f = SearchFilters(operacion="venta", moneda="gs")
        q = SQLFilterBuilder().build_query(f)
        assert "price_pyg" in q.sql
