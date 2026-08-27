"""TDD Phase 1 — property_types catalog table and property_type_normalized column.

Tests MUST fail before implementation (RED), pass after (GREEN).
All tests run against onnix_dev — NEVER production.
"""
import pytest
from sqlalchemy import text


class TestPropertyTypesTable:
    """Verify property_types catalog table exists and has correct data."""

    @pytest.mark.asyncio
    async def test_property_types_table_has_11_rows(self, db_session):
        """property_types table must have exactly 11 type entries."""
        result = await db_session.execute(
            text("SELECT COUNT(*) FROM property_types")
        )
        assert result.scalar() == 11

    @pytest.mark.asyncio
    async def test_property_types_codes_match_catalog(self, db_session):
        """All 11 expected codes exist in property_types."""
        result = await db_session.execute(
            text("SELECT code FROM property_types ORDER BY id")
        )
        codes = [row[0] for row in result.fetchall()]
        expected = [
            "CASA", "DEPARTAMENTO", "DUPLEX", "TERRENO", "OFICINA",
            "LOCAL", "DEPOSITO", "QUINTA", "CAMPO", "EDIFICIO", "OTRO"
        ]
        assert codes == expected

    @pytest.mark.asyncio
    async def test_property_type_normalized_column_exists(self, db_session):
        """properties.property_type_normalized column exists and is nullable."""
        result = await db_session.execute(
            text(
                "SELECT column_name, is_nullable, data_type "
                "FROM information_schema.columns "
                "WHERE table_name = 'properties' "
                "AND column_name = 'property_type_normalized'"
            )
        )
        row = result.first()
        assert row is not None, "Column property_type_normalized not found"
        assert row.is_nullable == "YES"
        assert row.data_type == "integer"

    @pytest.mark.asyncio
    async def test_property_type_normalized_fk(self, db_session):
        """property_type_normalized has FK to property_types.id."""
        result = await db_session.execute(
            text(
                "SELECT constraint_name FROM information_schema.table_constraints "
                "WHERE table_name = 'properties' "
                "AND constraint_type = 'FOREIGN KEY' "
                "AND constraint_name LIKE '%property_type_normalized%'"
            )
        )
        assert result.first() is not None

    @pytest.mark.asyncio
    async def test_property_type_normalized_index_exists(self, db_session):
        """Partial btree index on property_type_normalized exists."""
        result = await db_session.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'properties' "
                "AND indexname = 'idx_properties_type_normalized'"
            )
        )
        assert result.first() is not None
