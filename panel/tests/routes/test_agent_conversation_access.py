"""Plan 111-07 (Test #16 §8 ROLE-13) — RED tests for GET /contacts/{id}
permission scoping per agent_user_id.

Spec (must_haves):
    - Admin SIEMPRE puede ver cualquier contact (sin restricción).
    - role=user bloqueado (no es ni admin ni agent).
    - agent solo puede ver contacts donde agent_user_id == self.id; otro caso → 403.
    - Reassign rompe acceso inmediatamente: agent A pierde 403 después de reassign.
    - Nuevo dueño post-reassign puede ver el detalle.
    - Side-effect agent_seen_at NO se dispara para admin (solo para agent dueño).
"""
from __future__ import annotations

import random
import os
import subprocess
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, text as sa_text

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
    return f"+5959818{random.randint(100_000, 999_999)}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def access_users(db):
    _psql(
        "INSERT INTO users (email, name, role, password_hash, is_active) "
        "VALUES "
        "('pytest_access_agent_a@onnixtest.com','Access Agent A','agent',"
        "'$2b$12$q7iHvYIHneLdjGsG2AZFLeKKEyerDlRc4wkIvfPFPgVja403Ue7Xu',true), "
        "('pytest_access_agent_b@onnixtest.com','Access Agent B','agent',"
        "'$2b$12$q7iHvYIHneLdjGsG2AZFLeKKEyerDlRc4wkIvfPFPgVja403Ue7Xu',true) "
        "ON CONFLICT (email) DO UPDATE SET "
        "role=EXCLUDED.role, is_active=EXCLUDED.is_active, "
        "password_hash=EXCLUDED.password_hash"
    )
    res = await db.execute(sa_text(
        "SELECT id, email FROM users WHERE email IN ("
        "'pytest_access_agent_a@onnixtest.com','pytest_access_agent_b@onnixtest.com')"
    ))
    mapping = {row.email: row.id for row in res}
    return {
        "agent_a": mapping["pytest_access_agent_a@onnixtest.com"],
        "agent_b": mapping["pytest_access_agent_b@onnixtest.com"],
    }


@pytest.fixture
async def agent_a_client(access_users):
    from app.main import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        await c.post("/login", data={
            "email": "pytest_access_agent_a@onnixtest.com",
            "password": "test123",
        })
        yield c


@pytest.fixture
async def agent_b_client(access_users):
    from app.main import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        await c.post("/login", data={
            "email": "pytest_access_agent_b@onnixtest.com",
            "password": "test123",
        })
        yield c


@pytest.fixture
async def contact_assigned_to_a(db, access_users):
    """Fresh contact assigned to agent A with a bot message in conversation."""
    now = datetime.now(timezone.utc)
    c = Contact(
        phone=_phone(), source="manual", status="bot_replied",
        name="Conversation Test",
        agent_user_id=access_users["agent_a"],
        agent_assigned_at=now,
        agent_seen_at=None,
        created_at=now, last_activity_at=now,
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)

    # Add a conversation + bot message so the detail page renders content.
    conv = Conversation(
        contact_id=c.id,
        status="active",
        channel="telegram",
        is_bot_active=True,
        is_open=True,
        message_count=0,
        created_at=now,
        last_message_at=now,
    )
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    msg = Message(
        conversation_id=conv.id,
        contact_id=c.id,
        direction="outbound",
        sender_type="bot",
        body="Hola desde el bot — historial bot.",
        created_at=now,
    )
    db.add(msg)
    await db.commit()
    return c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAgentRole13Scoping:
    async def test_agent_can_see_own_contact_detail(
        self, agent_a_client, contact_assigned_to_a,
    ):
        """Agent A es dueño → 200 + ve historial."""
        resp = await agent_a_client.get(f"/contacts/{contact_assigned_to_a.id}")
        assert resp.status_code == 200, resp.text
        # The contact name is rendered in the detail page.
        assert b"Conversation Test" in resp.content

    async def test_agent_sees_full_history_post_assign(
        self, agent_a_client, contact_assigned_to_a, db,
    ):
        """Agent ve conversación pre-assign (handler la incluye en grouped_events
        + conversations). El detail page renderiza el bloque de conversaciones,
        que incluye la conv creada antes del assign."""
        resp = await agent_a_client.get(f"/contacts/{contact_assigned_to_a.id}")
        assert resp.status_code == 200
        # The conversations block lists each conv with a link to /conversations/{id}.
        # Confirm the conversation card is rendered (Plan §10 — historial completo
        # accesible al agent dueño).
        assert b"Conversaciones" in resp.content or b"conversaciones" in resp.content, (
            "agent should see conversation list block on detail page"
        )

    async def test_agent_cannot_see_other_agents_contact(
        self, agent_b_client, contact_assigned_to_a,
    ):
        """Agent B no es dueño → 403."""
        resp = await agent_b_client.get(f"/contacts/{contact_assigned_to_a.id}")
        assert resp.status_code == 403, (
            f"agent B should be forbidden from contact owned by A; got {resp.status_code}"
        )

    async def test_agent_loses_access_after_reassign(
        self, agent_a_client, agent_b_client, admin_client,
        contact_assigned_to_a, access_users, db,
    ):
        """Reassign A→B: A pierde 403, B obtiene 200."""
        # Initial: A can see.
        resp_a_pre = await agent_a_client.get(
            f"/contacts/{contact_assigned_to_a.id}"
        )
        assert resp_a_pre.status_code == 200

        # Admin reassigns to agent B.
        resp_assign = await admin_client.post(
            f"/leads/{contact_assigned_to_a.id}/agent-assign",
            data={"target_user_id": str(access_users["agent_b"])},
        )
        assert resp_assign.status_code in (200, 303), resp_assign.text

        # A now 403.
        resp_a_post = await agent_a_client.get(
            f"/contacts/{contact_assigned_to_a.id}"
        )
        assert resp_a_post.status_code == 403, (
            "agent A should be forbidden after reassign to B"
        )

        # B can see.
        resp_b = await agent_b_client.get(
            f"/contacts/{contact_assigned_to_a.id}"
        )
        assert resp_b.status_code == 200

    async def test_agent_unassigned_contact_returns_403(
        self, agent_a_client, db,
    ):
        """Contact con agent_user_id = NULL → agent A no es dueño → 403."""
        now = datetime.now(timezone.utc)
        c = Contact(
            phone=_phone(), source="manual", status="new",
            name="Unassigned",
            agent_user_id=None,
            created_at=now, last_activity_at=now,
        )
        db.add(c)
        await db.commit()
        await db.refresh(c)

        resp = await agent_a_client.get(f"/contacts/{c.id}")
        assert resp.status_code == 403


class TestAdminAlwaysHasAccess:
    async def test_admin_can_see_contact_assigned_to_agent(
        self, admin_client, contact_assigned_to_a,
    ):
        """Admin no tiene restricción ROLE-13."""
        resp = await admin_client.get(f"/contacts/{contact_assigned_to_a.id}")
        assert resp.status_code == 200

    async def test_admin_can_see_unassigned_contact(
        self, admin_client, db,
    ):
        now = datetime.now(timezone.utc)
        c = Contact(
            phone=_phone(), source="manual", status="new",
            name="Unassigned Admin View",
            agent_user_id=None,
            created_at=now, last_activity_at=now,
        )
        db.add(c)
        await db.commit()
        await db.refresh(c)

        resp = await admin_client.get(f"/contacts/{c.id}")
        assert resp.status_code == 200

    async def test_admin_view_does_not_set_agent_seen_at(
        self, admin_client, contact_assigned_to_a, db,
    ):
        """Side-effect SOLO ocurre para role=agent dueño.
        Admin abrir el detalle NO debe tocar agent_seen_at."""
        # contact_assigned_to_a was created with agent_seen_at = NULL.
        cid = contact_assigned_to_a.id
        resp = await admin_client.get(f"/contacts/{cid}")
        assert resp.status_code == 200

        db.expire_all()
        res = await db.execute(
            select(Contact.agent_seen_at).where(Contact.id == cid)
        )
        after_seen = res.scalar_one()
        assert after_seen is None, (
            "admin viewing detail must NOT set agent_seen_at"
        )


class TestSideEffectMarkSeen:
    async def test_agent_open_detail_updates_agent_seen_at(
        self, agent_a_client, contact_assigned_to_a, db,
    ):
        """Side-effect: GET /contacts/{id} con role=agent dueño →
        UPDATE contacts SET agent_seen_at = now() WHERE id=… AND agent_user_id=…"""
        # Bind id (avoid lazy-load after expire_all).
        cid = contact_assigned_to_a.id

        # Pre: agent_seen_at IS NULL.
        res = await db.execute(
            select(Contact.agent_seen_at).where(Contact.id == cid)
        )
        before = res.scalar_one()
        assert before is None

        resp = await agent_a_client.get(f"/contacts/{cid}")
        assert resp.status_code == 200

        db.expire_all()
        res = await db.execute(
            select(Contact.agent_seen_at).where(Contact.id == cid)
        )
        after = res.scalar_one()
        assert after is not None, "side-effect should set agent_seen_at"

    async def test_agent_second_open_still_updates_or_keeps_seen(
        self, agent_a_client, contact_assigned_to_a, db,
    ):
        """Open dos veces seguidas → agent_seen_at se actualiza (o mantiene),
        nunca queda NULL ni decrece."""
        cid = contact_assigned_to_a.id
        await agent_a_client.get(f"/contacts/{cid}")
        db.expire_all()
        res = await db.execute(
            select(Contact.agent_seen_at).where(Contact.id == cid)
        )
        first = res.scalar_one()
        assert first is not None

        # Second open.
        resp = await agent_a_client.get(f"/contacts/{cid}")
        assert resp.status_code == 200

        db.expire_all()
        res = await db.execute(
            select(Contact.agent_seen_at).where(Contact.id == cid)
        )
        second = res.scalar_one()
        assert second is not None
        # Second open must not move agent_seen_at backwards.
        assert second >= first

    async def test_other_agent_open_does_not_update_agent_seen_at(
        self, agent_b_client, contact_assigned_to_a, db,
    ):
        """Agent B (no dueño) abre → 403 + agent_seen_at no cambia."""
        cid = contact_assigned_to_a.id
        before_res = await db.execute(
            select(Contact.agent_seen_at).where(Contact.id == cid)
        )
        before_seen = before_res.scalar_one()  # NULL

        resp = await agent_b_client.get(f"/contacts/{cid}")
        assert resp.status_code == 403

        db.expire_all()
        after_res = await db.execute(
            select(Contact.agent_seen_at).where(Contact.id == cid)
        )
        after_seen = after_res.scalar_one()
        assert after_seen == before_seen, (
            f"agent B 403 must NOT mutate agent_seen_at; was {before_seen}, "
            f"now {after_seen}"
        )
