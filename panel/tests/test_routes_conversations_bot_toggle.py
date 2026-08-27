"""Tests for POST /conversations/{conv_id}/bot-toggle endpoint."""
from __future__ import annotations
import pytest
from datetime import datetime, timezone, timedelta


class TestBotToggle:
    async def test_unauthenticated_redirects(self, client):
        resp = await client.post("/conversations/1/bot-toggle")
        assert resp.status_code == 303
        assert "/login" in resp.headers["location"]

    async def test_nonexistent_conv_returns_404(self, admin_client):
        resp = await admin_client.post("/conversations/999999/bot-toggle")
        assert resp.status_code == 404

    async def test_toggle_deactivates_active_bot(self, admin_client, db):
        """Toggle an active bot → is_bot_active becomes False."""
        from app.models.conversation import Conversation
        from app.models.contact import Contact

        c = Contact(phone=None, source="manual", status="new",
                    created_at=datetime.now(timezone.utc))
        db.add(c)
        await db.flush()
        conv = Conversation(contact_id=c.id, channel="whatsapp", status="active",
                            is_bot_active=True, is_open=True,
                            created_at=datetime.now(timezone.utc),
                            updated_at=datetime.now(timezone.utc))
        db.add(conv)
        await db.flush()
        await db.commit()

        resp = await admin_client.post(f"/conversations/{conv.id}/bot-toggle")
        assert resp.status_code == 200
        assert b"amber" in resp.content or b"Off" in resp.content

    async def test_toggle_activates_inactive_bot(self, admin_client, db):
        """Toggle an inactive bot → is_bot_active becomes True."""
        from app.models.conversation import Conversation
        from app.models.contact import Contact

        c = Contact(phone=None, source="manual", status="new",
                    created_at=datetime.now(timezone.utc))
        db.add(c)
        await db.flush()
        conv = Conversation(contact_id=c.id, channel="whatsapp", status="active",
                            is_bot_active=False, is_open=True,
                            created_at=datetime.now(timezone.utc),
                            updated_at=datetime.now(timezone.utc))
        db.add(conv)
        await db.flush()
        await db.commit()

        resp = await admin_client.post(f"/conversations/{conv.id}/bot-toggle")
        assert resp.status_code == 200
        assert b"blue" in resp.content or b"On" in resp.content

    async def test_toggle_persists_to_db(self, admin_client, db):
        """After toggle, DB value is flipped."""
        from app.models.conversation import Conversation
        from app.models.contact import Contact
        from sqlalchemy import select

        c = Contact(phone=None, source="manual", status="new",
                    created_at=datetime.now(timezone.utc))
        db.add(c)
        await db.flush()
        conv = Conversation(contact_id=c.id, channel="whatsapp", status="active",
                            is_bot_active=True, is_open=True,
                            created_at=datetime.now(timezone.utc),
                            updated_at=datetime.now(timezone.utc))
        db.add(conv)
        await db.flush()
        await db.commit()
        conv_id = conv.id

        await admin_client.post(f"/conversations/{conv_id}/bot-toggle")

        db.expire_all()
        result = await db.execute(
            select(Conversation).where(Conversation.id == conv_id)
        )
        refreshed = result.scalar_one()
        assert refreshed.is_bot_active is False

    async def test_reactivate_clears_last_human_reply_at_in_db(self, admin_client, db):
        """Route: toggling inactive→active must set last_human_reply_at=NULL in DB.

        This ensures the 30-min human cooldown is lifted as soon as the operator
        re-enables the bot — without this, the bot would stay silent until the
        cooldown window expires naturally.
        """
        from app.models.conversation import Conversation
        from app.models.contact import Contact
        from sqlalchemy import select

        c = Contact(phone=None, source="manual", status="new",
                    created_at=datetime.now(timezone.utc))
        db.add(c)
        await db.flush()
        # last_human_reply_at set 5 minutes ago — would block the bot for 25 more minutes
        recent_ts = datetime.now(timezone.utc) - timedelta(minutes=5)
        conv = Conversation(contact_id=c.id, channel="whatsapp", status="active",
                            is_bot_active=False, is_open=True,
                            last_human_reply_at=recent_ts,
                            created_at=datetime.now(timezone.utc),
                            updated_at=datetime.now(timezone.utc))
        db.add(conv)
        await db.flush()
        await db.commit()
        conv_id = conv.id

        resp = await admin_client.post(f"/conversations/{conv_id}/bot-toggle")
        assert resp.status_code == 200

        db.expire_all()
        result = await db.execute(
            select(Conversation).where(Conversation.id == conv_id)
        )
        refreshed = result.scalar_one()
        assert refreshed.is_bot_active is True
        assert refreshed.last_human_reply_at is None, (
            "Reactivating the bot via route must clear last_human_reply_at so the "
            "30-min cooldown is immediately lifted"
        )

    async def test_deactivate_preserves_last_human_reply_at_in_db(self, admin_client, db):
        """Route: toggling active→inactive must NOT touch last_human_reply_at."""
        from app.models.conversation import Conversation
        from app.models.contact import Contact
        from sqlalchemy import select

        c = Contact(phone=None, source="manual", status="new",
                    created_at=datetime.now(timezone.utc))
        db.add(c)
        await db.flush()
        recent_ts = datetime.now(timezone.utc) - timedelta(minutes=10)
        conv = Conversation(contact_id=c.id, channel="whatsapp", status="active",
                            is_bot_active=True, is_open=True,
                            last_human_reply_at=recent_ts,
                            created_at=datetime.now(timezone.utc),
                            updated_at=datetime.now(timezone.utc))
        db.add(conv)
        await db.flush()
        await db.commit()
        conv_id = conv.id

        resp = await admin_client.post(f"/conversations/{conv_id}/bot-toggle")
        assert resp.status_code == 200

        db.expire_all()
        result = await db.execute(
            select(Conversation).where(Conversation.id == conv_id)
        )
        refreshed = result.scalar_one()
        assert refreshed.is_bot_active is False
        # timestamp must survive deactivation — only clear on reactivation
        assert refreshed.last_human_reply_at is not None, (
            "Deactivating the bot must NOT clear last_human_reply_at"
        )

    async def test_toggle_creates_lead_event(self, admin_client, db):
        """Toggle creates a bot_toggle lead_event with correct fields."""
        from app.models.conversation import Conversation
        from app.models.contact import Contact
        from app.models.lead_event import LeadEvent
        from sqlalchemy import select
        c = Contact(phone=None, source="manual", status="new",
                    created_at=datetime.now(timezone.utc))
        db.add(c)
        await db.flush()
        conv = Conversation(contact_id=c.id, channel="whatsapp", status="active",
                            is_bot_active=True, is_open=True,
                            created_at=datetime.now(timezone.utc),
                            updated_at=datetime.now(timezone.utc))
        db.add(conv)
        await db.flush()

        conv_id = conv.id
        contact_id = c.id
        await db.commit()

        await admin_client.post(f"/conversations/{conv_id}/bot-toggle")

        events = await db.execute(
            select(LeadEvent).where(
                LeadEvent.contact_id == contact_id,
                LeadEvent.event_type == "bot_toggle",
            )
        )
        event = events.scalar_one()
        assert event.event_type == "bot_toggle"
        assert event.triggered_by.startswith("user:")
        assert event.event_metadata["conversation_id"] == conv_id
        assert event.event_metadata["is_bot_active"] is False  # was True, toggled to False
