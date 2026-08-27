"""Plan 111-04 (Test #13 §8) — RED tests for /leads agent view ('Mis asignados').

Spec (must_haves):
    - Agent (role=agent) GET /leads → SOLO ve "Mis asignados" (sin tabs admin).
    - "Mis asignados" lista contacts con agent_user_id = current_user.id.
    - URL ?tab=leads / ?tab=interesados / ?tab=asignados (agent auth) → todos
      renderizan el bucket del agent (no leak de otros agents).
    - Agent NO ve dropdown "Asignar a…" (admin-only por 111-03).
    - Agent sin contacts → empty state propio ("No tenés leads asignados…").
"""
from __future__ import annotations

import random
import re
import os
import subprocess
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text as sa_text

from app.models.contact import Contact


def _psql(sql: str) -> None:
    subprocess.run(
        ["docker", "exec", "onnix-postgres",
         "psql", "-U", "onnix", "-d", os.environ["POSTGRES_DB"], "-c", sql],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=10,
    )


def _count_rows(html: bytes) -> int:
    return len(re.findall(rb'id="lead-row-\d+"', html))


def _contains_phone(html: bytes, phone: str) -> bool:
    return phone.encode() in html


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def agent_view_users(db):
    """Create agent_a, agent_b, and an empty agent_c for the view tests."""
    _psql(
        "INSERT INTO users (email, name, role, password_hash, is_active) "
        "VALUES "
        "('pytest_view_agent_a@onnixtest.com','View Agent A','agent',"
        "'$2b$12$q7iHvYIHneLdjGsG2AZFLeKKEyerDlRc4wkIvfPFPgVja403Ue7Xu',true), "
        "('pytest_view_agent_b@onnixtest.com','View Agent B','agent',"
        "'$2b$12$q7iHvYIHneLdjGsG2AZFLeKKEyerDlRc4wkIvfPFPgVja403Ue7Xu',true), "
        "('pytest_view_agent_c@onnixtest.com','View Agent C','agent',"
        "'$2b$12$q7iHvYIHneLdjGsG2AZFLeKKEyerDlRc4wkIvfPFPgVja403Ue7Xu',true) "
        "ON CONFLICT (email) DO UPDATE SET "
        "role=EXCLUDED.role, is_active=EXCLUDED.is_active, "
        "password_hash=EXCLUDED.password_hash"
    )
    res = await db.execute(sa_text(
        "SELECT id, email FROM users WHERE email IN ("
        "'pytest_view_agent_a@onnixtest.com','pytest_view_agent_b@onnixtest.com',"
        "'pytest_view_agent_c@onnixtest.com')"
    ))
    mapping = {row.email: row.id for row in res}
    return {
        "agent_a": mapping["pytest_view_agent_a@onnixtest.com"],
        "agent_b": mapping["pytest_view_agent_b@onnixtest.com"],
        "agent_c": mapping["pytest_view_agent_c@onnixtest.com"],
    }


@pytest.fixture
async def agent_view_contacts(db, agent_view_users):
    """8 contacts: 3 → agent_a, 3 → agent_b, 2 → NULL (unassigned)."""
    agent_a = agent_view_users["agent_a"]
    agent_b = agent_view_users["agent_b"]
    suffix = random.randint(100_000, 999_900)
    base = "+5959819"

    def ph(i: int) -> str:
        return f"{base}{suffix + i}"

    spec = [
        (ph(0), "new",          agent_a, "Aldana A"),
        (ph(1), "interested",   agent_a, "Bruno A"),
        (ph(2), "bot_replied",  agent_a, "Carla A"),
        (ph(3), "new",          agent_b, "Diego B"),
        (ph(4), "interested",   agent_b, "Elsa B"),
        (ph(5), "bot_replied",  agent_b, "Fede B"),
        (ph(6), "new",          None,    "Gaby U"),
        (ph(7), "new",          None,    "Hugo U"),
    ]
    now = datetime.now(timezone.utc)
    for phone, status, agent, name in spec:
        c = Contact(
            phone=phone, source="manual", status=status,
            agent_user_id=agent, name=name,
            created_at=now, last_activity_at=now,
        )
        db.add(c)
    await db.commit()
    return {
        "agent_a_phones": [s[0] for s in spec if s[2] == agent_a],
        "agent_b_phones": [s[0] for s in spec if s[2] == agent_b],
        "unassigned_phones": [s[0] for s in spec if s[2] is None],
    }


@pytest.fixture
async def agent_a_client(agent_view_users):
    """Authenticated client as agent_a (pytest_view_agent_a@onnixtest.com)."""
    from app.main import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        await c.post("/login", data={
            "email": "pytest_view_agent_a@onnixtest.com",
            "password": "test123",
        })
        yield c


@pytest.fixture
async def agent_c_client(agent_view_users):
    """Authenticated client as agent_c (no contacts assigned)."""
    from app.main import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        await c.post("/login", data={
            "email": "pytest_view_agent_c@onnixtest.com",
            "password": "test123",
        })
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAgentSeesOnlyAsignados:
    async def test_agent_sees_only_asignados_filtered_by_self(
        self, agent_a_client, agent_view_contacts,
    ):
        resp = await agent_a_client.get("/leads")
        assert resp.status_code == 200
        body = resp.content

        # Title (h1) says "Mis asignados", not "Leads".
        # Use the h1 tag specifically — "Leads" can leak via base.html chrome.
        # Sentence case desde el carril B4 (ui.md): era "Mis asignados".
        assert b"Mis asignados" in body
        # h1 should NOT be "Leads" — admin-only title.
        assert not re.search(
            rb'<h1[^>]*>\s*Leads\s*<', body,
        ), "agent view rendered admin h1 'Leads'"

        # Agent A's 3 contacts visible.
        for phone in agent_view_contacts["agent_a_phones"]:
            assert _contains_phone(body, phone), f"missing own phone {phone}"

        # Agent B's contacts NOT visible (other agent).
        for phone in agent_view_contacts["agent_b_phones"]:
            assert not _contains_phone(body, phone), \
                f"other-agent phone leaked: {phone}"

        # Unassigned contacts NOT visible.
        for phone in agent_view_contacts["unassigned_phones"]:
            assert not _contains_phone(body, phone), \
                f"unassigned phone leaked: {phone}"

        # Tabs nav block (admin-only) absent: no ?tab=leads / ?tab=interesados links.
        assert b"tab=leads" not in body, "admin tab 'leads' link leaked"
        assert b"tab=interesados" not in body, "admin tab 'interesados' link leaked"

        # No "Asignar a…" dropdown (admin only — 111-03).
        # The dropdown header text "Asignar a…" is the marker.
        assert "Asignar a…".encode("utf-8") not in body, \
            "agent should NOT see assign dropdown"

        # Row count == 3 (only agent_a's contacts).
        # The DB may contain unrelated test contacts of agent_a from prior
        # runs; assert at-least 3 + cap upper bound at per_page (25).
        n = _count_rows(body)
        assert n >= 3, f"expected ≥3 rows, got {n}"
        assert n <= 25, f"unexpected row count {n} — pagination broken?"

    async def test_agent_cannot_see_other_tabs_via_url_manipulation(
        self, agent_a_client, agent_view_contacts,
    ):
        """Manipular ?tab= → siempre se renderiza el bucket del agent."""
        agent_a_phones = agent_view_contacts["agent_a_phones"]
        agent_b_phones = agent_view_contacts["agent_b_phones"]
        unassigned_phones = agent_view_contacts["unassigned_phones"]

        for tab_value in ("leads", "interesados", "asignados"):
            resp = await agent_a_client.get(f"/leads?tab={tab_value}")
            assert resp.status_code == 200, f"tab={tab_value} failed"
            body = resp.content

            # Title always "Mis asignados" — URL manipulation cannot expose
            # the admin "Leads" title.
            assert b"Mis asignados" in body, \
                f"tab={tab_value} missing 'Mis asignados' title"

            # Always sees own 3 contacts.
            for phone in agent_a_phones:
                assert _contains_phone(body, phone), \
                    f"tab={tab_value} missing own phone {phone}"

            # Never sees other agent's contacts.
            for phone in agent_b_phones:
                assert not _contains_phone(body, phone), \
                    f"tab={tab_value} leaked other-agent phone {phone}"

            # Never sees unassigned contacts.
            for phone in unassigned_phones:
                assert not _contains_phone(body, phone), \
                    f"tab={tab_value} leaked unassigned phone {phone}"


class TestAgentEmptyState:
    async def test_agent_with_zero_contacts_shows_empty_state(
        self, agent_c_client,
    ):
        """Agent C has zero assigned contacts → agent-aware empty state copy."""
        resp = await agent_c_client.get("/leads")
        assert resp.status_code == 200
        body = resp.content

        # No rows.
        assert _count_rows(body) == 0, "agent_c should have zero rows"

        # Title still says "Mis asignados".
        assert b"Mis asignados" in body

        # Agent-specific empty-state copy.
        # Accept either "No tenés leads asignados" or "No tienes leads asignados"
        # (both are valid es-PY spellings).
        empty_markers = [
            "No tenés leads asignados".encode("utf-8"),
            "No tienes leads asignados".encode("utf-8"),
        ]
        assert any(m in body for m in empty_markers), (
            "agent-specific empty-state copy missing — expected "
            "'No tenés leads asignados…' or 'No tienes leads asignados…'"
        )
