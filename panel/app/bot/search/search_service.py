"""SearchService — orchestrates all search components for the bot.

Coordinates GeoResolver, SQLFilterBuilder, VectorSearch, FilterRelaxation,
and hybrid RRF fusion into a single search_properties() call.
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import text

from .geo_resolver import GeoResolver, ResolvedGeo
from .hybrid_search import reciprocal_rank_fusion
from .relaxation import DegradationInfo, FilterRelaxation
from .sql_filters import FilteredQuery, SQLFilterBuilder, SearchFilters, _resolve_tipo_to_id
from .vector_search import VectorSearch

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.bot.ai.gemini_client import GeminiClient
    from app.repositories.bot_setting_repo import BotSettingRepository

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """Result of a property search operation."""

    properties: list[dict] = field(default_factory=list)
    total_found: int = 0
    filters_used: dict = field(default_factory=dict)
    has_vector_search: bool = False
    degradation: DegradationInfo | None = None
    price_stats: dict | None = None


class SearchService:
    """Orchestrates geo resolution, SQL filtering, vector search, and relaxation.

    Usage:
        service = SearchService(gemini_client=gemini)
        result = await service.search_properties(filters, session)
    """

    def __init__(
        self,
        gemini_client: "GeminiClient | None" = None,
        bot_settings_repo: "BotSettingRepository | None" = None,
    ) -> None:
        self._geo_resolver = GeoResolver()
        self._sql_builder = SQLFilterBuilder()
        self._vector_search: VectorSearch | None = None
        if gemini_client is not None:
            self._vector_search = VectorSearch(gemini_client)
        self._relaxation = FilterRelaxation(self._sql_builder, self._geo_resolver)
        self._bot_settings_repo = bot_settings_repo

    async def search_properties(
        self,
        filters: SearchFilters,
        session: AsyncSession,
    ) -> SearchResult:
        """Run a full property search with optional vector fusion and relaxation.

        Pipeline:
        1. If pagination_ids: delegate to get_by_ids
        2. Geo resolve city/barrio aliases + neighbor expansion
        3. SQL query: build and execute
        4. Vector search (if descripcion_libre + vector_search available)
        5. RRF fusion (if both SQL and vector have results)
        6. If 0 results: try filter relaxation
        7. Re-fetch final IDs via pagination query, reorder by fused order
        8. Return SearchResult
        """
        search_start = time.monotonic()
        active_filters = filters.model_dump(exclude_none=True)
        logger.info(
            "Search started — {\"filters\": %s}",
            {k: v for k, v in active_filters.items() if k != "pagination_ids"},
        )

        # Step 1: pagination shortcut
        if filters.pagination_ids:
            return await self.get_by_ids(filters.pagination_ids, session)

        # Step 2: geo resolution
        barrios_list: list[str] | None = None
        if filters.barrio:
            barrios_list = [filters.barrio]
        if filters.barrios:
            barrios_list = (barrios_list or []) + filters.barrios

        geo = self._geo_resolver.resolve(
            city=filters.ciudad,
            barrios=barrios_list,
        )

        # Step 3: SQL query — resolve construction_state column flag
        use_cs_col = False
        if self._bot_settings_repo is not None:
            use_cs_col = await self._bot_settings_repo.get_bool(
                session,
                "m5_construction_state_filter_enabled",
                default=False,
            )
        fq = self._sql_builder.build_query(
            filters, geo=geo, use_construction_state_column=use_cs_col
        )
        result = await session.execute(text(fq.sql), fq.params)
        sql_rows = result.fetchall()
        sql_ids = [row.id for row in sql_rows]
        sql_total_count = sql_rows[0].total_count if sql_rows else 0

        # Step 4: vector search (if descripcion_libre + vector available)
        vector_ids: list[int] = []
        has_vector = False
        if (
            filters.descripcion_libre
            and self._vector_search is not None
        ):
            has_vector = True
            # Pass city filter so vector results respect geographic scope
            vec_where: list[str] = []
            vec_params: dict = {}
            if geo and geo.city_locations:
                city_names = [loc.name for loc in geo.city_locations]
                city_placeholders = []
                for i, cn in enumerate(city_names):
                    key = f"vec_city_{i}"
                    city_placeholders.append(f":{key}")
                    vec_params[key] = cn
                vec_where.append(
                    f"f_unaccent(lower(p.city)) IN ({', '.join(f'f_unaccent(lower({ph}))' for ph in city_placeholders)})"
                )
            vector_ids = await self._vector_search.search(
                description=filters.descripcion_libre,
                session=session,
                extra_where=vec_where or None,
                extra_params=vec_params or None,
            )
            logger.debug(
                "Vector search — {\"query\": \"%.60s\", \"results\": %d}",
                filters.descripcion_libre, len(vector_ids),
            )

        # Step 5: RRF fusion
        if sql_ids and vector_ids:
            fused_ids = reciprocal_rank_fusion(sql_ids, vector_ids)
        elif vector_ids and not sql_ids:
            fused_ids = vector_ids
        else:
            fused_ids = sql_ids

        # Step 6: relaxation on zero results
        if not fused_ids:
            logger.info("No results — attempting filter relaxation")
            relax_result = await self._relaxation.degrade(
                session, filters, geo
            )
            if relax_result is not None:
                elapsed_ms = (time.monotonic() - search_start) * 1000
                logger.info(
                    "Search complete with relaxation (%.0fms) — {\"results\": %d, \"degradation_level\": %d}",
                    elapsed_ms, relax_result.total_count,
                    relax_result.degradation.level if relax_result.degradation else 0,
                )
                return SearchResult(
                    properties=relax_result.properties,
                    total_found=relax_result.total_count,
                    filters_used=active_filters,
                    has_vector_search=has_vector,
                    degradation=relax_result.degradation,
                )
            # Nothing found even with relaxation
            elapsed_ms = (time.monotonic() - search_start) * 1000
            logger.info(
                "Search complete — no results even after relaxation (%.0fms)", elapsed_ms,
            )
            return SearchResult(
                filters_used=active_filters,
                has_vector_search=has_vector,
            )

        # Step 7: re-fetch via pagination query to get full property data
        # Limit to top 50 from the fused list
        top_ids = fused_ids[:50]
        pq = self._sql_builder.build_pagination_query(top_ids)
        re_result = await session.execute(text(pq.sql), pq.params)
        re_rows = re_result.fetchall()

        # Build dict for reordering
        props_by_id: dict[int, dict] = {}
        for row in re_rows:
            props_by_id[row.id] = dict(row._mapping)

        # Reorder by fused order
        ordered_props = [
            props_by_id[pid]
            for pid in top_ids
            if pid in props_by_id
        ]

        # Step 8: price stats (only when no price filters)
        price_stats: dict | None = None
        if (
            ordered_props
            and filters.precio_min is None
            and filters.precio_max is None
        ):
            price_stats = await self.get_price_stats(filters, session, geo=geo)

        elapsed_ms = (time.monotonic() - search_start) * 1000
        sources = dict(Counter(p.get("source") for p in ordered_props))
        logger.info(
            "Search complete (%.0fms) — {\"sql_results\": %d, \"vector_results\": %d, \"fused\": %d, \"returned\": %d, \"has_vector\": %s, \"sources\": %s}",
            elapsed_ms, len(sql_ids), len(vector_ids),
            len(fused_ids), len(ordered_props), has_vector, sources,
        )

        return SearchResult(
            properties=ordered_props,
            total_found=sql_total_count,
            filters_used=active_filters,
            has_vector_search=has_vector,
            price_stats=price_stats,
        )

    async def get_by_ids(
        self,
        ids: list[int],
        session: AsyncSession,
    ) -> SearchResult:
        """Fetch specific properties by their IDs."""
        if not ids:
            return SearchResult()

        fq = self._sql_builder.build_pagination_query(ids)
        result = await session.execute(text(fq.sql), fq.params)
        rows = result.fetchall()

        # Preserve requested order
        props_by_id: dict[int, dict] = {}
        for row in rows:
            props_by_id[row.id] = dict(row._mapping)

        ordered_props = [
            props_by_id[pid]
            for pid in ids
            if pid in props_by_id
        ]

        return SearchResult(
            properties=ordered_props,
            total_found=len(ordered_props),
        )

    async def count_by_barrios(
        self,
        barrios: list[str],
        city: str,
        filters: SearchFilters,
        session: AsyncSession,
    ) -> dict:
        """Count properties per barrio.

        Returns dict mapping barrio_name to:
        {count_total, count_in_budget, min_price_usd}.
        """
        fq = self._sql_builder.build_count_by_barrios_query(
            barrios=barrios,
            city=city,
            filters=filters,
        )
        result = await session.execute(text(fq.sql), fq.params)
        rows = result.fetchall()

        counts: dict[str, dict] = {}
        for row in rows:
            mapping = dict(row._mapping)
            counts[mapping["barrio_name"]] = {
                "count_total": mapping.get("count_total", 0),
                "count_in_budget": mapping.get("count_in_budget", 0),
                "min_price_usd": mapping.get("min_price_usd"),
            }
        return counts

    async def get_price_stats(
        self,
        filters: SearchFilters,
        session: AsyncSession,
        geo: "ResolvedGeo | None" = None,
    ) -> dict | None:
        """Get AVG/MIN/MAX price_usd for a set of filters.

        Returns {"avg_usd": ..., "min_usd": ..., "max_usd": ...} or None
        if no results match the filters.
        """
        clauses: list[str] = ["is_active = true", "price_usd > 0"]
        params: dict = {}

        if filters.operacion:
            clauses.append("operation = :operacion")
            params["operacion"] = filters.operacion

        if filters.tipo:
            tipo_id = _resolve_tipo_to_id(filters.tipo)
            if tipo_id is not None:
                clauses.append(
                    "(property_type_normalized = :tipo_id "
                    "OR (property_type_normalized IS NULL "
                    "AND f_unaccent(lower(property_type)) ILIKE "
                    "f_unaccent(lower(:tipo))))"
                )
                params["tipo_id"] = tipo_id
                params["tipo"] = f"%{filters.tipo}%"
            else:
                clauses.append(
                    "f_unaccent(lower(property_type)) ILIKE "
                    "f_unaccent(lower(:tipo))"
                )
                params["tipo"] = f"%{filters.tipo}%"

        if geo and geo.city_locations:
            city_names = [loc.name for loc in geo.city_locations]
            city_placeholders = []
            for i, cn in enumerate(city_names):
                key = f"ps_city_{i}"
                city_placeholders.append(f"f_unaccent(lower(:{key}))")
                params[key] = cn
            clauses.append(
                f"f_unaccent(lower(city)) IN ({', '.join(city_placeholders)})"
            )
        elif filters.ciudad:
            clauses.append(
                "f_unaccent(lower(city)) ILIKE "
                "'%' || f_unaccent(lower(:ciudad)) || '%'"
            )
            params["ciudad"] = filters.ciudad

        where = " AND ".join(clauses)
        sql = (
            f"SELECT AVG(price_usd) AS avg_usd, "
            f"MIN(price_usd) AS min_usd, "
            f"MAX(price_usd) AS max_usd "
            f"FROM properties WHERE {where}"
        )

        result = await session.execute(text(sql), params)
        row = result.fetchone()

        if row is None or row.avg_usd is None:
            return None

        return {
            "avg_usd": round(float(row.avg_usd), 0),
            "min_usd": round(float(row.min_usd), 0),
            "max_usd": round(float(row.max_usd), 0),
        }
