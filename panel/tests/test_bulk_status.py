"""Tests for POST /contacts/bulk-status (C1.3).

Covers:
  - Happy path: multiple contacts updated
  - Mixed: opt-out contacts skipped, others updated
  - Agent with foreign contacts: foreign ones skipped, own updated
  - Invalid status → 400
"""
from __future__ import annotations

import os
import random
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text as sa_text

from app.models.contact import Contact


def _phone() -> str:
    return f"+5959819{random.randint(100_000, 999_999)}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def bulk_contacts(db):
    """Create 3 contacts with status 'new': c_a, c_b, c_optout.

    c_optout has baja_at set (opt-out) — must be skipped by bulk actions.
    """
    now = datetime.now(timezone.utc)

    c_a = Contact(phone=_phone(), source="manual", status="new", name="BulkA", created_at=now)
    c_b = Contact(phone=_phone(), source="manual", status="new", name="BulkB", created_at=now)
    c_optout = Contact(
        phone=_phone(), source="manual", status="new",
        name="BulkOptout", baja_at=now, created_at=now,
    )
    db.add_all([c_a, c_b, c_optout])
    await db.commit()
    await db.refresh(c_a)
    await db.refresh(c_b)
    await db.refresh(c_optout)
    return {"c_a": c_a, "c_b": c_b, "c_optout": c_optout}


@pytest_asyncio.fixture
async def agent_and_contacts(db):
    """One agent with one own contact, one foreign contact.

    Handles its own teardown so the session-level cleanup never sees residual
    rows from this fixture (which uses subprocess for user creation).
    """
    import subprocess
    _hash = "$2b$12$q7iHvYIHneLdjGsG2AZFLeKKEyerDlRc4wkIvfPFPgVja403Ue7Xu"
    _email = "pytest_bulk_agent@onnixtest.com"

    subprocess.run(
        ["docker", "exec", "onnix-postgres",
         "psql", "-U", "onnix", "-d", os.environ["POSTGRES_DB"],
         "-c",
         f"INSERT INTO users (email, name, role, password_hash, is_active) "
         f"VALUES ('{_email}','Bulk Agent','agent','{_hash}',true) "
         f"ON CONFLICT (email) DO UPDATE SET "
         f"role=EXCLUDED.role, is_active=EXCLUDED.is_active, password_hash=EXCLUDED.password_hash"],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
    )

    res = await db.execute(
        sa_text("SELECT id FROM users WHERE email=:e").bindparams(e=_email)
    )
    agent_id = res.scalar_one()

    now = datetime.now(timezone.utc)
    own = Contact(phone=_phone(), source="manual", status="new", name="BulkOwn",
                  agent_user_id=agent_id, created_at=now)
    foreign = Contact(phone=_phone(), source="manual", status="new", name="BulkForeign",
                      agent_user_id=None, created_at=now)
    db.add_all([own, foreign])
    await db.commit()
    await db.refresh(own)
    await db.refresh(foreign)

    yield {"agent_id": agent_id, "own": own, "foreign": foreign}

    # Teardown: clean up in FK-safe order
    subprocess.run(
        ["docker", "exec", "onnix-postgres",
         "psql", "-U", "onnix", "-d", os.environ["POSTGRES_DB"],
         "-c",
         f"UPDATE contacts SET agent_user_id = NULL WHERE agent_user_id = {agent_id}; "
         f"DELETE FROM auth_audit WHERE email = '{_email}'; "
         f"DELETE FROM users WHERE email = '{_email}';"],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBulkStatus:

    # Happy path — admin updates 2 contacts
    async def test_bulk_happy_path(self, admin_client, bulk_contacts):
        c_a = bulk_contacts["c_a"]
        c_b = bulk_contacts["c_b"]
        resp = await admin_client.post(
            "/contacts/bulk-status",
            data={
                "ids[]": [str(c_a.id), str(c_b.id)],
                "new_status": "interested",
            },
        )
        assert resp.status_code == 200
        body = resp.text
        assert "2 actualizados" in body or "2 actualizado" in body

    # opt-out contact is skipped
    async def test_bulk_skips_optout(self, admin_client, bulk_contacts):
        c_a = bulk_contacts["c_a"]
        c_optout = bulk_contacts["c_optout"]
        resp = await admin_client.post(
            "/contacts/bulk-status",
            data={
                "ids[]": [str(c_a.id), str(c_optout.id)],
                "new_status": "closed",
            },
        )
        assert resp.status_code == 200
        body = resp.text
        # 1 updated, 1 skipped (opt-out)
        assert "opt-out" in body.lower() or "omitido" in body.lower()

    # Invalid status → 400
    async def test_bulk_invalid_status_400(self, admin_client, bulk_contacts):
        c_a = bulk_contacts["c_a"]
        resp = await admin_client.post(
            "/contacts/bulk-status",
            data={
                "ids[]": [str(c_a.id)],
                "new_status": "invalid_xyz",
            },
        )
        assert resp.status_code == 400

    # 'deleted' status rejected → 400
    async def test_bulk_deleted_status_rejected(self, admin_client, bulk_contacts):
        c_a = bulk_contacts["c_a"]
        resp = await admin_client.post(
            "/contacts/bulk-status",
            data={
                "ids[]": [str(c_a.id)],
                "new_status": "deleted",
            },
        )
        assert resp.status_code == 400

    # Agent: own contact updated, foreign skipped
    async def test_bulk_agent_skips_foreign_contacts(self, agent_and_contacts):
        """Agent can only bulk-update their own contacts."""
        from app.main import app
        from httpx import AsyncClient, ASGITransport
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            await client.post("/login", data={
                "email": "pytest_bulk_agent@onnixtest.com",
                "password": "test123",
            })
            own_id = agent_and_contacts["own"].id
            foreign_id = agent_and_contacts["foreign"].id
            resp = await client.post(
                "/contacts/bulk-status",
                data={
                    "ids[]": [str(own_id), str(foreign_id)],
                    "new_status": "interested",
                },
            )
        assert resp.status_code == 200
        body = resp.text
        # 1 own updated, 1 foreign skipped (sin permiso)
        assert "1 actualizado" in body
        assert "sin permiso" in body.lower()

    # Unauthenticated → redirect
    async def test_bulk_unauthenticated_redirects(self, client):
        resp = await client.post(
            "/contacts/bulk-status",
            data={"ids[]": ["1"], "new_status": "interested"},
        )
        assert resp.status_code == 303
