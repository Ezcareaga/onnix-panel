"""Tests for ContactReminderService (C2.2).

Pure unit tests using mocks — no DB required.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.contact_reminder_service import ContactReminderService

pytestmark = pytest.mark.asyncio

_NOW = datetime.now(timezone.utc)
_FUTURE = _NOW + timedelta(days=1)
_PAST = _NOW - timedelta(hours=1)


def _make_db() -> AsyncMock:
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def mocked_repos():
    """Patch both contact_repo and contact_reminder_repo used by the service."""
    with (
        patch("app.services.contact_reminder_service.contact_repo") as cr,
        patch("app.services.contact_reminder_service.contact_reminder_repo") as rr,
    ):
        for name in ["get_by_id", "create", "list_by_contact", "list_due",
                     "mark_done", "delete", "count_open_for_contact",
                     "count_overdue_for_contacts"]:
            setattr(rr, name, AsyncMock())
        cr.get_by_id = AsyncMock()
        yield {"cr": cr, "rr": rr}


# ---------------------------------------------------------------------------
# create_reminder
# ---------------------------------------------------------------------------

class TestCreateReminder:

    async def test_ok(self, mocked_repos):
        db = _make_db()
        mocked_repos["cr"].get_by_id.return_value = MagicMock()  # contact exists
        mocked_repos["rr"].count_open_for_contact.return_value = 0
        fake_reminder = MagicMock(id=1, contact_id=10, user_id=5)
        mocked_repos["rr"].create.return_value = fake_reminder

        r, err = await ContactReminderService.create_reminder(
            db, contact_id=10, user_id=5, due_at=_FUTURE, note="Call back"
        )

        assert err is None
        assert r is fake_reminder
        db.commit.assert_called_once()

    async def test_contact_not_found(self, mocked_repos):
        db = _make_db()
        mocked_repos["cr"].get_by_id.return_value = None

        r, err = await ContactReminderService.create_reminder(
            db, contact_id=999, user_id=1, due_at=_FUTURE, note="note"
        )

        assert r is None
        assert err == "Contacto no encontrado"
        mocked_repos["rr"].create.assert_not_called()

    async def test_due_at_in_past(self, mocked_repos):
        db = _make_db()
        mocked_repos["cr"].get_by_id.return_value = MagicMock()

        r, err = await ContactReminderService.create_reminder(
            db, contact_id=1, user_id=1, due_at=_PAST, note="note"
        )

        assert r is None
        assert err == "La fecha debe ser en el futuro"
        mocked_repos["rr"].create.assert_not_called()

    async def test_empty_note(self, mocked_repos):
        db = _make_db()
        mocked_repos["cr"].get_by_id.return_value = MagicMock()

        r, err = await ContactReminderService.create_reminder(
            db, contact_id=1, user_id=1, due_at=_FUTURE, note="   "
        )

        assert r is None
        assert err == "La nota no puede estar vacía"
        mocked_repos["rr"].create.assert_not_called()

    async def test_max_open_reminders_exceeded(self, mocked_repos):
        db = _make_db()
        mocked_repos["cr"].get_by_id.return_value = MagicMock()
        mocked_repos["rr"].count_open_for_contact.return_value = 20  # at limit

        r, err = await ContactReminderService.create_reminder(
            db, contact_id=1, user_id=1, due_at=_FUTURE, note="note"
        )

        assert r is None
        assert "Límite de 20" in err
        mocked_repos["rr"].create.assert_not_called()

    async def test_exactly_at_limit_passes(self, mocked_repos):
        """19 open reminders: should still allow creation."""
        db = _make_db()
        mocked_repos["cr"].get_by_id.return_value = MagicMock()
        mocked_repos["rr"].count_open_for_contact.return_value = 19
        mocked_repos["rr"].create.return_value = MagicMock(id=1, contact_id=1, user_id=1)

        r, err = await ContactReminderService.create_reminder(
            db, contact_id=1, user_id=1, due_at=_FUTURE, note="note"
        )

        assert err is None
        assert r is not None

    async def test_naive_due_at_treated_as_utc_future(self, mocked_repos):
        """Naive datetime in future should work (no tz error)."""
        db = _make_db()
        mocked_repos["cr"].get_by_id.return_value = MagicMock()
        mocked_repos["rr"].count_open_for_contact.return_value = 0
        mocked_repos["rr"].create.return_value = MagicMock(id=1, contact_id=1, user_id=1)

        naive_future = datetime.utcnow() + timedelta(days=1)
        assert naive_future.tzinfo is None

        r, err = await ContactReminderService.create_reminder(
            db, contact_id=1, user_id=1, due_at=naive_future, note="note"
        )
        assert err is None


# ---------------------------------------------------------------------------
# mark_done
# ---------------------------------------------------------------------------

class TestMarkDone:

    async def test_ok(self, mocked_repos):
        db = _make_db()
        fake = MagicMock(id=5, contact_id=10, done_at=None)
        mocked_repos["rr"].get_by_id.return_value = fake
        done = MagicMock(done_at=_NOW)
        mocked_repos["rr"].mark_done.return_value = done

        r, err = await ContactReminderService.mark_done(db, reminder_id=5, user_id=1)

        assert err is None
        assert r is done
        db.commit.assert_called_once()

    async def test_already_done_is_idempotent(self, mocked_repos):
        db = _make_db()
        fake = MagicMock(id=5, contact_id=10, done_at=_NOW)  # already done
        mocked_repos["rr"].get_by_id.return_value = fake

        r, err = await ContactReminderService.mark_done(db, reminder_id=5, user_id=1)

        assert err is None
        assert r is fake
        mocked_repos["rr"].mark_done.assert_not_called()
        db.commit.assert_not_called()

    async def test_not_found(self, mocked_repos):
        db = _make_db()
        mocked_repos["rr"].get_by_id.return_value = None

        r, err = await ContactReminderService.mark_done(db, reminder_id=99, user_id=1)

        assert r is None
        assert err == "Recordatorio no encontrado"


# ---------------------------------------------------------------------------
# delete_reminder
# ---------------------------------------------------------------------------

class TestDeleteReminder:

    async def test_ok(self, mocked_repos):
        db = _make_db()
        fake = MagicMock(id=3, contact_id=10)
        mocked_repos["rr"].get_by_id.return_value = fake
        mocked_repos["rr"].delete.return_value = True

        ok, err = await ContactReminderService.delete_reminder(db, reminder_id=3, user_id=1)

        assert ok is True
        assert err is None
        db.commit.assert_called_once()

    async def test_not_found(self, mocked_repos):
        db = _make_db()
        mocked_repos["rr"].get_by_id.return_value = None

        ok, err = await ContactReminderService.delete_reminder(db, reminder_id=99, user_id=1)

        assert ok is False
        assert err == "Recordatorio no encontrado"
        mocked_repos["rr"].delete.assert_not_called()


# ---------------------------------------------------------------------------
# list_reminders / list_due
# ---------------------------------------------------------------------------

class TestListMethods:

    async def test_list_reminders_delegates(self, mocked_repos):
        db = _make_db()
        fake_list = [MagicMock(), MagicMock()]
        mocked_repos["rr"].list_by_contact.return_value = fake_list

        result = await ContactReminderService.list_reminders(db, contact_id=1)

        assert result is fake_list
        mocked_repos["rr"].list_by_contact.assert_called_once_with(db, 1)

    async def test_list_due_delegates(self, mocked_repos):
        db = _make_db()
        fake_list = [MagicMock()]
        mocked_repos["rr"].list_due.return_value = fake_list

        result = await ContactReminderService.list_due(db)

        assert result is fake_list
        mocked_repos["rr"].list_due.assert_called_once_with(db)
