"""Tests for Alembic migration 022: infocasas_inquiry_history + trigger cleanup.

Verifies the table exists with correct columns, FK, index, and that the
inert enforce_baja_terminal trigger was removed.
"""
import pytest
from sqlalchemy import text


class TestInquiryHistoryTable:
    async def test_table_exists(self, db):
        result = await db.execute(text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.tables"
            "  WHERE table_name = 'infocasas_inquiry_history'"
            ")"
        ))
        assert result.scalar() is True

    async def test_columns_correct(self, db):
        result = await db.execute(text(
            "SELECT column_name, is_nullable"
            " FROM information_schema.columns"
            " WHERE table_name = 'infocasas_inquiry_history'"
            " ORDER BY ordinal_position"
        ))
        rows = result.fetchall()
        columns = {r[0]: r[1] for r in rows}
        assert "id" in columns
        assert "contact_id" in columns
        assert "infocasas_ref" in columns
        assert "consulta_id" in columns
        assert "consulta_date" in columns
        assert "property_title" in columns
        assert "archived_at" in columns
        assert columns["contact_id"] == "NO"
        assert columns["infocasas_ref"] == "NO"
        assert columns["archived_at"] == "NO"

    async def test_fk_to_contacts(self, db):
        result = await db.execute(text(
            "SELECT tc.constraint_type"
            " FROM information_schema.table_constraints tc"
            " JOIN information_schema.key_column_usage kcu"
            "   ON tc.constraint_name = kcu.constraint_name"
            " WHERE tc.table_name = 'infocasas_inquiry_history'"
            "   AND kcu.column_name = 'contact_id'"
            "   AND tc.constraint_type = 'FOREIGN KEY'"
        ))
        assert result.fetchone() is not None

    async def test_index_on_contact_id(self, db):
        result = await db.execute(text(
            "SELECT indexname FROM pg_indexes"
            " WHERE tablename = 'infocasas_inquiry_history'"
            "   AND indexname = 'idx_inquiry_history_contact'"
        ))
        assert result.fetchone() is not None


class TestEnforceBajaTerminalRemoved:
    async def test_trigger_does_not_exist(self, db):
        result = await db.execute(text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.triggers"
            "  WHERE trigger_name = 'enforce_baja_terminal'"
            ")"
        ))
        assert result.scalar() is False

    async def test_function_does_not_exist(self, db):
        result = await db.execute(text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM pg_proc WHERE proname = 'prevent_baja_reversal'"
            ")"
        ))
        assert result.scalar() is False
