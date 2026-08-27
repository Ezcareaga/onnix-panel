"""FilterRelaxation — progressive filter degradation for zero-result searches.

When the initial property search returns 0 results, this module tries
progressively relaxed queries: wider price range, drop bedrooms, expand zone,
drop property type, and finally a cheapest-property fallback.

Levels are CUMULATIVE: level 3 includes all relaxations from levels 1 and 2.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.search.geo_resolver import GeoResolver, ResolvedGeo, GeoLocation
from app.bot.search.sql_filters import FilteredQuery, SQLFilterBuilder, SearchFilters, _resolve_tipo_to_id
from app.utils.money import miles

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Level descriptions (Spanish, user-facing)
# ---------------------------------------------------------------------------

_LEVEL_DESCRIPTIONS: dict[int, str] = {
    1: "Ampliamos el presupuesto un 30%",
    2: "Sin filtro de dormitorios",
    3: "Buscando en zonas cercanas",
    99: "La opcion mas economica en la zona",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DegradationInfo:
    """Metadata about which relaxation level was applied."""

    level: int                    # 1, 2, 3, 4, or 99
    description: str              # human-readable explanation (Spanish)
    text_only: bool = False       # True if cheapest > 2x budget (level 99)
    original_filters: dict = field(default_factory=dict)
    min_price_in_zone: float | None = None  # cheapest of the requested tipo in zone
    relaxed_filters: list[str] = field(default_factory=list)


@dataclass
class RelaxationResult:
    """Result of a successful filter relaxation."""

    properties: list[dict]
    total_count: int
    degradation: DegradationInfo


# ---------------------------------------------------------------------------
# Select columns (same as sql_filters._SELECT_COLUMNS)
# ---------------------------------------------------------------------------

_SELECT_COLUMNS = (
    "p.id, p.source, p.external_id, p.title, p.description, "
    "p.price_usd, p.price_pyg, p.price_currency, p.city, p.neighborhood, "
    "p.operation, p.property_type, p.bedrooms, p.bathrooms, "
    "p.total_area_m2, p.built_area_m2, p.main_image_url, "
    "p.local_image_count, p.address, p.latitude, p.longitude"
)


# ---------------------------------------------------------------------------
# FilterRelaxation
# ---------------------------------------------------------------------------

class FilterRelaxation:
    """Progressive filter relaxation for zero-result property searches.

    Tries 5 cumulative levels:
    - Level 1: price +30%
    - Level 2: drop dormitorios
    - Level 3: expand zone (barrio -> city, city -> neighbors)
    - Level 4: drop tipo
    - Level 99: cheapest fallback with 2x budget rule
    """

    def __init__(
        self,
        builder: SQLFilterBuilder,
        geo_resolver: GeoResolver,
    ) -> None:
        self.builder = builder
        self.geo_resolver = geo_resolver

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def degrade(
        self,
        session: AsyncSession,
        filters: SearchFilters,
        geo: ResolvedGeo | None = None,
    ) -> RelaxationResult | None:
        """Try progressively relaxed queries until one returns results.

        Levels are cumulative: each level builds on all previous relaxations.
        Returns the first RelaxationResult with >0 results, or None.
        """
        original_dict = filters.model_dump(exclude_none=True)

        # Build cumulative state
        current_filters = filters.model_copy()
        current_geo = geo

        levels = [
            (1, self._relax_price),
            (2, self._relax_dormitorios),
            (3, self._relax_zone),
        ]

        applied_relaxations: list[str] = []

        for level_num, relax_fn in levels:
            filters_before = current_filters
            geo_before = current_geo

            result = relax_fn(current_filters, current_geo)
            if result is None:
                continue

            current_filters, current_geo = result

            # Build human-readable description for this relaxation level
            if level_num == 1:
                # Price relaxation — use the original filters' moneda
                if filters.moneda == "gs":
                    desc = f"presupuesto máximo ampliado a {miles(current_filters.precio_max)} Gs"
                else:
                    desc = f"presupuesto máximo ampliado a USD {miles(current_filters.precio_max)}"
                applied_relaxations.append(desc)
            elif level_num == 2:
                applied_relaxations.append("filtro de dormitorios mínimos eliminado")
            elif level_num == 3:
                had_barrio = (
                    filters_before.barrio is not None
                    or (filters_before.barrios is not None and len(filters_before.barrios) > 0)
                    or (geo_before is not None and len(geo_before.barrio_locations) > 0)
                )
                if had_barrio:
                    applied_relaxations.append("barrio eliminado, búsqueda ampliada a toda la ciudad")
                else:
                    applied_relaxations.append("búsqueda ampliada a ciudades vecinas")

            # Execute the query with cumulative relaxations
            query = self.builder.build_query(current_filters, current_geo)
            rows = await session.execute(text(query.sql), query.params)
            properties = rows.fetchall()

            if len(properties) > 0:
                prop_dicts = [dict(row._mapping) for row in properties]
                return RelaxationResult(
                    properties=prop_dicts,
                    total_count=len(prop_dicts),
                    degradation=DegradationInfo(
                        level=level_num,
                        description=_LEVEL_DESCRIPTIONS[level_num],
                        original_filters=original_dict,
                        relaxed_filters=list(applied_relaxations),
                    ),
                )

        # Levels 1-3 exhausted with no results.
        # Instead of dropping tipo (level 4, which returns irrelevant
        # property types), fetch min_price for the original tipo+zone
        # so Claude can tell the user what to expect.
        min_price = await self._get_min_price_for_type(
            session, filters, geo,
        )
        if min_price is not None:
            tipo_label = filters.tipo or "propiedad"
            zona_label = filters.barrio or filters.ciudad or "la zona"
            return RelaxationResult(
                properties=[],
                total_count=0,
                degradation=DegradationInfo(
                    level=4,
                    description=(
                        f"No hay {tipo_label}s en {zona_label} a ese presupuesto"
                    ),
                    original_filters=original_dict,
                    min_price_in_zone=min_price,
                ),
            )

        # Level 99: cheapest fallback (any type in the zone)
        return await self._cheapest_fallback(session, filters, geo)

    # ------------------------------------------------------------------
    # Private relaxation methods
    # ------------------------------------------------------------------

    def _relax_price(
        self,
        filters: SearchFilters,
        geo: ResolvedGeo | None,
    ) -> tuple[SearchFilters, ResolvedGeo | None] | None:
        """Level 1: increase precio_max by 30%.

        Returns None if no precio_max is set (nothing to relax).
        """
        if filters.precio_max is None:
            return None

        new_filters = filters.model_copy()
        new_filters.precio_max = filters.precio_max * 1.3
        return new_filters, geo

    def _relax_dormitorios(
        self,
        filters: SearchFilters,
        geo: ResolvedGeo | None,
    ) -> tuple[SearchFilters, ResolvedGeo | None] | None:
        """Level 2: relax dormitorios_min only.

        dormitorios_max is a hard constraint (user explicitly said "máximo N") —
        never relaxed. If only dormitorios_max is set, skip this level.
        Returns None if there is nothing soft to relax.
        The input filters already carry any price relaxation from level 1.
        """
        if filters.dormitorios_min is None:
            return None  # nothing soft to relax (max-only is a hard constraint)

        new_filters = filters.model_copy()
        new_filters.dormitorios_min = None  # relax soft minimum
        # dormitorios_max intentionally kept — hard constraint
        return new_filters, geo

    def _relax_zone(
        self,
        filters: SearchFilters,
        geo: ResolvedGeo | None,
    ) -> tuple[SearchFilters, ResolvedGeo | None] | None:
        """Level 3: expand zone — barrio to city, city to neighbors.

        If barrio is set: drop barrio (search entire city).
        If only city: expand city to include neighbor cities.
        Returns None if no geographic filter is set.
        """
        has_barrio = (
            filters.barrio is not None
            or (filters.barrios is not None and len(filters.barrios) > 0)
            or (geo is not None and len(geo.barrio_locations) > 0)
        )
        has_city = (
            filters.ciudad is not None
            or (geo is not None and geo.canonical_city is not None)
        )

        if not has_barrio and not has_city:
            return None

        new_filters = filters.model_copy()

        if has_barrio:
            # Drop barrio — search whole city
            new_filters.barrio = None
            new_filters.barrios = None
            if geo is not None:
                # Rebuild geo with only city locations (no barrio)
                new_geo = ResolvedGeo(
                    canonical_city=geo.canonical_city,
                    city_locations=geo.city_locations,
                    barrio_locations=[],
                    landmark=geo.landmark,
                )
                return new_filters, new_geo
            return new_filters, geo

        # City only — expand to neighbor cities
        city_name = (
            (geo.canonical_city if geo else None)
            or filters.ciudad
        )
        if city_name:
            expanded_cities = self.geo_resolver.expand_city_neighbors(
                city_name, max_distance=1,
            )
            new_geo = ResolvedGeo(
                canonical_city=city_name if geo is None else geo.canonical_city,
                city_locations=expanded_cities,
                barrio_locations=[],
            )
            return new_filters, new_geo

        return None

    async def _get_min_price_for_type(
        self,
        session: AsyncSession,
        filters: SearchFilters,
        geo: ResolvedGeo | None,
    ) -> float | None:
        """Get the minimum price for the requested property type in the zone.

        Used when levels 1-3 are exhausted: instead of dropping the type
        filter (which returns irrelevant results), we tell the user what
        the cheapest matching property actually costs.
        """
        if not filters.tipo:
            return None

        where_parts: list[str] = [
            "p.is_active = true",
            "p.duplicate_of IS NULL",
            "p.price_usd IS NOT NULL",
            "p.price_usd > 0",
        ]
        params: dict = {}

        tipo_id = _resolve_tipo_to_id(filters.tipo)
        if tipo_id is not None:
            where_parts.append(
                "(p.property_type_normalized = :tipo_id "
                "OR (p.property_type_normalized IS NULL "
                "AND f_unaccent(lower(p.property_type)) LIKE "
                "f_unaccent(lower(:tipo))))"
            )
            params["tipo_id"] = tipo_id
            params["tipo"] = f"%{filters.tipo}%"
        else:
            where_parts.append(
                "f_unaccent(lower(p.property_type)) LIKE "
                "f_unaccent(lower(:tipo))"
            )
            params["tipo"] = f"%{filters.tipo}%"

        if filters.operacion:
            where_parts.append(
                "f_unaccent(lower(p.operation)) = "
                "f_unaccent(lower(:operacion))"
            )
            params["operacion"] = filters.operacion

        # Geographic scope: prefer city from geo, fallback to filters
        city = (geo.canonical_city if geo else None) or filters.ciudad
        if city:
            where_parts.append(
                "f_unaccent(lower(p.city)) = "
                "f_unaccent(lower(:min_city))"
            )
            params["min_city"] = city

        guardrail = self.builder._price_guardrail(filters.operacion)
        if guardrail:
            where_parts.append(guardrail)

        where_clause = " AND ".join(where_parts)
        sql = (
            f"SELECT MIN(p.price_usd) AS min_price\n"
            f"FROM properties p\n"
            f"WHERE {where_clause}"
        )

        result = await session.execute(text(sql), params)
        row = result.first()
        if row is None or row.min_price is None:
            return None
        return float(row.min_price)

    async def _cheapest_fallback(
        self,
        session: AsyncSession,
        filters: SearchFilters,
        geo: ResolvedGeo | None,
    ) -> RelaxationResult | None:
        """Level 99: find cheapest property with 2x budget rule.

        Builds a simple query: active, non-duplicate properties in the zone
        matching only operacion (if set), ordered by price_usd ASC, LIMIT 1.
        If the cheapest property costs > 2x the user's budget, sets text_only=True.
        """
        where_parts: list[str] = [
            "p.is_active = true",
            "p.duplicate_of IS NULL",
            "p.price_usd IS NOT NULL",
        ]
        params: dict = {}

        # Operacion filter
        if filters.operacion:
            where_parts.append(
                "f_unaccent(lower(p.operation)) = "
                "f_unaccent(lower(:operacion))"
            )
            params["operacion"] = filters.operacion

        # Price guardrail
        guardrail = self.builder._price_guardrail(filters.operacion)
        if guardrail:
            where_parts.append(guardrail)

        # Geographic filter from geo
        if geo is not None:
            if geo.canonical_city:
                where_parts.append(
                    "f_unaccent(lower(p.city)) = "
                    "f_unaccent(lower(:fallback_city))"
                )
                params["fallback_city"] = geo.canonical_city

        where_clause = " AND ".join(where_parts)
        sql = (
            f"SELECT {_SELECT_COLUMNS}\n"
            f"FROM properties p\n"
            f"WHERE {where_clause}\n"
            f"ORDER BY p.price_usd ASC NULLS LAST\n"
            f"LIMIT 1"
        )

        result = await session.execute(text(sql), params)
        row = result.first()

        if row is None:
            return None

        prop_dict = dict(row._mapping)
        cheapest_price = float(prop_dict.get("price_usd") or 0)

        # 2x budget rule
        text_only = False
        if filters.precio_max and cheapest_price > 2 * filters.precio_max:
            text_only = True

        original_dict = filters.model_dump(exclude_none=True)

        return RelaxationResult(
            properties=[prop_dict],
            total_count=1,
            degradation=DegradationInfo(
                level=99,
                description=_LEVEL_DESCRIPTIONS[99],
                text_only=text_only,
                original_filters=original_dict,
            ),
        )
