"""Tests for POST /leads/{contact_id}/agent-assign endpoint."""
from __future__ import annotations
import pytest
from datetime import datetime, timezone


class TestAgentAssign:
    async def test_unauthenticated_redirects(self, client):
        resp = await client.post("/leads/1/agent-assign")
        assert resp.status_code == 303

    async def test_nonexistent_lead_returns_404(self, admin_client):
        resp = await admin_client.post("/leads/999999/agent-assign")
        assert resp.status_code == 404

    async def test_new_contact_gets_agent_replied(self, admin_client, db):
        from app.models.contact import Contact
        from sqlalchemy import select
        c = Contact(phone=None, source="manual", status="new",
                    created_at=datetime.now(timezone.utc))
        db.add(c)
        await db.flush()
        contact_id = c.id
        await db.commit()

        resp = await admin_client.post(f"/leads/{contact_id}/agent-assign")
        assert resp.status_code == 200

        db.expire_all()
        result = await db.execute(select(Contact).where(Contact.id == contact_id))
        refreshed = result.scalar_one()
        assert refreshed.status == "agent_replied"
        assert refreshed.agent_user_id is not None

    async def test_bot_replied_contact_gets_agent_replied(self, admin_client, db):
        from app.models.contact import Contact
        from sqlalchemy import select
        c = Contact(phone=None, source="manual", status="bot_replied",
                    created_at=datetime.now(timezone.utc))
        db.add(c)
        await db.flush()
        contact_id = c.id
        await db.commit()

        resp = await admin_client.post(f"/leads/{contact_id}/agent-assign")
        assert resp.status_code == 200

        db.expire_all()
        result = await db.execute(select(Contact).where(Contact.id == contact_id))
        refreshed = result.scalar_one()
        assert refreshed.status == "agent_replied"

    async def test_interested_contact_status_not_downgraded(self, admin_client, db):
        """Interested contacts keep their status when agent assigns."""
        from app.models.contact import Contact
        from sqlalchemy import select
        c = Contact(phone=None, source="manual", status="interested",
                    created_at=datetime.now(timezone.utc))
        db.add(c)
        await db.flush()
        contact_id = c.id
        await db.commit()

        resp = await admin_client.post(f"/leads/{contact_id}/agent-assign")
        assert resp.status_code == 200

        db.expire_all()
        result = await db.execute(select(Contact).where(Contact.id == contact_id))
        refreshed = result.scalar_one()
        assert refreshed.status == "interested"  # not downgraded
        assert refreshed.agent_user_id is not None  # but agent was set

    async def test_creates_lead_event(self, admin_client, db):
        from app.models.contact import Contact
        from app.models.lead_event import LeadEvent
        from sqlalchemy import select
        c = Contact(phone=None, source="manual", status="bot_replied",
                    created_at=datetime.now(timezone.utc))
        db.add(c)
        await db.flush()
        contact_id = c.id
        await db.commit()

        await admin_client.post(f"/leads/{contact_id}/agent-assign")

        events = await db.execute(
            select(LeadEvent).where(
                LeadEvent.contact_id == contact_id,
                LeadEvent.event_type == "agent_assigned",
            )
        )
        event = events.scalar_one()
        assert event.triggered_by.startswith("user:")
        assert event.event_metadata.get("agent_user_id") is not None
