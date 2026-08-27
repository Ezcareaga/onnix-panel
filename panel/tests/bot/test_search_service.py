"""Tests for SearchService — orchestrator for property search.

Tests cover:
- SearchResult dataclass defaults and construction (3 tests)
- SearchService unit tests with mocked dependencies (6 tests)
- SearchService DB integration tests against onnix_dev (6 tests)
"""
import os


from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

from app.bot.search.relaxation import DegradationInfo
from app.bot.search.search_service import SearchResult, SearchService
from app.bot.search.sql_filters import SearchFilters


# ===========================================================================
# TestSearchResult — dataclass validation (3 tests)
# ===========================================================================


class TestSearchResult:
    """Validate SearchResult dataclass fields and defaults."""

    def test_search_result_default(self):
        """Empty SearchResult has sensible defaults."""
        result = SearchResult()
        assert result.properties == []
        assert result.total_found == 0
        assert result.filters_used == {}
        assert result.has_vector_search is False
        assert result.degradation is None

    def test_search_result_with_degradation(self):
        """SearchResult can carry a DegradationInfo."""
        deg = DegradationInfo(
            level=2,
            description="Sin filtro de dormitorios",
        )
        result = SearchResult(
            properties=[{"id": 1}],
            total_found=1,
            degradation=deg,
        )
        assert result.degradation is not None
        assert result.degradation.level == 2
        assert result.total_found == 1

    def test_search_result_has_vector_flag(self):
        """SearchResult has_vector_search flag set correctly."""
        result = SearchResult(
            properties=[{"id": 1}, {"id": 2}],
            total_found=2,
            has_vector_search=True,
        )
        assert result.has_vector_search is True
        assert len(result.properties) == 2


# ===========================================================================
# TestSearchServiceUnit — mocked dependency tests (6 tests)
# ===========================================================================


class TestSearchServiceUnit:
    """Test SearchService logic with mocked GeoResolver, SQLFilterBuilder, etc."""

    def setup_method(self):
        self.mock_gemini = MagicMock()

    def _make_service(self):
        """Build a SearchService with real dependencies (mocked at call level)."""
        return SearchService(gemini_client=self.mock_gemini)

    @pytest.mark.asyncio
    async def test_search_calls_geo_resolver(self):
        """search_properties calls geo_resolver.resolve() with city."""
        service = self._make_service()
        filters = SearchFilters(operacion="venta", ciudad="asuncion")
        mock_session = AsyncMock()

        # Mock the SQL execution to return some rows
        mock_result = MagicMock()
        mock_row = MagicMock()
        mock_row._mapping = {"id": 1, "title": "Test"}
        mock_result.fetchall.return_value = [mock_row]
        mock_session.execute.return_value = mock_result

        with patch.object(
            service._geo_resolver, "resolve", wraps=service._geo_resolver.resolve
        ) as mock_resolve:
            await service.search_properties(filters, mock_session)
            mock_resolve.assert_called_once()
            call_kwargs = mock_resolve.call_args
            # Should pass city="asuncion"
            assert call_kwargs.kwargs.get("city") == "asuncion" or (
                call_kwargs.args and call_kwargs.args[0] == "asuncion"
            )

    @pytest.mark.asyncio
    async def test_search_calls_sql_builder(self):
        """search_properties calls sql_builder.build_query()."""
        service = self._make_service()
        filters = SearchFilters(operacion="venta")
        mock_session = AsyncMock()

        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_session.execute.return_value = mock_result

        with patch.object(
            service._sql_builder, "build_query", wraps=service._sql_builder.build_query
        ) as mock_build:
            await service.search_properties(filters, mock_session)
            mock_build.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_calls_vector_when_descripcion_libre(self):
        """VectorSearch.search() called when descripcion_libre is set."""
        service = self._make_service()
        filters = SearchFilters(
            operacion="venta",
            descripcion_libre="piscina grande con jardin",
        )
        mock_session = AsyncMock()

        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_session.execute.return_value = mock_result

        if service._vector_search is not None:
            with patch.object(
                service._vector_search,
                "search",
                new_callable=AsyncMock,
                return_value=[10, 20, 30],
            ) as mock_vs:
                await service.search_properties(filters, mock_session)
                mock_vs.assert_called_once()
        else:
            pytest.skip("VectorSearch not available (no gemini client)")

    @pytest.mark.asyncio
    async def test_search_no_vector_without_descripcion_libre(self):
        """VectorSearch.search() NOT called without descripcion_libre."""
        service = self._make_service()
        filters = SearchFilters(operacion="venta", ciudad="asuncion")
        mock_session = AsyncMock()

        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_session.execute.return_value = mock_result

        if service._vector_search is not None:
            with patch.object(
                service._vector_search,
                "search",
                new_callable=AsyncMock,
            ) as mock_vs:
                await service.search_properties(filters, mock_session)
                mock_vs.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_calls_relaxation_on_zero_results(self):
        """FilterRelaxation.degrade() called when SQL returns 0 results."""
        service = self._make_service()
        filters = SearchFilters(operacion="venta", dormitorios_min=10)
        mock_session = AsyncMock()

        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_session.execute.return_value = mock_result

        with patch.object(
            service._relaxation,
            "degrade",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_degrade:
            await service.search_properties(filters, mock_session)
            mock_degrade.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_skips_relaxation_on_results(self):
        """FilterRelaxation.degrade() NOT called when results exist."""
        service = self._make_service()
        filters = SearchFilters(operacion="venta")
        mock_session = AsyncMock()

        mock_result = MagicMock()
        mock_row = MagicMock()
        mock_row._mapping = {"id": 1, "title": "Test"}
        mock_result.fetchall.return_value = [mock_row]
        mock_session.execute.return_value = mock_result

        with patch.object(
            service._relaxation,
            "degrade",
            new_callable=AsyncMock,
        ) as mock_degrade:
            await service.search_properties(filters, mock_session)
            mock_degrade.assert_not_called()


# ===========================================================================
# TestSearchServiceDB — integration tests against onnix_dev (6 tests)
# ===========================================================================


class TestSearchServiceDB:
    """Execute SearchService against onnix_dev with real data."""

    def setup_method(self):
        # No gemini client for integration tests — no vector search
        self.service = SearchService(gemini_client=None)

    @pytest.mark.asyncio
    async def test_search_venta_asuncion(self, db_session):
        """Search venta in Asuncion returns at least 1 property."""
        filters = SearchFilters(operacion="venta", ciudad="asuncion")
        result = await self.service.search_properties(filters, db_session)
        assert result.total_found >= 1
        assert len(result.properties) >= 1

    @pytest.mark.asyncio
    async def test_search_returns_max_50(self, db_session):
        """Broad search returns at most 50 properties."""
        filters = SearchFilters(operacion="venta")
        result = await self.service.search_properties(filters, db_session)
        assert len(result.properties) <= 50

    @pytest.mark.asyncio
    async def test_search_all_active_no_duplicates(self, db_session):
        """All returned properties are active and non-duplicate."""
        filters = SearchFilters(operacion="venta", ciudad="asuncion")
        result = await self.service.search_properties(filters, db_session)
        if result.total_found == 0:
            pytest.skip("No results to verify")

        returned_ids = [p["id"] for p in result.properties]
        check = await db_session.execute(
            text(
                "SELECT id, is_active, duplicate_of FROM properties "
                "WHERE id = ANY(:ids)"
            ),
            {"ids": returned_ids},
        )
        for row in check.fetchall():
            assert row.is_active is True, f"Property {row.id} not active"
            assert row.duplicate_of is None, f"Property {row.id} is duplicate"

    @pytest.mark.asyncio
    async def test_get_by_ids(self, db_session):
        """get_by_ids returns exactly the requested properties."""
        # Get 3 real property IDs
        pre = await db_session.execute(
            text(
                "SELECT id FROM properties "
                "WHERE is_active = true LIMIT 3"
            )
        )
        real_ids = [row.id for row in pre.fetchall()]
        assert len(real_ids) == 3

        result = await self.service.get_by_ids(real_ids, db_session)
        assert result.total_found == 3
        returned_ids = {p["id"] for p in result.properties}
        assert returned_ids == set(real_ids)

    @pytest.mark.asyncio
    async def test_count_by_barrios(self, db_session):
        """count_by_barrios returns dict with counts for villa morra and recoleta."""
        filters = SearchFilters(operacion="venta")
        counts = await self.service.count_by_barrios(
            barrios=["villa morra", "recoleta"],
            city="asuncion",
            filters=filters,
            session=db_session,
        )
        assert isinstance(counts, dict)
        # Should have entries for the requested barrios
        assert len(counts) > 0

    @pytest.mark.asyncio
    async def test_search_with_excluded_ids(self, db_session):
        """Two searches with excluded_ids produce no overlapping results."""
        filters = SearchFilters(operacion="venta", ciudad="asuncion")
        result1 = await self.service.search_properties(filters, db_session)
        if result1.total_found == 0:
            pytest.skip("No results for exclusion test")

        first_ids = [p["id"] for p in result1.properties]
        filters2 = SearchFilters(
            operacion="venta",
            ciudad="asuncion",
            excluded_ids=first_ids,
        )
        result2 = await self.service.search_properties(filters2, db_session)
        second_ids = {p["id"] for p in result2.properties}
        overlap = set(first_ids) & second_ids
        assert len(overlap) == 0, f"Overlapping IDs: {overlap}"


# ===========================================================================
# TestPriceStats — price statistics (2 tests)
# ===========================================================================


class TestPriceStats:
    """Test price_stats population on SearchResult."""

    def setup_method(self):
        self.mock_gemini = MagicMock()

    def _make_service(self):
        return SearchService(gemini_client=self.mock_gemini)

    @pytest.mark.asyncio
    async def test_price_stats_returned_when_no_price_filter(self):
        """price_stats is populated when no precio_min/precio_max filters are set."""
        service = self._make_service()
        filters = SearchFilters(operacion="venta", ciudad="asuncion")
        mock_session = AsyncMock()

        # Mock SQL search result (step 3) — returns 1 row with id and total_count
        mock_search_row = MagicMock()
        mock_search_row.id = 1
        mock_search_row.total_count = 5
        mock_search_result = MagicMock()
        mock_search_result.fetchall.return_value = [mock_search_row]

        # Mock re-fetch result (step 7) — property data
        mock_refetch_row = MagicMock()
        mock_refetch_row.id = 1
        mock_refetch_row._mapping = {"id": 1, "title": "Casa test", "price_usd": 150000}
        mock_refetch_result = MagicMock()
        mock_refetch_result.fetchall.return_value = [mock_refetch_row]

        # Mock price stats result (step 8)
        mock_stats_row = MagicMock()
        mock_stats_row.avg_usd = 180000.0
        mock_stats_row.min_usd = 100000.0
        mock_stats_row.max_usd = 300000.0
        mock_stats_result = MagicMock()
        mock_stats_result.fetchone.return_value = mock_stats_row

        # session.execute returns different results for each call
        mock_session.execute.side_effect = [
            mock_search_result,   # step 3: SQL search
            mock_refetch_result,  # step 7: re-fetch
            mock_stats_result,    # step 8: price stats
        ]

        result = await service.search_properties(filters, mock_session)

        assert result.price_stats is not None
        assert result.price_stats["avg_usd"] == 180000.0
        assert result.price_stats["min_usd"] == 100000.0
        assert result.price_stats["max_usd"] == 300000.0

    @pytest.mark.asyncio
    async def test_price_stats_none_when_price_filter_set(self):
        """price_stats is None when precio_max is set."""
        service = self._make_service()
        filters = SearchFilters(
            operacion="venta", ciudad="asuncion", precio_max=200000,
        )
        mock_session = AsyncMock()

        # Mock SQL search result
        mock_search_row = MagicMock()
        mock_search_row.id = 1
        mock_search_row.total_count = 3
        mock_search_result = MagicMock()
        mock_search_result.fetchall.return_value = [mock_search_row]

        # Mock re-fetch result
        mock_refetch_row = MagicMock()
        mock_refetch_row.id = 1
        mock_refetch_row._mapping = {"id": 1, "title": "Casa test", "price_usd": 150000}
        mock_refetch_result = MagicMock()
        mock_refetch_result.fetchall.return_value = [mock_refetch_row]

        mock_session.execute.side_effect = [
            mock_search_result,
            mock_refetch_result,
        ]

        result = await service.search_properties(filters, mock_session)

        # price_stats should be None because precio_max is set
        assert result.price_stats is None
        # session.execute should have been called only twice (search + re-fetch),
        # NOT a third time for price stats
        assert mock_session.execute.call_count == 2
