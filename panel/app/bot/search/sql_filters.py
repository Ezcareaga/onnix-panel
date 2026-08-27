"""SQLFilterBuilder — constructs parameterized SQL queries for property search.

Transforms structured search parameters into safe, indexed PostgreSQL queries
using sqlalchemy.text() parameter binding. Never uses string interpolation for values.
All text comparisons use f_unaccent(lower()) to match expression indexes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.bot.search.geo_resolver import ResolvedGeo

# Sentinel for build_count_query overrides: distinguishes "not set" from None.
_UNSET: Any = object()


# ---------------------------------------------------------------------------
# Normalized type catalog: user-facing label → property_types.id
# ---------------------------------------------------------------------------

_TIPO_USER_TO_ID: dict[str, int] = {
    "casa": 1,
    "departamento": 2,
    "duplex": 3,
    "terreno": 4,
    "oficina": 5,
    "local": 6,
    "deposito": 7,
    "quinta": 8,
    "campo": 9,
    "edificio": 10,
    "ph": 3,   # alias: ph = duplex in Paraguay
    "otro": 99,
}


def _resolve_tipo_to_id(tipo: str | None) -> int | None:
    """Map a user-supplied tipo string to its property_types.id.

    Case-insensitive. Returns None for unknown types or None input.

    Args:
        tipo: Raw tipo string from Claude tool call (e.g. "casa", "ph").

    Returns:
        Integer ID from the property_types table, or None if unmapped.
    """
    if tipo is None:
        return None
    return _TIPO_USER_TO_ID.get(tipo.lower().strip())


# ---------------------------------------------------------------------------
# Select columns used in all main queries
# ---------------------------------------------------------------------------

_SELECT_COLUMNS = (
    "p.id, p.source, p.external_id, p.title, p.description, "
    "p.price_usd, p.price_pyg, p.price_currency, p.city, p.neighborhood, "
    "p.operation, p.property_type, p.bedrooms, p.bathrooms, "
    "p.total_area_m2, p.built_area_m2, p.main_image_url, "
    "p.image_urls, p.local_image_count, p.address, p.latitude, p.longitude"
)


class SearchFilters(BaseModel):
    """Validated search parameters from Claude tool-use calls.

    The ``construction_state`` field accepts both its canonical name and the
    legacy alias ``estado_construccion`` used by the tool schema.  Claude always
    sends ``estado_construccion``; internal code uses ``construction_state``.
    """

    model_config = ConfigDict(populate_by_name=True)

    operacion: Optional[str] = None
    tipo: Optional[str] = None
    ciudad: Optional[str] = None
    barrio: Optional[str] = None
    barrios: Optional[list[str]] = None
    precio_min: Optional[float] = None
    precio_max: Optional[float] = None
    dormitorios_min: Optional[int] = None
    dormitorios_max: Optional[int] = None
    bathrooms_min: Optional[int] = None
    bathrooms_max: Optional[int] = None
    area_min: Optional[float] = None
    area_max: Optional[float] = None
    moneda: str = "usd"
    descripcion_libre: Optional[str] = None
    construction_state: Optional[str] = Field(default=None, alias="estado_construccion")
    excluded_ids: list[int] = Field(default_factory=list)
    pagination_ids: list[int] = Field(default_factory=list)


@dataclass
class FilteredQuery:
    """A parameterized SQL query ready for sqlalchemy.text() execution."""

    sql: str
    params: dict


class SQLFilterBuilder:
    """Builds parameterized SQL queries for property search.

    Produces FilteredQuery objects containing a SQL string with :param
    placeholders and a params dict. The caller wraps the SQL with
    sqlalchemy.text() for execution.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_query(
        self,
        filters: SearchFilters,
        geo: ResolvedGeo | None = None,
        *,
        use_construction_state_column: bool = False,
    ) -> FilteredQuery:
        """Build a filtered property search query with optional geo expansion.

        Args:
            filters: Structured search parameters.
            geo: Resolved geo context for CTE expansion.
            use_construction_state_column: When True, filter using the
                structured ``properties.construction_state`` column (feature
                flag ``m5_construction_state_filter_enabled``).  When False
                (default/flag-off), falls back to ILIKE on title/description
                for ``en_pozo`` only; other values are silently ignored so the
                query remains valid without the column data.

        Returns a FilteredQuery with:
        - CTE for geo-expanded locations (if geo provided)
        - WHERE clauses for all active filters
        - Price guardrails based on operacion
        - ORDER BY geo_distance ASC NULLS LAST, price_usd DESC NULLS LAST
        - LIMIT 50
        """
        params: dict = {}
        where_parts: list[str] = []
        cte_sql = ""
        has_cte = False
        join_field = ""

        # --- Base WHERE (always) ---
        where_parts.append("p.is_active = true")
        where_parts.append("p.duplicate_of IS NULL")

        # --- CTE for geo expansion ---
        if geo is not None:
            cte_sql, cte_params, join_field = self._build_geo_cte(geo)
            if cte_sql:
                params.update(cte_params)
                has_cte = True

        # --- Operacion ---
        if filters.operacion:
            where_parts.append(
                "f_unaccent(lower(p.operation)) = "
                "f_unaccent(lower(:operacion))"
            )
            params["operacion"] = filters.operacion

        # --- Tipo ---
        if filters.tipo:
            tipo_id = _resolve_tipo_to_id(filters.tipo)
            if tipo_id is not None:
                # Primary: match normalized FK; fallback ILIKE for NULL rows
                # (231 properties not yet classified retain ILIKE coverage)
                where_parts.append(
                    "(p.property_type_normalized = :tipo_id "
                    "OR (p.property_type_normalized IS NULL "
                    "AND f_unaccent(lower(p.property_type)) LIKE "
                    "f_unaccent(lower(:tipo))))"
                )
                params["tipo_id"] = tipo_id
                params["tipo"] = f"%{filters.tipo}%"
            else:
                # Unknown type: pure ILIKE fallback
                where_parts.append(
                    "f_unaccent(lower(p.property_type)) LIKE "
                    "f_unaccent(lower(:tipo))"
                )
                params["tipo"] = f"%{filters.tipo}%"

        # --- Price filters ---
        price_col = self._price_column(filters.moneda)
        if filters.precio_min is not None:
            where_parts.append(f"{price_col} >= :precio_min")
            params["precio_min"] = filters.precio_min
        if filters.precio_max is not None:
            where_parts.append(f"{price_col} <= :precio_max")
            params["precio_max"] = filters.precio_max

        # --- Price guardrails ---
        guardrail = self._price_guardrail(filters.operacion, filters.moneda)
        if guardrail:
            where_parts.append(guardrail)

        # --- Dormitorios ---
        if filters.dormitorios_min is not None:
            where_parts.append("p.bedrooms >= :dorms_min")
            params["dorms_min"] = filters.dormitorios_min
        if filters.dormitorios_max is not None:
            where_parts.append("p.bedrooms <= :dorms_max")
            params["dorms_max"] = filters.dormitorios_max
        if filters.bathrooms_min is not None:
            where_parts.append("p.bathrooms >= :baths_min")
            params["baths_min"] = filters.bathrooms_min
        if filters.bathrooms_max is not None:
            where_parts.append("p.bathrooms <= :baths_max")
            params["baths_max"] = filters.bathrooms_max
        if filters.area_min is not None:
            where_parts.append("p.total_area_m2 >= :area_min")
            params["area_min"] = filters.area_min
        if filters.area_max is not None:
            where_parts.append("p.total_area_m2 <= :area_max")
            params["area_max"] = filters.area_max

        # --- Construction state ---
        if filters.construction_state:
            if use_construction_state_column:
                # Flag ON: use the structured column (backfilled in migration 033)
                where_parts.append("p.construction_state = :construction_state_val")
                params["construction_state_val"] = filters.construction_state
            else:
                # Flag OFF: ILIKE fallback — only en_pozo is covered; other values
                # are silently ignored so the query stays valid without column data.
                if filters.construction_state == "en_pozo":
                    where_parts.append(
                        "(f_unaccent(lower(p.property_type)) LIKE '%pozo%' "
                        "OR f_unaccent(lower(p.title)) LIKE '%pozo%' "
                        "OR f_unaccent(lower(COALESCE(p.description, ''))) LIKE '%pozo%')"
                    )
                # en_construccion / a_estrenar / terminado in flag-off mode: no-op

        # --- Excluded IDs ---
        if filters.excluded_ids:
            ex_placeholders = []
            for i, eid in enumerate(filters.excluded_ids):
                key = f"ex_{i}"
                ex_placeholders.append(f":{key}")
                params[key] = eid
            where_parts.append(
                f"p.id NOT IN ({', '.join(ex_placeholders)})"
            )

        # --- Assemble query ---
        where_clause = " AND ".join(where_parts)

        if has_cte:
            select_extra = ", tl.dist AS geo_distance"
            join_clause = (
                f"LEFT JOIN target_locations tl "
                f"ON f_unaccent(lower(p.{join_field})) = tl.loc"
            )
            # Filter to only matching locations
            where_clause += " AND tl.loc IS NOT NULL"
            # When joining by neighborhood, also constrain city so barrio
            # names shared across cities (e.g. Bella Vista) don't leak.
            if join_field == "neighborhood" and geo is not None and geo.canonical_city:
                where_clause += (
                    " AND f_unaccent(lower(p.city)) = "
                    "f_unaccent(lower(:geo_city))"
                )
                params["geo_city"] = geo.canonical_city
            order_by = (
                "geo_distance ASC NULLS LAST, "
                "p.price_usd DESC NULLS LAST"
            )
            sql = (
                f"{cte_sql}\n"
                f"SELECT {_SELECT_COLUMNS}{select_extra}, COUNT(*) OVER() AS total_count\n"
                f"FROM properties p\n"
                f"{join_clause}\n"
                f"WHERE {where_clause}\n"
                f"ORDER BY {order_by}\n"
                f"LIMIT 50"
            )
        else:
            order_by = "p.price_usd DESC NULLS LAST"
            sql = (
                f"SELECT {_SELECT_COLUMNS}, COUNT(*) OVER() AS total_count\n"
                f"FROM properties p\n"
                f"WHERE {where_clause}\n"
                f"ORDER BY {order_by}\n"
                f"LIMIT 50"
            )

        return FilteredQuery(sql=sql, params=params)

    def build_pagination_query(self, ids: list[int]) -> FilteredQuery:
        """Build a query to fetch specific properties by ID list.

        Returns only active properties matching the given IDs.
        """
        params: dict = {}
        id_placeholders = []
        for i, pid in enumerate(ids):
            key = f"id_{i}"
            id_placeholders.append(f":{key}")
            params[key] = pid

        sql = (
            f"SELECT {_SELECT_COLUMNS}\n"
            f"FROM properties p\n"
            f"WHERE p.id IN ({', '.join(id_placeholders)}) "
            f"AND p.is_active = true"
        )
        return FilteredQuery(sql=sql, params=params)

    def build_count_query(
        self,
        filters: SearchFilters,
        city_override: Any = _UNSET,
        barrio_override: Any = _UNSET,
        precio_max_override: Any = _UNSET,
        tipo_override: Any = _UNSET,
    ) -> FilteredQuery:
        """Build a SELECT COUNT(*) query applying filters with optional overrides.

        Supports per-call overrides for city, barrio, precio_max, and tipo so
        callers can probe alternate parameter combinations without mutating the
        original SearchFilters object.

        Pass ``None`` explicitly to *remove* a filter (e.g. barrio_override=None
        suppresses the barrio constraint even when filters.barrio is set).
        Omitting an override (or passing _UNSET) preserves the original value.
        """
        params: dict = {}
        where_parts: list[str] = [
            "p.is_active = true",
            "p.duplicate_of IS NULL",
        ]

        # Resolve effective values (override takes precedence; _UNSET = use original)
        effective_ciudad = filters.ciudad if city_override is _UNSET else city_override
        effective_barrio = filters.barrio if barrio_override is _UNSET else barrio_override
        effective_precio_max = (
            filters.precio_max if precio_max_override is _UNSET else precio_max_override
        )
        effective_tipo = filters.tipo if tipo_override is _UNSET else tipo_override

        # --- Operacion ---
        if filters.operacion:
            where_parts.append(
                "f_unaccent(lower(p.operation)) = f_unaccent(lower(:operacion))"
            )
            params["operacion"] = filters.operacion

        # --- Tipo ---
        if effective_tipo:
            tipo_id = _resolve_tipo_to_id(effective_tipo)
            if tipo_id is not None:
                where_parts.append(
                    "(p.property_type_normalized = :tipo_id "
                    "OR (p.property_type_normalized IS NULL "
                    "AND f_unaccent(lower(p.property_type)) LIKE "
                    "f_unaccent(lower(:tipo))))"
                )
                params["tipo_id"] = tipo_id
                params["tipo"] = f"%{effective_tipo}%"
            else:
                where_parts.append(
                    "f_unaccent(lower(p.property_type)) LIKE "
                    "f_unaccent(lower(:tipo))"
                )
                params["tipo"] = f"%{effective_tipo}%"

        # --- City ---
        if effective_ciudad:
            where_parts.append(
                "f_unaccent(lower(p.city)) = f_unaccent(lower(:ciudad))"
            )
            params["ciudad"] = effective_ciudad

        # --- Barrio ---
        if effective_barrio:
            where_parts.append(
                "f_unaccent(lower(p.neighborhood)) = f_unaccent(lower(:barrio))"
            )
            params["barrio"] = effective_barrio

        # --- Price ---
        price_col = self._price_column(filters.moneda)
        if filters.precio_min is not None:
            where_parts.append(f"{price_col} >= :precio_min")
            params["precio_min"] = filters.precio_min
        if effective_precio_max is not None:
            where_parts.append(f"{price_col} <= :precio_max")
            params["precio_max"] = effective_precio_max

        # --- Price guardrails ---
        guardrail = self._price_guardrail(filters.operacion, filters.moneda)
        if guardrail:
            where_parts.append(guardrail)

        # --- Dormitorios / bathrooms / area ---
        if filters.dormitorios_min is not None:
            where_parts.append("p.bedrooms >= :dorms_min")
            params["dorms_min"] = filters.dormitorios_min
        if filters.dormitorios_max is not None:
            where_parts.append("p.bedrooms <= :dorms_max")
            params["dorms_max"] = filters.dormitorios_max
        if filters.bathrooms_min is not None:
            where_parts.append("p.bathrooms >= :baths_min")
            params["baths_min"] = filters.bathrooms_min
        if filters.bathrooms_max is not None:
            where_parts.append("p.bathrooms <= :baths_max")
            params["baths_max"] = filters.bathrooms_max
        if filters.area_min is not None:
            where_parts.append("p.total_area_m2 >= :area_min")
            params["area_min"] = filters.area_min
        if filters.area_max is not None:
            where_parts.append("p.total_area_m2 <= :area_max")
            params["area_max"] = filters.area_max

        where_clause = " AND ".join(where_parts)
        sql = (
            f"SELECT COUNT(*) AS total\n"
            f"FROM properties p\n"
            f"WHERE {where_clause}"
        )
        return FilteredQuery(sql=sql, params=params)

    def build_count_by_barrios_query(
        self,
        barrios: list[str],
        city: str,
        filters: SearchFilters,
    ) -> FilteredQuery:
        """Build a query to count properties per barrio.

        Returns rows with: barrio_name, count_total, count_in_budget, min_price_usd.
        Filters by city and operacion. Budget filtering uses the price range
        from filters if set.
        """
        params: dict = {}
        where_base: list[str] = [
            "p.is_active = true",
            "p.duplicate_of IS NULL",
            "f_unaccent(lower(p.city)) = f_unaccent(lower(:city))",
        ]
        params["city"] = city

        if filters.operacion:
            where_base.append(
                "f_unaccent(lower(p.operation)) = "
                "f_unaccent(lower(:operacion))"
            )
            params["operacion"] = filters.operacion

        # Price guardrails
        guardrail = self._price_guardrail(filters.operacion, filters.moneda)
        if guardrail:
            where_base.append(guardrail)

        # Build barrio CTE with UNION ALL
        barrio_unions = []
        for i, barrio in enumerate(barrios):
            key = f"barrio_{i}"
            params[key] = barrio
            barrio_unions.append(f"SELECT :{key} AS barrio_name")

        cte_barrios = " UNION ALL ".join(barrio_unions)

        where_clause = " AND ".join(where_base)

        # Budget conditions for count_in_budget
        budget_conditions = []
        price_col = self._price_column(filters.moneda)
        if filters.precio_max is not None:
            budget_conditions.append(f"{price_col} <= :precio_max")
            params["precio_max"] = filters.precio_max
        if filters.precio_min is not None:
            budget_conditions.append(f"{price_col} >= :precio_min")
            params["precio_min"] = filters.precio_min

        if budget_conditions:
            budget_case = " AND ".join(budget_conditions)
            count_in_budget_expr = (
                f"COUNT(*) FILTER (WHERE {budget_case})"
            )
        else:
            count_in_budget_expr = "COUNT(*)"

        sql = (
            f"WITH barrio_list AS (\n"
            f"  {cte_barrios}\n"
            f")\n"
            f"SELECT bl.barrio_name,\n"
            f"       COUNT(*) AS count_total,\n"
            f"       {count_in_budget_expr} AS count_in_budget,\n"
            f"       MIN(p.price_usd) AS min_price_usd\n"
            f"FROM barrio_list bl\n"
            f"LEFT JOIN properties p\n"
            f"  ON f_unaccent(lower(p.neighborhood)) = "
            f"f_unaccent(lower(bl.barrio_name))\n"
            f"  AND {where_clause}\n"
            f"GROUP BY bl.barrio_name\n"
            f"ORDER BY count_total DESC"
        )

        return FilteredQuery(sql=sql, params=params)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _price_column(moneda: str) -> str:
        """Return the price column name based on currency."""
        if moneda == "gs":
            return "p.price_pyg"
        return "p.price_usd"

    @staticmethod
    def _price_guardrail(operacion: str | None, moneda: str = "usd") -> str:
        """Return a price guardrail clause based on operation type and currency.

        For GS (guaraníes):
        - venta: price_pyg >= 10_000_000
        - alquiler: price_pyg >= 50_000 AND price_pyg < 800_000_000
        - unset/other: price_pyg >= 50_000

        For USD (default):
        - venta: price_usd >= 5000
        - alquiler: price_usd >= 50 AND price_usd < 5000
        - unset/other: price_usd >= 50
        """
        if moneda == "gs":
            if operacion and operacion.lower() == "venta":
                return "p.price_pyg >= 10000000"
            if operacion and operacion.lower() == "alquiler":
                return "p.price_pyg >= 50000 AND p.price_pyg < 800000000"
            return "p.price_pyg >= 50000"
        # Default: USD
        if operacion and operacion.lower() == "venta":
            return "p.price_usd >= 5000"
        if operacion and operacion.lower() == "alquiler":
            return "p.price_usd >= 50 AND p.price_usd < 5000"
        return "p.price_usd >= 50"

    @staticmethod
    def _build_geo_cte(
        geo: ResolvedGeo,
    ) -> tuple[str, dict, str]:
        """Build a CTE for geo-expanded locations.

        Returns (cte_sql, params, join_field) where join_field is
        'neighborhood' or 'city' depending on resolution level.
        """
        params: dict = {}

        # Prefer barrio-level resolution if available
        if geo.barrio_locations:
            locations = geo.barrio_locations
            join_field = "neighborhood"
        elif geo.city_locations:
            locations = geo.city_locations
            join_field = "city"
        else:
            return "", {}, ""

        if not locations:
            return "", {}, ""

        union_parts = []
        for i, loc in enumerate(locations):
            loc_key = f"loc_{i}"
            dist_key = f"dist_{i}"
            params[loc_key] = loc.name
            params[dist_key] = loc.distance
            if i == 0:
                union_parts.append(
                    f"SELECT CAST(:{loc_key} AS text) AS loc, "
                    f"CAST(:{dist_key} AS int) AS dist"
                )
            else:
                union_parts.append(
                    f"UNION ALL SELECT CAST(:{loc_key} AS text), "
                    f"CAST(:{dist_key} AS int)"
                )

        cte_body = "\n  ".join(union_parts)
        cte_sql = f"WITH target_locations AS (\n  {cte_body}\n)"

        return cte_sql, params, join_field
