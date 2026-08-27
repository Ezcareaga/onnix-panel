"""AlternativesBuilder — zero-results alternatives service.

When a property search returns 0 results, builds up to 3 actionable
alternatives (zona vecina, presupuesto relajado, tipo similar) with
real DB counts so the bot can offer concrete choices.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import text

from app.bot.search.geo_resolver import GeoResolver, ResolvedGeo, _normalize
from app.bot.search.search_service import SearchService
from app.bot.search.sql_filters import SearchFilters, SQLFilterBuilder, _UNSET
from app.utils.money import miles

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Minimum results an alternative must have to be included.
_MIN_COUNT = 3

# Hard cap on total alternatives returned.
_MAX_ALTERNATIVES = 3

# Maximum length for the callback_payload field (Twilio Quick Reply limit).
_MAX_PAYLOAD_LEN = 50

# Maximum length for the id suffix before truncation.
_MAX_ID_SUFFIX_LEN = 30

# Fields excluded when counting active filters for activation check.
_EXCLUDED_FROM_ACTIVE_COUNT = frozenset({
    "operacion",
    "moneda",
    "excluded_ids",
    "pagination_ids",
    "descripcion_libre",
})

# Fixed mapping for tipo similar alternatives.
_TIPO_SIMILAR: dict[str, str] = {
    "casa": "duplex",
    "departamento": "duplex",
    "duplex": "casa",
    "quinta": "campo",
    "campo": "quinta",
}


def _safe_id(name: str) -> str:
    """Normalize a name and truncate to _MAX_ID_SUFFIX_LEN chars for stable IDs."""
    normed = _normalize(name)
    return normed[:_MAX_ID_SUFFIX_LEN]


@dataclass
class Alternative:
    """A single search alternative with real count and ready-to-use filters."""

    id: str
    """Stable identifier, e.g. 'zona_vecina:lambare', 'presupuesto_20pct'."""

    label: str
    """Short human-readable label, e.g. 'En Lambaré hay 8 deptos'."""

    count: int
    """Real DB count, always >= 3."""

    filters: dict
    """Ready-to-use filter dict for SearchFilters(**filters)."""

    reason: str
    """Short explanation, e.g. 'zona vecina'."""

    callback_payload: str
    """Twilio Quick Reply payload — 'ALT:<id>' — at most 50 chars."""


@dataclass
class AlternativesResult:
    """Container for 0-3 alternatives."""

    alternatives: list[Alternative] = field(default_factory=list)


class AlternativesBuilder:
    """Builds zero-results alternatives with real DB counts.

    Priority order (stops at _MAX_ALTERNATIVES):
    1. Zona vecina — up to 2 neighbor barrios or cities
    2. Presupuesto relajado — +20% or +30% budget
    3. Tipo similar — fixed mapping (casa↔duplex, quinta↔campo)
    """

    def __init__(self, search_service: SearchService, geo_resolver: GeoResolver) -> None:
        self._search_service = search_service
        self._geo_resolver = geo_resolver
        self._sql_builder = SQLFilterBuilder()

    async def build(
        self,
        session: AsyncSession,
        original_filters: SearchFilters,
        geo: ResolvedGeo,
    ) -> AlternativesResult:
        """Build alternatives for a zero-results search.

        Returns AlternativesResult(alternatives=[]) when activation
        threshold is not met (fewer than 2 active filters).
        """
        if not self._has_enough_active_filters(original_filters):
            return AlternativesResult()

        collected: list[Alternative] = []

        # Priority 1: zona vecina
        zona_alts = await self._build_zona_vecina(session, original_filters, geo)
        collected.extend(zona_alts)

        # Priority 2: presupuesto relajado (max 1)
        if len(collected) < _MAX_ALTERNATIVES:
            presup_alt = await self._build_presupuesto(session, original_filters)
            if presup_alt:
                collected.append(presup_alt)

        # Priority 3: tipo similar (max 1)
        if len(collected) < _MAX_ALTERNATIVES:
            tipo_alt = await self._build_tipo_similar(session, original_filters)
            if tipo_alt:
                collected.append(tipo_alt)

        result_count = len(collected)
        logger.info("AlternativesBuilder built N=%d", result_count)
        return AlternativesResult(alternatives=collected[:_MAX_ALTERNATIVES])

    # ------------------------------------------------------------------
    # Private: activation check
    # ------------------------------------------------------------------

    @staticmethod
    def _has_enough_active_filters(filters: SearchFilters) -> bool:
        """Return True if >= 2 non-excluded, non-None filters are set."""
        dump = filters.model_dump(exclude_none=True)
        active = [k for k in dump if k not in _EXCLUDED_FROM_ACTIVE_COUNT]
        return len(active) >= 2

    # ------------------------------------------------------------------
    # Private: count helper
    # ------------------------------------------------------------------

    async def _count(
        self,
        session: AsyncSession,
        filters: SearchFilters,
        city_override: object = _UNSET,
        barrio_override: object = _UNSET,
        precio_max_override: object = _UNSET,
        tipo_override: object = _UNSET,
    ) -> int:
        """Execute a COUNT(*) query and return the integer result.

        Omitting an override preserves the original value from filters.
        Pass None explicitly to suppress a filter (e.g. barrio_override=None).
        """
        fq = self._sql_builder.build_count_query(
            filters,
            city_override=city_override,
            barrio_override=barrio_override,
            precio_max_override=precio_max_override,
            tipo_override=tipo_override,
        )
        result = await session.execute(text(fq.sql), fq.params)
        row = result.fetchone()
        return int(row.total) if row else 0

    # ------------------------------------------------------------------
    # Private: filters dump helper
    # ------------------------------------------------------------------

    @staticmethod
    def _base_filters_dict(filters: SearchFilters) -> dict:
        """Dump filters excluding list fields and None values."""
        return filters.model_dump(exclude_none=True, exclude={
            "excluded_ids", "pagination_ids"
        })

    # ------------------------------------------------------------------
    # Private: zona vecina
    # ------------------------------------------------------------------

    async def _build_zona_vecina(
        self,
        session: AsyncSession,
        filters: SearchFilters,
        geo: ResolvedGeo,
    ) -> list[Alternative]:
        """Build up to 2 zona vecina alternatives."""
        alts: list[Alternative] = []

        if filters.barrio:
            # Barrio expansion: use neighbor barrios from the resolved geo
            alts = await self._zona_vecina_barrio(session, filters, geo)
        elif filters.ciudad:
            # City expansion: use neighbor cities
            alts = await self._zona_vecina_city(session, filters)

        return alts[:2]

    async def _zona_vecina_barrio(
        self,
        session: AsyncSession,
        filters: SearchFilters,
        geo: ResolvedGeo,
    ) -> list[Alternative]:
        """Barrio-level zona vecina: query count_by_barrios for neighbors."""
        city = filters.ciudad or geo.canonical_city
        if not city:
            return []

        # Neighbor barrios: distance > 0 from barrio_locations
        neighbor_barrios = [
            loc.name
            for loc in geo.barrio_locations
            if loc.distance > 0
        ]
        if not neighbor_barrios:
            return []

        # Build filters without barrio for the count_by_barrios call
        filters_no_barrio = SearchFilters(
            **{
                k: v for k, v in self._base_filters_dict(filters).items()
                if k not in ("barrio", "barrios", "ciudad")
            }
        )

        counts = await self._search_service.count_by_barrios(
            barrios=neighbor_barrios,
            city=city,
            filters=filters_no_barrio,
            session=session,
        )

        # Sort by count_in_budget descending, filter by threshold
        ranked = sorted(
            [
                (barrio_name, info)
                for barrio_name, info in counts.items()
                if info.get("count_in_budget", 0) >= _MIN_COUNT
            ],
            key=lambda x: x[1].get("count_in_budget", 0),
            reverse=True,
        )

        results: list[Alternative] = []
        for barrio_name, info in ranked[:2]:
            count = info["count_in_budget"]
            safe_name = _safe_id(barrio_name)
            alt_id = f"zona_vecina:{safe_name}"
            payload = f"ALT:{alt_id}"
            if len(payload) > _MAX_PAYLOAD_LEN:
                truncated = safe_name[: _MAX_PAYLOAD_LEN - len("ALT:zona_vecina:") - 1]
                alt_id = f"zona_vecina:{truncated}"
                payload = f"ALT:{alt_id}"

            # Display name: capitalize first letter of each word
            display = barrio_name.title()
            base_dump = self._base_filters_dict(filters)
            base_dump["barrio"] = barrio_name
            base_dump.pop("barrios", None)

            results.append(Alternative(
                id=alt_id,
                label=f"En {display} hay {count}",
                count=count,
                filters=base_dump,
                reason="zona vecina",
                callback_payload=payload,
            ))

        return results

    async def _zona_vecina_city(
        self,
        session: AsyncSession,
        filters: SearchFilters,
    ) -> list[Alternative]:
        """City-level zona vecina: query each neighbor city with COUNT(*)."""
        city = filters.ciudad
        if not city:
            return []

        neighbor_locations = self._geo_resolver.expand_city_neighbors(city, max_distance=1)
        # Filter out the origin city (distance == 0)
        neighbors = [loc for loc in neighbor_locations if loc.distance > 0]
        if not neighbors:
            return []

        # Query each neighbor city for count
        candidates: list[tuple[str, int]] = []
        for loc in neighbors:
            count = await self._count(
                session,
                filters,
                city_override=loc.name,
                barrio_override=None,
            )
            if count >= _MIN_COUNT:
                candidates.append((loc.name, count))

        # Sort by count descending, take top 2
        candidates.sort(key=lambda x: x[1], reverse=True)

        results: list[Alternative] = []
        for city_name, count in candidates[:2]:
            safe_name = _safe_id(city_name)
            alt_id = f"zona_vecina:{safe_name}"
            payload = f"ALT:{alt_id}"
            if len(payload) > _MAX_PAYLOAD_LEN:
                truncated = safe_name[: _MAX_PAYLOAD_LEN - len("ALT:zona_vecina:") - 1]
                alt_id = f"zona_vecina:{truncated}"
                payload = f"ALT:{alt_id}"

            display = city_name.title()
            base_dump = self._base_filters_dict(filters)
            base_dump["ciudad"] = city_name
            base_dump.pop("barrio", None)
            base_dump.pop("barrios", None)

            results.append(Alternative(
                id=alt_id,
                label=f"En {display} hay {count}",
                count=count,
                filters=base_dump,
                reason="zona vecina",
                callback_payload=payload,
            ))

        return results

    # ------------------------------------------------------------------
    # Private: presupuesto relajado
    # ------------------------------------------------------------------

    async def _build_presupuesto(
        self,
        session: AsyncSession,
        filters: SearchFilters,
    ) -> Alternative | None:
        """Try +20% then +30% budget relaxation. Returns first that hits >= 3."""
        if filters.precio_max is None:
            return None

        for pct, suffix in ((1.20, "20pct"), (1.30, "30pct")):
            nuevo_max = filters.precio_max * pct
            count = await self._count(
                session,
                filters,
                precio_max_override=nuevo_max,
            )
            if count >= _MIN_COUNT:
                base_dump = self._base_filters_dict(filters)
                base_dump["precio_max"] = nuevo_max

                alt_id = f"presupuesto_{suffix}"
                payload = f"ALT:{alt_id}"
                nuevo_max_fmt = miles(nuevo_max)

                return Alternative(
                    id=alt_id,
                    label=f"Subiendo a USD {nuevo_max_fmt} hay {count}",
                    count=count,
                    filters=base_dump,
                    reason=f"presupuesto +{suffix[:2]}%",
                    callback_payload=payload,
                )

        return None

    # ------------------------------------------------------------------
    # Private: tipo similar
    # ------------------------------------------------------------------

    async def _build_tipo_similar(
        self,
        session: AsyncSession,
        filters: SearchFilters,
    ) -> Alternative | None:
        """Try the mapped similar property type. Returns alternative if count >= 3."""
        if not filters.tipo:
            return None

        alt_tipo = _TIPO_SIMILAR.get(filters.tipo.lower())
        if alt_tipo is None:
            return None

        count = await self._count(
            session,
            filters,
            tipo_override=alt_tipo,
        )
        if count < _MIN_COUNT:
            return None

        base_dump = self._base_filters_dict(filters)
        base_dump["tipo"] = alt_tipo

        alt_id = f"tipo_{alt_tipo}"
        payload = f"ALT:{alt_id}"

        cap_tipo = alt_tipo.capitalize()

        return Alternative(
            id=alt_id,
            label=f"{cap_tipo} similar: {count} disponibles",
            count=count,
            filters=base_dump,
            reason="tipo similar",
            callback_payload=payload,
        )
