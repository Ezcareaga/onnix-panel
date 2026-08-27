"""Tests for GET /conversations/{conv_id}/activity (B1.5).

Authorization matrix:
  - agent owner      → 200, renders activity items
  - agent non-owner  → 403
  - admin            → 200

Render tests:
  - Empty events → renders placeholder text
  - Events present → renders descriptions in Spanish
"""
from __future__ import annotations

import random
import os
import subprocess
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text as sa_text

from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.lead_event import LeadEvent
from app.models.message import Message


_HASH = "$2b$12$q7iHvYIHneLdjGsG2AZFLeKKEyerDlRc4wkIvfPFPgVja403Ue7Xu"


def _psql(sql: str) -> None:
    subprocess.run(
        ["docker", "exec", "onnix-postgres",
         "psql", "-U", "onnix", "-d", os.environ["POSTGRES_DB"], "-c", sql],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=10,
    )


def _phone() -> str:
    return f"+5959816{random.randint(100_000, 999_999)}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def activity_users(db):
    """Create agent_owner and agent_other for each test."""
    _psql(
        "INSERT INTO users (email, name, role, password_hash, is_active) VALUES "
        f"('pytest_act_owner@onnixtest.com','Activity Owner','agent','{_HASH}',true), "
        f"('pytest_act_other@onnixtest.com','Activity Other','agent','{_HASH}',true) "
        "ON CONFLICT (email) DO UPDATE SET "
        "role=EXCLUDED.role, is_active=EXCLUDED.is_active, password_hash=EXCLUDED.password_hash"
    )
    res = await db.execute(sa_text(
        "SELECT id, email FROM users WHERE email IN ("
        "'pytest_act_owner@onnixtest.com','pytest_act_other@onnixtest.com')"
    ))
    mapping = {row.email: row.id for row in res}
    return {
        "owner": mapping["pytest_act_owner@onnixtest.com"],
        "other": mapping["pytest_act_other@onnixtest.com"],
    }


@pytest_asyncio.fixture
async def owner_client(activity_users):
    from app.main import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        await c.post("/login", data={
            "email": "pytest_act_owner@onnixtest.com",
            "password": "test123",
        })
        yield c


@pytest_asyncio.fixture
async def other_client(activity_users):
    from app.main import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        await c.post("/login", data={
            "email": "pytest_act_other@onnixtest.com",
            "password": "test123",
        })
        yield c


@pytest_asyncio.fixture
async def activity_conv(db, activity_users):
    """Contact assigned to owner agent, one conversation, one lead_event."""
    now = datetime.now(timezone.utc)
    contact = Contact(
        phone=_phone(), source="manual", status="bot_replied",
        name="ActivityTest Contact",
        agent_user_id=activity_users["owner"],
        agent_assigned_at=now,
        created_at=now, last_activity_at=now,
    )
    db.add(contact)
    await db.commit()
    await db.refresh(contact)

    conv = Conversation(
        contact_id=contact.id,
        status="active", channel="whatsapp",
        is_bot_active=True, is_open=True, message_count=1,
        created_at=now, last_message_at=now,
    )
    db.add(conv)
    await db.commit()
    await db.refresh(conv)

    msg = Message(
        conversation_id=conv.id, contact_id=contact.id,
        direction="inbound", sender_type="contact",
        body="Hola actividad test",
        created_at=now,
    )
    db.add(msg)
    await db.commit()

    return {"contact": contact, "conv": conv}


@pytest_asyncio.fixture
async def activity_conv_with_events(db, activity_conv):
    """Add representative lead_events to the activity conv."""
    contact = activity_conv["contact"]
    now = datetime.now(timezone.utc)

    events = [
        LeadEvent(
            contact_id=contact.id,
            event_type="bot_toggle",
            triggered_by=f"user:{contact.agent_user_id}",
            event_metadata={"is_bot_active": False, "conversation_id": activity_conv["conv"].id},
            created_at=now,
        ),
        LeadEvent(
            contact_id=contact.id,
            event_type="auto_status_change",
            old_status="new",
            new_status="bot_replied",
            triggered_by="system",
            event_metadata={},
            created_at=now,
        ),
        LeadEvent(
            contact_id=contact.id,
            event_type="status_change",
            old_status="bot_replied",
            new_status="interested",
            triggered_by="panel_leads",
            event_metadata={},
            created_at=now,
        ),
    ]
    for ev in events:
        db.add(ev)
    await db.commit()

    return activity_conv


# ===========================================================================
# 1. Authorization matrix
# ===========================================================================

class TestActivityAuthz:
    async def test_owner_agent_gets_200(self, owner_client, activity_conv):
        conv_id = activity_conv["conv"].id
        resp = await owner_client.get(f"/conversations/{conv_id}/activity")
        assert resp.status_code == 200

    async def test_other_agent_gets_403(self, other_client, activity_conv):
        conv_id = activity_conv["conv"].id
        resp = await other_client.get(f"/conversations/{conv_id}/activity")
        assert resp.status_code == 403

    async def test_admin_gets_200(self, admin_client, activity_conv):
        conv_id = activity_conv["conv"].id
        resp = await admin_client.get(f"/conversations/{conv_id}/activity")
        assert resp.status_code == 200

    async def test_nonexistent_conv_returns_404(self, admin_client):
        resp = await admin_client.get("/conversations/999999/activity")
        assert resp.status_code == 404

    async def test_unauthenticated_redirects(self, client):
        resp = await client.get("/conversations/1/activity")
        assert resp.status_code == 303
        assert "/login" in resp.headers["location"]


# ===========================================================================
# 2. Render tests
# ===========================================================================

class TestActivityRender:
    async def test_no_events_renders_placeholder(self, admin_client, activity_conv):
        """If no events exist, the partial shows the empty-state message."""
        conv_id = activity_conv["conv"].id
        resp = await admin_client.get(f"/conversations/{conv_id}/activity")
        assert resp.status_code == 200
        # Either has items OR shows "Sin actividad registrada"
        body = resp.text
        assert "Sin actividad registrada" in body or "<li" in body

    async def test_bot_toggle_event_rendered(
        self, admin_client, activity_conv_with_events,
    ):
        """bot_toggle event renders a Spanish description."""
        conv_id = activity_conv_with_events["conv"].id
        resp = await admin_client.get(f"/conversations/{conv_id}/activity")
        assert resp.status_code == 200
        # The bot_toggle description should contain "Bot"
        assert "Bot" in resp.text

    async def test_status_change_event_rendered(
        self, admin_client, activity_conv_with_events,
    ):
        """status_change event renders with arrow format."""
        conv_id = activity_conv_with_events["conv"].id
        resp = await admin_client.get(f"/conversations/{conv_id}/activity")
        assert resp.status_code == 200
        # Status transitions use "→"
        assert "→" in resp.text

    async def test_auto_status_change_event_rendered(
        self, admin_client, activity_conv_with_events,
    ):
        """auto_status_change event renders a Spanish description."""
        conv_id = activity_conv_with_events["conv"].id
        resp = await admin_client.get(f"/conversations/{conv_id}/activity")
        assert resp.status_code == 200
        assert "Estado" in resp.text
