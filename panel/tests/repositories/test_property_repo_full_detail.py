"""TDD — property_repo.get_full_detail (real DB)

These tests hit the dev database directly to catch SQL/schema drift
that mocked tests miss (e.g., a SELECT referencing a column that does
not exist anymore).
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.repositories.property_repo import PropertyRepository


@pytest.mark.asyncio
class TestGetFullDetailHitsRealSchema:
    async def test_get_full_detail_returns_none_for_missing_id(self, db):
        result = await PropertyRepository.get_full_detail(db, -1)
        assert result is None

    async def test_get_full_detail_returns_dict_for_existing_property(self, db):
        # Pick any active property currently in the dev DB; the test only
        # cares that the SELECT executes without column errors.
        row = (
            await db.execute(
                text("SELECT MIN(id) AS id FROM properties WHERE is_active = true")
            )
        ).mappings().one()
        property_id = row["id"]
        if property_id is None:
            pytest.skip("dev DB has no active properties to test against")

        result = await PropertyRepository.get_full_detail(db, property_id)

        assert result is not None
        assert result["id"] == property_id
        # Every column the detail template reads must be present in the result
        # so a column-rename in the schema breaks this test immediately.
        for required_field in (
            "source", "external_id", "title", "url",
            "price_usd", "price_pyg", "price_currency",
            "operation", "property_type",
            "city", "neighborhood",
            "bedrooms", "bathrooms", "parking", "total_area_m2",
            "construction_state", "description",
            "agent_name", "agent_phone", "agent_whatsapp",
            "is_active", "on_hold",
            "local_image_count", "main_image_url",
            "created_at", "updated_at", "last_scraped_at",
            "portal_listed_at", "portal_expires_at",
        ):
            assert required_field in result, f"missing column: {required_field}"
