"""Plan 111-03 (Tests #14, #15 §8) — RED tests for POST /leads/{id}/agent-assign refactor.

Spec:
    - POST with form `target_user_id=<int>`
    - admin can assign to any active admin|agent user
    - admin gets 400 if target is inactive, role=user, or non-existent
    - agent (role='agent') can only self-assign — target_user_id != self.id → 403
    - On success: contact.agent_user_id = target_user_id AND
      contact.agent_assigned_at = NOW() (ROLE-15)
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


def _psql(sql: str) -> None:
    subprocess.run(
        ["docker", "exec", "onnix-postgres",
         "psql", "-U", "onnix", "-d", os.environ["POSTGRES_DB"], "-c", sql],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=10,
    )


@pytest.fixture
async def assign_users(db):
    """Create 3 users for the agent-assign matrix:
        agent_a  (role=agent, active)
        agent_b  (role=agent, active)
        agent_inactive (role=agent, INACTIVE)
        regular_user (role=user, active)
    Returns dict of id mappings.
    """
    _psql(
        "INSERT INTO users (email, name, role, password_hash, is_active) "
        "VALUES "
        "('pytest_agent_a@onnixtest.com','Agent A','agent','$2b$12$q7iHvYIHneLdjGsG2AZFLeKKEyerDlRc4wkIvfPFPgVja403Ue7Xu',true), "
        "('pytest_agent_b@onnixtest.com','Agent B','agent','$2b$12$q7iHvYIHneLdjGsG2AZFLeKKEyerDlRc4wkIvfPFPgVja403Ue7Xu',true), "
        "('pytest_agent_inactive@onnixtest.com','Agent Inactive','agent','$2b$12$q7iHvYIHneLdjGsG2AZFLeKKEyerDlRc4wkIvfPFPgVja403Ue7Xu',false), "
        "('pytest_regular_user@onnixtest.com','Regular User','user','$2b$12$q7iHvYIHneLdjGsG2AZFLeKKEyerDlRc4wkIvfPFPgVja403Ue7Xu',true) "
        "ON CONFLICT (email) DO UPDATE SET "
        "role=EXCLUDED.role, is_active=EXCLUDED.is_active, "
        "password_hash=EXCLUDED.password_hash"
    )
    res = await db.execute(sa_text(
        "SELECT id, email, is_active FROM users WHERE email IN ("
        "'pytest_agent_a@onnixtest.com','pytest_agent_b@onnixtest.com',"
        "'pytest_agent_inactive@onnixtest.com','pytest_regular_user@onnixtest.com')"
    ))
    mapping = {row.email: row.id for row in res}
    return {
        "agent_a": mapping["pytest_agent_a@onnixtest.com"],
        "agent_b": mapping["pytest_agent_b@onnixtest.com"],
        "agent_inactive": mapping["pytest_agent_inactive@onnixtest.com"],
        "regular_user": mapping["pytest_regular_user@onnixtest.com"],
    }


@pytest.fixture
async def new_contact(db):
    """Create a fresh unassigned contact with a unique test-prefix phone.

    Uses 6 random digits to avoid collisions when prior pytest runs leave
    a partial contact behind in onnix_dev (session-scoped cleanup may
    not yet have run when tests are re-executed).
    """
    suffix = random.randint(100_000, 999_999)
    phone = f"+595981820{suffix}"  # within +5959818 test range
    c = Contact(
        phone=phone, source="manual", status="new",
        created_at=datetime.now(timezone.utc),
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    yield c.id


@pytest.fixture
async def agent_a_client(assign_users):
    """Authenticated client as agent A."""
    from app.main import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        await c.post("/login", data={
            "email": "pytest_agent_a@onnixtest.com",
            "password": "test123",
        })
        yield c


class TestAdminAssignToAnyAgent:
    async def test_admin_can_assign_to_active_agent(
        self, admin_client, db, assign_users, new_contact
    ):
        agent_a_id = assign_users["agent_a"]
        resp = await admin_client.post(
            f"/leads/{new_contact}/agent-assign",
            data={"target_user_id": str(agent_a_id)},
        )
        assert resp.status_code in (200, 303), resp.text

        db.expire_all()
        res = await db.execute(select(Contact).where(Contact.id == new_contact))
        contact = res.scalar_one()
        assert contact.agent_user_id == agent_a_id
        assert contact.agent_assigned_at is not None

    async def test_admin_target_inactive_returns_400(
        self, admin_client, db, assign_users, new_contact
    ):
        resp = await admin_client.post(
            f"/leads/{new_contact}/agent-assign",
            data={"target_user_id": str(assign_users["agent_inactive"])},
        )
        assert resp.status_code == 400

    async def test_admin_target_regular_user_returns_400(
        self, admin_client, db, assign_users, new_contact
    ):
        resp = await admin_client.post(
            f"/leads/{new_contact}/agent-assign",
            data={"target_user_id": str(assign_users["regular_user"])},
        )
        assert resp.status_code == 400

    async def test_admin_target_nonexistent_returns_400(
        self, admin_client, new_contact
    ):
        resp = await admin_client.post(
            f"/leads/{new_contact}/agent-assign",
            data={"target_user_id": "99999"},
        )
        assert resp.status_code == 400


class TestAgentSelfAssignOnly:
    async def test_agent_cannot_assign_to_other_agent(
        self, agent_a_client, assign_users, new_contact
    ):
        # Agent A tries to assign to Agent B → 403
        resp = await agent_a_client.post(
            f"/leads/{new_contact}/agent-assign",
            data={"target_user_id": str(assign_users["agent_b"])},
        )
        assert resp.status_code == 403
        assert b"auto-asignarte" in resp.content.lower() or \
               b"solo pod" in resp.content.lower()

    async def test_agent_can_self_assign(
        self, agent_a_client, db, assign_users, new_contact
    ):
        agent_a_id = assign_users["agent_a"]
        resp = await agent_a_client.post(
            f"/leads/{new_contact}/agent-assign",
            data={"target_user_id": str(agent_a_id)},
        )
        assert resp.status_code in (200, 303), resp.text

        db.expire_all()
        res = await db.execute(select(Contact).where(Contact.id == new_contact))
        contact = res.scalar_one()
        assert contact.agent_user_id == agent_a_id
        assert contact.agent_assigned_at is not None
