"""
Tests for app/services/lead_service.py

Covers: get_leads (non-excel sources), change_status transitions with
        distinct error returns (tuple of contact/error).
"""
import pytest
from app.services.lead_service import lead_service
from app.constants import VALID_STATUSES


class TestGetLeads:
    async def test_returns_list(self, db):
        leads = await lead_service.get_leads(db)
        assert isinstance(leads, list)

    async def test_no_excel_leads_shown(self, db):
        """The 10,812 excel contacts must not appear as leads."""
        leads = await lead_service.get_leads(db)
        for lead in leads:
            assert lead.source != "import:excel"

    async def test_leads_have_actionable_status(self, db):
        """All returned leads should have a status the panel can act on."""
        leads = await lead_service.get_leads(db)
        actionable = {"interested", "visit_scheduled", "new"}
        for lead in leads:
            assert lead.status in actionable


class TestChangeStatus:
    async def test_nonexistent_contact_returns_not_found(self, db):
        contact, error = await lead_service.change_status(db, 999999, "bot_replied", user_id=1)
        assert contact is None
        assert error == "not_found"

    async def test_invalid_status_returns_invalid_status(self, db):
        contact, error = await lead_service.change_status(db, 1, "invalid_status", user_id=1)
        assert contact is None
        assert error == "invalid_status"

    async def test_valid_statuses_defined(self):
        """Ensure the valid statuses set matches expected values."""
        expected = {"new", "bot_replied", "agent_replied", "visit_scheduled",
                    "interested", "closed", "no_response", "discarded"}
        assert VALID_STATUSES == expected

    async def test_same_status_returns_contact_no_error(self, db):
        """Changing to the same status should return contact with no error."""
        from app.models.contact import Contact
        from datetime import datetime, timezone

        # Use None phone to avoid unique constraint conflicts
        c = Contact(
            name="Same Status Test",
            phone=None,
            source="manual",
            status="new",
            created_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.flush()

        contact, error = await lead_service.change_status(db, c.id, "new", user_id=1)
        assert contact is not None
        assert error is None
        assert contact.status == "new"

    async def test_successful_change_returns_contact_no_error(self, db):
        """A valid status change returns the updated contact with no error."""
        from app.models.contact import Contact
        from datetime import datetime, timezone

        # Use None phone to avoid unique constraint conflicts
        c = Contact(
            name="Change Status Test",
            phone=None,
            source="manual",
            status="new",
            created_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.flush()

        contact, error = await lead_service.change_status(db, c.id, "bot_replied", user_id=1)
        assert contact is not None
        assert error is None
        assert contact.status == "bot_replied"

    async def test_discarded_to_new_succeeds(self, db):
        """Changing status from discarded to new should succeed after removing the guard."""
        from app.models.contact import Contact
        from datetime import datetime, timezone

        c = Contact(
            name="Discarded Reactivation Test",
            phone=None,
            source="manual",
            status="discarded",
            created_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.flush()

        contact, error = await lead_service.change_status(db, c.id, "new", user_id=1)
        assert contact is not None
        assert error is None
        assert contact.status == "new"

    async def test_discarded_to_interested_succeeds(self, db):
        """Changing status from discarded to interested should succeed."""
        from app.models.contact import Contact
        from datetime import datetime, timezone

        c = Contact(
            name="Discarded to Interested Test",
            phone=None,
            source="manual",
            status="discarded",
            created_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.flush()

        contact, error = await lead_service.change_status(db, c.id, "interested", user_id=1)
        assert contact is not None
        assert error is None
        assert contact.status == "interested"

    async def test_baja_at_blocks_status_change(self, db):
        """A contact with baja_at set cannot have status changed via lead_service."""
        from app.models.contact import Contact
        from datetime import datetime, timezone

        c = Contact(
            name="Opted Out Contact",
            phone=None,
            source="manual",
            status="discarded",
            baja_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.flush()

        contact, error = await lead_service.change_status(db, c.id, "new", user_id=1)
        assert contact is None
        assert error == "baja"
