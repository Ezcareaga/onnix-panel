"""VectorSearch — semantic search via pgvector cosine similarity.

Uses GeminiClient to generate query embeddings and searches the pgvector
HNSW index for semantically similar properties. Results are combined with
base SQL filters (is_active, duplicate_of IS NULL) and optionally with
geo/price filters passed as extra_where clauses.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import text

from .sql_filters import FilteredQuery

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.bot.ai.gemini_client import GeminiClient

logger = logging.getLogger(__name__)

# Same columns as sql_filters._SELECT_COLUMNS + vector_distance
_VECTOR_SELECT = (
    "p.id, p.source, p.external_id, p.title, p.description, "
    "p.price_usd, p.price_pyg, p.price_currency, p.city, p.neighborhood, "
    "p.operation, p.property_type, p.bedrooms, p.bathrooms, "
    "p.total_area_m2, p.built_area_m2, p.main_image_url, "
    "p.local_image_count, p.address, p.latitude, p.longitude, "
    "description_embedding <=> CAST(:query_embedding AS vector) "
    "AS vector_distance"
)


class VectorSearch:
    """Semantic property search using pgvector cosine distance."""

    def __init__(self, gemini_client: GeminiClient) -> None:
        self._gemini = gemini_client

    def build_vector_query(
        self,
        embedding: list[float],
        extra_where: list[str] | None = None,
        extra_params: dict | None = None,
        cte_sql: str | None = None,
    ) -> FilteredQuery:
        """Build a pgvector cosine-distance query.

        Returns a FilteredQuery ready for sqlalchemy.text() execution.
        The embedding is serialized as a string ``[v1, v2, ...]`` so that
        pgvector's ``::vector`` / ``CAST(... AS vector)`` can parse it.
        """
        params: dict = {}

        # Serialize embedding as pgvector-compatible string
        embedding_str = "[" + ", ".join(str(v) for v in embedding) + "]"
        params["query_embedding"] = embedding_str

        # Base WHERE clauses (always present)
        where_parts: list[str] = [
            "p.is_active = true",
            "p.duplicate_of IS NULL",
            "p.description_embedding IS NOT NULL",
        ]

        # Append caller-provided extra filters
        if extra_where:
            where_parts.extend(extra_where)

        if extra_params:
            params.update(extra_params)

        where_clause = " AND ".join(where_parts)

        # Build full SQL
        prefix = f"{cte_sql}\n" if cte_sql else ""
        sql = (
            f"{prefix}"
            f"SELECT {_VECTOR_SELECT}\n"
            f"FROM properties p\n"
            f"WHERE {where_clause}\n"
            f"ORDER BY vector_distance ASC\n"
            f"LIMIT 50"
        )

        return FilteredQuery(sql=sql, params=params)

    async def search(
        self,
        description: str,
        session: AsyncSession,
        extra_where: list[str] | None = None,
        extra_params: dict | None = None,
        cte_sql: str | None = None,
    ) -> list[int]:
        """Search for properties semantically similar to *description*.

        Returns a list of property IDs ordered by cosine similarity
        (ascending distance). Returns an empty list on errors or when
        the description is too short.
        """
        if len(description.strip()) <= 3:
            return []

        # Generate query embedding
        try:
            embedding = await self._gemini.generate_embedding(description)
        except Exception:
            logger.error(
                "Failed to generate embedding for description: %s",
                description[:80],
            )
            return []

        # Build and execute the vector query
        fq = self.build_vector_query(
            embedding,
            extra_where=extra_where,
            extra_params=extra_params,
            cte_sql=cte_sql,
        )
        result = await session.execute(text(fq.sql), fq.params)
        return [row.id for row in result.fetchall()]
