"""Integration — barato P25 against onnix_dev (real DB).

Uses the shared `db` fixture from tests/conftest.py (NullPool session against
onnix_dev, never production). Verifies the CTE executes on real PostgreSQL
(PERCENTILE_CONT + CAST NULL-checks with asyncpg) and that every returned row
respects the independently-computed P25 cap, with count == list semantics.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.repositories.property_repo import PropertyRepository
from app.services.property_service import PropertyFilters


class TestBaratoP25Integration:
    async def test_barato_p25_executes_and_caps_price(self, db):
        filters = PropertyFilters(barato=True, operation="venta")
        rows = await PropertyRepository.list_with_filters(
            db, filters, limit=50, offset=0
        )
        total = await PropertyRepository.count_with_filters(db, filters)

        # P25 calculado de forma independiente (misma poblacion que el CTE)
        p25 = (
            await db.execute(
                text(
                    "SELECT PERCENTILE_CONT(0.25) WITHIN GROUP"
                    " (ORDER BY price_usd)"
                    " FROM properties"
                    " WHERE is_active = TRUE AND price_usd IS NOT NULL"
                    "   AND operation = 'venta'"
                )
            )
        ).scalar()
        if p25 is None:
            pytest.skip("onnix_dev sin propiedades venta con price_usd")

        assert total >= len(rows)
        for row in rows:
            assert row["price_usd"] is not None
            assert float(row["price_usd"]) <= float(p25)
