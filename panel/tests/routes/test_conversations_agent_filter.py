"""ROLE-04 — /conversations routes filtradas por agent_user_id cuando role=agent.

Tests:
  - test_agent_only_sees_assigned_conversations
  - test_admin_sees_all_conversations
  - test_agent_cannot_view_other_agents_conversation_detail
  - test_search_respects_agent_filter
"""
from __future__ import annotations

import random
import os
import subprocess
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select as sa_select, text as sa_text

from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.message import Message


def _psql(sql: str) -> None:
    subprocess.run(
        ["docker", "exec", "onnix-postgres",
         "psql", "-U", "onnix", "-d", os.environ["POSTGRES_DB"], "-c", sql],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=10,
    )


def _phone() -> str:
    return f"+5959819{random.randint(100_000, 999_999)}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def conv_filter_users(db):
    """Create two agents for conversation filter tests."""
    _psql(
        "INSERT INTO users (email, name, role, password_hash, is_active) "
        "VALUES "
        "('pytest_conv_agent_a@onnixtest.com','Conv Agent A','agent',"
        "'$2b$12$q7iHvYIHneLdjGsG2AZFLeKKEyerDlRc4wkIvfPFPgVja403Ue7Xu',true), "
        "('pytest_conv_agent_b@onnixtest.com','Conv Agent B','agent',"
        "'$2b$12$q7iHvYIHneLdjGsG2AZFLeKKEyerDlRc4wkIvfPFPgVja403Ue7Xu',true) "
        "ON CONFLICT (email) DO UPDATE SET "
        "role=EXCLUDED.role, is_active=EXCLUDED.is_active, "
        "password_hash=EXCLUDED.password_hash"
    )
    res = await db.execute(sa_text(
        "SELECT id, email FROM users WHERE email IN ("
        "'pytest_conv_agent_a@onnixtest.com','pytest_conv_agent_b@onnixtest.com')"
    ))
    mapping = {row.email: row.id for row in res}
    return {
        "agent_a_id": mapping["pytest_conv_agent_a@onnixtest.com"],
        "agent_b_id": mapping["pytest_conv_agent_b@onnixtest.com"],
    }


@pytest_asyncio.fixture
async def conv_filter_data(db, conv_filter_users):
    """Create two contacts+conversations assigned to agent A and agent B."""
    now = datetime.now(timezone.utc)

    contact_a = Contact(
        phone=_phone(), source="manual", status="bot_replied",
        name="ConvTest ContactA",
        agent_user_id=conv_filter_users["agent_a_id"],
        agent_assigned_at=now,
        created_at=now, last_activity_at=now,
    )
    contact_b = Contact(
        phone=_phone(), source="manual", status="bot_replied",
        name="ConvTest ContactB",
        agent_user_id=conv_filter_users["agent_b_id"],
        agent_assigned_at=now,
        created_at=now, last_activity_at=now,
    )
    db.add_all([contact_a, contact_b])
    await db.commit()
    await db.refresh(contact_a)
    await db.refresh(contact_b)

    conv_a = Conversation(
        contact_id=contact_a.id, status="active", channel="whatsapp",
        is_bot_active=True, is_open=True, message_count=1,
        created_at=now, last_message_at=now,
    )
    conv_b = Conversation(
        contact_id=contact_b.id, status="active", channel="whatsapp",
        is_bot_active=True, is_open=True, message_count=1,
        created_at=now, last_message_at=now,
    )
    db.add_all([conv_a, conv_b])
    await db.commit()
    await db.refresh(conv_a)
    await db.refresh(conv_b)

    # Add messages so conversations are not "ghost" (message_count > 0 alone is enough,
    # but real messages help with search tests)
    msg_a = Message(
        conversation_id=conv_a.id, contact_id=contact_a.id,
        direction="inbound", sender_type="contact",
        body="Busco departamento convtest alpha", created_at=now,
    )
    msg_b = Message(
        conversation_id=conv_b.id, contact_id=contact_b.id,
        direction="inbound", sender_type="contact",
        body="Busco casa convtest beta", created_at=now,
    )
    db.add_all([msg_a, msg_b])
    await db.commit()

    return {
        "contact_a": contact_a,
        "contact_b": contact_b,
        "conv_a": conv_a,
        "conv_b": conv_b,
        "agent_a_id": conv_filter_users["agent_a_id"],
        "agent_b_id": conv_filter_users["agent_b_id"],
    }


@pytest_asyncio.fixture
async def conv_agent_a_client(conv_filter_users):
    from app.main import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        await c.post("/login", data={
            "email": "pytest_conv_agent_a@onnixtest.com",
            "password": "test123",
        })
        yield c


@pytest_asyncio.fixture
async def conv_agent_b_client(conv_filter_users):
    from app.main import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        await c.post("/login", data={
            "email": "pytest_conv_agent_b@onnixtest.com",
            "password": "test123",
        })
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestConversationAgentFilter:
    async def test_agent_only_sees_assigned_conversations(
        self, conv_agent_a_client, conv_filter_data,
    ):
        """Agent A GET /conversations → solo ve ConvTest ContactA, no ContactB."""
        resp = await conv_agent_a_client.get("/conversations")
        assert resp.status_code == 200
        body = resp.text
        assert "ConvTest ContactA" in body
        assert "ConvTest ContactB" not in body

    async def test_admin_sees_all_conversations(
        self, admin_client, conv_filter_data,
    ):
        """Admin GET /conversations → ve ambas conversaciones."""
        resp = await admin_client.get("/conversations")
        assert resp.status_code == 200
        body = resp.text
        assert "ConvTest ContactA" in body
        assert "ConvTest ContactB" in body

    async def test_agent_cannot_view_other_agents_conversation_detail(
        self, conv_agent_a_client, conv_filter_data,
    ):
        """Agent A GET /conversations/{conv_b.id} → 403."""
        conv_b_id = conv_filter_data["conv_b"].id
        resp = await conv_agent_a_client.get(f"/conversations/{conv_b_id}")
        assert resp.status_code == 403, (
            f"agent A should be forbidden from conv owned by B; got {resp.status_code}"
        )

    async def test_search_respects_agent_filter(
        self, conv_agent_a_client, conv_filter_data,
    ):
        """Search 'convtest' as agent A → solo resultados de A (alpha), no de B (beta)."""
        resp = await conv_agent_a_client.get("/conversations/list?q=convtest")
        assert resp.status_code == 200
        body = resp.text
        assert "ConvTest ContactA" in body
        assert "ConvTest ContactB" not in body
