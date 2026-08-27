"""Panel hybrid search — SQL filters + pgvector fused with RRF (M6.5 T2).

Modo IA del buscador del panel: cuando el parse de Claude devuelve
``descripcion_libre``, el frontend agrega ``ia_query`` a la URL y la route
delega acá en vez de property_service.get_properties.

Pipeline:
  1. SQL leg  — property_repo.list_ids_with_filters (filtros estructurados,
     top-100, mismo ORDER BY que el listado clásico).
  2. Vector leg — VectorSearch (pgvector cosine, top-50) restringido a los
     mismos filtros del panel via extra_where (params prefijados ``pf_`` para
     no colisionar con :query_embedding ni futuros params internos).
  3. reciprocal_rank_fusion(sql_ids, vector_ids) → ranking fusionado.
  4. Paginación EN MEMORIA sobre la lista fusionada; las filas de la página
     se hidratan con list_by_ids (orden preservado).

Degradación: sin GEMINI_API_KEY, con error de construcción del cliente o
con cualquier excepción en el camino vector, el resultado es el SQL puro.
El usuario nunca ve un error técnico.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.search.hybrid_search import reciprocal_rank_fusion
from app.bot.search.vector_search import VectorSearch
from app.repositories.property_repo import (
    _apply_barato,
    _build_filter_sql,
    property_repo,
)
from app.services.property_service import attach_public_paths

logger = logging.getLogger(__name__)

# Queries más cortas que esto no aportan señal semántica → skip vector leg.
_MIN_IA_QUERY_LEN = 4

# Cuántos ids trae el SQL leg antes de fusionar (vector trae 50 fijos).
_SQL_LEG_LIMIT = 100

# Placeholder :name que no es parte de un cast ``::type``.
_PARAM_RE = re.compile(r"(?<!:):([a-zA-Z_]\w*)")

# Lazy singleton — GeminiClient se construye una sola vez por proceso.
# None = aún no intentado; False = intento fallido/sin API key (no reintentar).
_gemini_singleton: object | None = None
_gemini_resolved = False


def _get_gemini():
    """Return a shared GeminiClient or None (missing key / build error).

    Nunca levanta: cualquier problema de configuración degrada el buscador
    a SQL puro sin romper el startup ni el request.
    """
    global _gemini_singleton, _gemini_resolved
    if _gemini_resolved:
        return _gemini_singleton
    try:
        from app.bot.config import bot_settings
        from app.bot.ai.gemini_client import GeminiClient

        if not bot_settings.GEMINI_API_KEY:
            logger.warning(
                "Panel hybrid search: GEMINI_API_KEY vacía — modo IA degradado a SQL puro"
            )
            _gemini_singleton = None
        else:
            _gemini_singleton = GeminiClient(
                api_key=bot_settings.GEMINI_API_KEY,
                embedding_model=bot_settings.GEMINI_EMBEDDING_MODEL,
            )
    except Exception:
        logger.warning(
            "Panel hybrid search: no se pudo construir GeminiClient — SQL puro",
            exc_info=True,
        )
        _gemini_singleton = None
    _gemini_resolved = True
    return _gemini_singleton


def _build_vector_filter(
    filters,  # PropertyFilters
) -> tuple[list[str], dict, str | None]:
    """Translate panel filters into (extra_where, extra_params, cte_sql).

    Reusa _build_filter_sql + _apply_barato (mismo pipeline que el SQL leg)
    y prefija TODOS los placeholders con ``pf_`` — tanto en el WHERE como en
    el CTE de barato — para que no colisionen con los params internos del
    vector query (:query_embedding). El CTE p25 es self-contained, así que
    se pasa tal cual a VectorSearch.cte_sql y ambos legs aplican el mismo cap.
    """
    where, params = _build_filter_sql(filters)
    cte_prefix, where = _apply_barato(filters, where, params)

    prefixed_params = {f"pf_{k}": v for k, v in params.items()}
    where = _PARAM_RE.sub(r":pf_\1", where)
    cte_sql = _PARAM_RE.sub(r":pf_\1", cte_prefix).strip() or None

    return [f"({where})"], prefixed_params, cte_sql


async def search(
    db: AsyncSession,
    filters,  # PropertyFilters
    ia_query: str,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict], int]:
    """Hybrid search for the panel listing. Returns (rows, total_fused)."""
    sql_ids = await property_repo.list_ids_with_filters(
        db, filters, limit=_SQL_LEG_LIMIT
    )

    vector_ids: list[int] = []
    query = (ia_query or "").strip()
    if len(query) >= _MIN_IA_QUERY_LEN:
        gemini = _get_gemini()
        if gemini is not None:
            try:
                extra_where, extra_params, cte_sql = _build_vector_filter(filters)
                vector = VectorSearch(gemini_client=gemini)
                vector_ids = await vector.search(
                    query,
                    db,
                    extra_where=extra_where,
                    extra_params=extra_params,
                    cte_sql=cte_sql,
                )
            except Exception:
                logger.warning(
                    "Panel hybrid search: vector leg falló — degradando a SQL puro"
                    " (ia_query=%r)",
                    query[:80],
                    exc_info=True,
                )
                vector_ids = []

    fused = reciprocal_rank_fusion(sql_ids, vector_ids)
    total = len(fused)
    page_ids = fused[(page - 1) * per_page : page * per_page]
    rows = await property_repo.list_by_ids(db, page_ids)
    return attach_public_paths(rows), total
