"""Tests for VectorSearch — pgvector cosine-similarity search.

Tests cover:
- VectorSearch unit tests — SQL string + params inspection (8 tests)
- VectorSearch DB integration tests — execute against onnix_dev (3 tests)
"""
import os


import logging

import pytest
from sqlalchemy import text
from unittest.mock import AsyncMock, MagicMock

from app.bot.search.vector_search import VectorSearch
from app.bot.search.sql_filters import FilteredQuery


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_gemini(embedding: list[float] | None = None) -> MagicMock:
    """Create a mocked GeminiClient with generate_embedding."""
    mock = MagicMock()
    if embedding is None:
        embedding = [0.1] * 768
    mock.generate_embedding = AsyncMock(return_value=embedding)
    return mock


# ===========================================================================
# TestVectorSearch — unit tests (8 tests, mocked GeminiClient)
# ===========================================================================


class TestVectorSearch:
    """Unit tests for VectorSearch query construction and search flow."""

    def setup_method(self):
        self.mock_gemini = _make_mock_gemini()
        self.vs = VectorSearch(self.mock_gemini)

    # 1. test_build_vector_query_base
    def test_build_vector_query_base(self):
        """build_vector_query SQL contains <=> operator and NOT NULL filter."""
        embedding = [0.1] * 768
        fq = self.vs.build_vector_query(embedding)
        sql_lower = fq.sql.lower()
        assert "description_embedding <=>" in fq.sql
        assert "description_embedding is not null" in sql_lower

    # 2. test_build_vector_query_includes_base_where
    def test_build_vector_query_includes_base_where(self):
        """Generated SQL contains is_active = true and duplicate_of IS NULL."""
        embedding = [0.1] * 768
        fq = self.vs.build_vector_query(embedding)
        sql_lower = fq.sql.lower()
        assert "is_active = true" in sql_lower
        assert "duplicate_of is null" in sql_lower

    # 3. test_build_vector_query_with_extra_filters
    def test_build_vector_query_with_extra_filters(self):
        """Extra WHERE clauses are included in the vector query."""
        embedding = [0.1] * 768
        extra_where = [
            "f_unaccent(lower(p.operation)) = f_unaccent(lower(:operacion))",
            "p.price_usd <= :precio_max",
        ]
        extra_params = {"operacion": "venta", "precio_max": 200000}
        fq = self.vs.build_vector_query(
            embedding, extra_where=extra_where, extra_params=extra_params
        )
        assert ":operacion" in fq.sql
        assert ":precio_max" in fq.sql
        assert fq.params["operacion"] == "venta"
        assert fq.params["precio_max"] == 200000

    # 4. test_build_vector_query_limit_50
    def test_build_vector_query_limit_50(self):
        """Vector query uses LIMIT 50 for RRF fusion pool."""
        embedding = [0.1] * 768
        fq = self.vs.build_vector_query(embedding)
        assert "LIMIT 50" in fq.sql

    # 5. test_build_vector_query_embedding_param
    def test_build_vector_query_embedding_param(self):
        """Embedding is passed as :query_embedding param, never interpolated."""
        embedding = [0.1] * 768
        fq = self.vs.build_vector_query(embedding)
        assert ":query_embedding" in fq.sql
        assert "query_embedding" in fq.params
        # The param value should be a string representation, not the raw list
        param_value = fq.params["query_embedding"]
        assert isinstance(param_value, str)
        assert param_value.startswith("[")
        assert param_value.endswith("]")

    # 6. test_search_calls_gemini_embedding
    @pytest.mark.asyncio
    async def test_search_calls_gemini_embedding(self):
        """search() calls gemini_client.generate_embedding with the description."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        await self.vs.search(
            "casa con piscina y jardin", mock_session
        )
        self.mock_gemini.generate_embedding.assert_awaited_once_with(
            "casa con piscina y jardin"
        )

    # 7. test_search_returns_property_ids_ordered
    @pytest.mark.asyncio
    async def test_search_returns_property_ids_ordered(self):
        """search() returns property IDs in the order returned by DB."""
        # Mock rows with id attribute
        row1 = MagicMock()
        row1.id = 5
        row2 = MagicMock()
        row2.id = 3
        row3 = MagicMock()
        row3.id = 8

        mock_result = MagicMock()
        mock_result.fetchall.return_value = [row1, row2, row3]

        mock_session = MagicMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        ids = await self.vs.search(
            "casa con piscina grande", mock_session
        )
        assert ids == [5, 3, 8]

    # 8. test_search_handles_embedding_error_gracefully
    @pytest.mark.asyncio
    async def test_search_handles_embedding_error_gracefully(self, caplog):
        """Embedding generation failure returns empty list, no crash."""
        self.mock_gemini.generate_embedding = AsyncMock(
            side_effect=RuntimeError("Gemini API down")
        )
        mock_session = MagicMock()

        with caplog.at_level(logging.ERROR):
            ids = await self.vs.search(
                "departamento luminoso", mock_session
            )
        assert ids == []
        # Verify error was logged
        assert any("embedding" in r.message.lower() for r in caplog.records)


# ===========================================================================
# TestVectorSearchDB — integration tests against onnix_dev (3 tests)
# ===========================================================================


class TestVectorSearchDB:
    """Execute vector queries against onnix_dev."""

    def setup_method(self):
        self.mock_gemini = _make_mock_gemini()
        self.vs = VectorSearch(self.mock_gemini)

    @pytest.mark.asyncio
    async def test_vector_query_executes_without_error(self, db_session):
        """Build and execute a vector query with dummy 768-dim embedding."""
        embedding = [0.1] * 768
        fq = self.vs.build_vector_query(embedding)
        # Should not raise even if 0 results
        result = await db_session.execute(text(fq.sql), fq.params)
        rows = result.fetchall()
        # Rows may be 0 if no embeddings exist yet — that's OK
        assert isinstance(rows, list)

    @pytest.mark.asyncio
    async def test_vector_query_excludes_null_embeddings(self, db_session):
        """No row in results has NULL description_embedding."""
        embedding = [0.1] * 768
        fq = self.vs.build_vector_query(embedding)
        result = await db_session.execute(text(fq.sql), fq.params)
        rows = result.fetchall()
        if len(rows) > 0:
            # Verify returned IDs have non-null embedding
            returned_ids = [row.id for row in rows]
            check = await db_session.execute(
                text(
                    "SELECT id, description_embedding IS NOT NULL AS has_emb "
                    "FROM properties WHERE id = ANY(:ids)"
                ),
                {"ids": returned_ids},
            )
            for r in check.fetchall():
                assert r.has_emb is True

    @pytest.mark.asyncio
    async def test_short_description_skipped(self):
        """Short description (<=3 chars) returns empty list without calling gemini."""
        mock_session = MagicMock()
        ids = await self.vs.search("ab", mock_session)
        assert ids == []
        self.mock_gemini.generate_embedding.assert_not_awaited()
