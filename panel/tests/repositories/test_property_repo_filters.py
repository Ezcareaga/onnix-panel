"""TDD — property_repo.list_with_filters / count_with_filters

Pure unit tests: mocked session, no DB connection required.
Captures the SQLAlchemy text() object passed to session.execute and
inspects its string representation.
"""
from __future__ import annotations

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, call

from app.repositories.property_repo import PropertyRepository, _build_filter_sql
from app.services.property_service import PropertyFilters


def _make_db(rows=None, scalar_value=0):
    """Return a mocked AsyncSession.

    db.execute() returns a result whose .mappings().all() gives rows,
    and whose .scalar() gives scalar_value for count queries.
    """
    db = AsyncMock()
    mock_result = MagicMock()
    mock_mappings = MagicMock()
    mock_mappings.all.return_value = rows or []
    mock_result.mappings.return_value = mock_mappings
    mock_result.scalar.return_value = scalar_value
    db.execute = AsyncMock(return_value=mock_result)
    return db


def _executed_sql(db) -> str:
    """Extract the SQL string from the first db.execute() call."""
    args, _ = db.execute.call_args
    sql_obj = args[0]
    return str(sql_obj)


class TestListWithFiltersExcludesDuplicates:
    async def test_list_with_filters_excludes_duplicates(self):
        db = _make_db()
        filters = PropertyFilters()
        await PropertyRepository.list_with_filters(db, filters, limit=10, offset=0)
        sql = _executed_sql(db)
        assert "duplicate_of IS NULL" in sql


class TestListWithFiltersStateFilters:
    async def test_list_with_filters_state_active_excludes_on_hold(self):
        db = _make_db()
        filters = PropertyFilters(state="active")
        await PropertyRepository.list_with_filters(db, filters, limit=10, offset=0)
        sql = _executed_sql(db)
        assert "is_active = true" in sql.lower() or "is_active = TRUE" in sql
        assert "on_hold = false" in sql.lower() or "on_hold = FALSE" in sql

    async def test_list_with_filters_state_inactive_only(self):
        db = _make_db()
        filters = PropertyFilters(state="inactive")
        await PropertyRepository.list_with_filters(db, filters, limit=10, offset=0)
        sql = _executed_sql(db)
        assert "is_active = false" in sql.lower() or "is_active = FALSE" in sql

    async def test_list_with_filters_state_on_hold_only(self):
        db = _make_db()
        filters = PropertyFilters(state="on_hold")
        await PropertyRepository.list_with_filters(db, filters, limit=10, offset=0)
        sql = _executed_sql(db)
        assert "on_hold = true" in sql.lower() or "on_hold = TRUE" in sql
        assert "is_active = true" in sql.lower() or "is_active = TRUE" in sql


class TestListWithFiltersSorting:
    async def test_list_with_filters_sort_inactive_uses_updated_at_desc(self):
        db = _make_db()
        filters = PropertyFilters(state="inactive")
        await PropertyRepository.list_with_filters(db, filters, limit=10, offset=0)
        sql = _executed_sql(db)
        assert "updated_at DESC" in sql

    async def test_list_with_filters_active_uses_created_at_desc(self):
        db = _make_db()
        filters = PropertyFilters(state="active")
        await PropertyRepository.list_with_filters(db, filters, limit=10, offset=0)
        sql = _executed_sql(db)
        assert "created_at DESC" in sql


class TestListWithFiltersPriceRange:
    async def test_list_with_filters_price_range(self):
        db = _make_db()
        filters = PropertyFilters(price_min=Decimal("50000"), price_max=Decimal("200000"))
        await PropertyRepository.list_with_filters(db, filters, limit=10, offset=0)
        _, kwargs = db.execute.call_args
        params = kwargs if kwargs else db.execute.call_args[0][1]
        # params passed as second positional arg
        args = db.execute.call_args[0]
        assert len(args) == 2
        bound_params = args[1]
        assert "price_min" in bound_params
        assert "price_max" in bound_params
        assert bound_params["price_min"] == Decimal("50000")
        assert bound_params["price_max"] == Decimal("200000")


class TestListWithFiltersSearchText:
    async def test_search_text_matches_title_or_external_id(self):
        """search_text must filter on (title OR external_id), not title alone.

        Reason: la administradora pega un ID externo en el filtro de texto libre y
        espera que la propiedad aparezca. Antes del fix sólo se buscaba en
        title y los IDs nunca coincidían.
        """
        db = _make_db()
        filters = PropertyFilters(search_text="143025134-72")
        await PropertyRepository.list_with_filters(db, filters, limit=10, offset=0)
        sql = _executed_sql(db)
        # Match on external_id alongside title — disjunction must be in WHERE.
        assert "external_id" in sql
        assert "title" in sql
        # The disjunction lives between WHERE and ORDER BY.
        where_part = sql.split("ORDER BY", 1)[0]
        assert " OR " in where_part, (
            f"Expected an OR clause in WHERE that joins title and external_id; "
            f"got:\n{where_part}"
        )

    async def test_search_text_param_bound_once(self):
        db = _make_db()
        filters = PropertyFilters(search_text="143025134-72")
        await PropertyRepository.list_with_filters(db, filters, limit=10, offset=0)
        bound_params = db.execute.call_args[0][1]
        assert "search_text" in bound_params
        assert bound_params["search_text"] == "%143025134-72%"


class TestAmenitiesFilter:
    """M6.5 — amenities filter via ILIKE unaccent on description."""

    async def test_amenity_piscina_filters_descriptions(self):
        db = _make_db()
        filters = PropertyFilters(amenities=["piscina"])
        await PropertyRepository.list_with_filters(db, filters, limit=10, offset=0)
        sql = _executed_sql(db)
        assert "unaccent(lower(description)) ILIKE unaccent(:amenity_0)" in sql
        bound_params = db.execute.call_args[0][1]
        assert bound_params["amenity_0"] == "%piscina%"

    async def test_amenity_garage_filters_descriptions(self):
        """Two amenities → two AND-joined ILIKE clauses with separate params."""
        db = _make_db()
        filters = PropertyFilters(amenities=["piscina", "garage"])
        await PropertyRepository.list_with_filters(db, filters, limit=10, offset=0)
        sql = _executed_sql(db)
        assert "unaccent(lower(description)) ILIKE unaccent(:amenity_0)" in sql
        assert "unaccent(lower(description)) ILIKE unaccent(:amenity_1)" in sql
        where_part = sql.split("ORDER BY", 1)[0]
        assert (
            where_part.index(":amenity_0") < where_part.index(":amenity_1")
        )
        bound_params = db.execute.call_args[0][1]
        assert bound_params["amenity_0"] == "%piscina%"
        assert bound_params["amenity_1"] == "%garage%"

    async def test_amenity_rejected_if_not_whitelisted(self):
        """Non-whitelisted amenity (defense in depth) → no clause, no param."""
        db = _make_db()
        filters = PropertyFilters(amenities=["jacuzzi"])
        await PropertyRepository.list_with_filters(db, filters, limit=10, offset=0)
        sql = _executed_sql(db)
        assert "amenity_" not in sql
        assert "jacuzzi" not in sql
        bound_params = db.execute.call_args[0][1]
        assert not any(k.startswith("amenity_") for k in bound_params)


class TestBaratoP25:
    """M6.5 — barato filter caps price_usd at the 25th percentile (CTE p25)."""

    async def test_barato_uses_p25_filter(self):
        db = _make_db()
        filters = PropertyFilters(barato=True)
        await PropertyRepository.list_with_filters(db, filters, limit=10, offset=0)
        sql = _executed_sql(db)
        assert "PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY price_usd)" in sql
        assert "price_usd <= (SELECT v FROM p25)" in sql
        bound_params = db.execute.call_args[0][1]
        # Without tipo/ciudad the P25 is global per operation (all None here)
        assert bound_params["p25_type"] is None
        assert bound_params["p25_city"] is None
        assert "p25_operation" in bound_params
        assert bound_params["p25_operation"] is None

    async def test_barato_with_tipo_ciudad_uses_local_p25(self):
        db = _make_db()
        filters = PropertyFilters(
            barato=True,
            property_type="casa",
            city="luque",
            operation="venta",
        )
        await PropertyRepository.list_with_filters(db, filters, limit=10, offset=0)
        bound_params = db.execute.call_args[0][1]
        assert bound_params["p25_type"] == "casa"
        assert bound_params["p25_city"] == "%luque%"
        assert bound_params["p25_operation"] == "venta"

    async def test_barato_count_uses_same_cte(self):
        """count_with_filters must apply the identical CTE + cap (totals match list)."""
        db = _make_db(scalar_value=7)
        filters = PropertyFilters(barato=True, operation="venta")
        await PropertyRepository.count_with_filters(db, filters)
        sql = _executed_sql(db)
        assert sql.lstrip().startswith("WITH p25 AS")
        assert "PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY price_usd)" in sql
        assert "price_usd <= (SELECT v FROM p25)" in sql
        bound_params = db.execute.call_args[0][1]
        assert bound_params["p25_operation"] == "venta"

    async def test_barato_false_leaves_sql_untouched(self):
        db = _make_db()
        filters = PropertyFilters()
        await PropertyRepository.list_with_filters(db, filters, limit=10, offset=0)
        sql = _executed_sql(db)
        assert "p25" not in sql
        assert "PERCENTILE_CONT" not in sql


class TestUpdatedWithinDaysInterval:
    """Fix bug pre-existente: bindparam dentro de literal INTERVAL no se bindea."""

    async def test_updated_within_days_uses_make_interval(self):
        db = _make_db()
        filters = PropertyFilters(updated_within_days=7)
        await PropertyRepository.list_with_filters(db, filters, limit=10, offset=0)
        sql = _executed_sql(db)
        assert "make_interval(days => :updated_within_days)" in sql
        assert "INTERVAL ':" not in sql
        bound_params = db.execute.call_args[0][1]
        assert bound_params["updated_within_days"] == 7


class TestCountWithFilters:
    async def test_count_with_filters_returns_int(self):
        db = _make_db(scalar_value=42)
        filters = PropertyFilters()
        result = await PropertyRepository.count_with_filters(db, filters)
        assert result == 42
        assert isinstance(result, int)

    async def test_count_with_filters_excludes_duplicates(self):
        db = _make_db(scalar_value=0)
        filters = PropertyFilters()
        await PropertyRepository.count_with_filters(db, filters)
        sql = _executed_sql(db)
        assert "duplicate_of IS NULL" in sql


class TestCurrencySelectsPriceColumn:
    """La moneda dice EN QUÉ está escrito el número del rango, no qué etiqueta
    tiene la propiedad.

    `price_currency = :currency` filtraba por la ETIQUETA mientras `price_min`
    y `price_max` comparaban siempre contra `price_usd`. Medido contra
    producción el 2026-08-24 sobre las 14.033 activas no duplicadas: 6.747
    tienen `price_currency='PYG'` **y** `price_usd` cargado, o sea el precio en
    dólares ya estaba calculado y la fila se descartaba igual.
    «casa 3 dorm Lambaré ≤150k» devolvía 6 en vez de 42.

    El criterio es el que el bot ya usa desde siempre
    (`bot/search/sql_filters.py::_price_column`): la moneda elige la COLUMNA.
    """

    def test_sin_moneda_el_rango_compara_contra_price_usd(self):
        where, params = _build_filter_sql(PropertyFilters(price_max=Decimal("150000")))
        assert "price_usd <= :price_max" in where
        assert params["price_max"] == Decimal("150000")

    def test_usd_no_agrega_clausula_de_etiqueta(self):
        """El prompt de property_chatbot emite currency:"USD" por defecto, así
        que casi toda búsqueda con precio pasaba por acá."""
        where, params = _build_filter_sql(
            PropertyFilters(
                price_min=Decimal("1000"), price_max=Decimal("150000"), currency="USD"
            )
        )
        assert "price_usd >= :price_min" in where
        assert "price_usd <= :price_max" in where
        assert "price_currency" not in where
        assert "currency" not in params

    def test_pyg_compara_contra_price_pyg_no_contra_price_usd(self):
        """Claude emite el número en guaraníes crudos («hasta 350 millones» →
        350000000). Comparado contra price_usd el rango no filtraba nada:
        medido en prod, terrenos en Luque daba 120 (todos los rotulados PYG)
        contra 62 con la columna correcta."""
        where, params = _build_filter_sql(
            PropertyFilters(price_max=Decimal("350000000"), currency="PYG")
        )
        assert "price_pyg <= :price_max" in where
        assert "price_usd" not in where
        assert "price_currency" not in where
        assert params["price_max"] == Decimal("350000000")

    def test_pyg_en_minuscula_elige_la_misma_columna(self):
        where, _ = _build_filter_sql(
            PropertyFilters(price_min=Decimal("50000"), currency="pyg")
        )
        assert "price_pyg >= :price_min" in where

    def test_moneda_desconocida_cae_a_dolares(self):
        where, _ = _build_filter_sql(
            PropertyFilters(price_max=Decimal("100"), currency="EUR")
        )
        assert "price_usd <= :price_max" in where

    def test_moneda_sola_sin_rango_no_filtra_nada(self):
        """Sin precio la moneda no tiene qué elegir: no puede sacar filas."""
        where, params = _build_filter_sql(PropertyFilters(currency="PYG"))
        assert "price_" not in where
        assert params == {}


class TestBathroomsFilter:
    """El bot podía filtrar baños y el panel no: misma columna, misma capa de
    datos, dos capacidades distintas. `bathrooms` ya se selecciona para
    mostrarse en la tabla — sólo faltaba poder filtrarla."""

    def test_bathrooms_min_filtra_por_la_columna_bathrooms(self):
        where, params = _build_filter_sql(PropertyFilters(bathrooms_min=3))
        assert "bathrooms >= :bathrooms_min" in where
        assert params["bathrooms_min"] == 3

    def test_sin_bathrooms_min_no_hay_clausula(self):
        where, params = _build_filter_sql(PropertyFilters(bedrooms_min=3))
        assert "bathrooms" not in where
        assert "bathrooms_min" not in params
