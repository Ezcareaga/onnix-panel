"""Integration tests for ContactReminderRepository (C2.2).

Runs against onnix_dev. Tests: create, list_by_contact, list_due,
count_overdue_for_contacts, mark_done, delete.

NOTE: These tests require migration 044 to be applied first:
    alembic upgrade 044_contact_reminders
The test suite skips gracefully if the table does not yet exist.
"""
from __future__ import annotations

import os
import random
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text as sa_text
from sqlalchemy.exc import ProgrammingError

from app.models.contact import Contact
from app.models.contact_reminder import ContactReminder
from app.repositories.contact_reminder_repo import contact_reminder_repo

pytestmark = pytest.mark.asyncio


def _table_exists_check() -> bool:
    """Return True if contact_reminders table exists in onnix_dev."""
    import subprocess
    result = subprocess.run(
        ["docker", "exec", "onnix-postgres",
         "psql", "-U", "onnix", "-d", os.environ["POSTGRES_DB"],
         "-tAc", "SELECT to_regclass('public.contact_reminders')::text"],
        capture_output=True, text=True, timeout=10,
    )
    out = result.stdout.strip()
    # to_regclass returns NULL (printed as empty or '\N') when missing
    return bool(out) and out not in ("", "\\N", "NULL")


_TABLE_EXISTS = _table_exists_check()

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not _TABLE_EXISTS,
        reason="contact_reminders table not yet created — run alembic upgrade 044_contact_reminders",
    ),
]


def _phone() -> str:
    return f"+5959815{random.randint(100_000, 999_999)}"


def _email() -> str:
    return f"pytest_reminder_{random.randint(10000, 99999)}@onnixtest.com"


@pytest_asyncio.fixture
async def reminder_contact(db):
    """Create a minimal contact + user for reminder tests."""
    # Ensure at least one user exists (id=1 works for user_id FK; use admin)
    result = await db.execute(sa_text("SELECT id FROM users LIMIT 1"))
    row = result.fetchone()
    assert row is not None, "No users in test DB — run migrations"
    user_id = row[0]

    contact = Contact(
        phone=_phone(), source="manual", status="new",
        name="ReminderTest Contact",
        created_at=datetime.now(timezone.utc),
    )
    db.add(contact)
    await db.commit()
    await db.refresh(contact)
    return {"contact": contact, "user_id": user_id}


class TestCreateAndList:

    async def test_create_and_list_by_contact(self, db, reminder_contact):
        cid = reminder_contact["contact"].id
        uid = reminder_contact["user_id"]
        due = datetime.now(timezone.utc) + timedelta(days=2)

        r = await contact_reminder_repo.create(
            db, contact_id=cid, user_id=uid, due_at=due, note="Test reminder"
        )
        await db.commit()

        assert r.id is not None
        assert r.contact_id == cid
        assert r.done_at is None

        reminders = await contact_reminder_repo.list_by_contact(db, cid)
        assert any(rem.id == r.id for rem in reminders)

    async def test_multiple_reminders_ordered(self, db, reminder_contact):
        cid = reminder_contact["contact"].id
        uid = reminder_contact["user_id"]
        now = datetime.now(timezone.utc)

        # Create two: one due sooner, one later
        r1 = await contact_reminder_repo.create(
            db, contact_id=cid, user_id=uid,
            due_at=now + timedelta(days=1), note="First"
        )
        r2 = await contact_reminder_repo.create(
            db, contact_id=cid, user_id=uid,
            due_at=now + timedelta(days=3), note="Second"
        )
        await db.commit()

        reminders = await contact_reminder_repo.list_by_contact(db, cid)
        open_reminders = [r for r in reminders if r.done_at is None]
        ids = [r.id for r in open_reminders]
        assert ids.index(r1.id) < ids.index(r2.id)


class TestListDue:

    async def test_list_due_returns_overdue_open(self, db, reminder_contact):
        cid = reminder_contact["contact"].id
        uid = reminder_contact["user_id"]
        past_due = datetime.now(timezone.utc) - timedelta(hours=2)

        r = await contact_reminder_repo.create(
            db, contact_id=cid, user_id=uid, due_at=past_due, note="Overdue"
        )
        await db.commit()

        due_list = await contact_reminder_repo.list_due(db)
        assert any(x.id == r.id for x in due_list)

    async def test_list_due_excludes_future(self, db, reminder_contact):
        cid = reminder_contact["contact"].id
        uid = reminder_contact["user_id"]
        future_due = datetime.now(timezone.utc) + timedelta(days=5)

        r = await contact_reminder_repo.create(
            db, contact_id=cid, user_id=uid, due_at=future_due, note="Future"
        )
        await db.commit()

        due_list = await contact_reminder_repo.list_due(db)
        assert not any(x.id == r.id for x in due_list)

    async def test_list_due_excludes_done(self, db, reminder_contact):
        cid = reminder_contact["contact"].id
        uid = reminder_contact["user_id"]
        past_due = datetime.now(timezone.utc) - timedelta(hours=1)

        r = await contact_reminder_repo.create(
            db, contact_id=cid, user_id=uid, due_at=past_due, note="Done overdue"
        )
        await db.commit()

        await contact_reminder_repo.mark_done(db, r.id)
        await db.commit()

        due_list = await contact_reminder_repo.list_due(db)
        assert not any(x.id == r.id for x in due_list)


class TestCountOverdue:

    async def test_overdue_contact_included(self, db, reminder_contact):
        cid = reminder_contact["contact"].id
        uid = reminder_contact["user_id"]
        past_due = datetime.now(timezone.utc) - timedelta(hours=3)

        await contact_reminder_repo.create(
            db, contact_id=cid, user_id=uid, due_at=past_due, note="Overdue reminder"
        )
        await db.commit()

        result = await contact_reminder_repo.count_overdue_for_contacts(db, [cid])
        assert cid in result

    async def test_no_overdue_contact_excluded(self, db, reminder_contact):
        cid = reminder_contact["contact"].id
        uid = reminder_contact["user_id"]
        future = datetime.now(timezone.utc) + timedelta(days=2)

        await contact_reminder_repo.create(
            db, contact_id=cid, user_id=uid, due_at=future, note="Future"
        )
        await db.commit()

        result = await contact_reminder_repo.count_overdue_for_contacts(db, [cid])
        assert cid not in result

    async def test_empty_list_returns_empty_set(self, db):
        result = await contact_reminder_repo.count_overdue_for_contacts(db, [])
        assert result == set()


class TestMarkDoneAndDelete:

    async def test_mark_done(self, db, reminder_contact):
        cid = reminder_contact["contact"].id
        uid = reminder_contact["user_id"]
        r = await contact_reminder_repo.create(
            db, contact_id=cid, user_id=uid,
            due_at=datetime.now(timezone.utc) + timedelta(days=1),
            note="Mark done test"
        )
        await db.commit()

        updated = await contact_reminder_repo.mark_done(db, r.id)
        await db.commit()

        assert updated is not None
        assert updated.done_at is not None

    async def test_delete(self, db, reminder_contact):
        cid = reminder_contact["contact"].id
        uid = reminder_contact["user_id"]
        r = await contact_reminder_repo.create(
            db, contact_id=cid, user_id=uid,
            due_at=datetime.now(timezone.utc) + timedelta(days=1),
            note="Delete test"
        )
        await db.commit()
        rid = r.id

        ok = await contact_reminder_repo.delete(db, rid)
        await db.commit()

        assert ok is True
        gone = await contact_reminder_repo.get_by_id(db, rid)
        assert gone is None

    async def test_delete_nonexistent_returns_false(self, db):
        ok = await contact_reminder_repo.delete(db, 999999)
        assert ok is False
