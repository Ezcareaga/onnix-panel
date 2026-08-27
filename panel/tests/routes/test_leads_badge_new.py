"""Plan 111-07 (Test #18 §8) — RED tests for "Nuevo" badge in lead_item.html.

Spec (must_haves):
    - Badge visible si contact.agent_assigned_at > contact.agent_seen_at
      OR agent_seen_at IS NULL (con agent_assigned_at NOT NULL).
    - Cuando el agent dueño abre GET /contacts/{id}, side-effect:
      UPDATE contacts SET agent_seen_at = now() WHERE id = :id AND agent_user_id = :uid
      → badge desaparece en próximo render.
    - Reassign actualiza agent_assigned_at = now() (>= agent_seen_at) → badge
      reaparece para el nuevo dueño (el viejo dueño pierde el row por ROLE-13).
"""
from __future__ import annotations

import random
import re
import os
import subprocess
from datetime import datetime, timedelta, timezone

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


def _extract_lead_row(html: bytes, contact_id: int) -> bytes | None:
    """Return the substring of html that is the <tr id="lead-row-{id}"…</tr>.
    Used to scope assertions to a single row (avoid leakage from other rows).
    """
    pattern = (
        rb'<tr[^>]*id="lead-row-' + str(contact_id).encode() + rb'"[\s\S]*?</tr>'
    )
    m = re.search(pattern, html)
    return m.group(0) if m else None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def badge_users(db):
    """Create agent_a and agent_b for the badge tests."""
    _psql(
        "INSERT INTO users (email, name, role, password_hash, is_active) "
        "VALUES "
        "('pytest_badge_agent_a@onnixtest.com','Badge Agent A','agent',"
        "'$2b$12$q7iHvYIHneLdjGsG2AZFLeKKEyerDlRc4wkIvfPFPgVja403Ue7Xu',true), "
        "('pytest_badge_agent_b@onnixtest.com','Badge Agent B','agent',"
        "'$2b$12$q7iHvYIHneLdjGsG2AZFLeKKEyerDlRc4wkIvfPFPgVja403Ue7Xu',true) "
        "ON CONFLICT (email) DO UPDATE SET "
        "role=EXCLUDED.role, is_active=EXCLUDED.is_active, "
        "password_hash=EXCLUDED.password_hash"
    )
    res = await db.execute(sa_text(
        "SELECT id, email FROM users WHERE email IN ("
        "'pytest_badge_agent_a@onnixtest.com','pytest_badge_agent_b@onnixtest.com')"
    ))
    mapping = {row.email: row.id for row in res}
    return {
        "agent_a": mapping["pytest_badge_agent_a@onnixtest.com"],
        "agent_b": mapping["pytest_badge_agent_b@onnixtest.com"],
    }


def _phone() -> str:
    return f"+5959818{random.randint(100_000, 999_999)}"


@pytest.fixture
async def agent_a_client(badge_users):
    from app.main import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        await c.post("/login", data={
            "email": "pytest_badge_agent_a@onnixtest.com",
            "password": "test123",
        })
        yield c


@pytest.fixture
async def agent_b_client(badge_users):
    from app.main import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        await c.post("/login", data={
            "email": "pytest_badge_agent_b@onnixtest.com",
            "password": "test123",
        })
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNuevoBadgeVisibility:
    async def test_badge_visible_when_assigned_and_unseen(
        self, agent_a_client, db, badge_users,
    ):
        """Recién asignado (agent_seen_at IS NULL) → badge visible."""
        now = datetime.now(timezone.utc)
        c = Contact(
            phone=_phone(), source="manual", status="new",
            name="Badge Test A1",
            agent_user_id=badge_users["agent_a"],
            agent_assigned_at=now,
            agent_seen_at=None,
            created_at=now, last_activity_at=now,
        )
        db.add(c)
        await db.commit()
        await db.refresh(c)

        resp = await agent_a_client.get("/leads")
        assert resp.status_code == 200
        row = _extract_lead_row(resp.content, c.id)
        assert row is not None, f"lead-row-{c.id} missing in /leads HTML"
        assert b"badge-new" in row, (
            f"expected 'badge-new' marker in row of contact {c.id} "
            "(agent_seen_at is NULL → badge must show)"
        )

    async def test_badge_disappears_after_agent_opens_detail(
        self, agent_a_client, db, badge_users,
    ):
        """Agent abre /contacts/{id} → agent_seen_at = now() → próximo
        /leads render NO muestra badge para ese contact."""
        now = datetime.now(timezone.utc)
        c = Contact(
            phone=_phone(), source="manual", status="new",
            name="Badge Test A2",
            agent_user_id=badge_users["agent_a"],
            agent_assigned_at=now,
            agent_seen_at=None,
            created_at=now, last_activity_at=now,
        )
        db.add(c)
        await db.commit()
        await db.refresh(c)
        # Bind id before any expire_all to avoid MissingGreenlet on later
        # attribute lazy-loads.
        cid = c.id

        # 1) badge present initially
        resp1 = await agent_a_client.get("/leads")
        row1 = _extract_lead_row(resp1.content, cid)
        assert row1 is not None and b"badge-new" in row1

        # 2) agent A opens detail → side-effect UPDATE agent_seen_at = now()
        resp_detail = await agent_a_client.get(f"/contacts/{cid}")
        assert resp_detail.status_code == 200, resp_detail.text

        # 3) DB confirms agent_seen_at NOT NULL
        db.expire_all()
        res = await db.execute(
            sa_text(
                "SELECT agent_seen_at, agent_assigned_at "
                "FROM contacts WHERE id = :cid"
            ),
            {"cid": cid},
        )
        row = res.one()
        assert row.agent_seen_at is not None, (
            "agent_seen_at should have been set by GET /contacts/{id} side-effect"
        )
        # And agent_seen_at >= agent_assigned_at (now() called after).
        assert row.agent_seen_at >= row.agent_assigned_at

        # 4) badge no longer visible on /leads
        resp2 = await agent_a_client.get("/leads")
        row2 = _extract_lead_row(resp2.content, cid)
        assert row2 is not None
        assert b"badge-new" not in row2, (
            "badge should have disappeared after agent opened detail"
        )

    async def test_badge_not_visible_when_seen_after_assign(
        self, agent_a_client, db, badge_users,
    ):
        """Contact donde agent_seen_at > agent_assigned_at → badge NO visible."""
        now = datetime.now(timezone.utc)
        c = Contact(
            phone=_phone(), source="manual", status="new",
            name="Badge Test A3",
            agent_user_id=badge_users["agent_a"],
            agent_assigned_at=now - timedelta(days=2),
            agent_seen_at=now - timedelta(days=1),
            created_at=now - timedelta(days=2),
            last_activity_at=now,
        )
        db.add(c)
        await db.commit()
        await db.refresh(c)

        resp = await agent_a_client.get("/leads")
        row = _extract_lead_row(resp.content, c.id)
        assert row is not None
        assert b"badge-new" not in row, (
            "badge must NOT be visible when agent_seen_at > agent_assigned_at"
        )

    async def test_badge_reappears_after_reassign(
        self, agent_b_client, admin_client, db, badge_users,
    ):
        """Reasignar → agent_assigned_at = now() (mayor que agent_seen_at viejo)
        → para el NUEVO agent (B) el badge aparece."""
        now = datetime.now(timezone.utc)
        # Start state: contact "owned" by agent A but already seen (no badge).
        c = Contact(
            phone=_phone(), source="manual", status="new",
            name="Badge Test Reassign",
            agent_user_id=badge_users["agent_a"],
            agent_assigned_at=now - timedelta(days=2),
            agent_seen_at=now - timedelta(days=1),
            created_at=now - timedelta(days=2),
            last_activity_at=now,
        )
        db.add(c)
        await db.commit()
        await db.refresh(c)

        # Admin reassigns to agent B → triggers agent_assigned_at = NOW()
        # (agent_seen_at stays at old value).
        resp_assign = await admin_client.post(
            f"/leads/{c.id}/agent-assign",
            data={"target_user_id": str(badge_users["agent_b"])},
        )
        assert resp_assign.status_code in (200, 303), resp_assign.text

        # Agent B → /leads → badge visible on this contact.
        resp = await agent_b_client.get("/leads")
        row = _extract_lead_row(resp.content, c.id)
        assert row is not None, (
            f"agent B should see lead-row-{c.id} after reassign"
        )
        assert b"badge-new" in row, (
            "badge must reappear for new owner after reassign"
        )
