"""Tests for app/repositories/inquiry_history_repo.py"""
import pytest
from datetime import datetime, timezone
from sqlalchemy import text

from app.repositories.inquiry_history_repo import inquiry_history_repo


class TestGetByContact:
    async def test_returns_empty_if_no_history(self, db):
        result = await inquiry_history_repo.get_by_contact(db, contact_id=999999)
        assert result == []

    async def test_returns_entries_ordered_by_archived_at_desc(self, db):
        """If entries exist for a contact, they come back newest-first."""
        try:
            # Clean up any leftover rows from previous runs to keep assertions deterministic
            await db.execute(text("DELETE FROM infocasas_inquiry_history WHERE contact_id = 90001"))
            await db.execute(text(
                "INSERT INTO contacts (id, name, source, status, created_at)"
                " VALUES (90001, 'Hist Test', 'infocasas', 'new', NOW())"
                " ON CONFLICT (id) DO NOTHING"
            ))
            await db.execute(text(
                "INSERT INTO infocasas_inquiry_history (contact_id, infocasas_ref, consulta_id, property_title, archived_at)"
                " VALUES (90001, 'REF_A', 'C001', 'Casa A', '2026-01-01 10:00:00+00')"
            ))
            await db.execute(text(
                "INSERT INTO infocasas_inquiry_history (contact_id, infocasas_ref, consulta_id, property_title, archived_at)"
                " VALUES (90001, 'REF_B', 'C002', 'Casa B', '2026-02-01 10:00:00+00')"
            ))
            await db.flush()

            result = await inquiry_history_repo.get_by_contact(db, 90001)
            assert len(result) == 2
            assert result[0].infocasas_ref == "REF_B"
            assert result[1].infocasas_ref == "REF_A"
        finally:
            await db.execute(text("DELETE FROM infocasas_inquiry_history WHERE contact_id = 90001"))
            await db.execute(text("DELETE FROM contacts WHERE id = 90001"))
            await db.flush()

    async def test_create_saves_all_fields(self, db):
        """create() persists all fields correctly."""
        try:
            await db.execute(text("DELETE FROM infocasas_inquiry_history WHERE contact_id = 90002"))
            await db.execute(text(
                "INSERT INTO contacts (id, name, source, status, created_at)"
                " VALUES (90002, 'Create Test', 'infocasas', 'new', NOW())"
                " ON CONFLICT (id) DO NOTHING"
            ))
            await db.flush()

            entry = await inquiry_history_repo.create(
                db,
                contact_id=90002,
                infocasas_ref="REF_X",
                consulta_id="C999",
                consulta_date=datetime(2026, 3, 15, tzinfo=timezone.utc),
                property_title="Depto en Lambare",
                archived_at=datetime(2026, 4, 16, tzinfo=timezone.utc),
            )
            assert entry.id is not None
            assert entry.contact_id == 90002
            assert entry.infocasas_ref == "REF_X"
            assert entry.consulta_id == "C999"
            assert entry.property_title == "Depto en Lambare"
            assert entry.consulta_date == datetime(2026, 3, 15, tzinfo=timezone.utc)
            assert entry.archived_at == datetime(2026, 4, 16, tzinfo=timezone.utc)
        finally:
            await db.execute(text("DELETE FROM infocasas_inquiry_history WHERE contact_id = 90002"))
            await db.execute(text("DELETE FROM contacts WHERE id = 90002"))
            await db.flush()
