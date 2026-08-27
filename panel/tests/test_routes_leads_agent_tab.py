"""Tests for the /leads agent-handled tab.

NOTE (STAB-05, 119-07): this file originally targeted a ``tab=agent_replied``
tab created in ``feat(v17): tab 'Contactados por agente'``. The M6.1 refactor
``feat(111-03): refactor /leads to 3 tabs`` REMOVED ``agent_replied`` from
``TAB_WHERE_CLAUSES`` (current tabs: leads / interesados / asignados /
sin_respuesta). ``agent_replied`` remained a valid contact *status* but is no
longer a /leads *tab*, so ``/leads?tab=agent_replied`` silently falls back to
the ``leads`` tab — whose predicate (``status IN ('new','bot_replied') AND
agent_user_id IS NULL``) MATCHES a new/unassigned contact. That made
``test_tab_excludes_non_agent_replied`` a real (not random) failure that only
*looked* flaky while polluted dev data pushed the inserted contact off page 1.

These tests now target the ``asignados`` tab (``agent_user_id IS NOT NULL``) —
the current equivalent of "contacts handled by an agent" — and assert real
exclusion semantics against today's behavior.
"""
from __future__ import annotations
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def make_contact(db):
    """Create contacts and ALWAYS delete them at teardown.

    These tests insert contacts with ``phone=None``, which the session-level
    conftest cleanup (keyed on the pytest phone prefix ``+595981[5-9]%``) can
    NEVER match — they used to accumulate in onnix_dev across runs. With
    ``last_activity_at=2099`` sorting first in the asignados tab (per_page=25),
    the accumulated rows pushed other tests' fixtures off page 1. Teardown here
    tracks the created ids and deletes them unconditionally.
    """
    from sqlalchemy import delete
    from app.models.contact import Contact

    created_ids: list[int] = []

    async def _make(**kwargs) -> Contact:
        c = Contact(**kwargs)
        db.add(c)
        await db.commit()
        created_ids.append(c.id)
        return c

    yield _make

    if created_ids:
        await db.execute(delete(Contact).where(Contact.id.in_(created_ids)))
        await db.commit()


class TestAgentHandledTab:
    async def test_tab_returns_200(self, admin_client):
        resp = await admin_client.get("/leads?tab=asignados")
        assert resp.status_code == 200

    async def test_tab_shows_assigned_contacts(self, admin_client, make_contact):
        """A contact assigned to an agent appears in the asignados tab.

        The asignados tab orders by ``last_activity_at DESC NULLS LAST`` and
        paginates at per_page=25 against the staging baseline (~86 assigned
        contacts). A far-future ``last_activity_at`` deterministically sorts
        this contact to the very top → guaranteed on page 1 regardless of
        baseline volume or test ordering (root cause of the seed-9999 page-1
        visibility failure).
        """
        from datetime import datetime, timezone
        # Assign to the seeded admin user (id 1) so agent_user_id IS NOT NULL.
        await make_contact(
            name="AssignedLead", phone=None, source="manual",
            status="agent_replied", agent_user_id=1,
            created_at=datetime.now(timezone.utc),
            last_activity_at=datetime(2099, 1, 1, tzinfo=timezone.utc))

        resp = await admin_client.get("/leads?tab=asignados")
        assert resp.status_code == 200
        assert b"AssignedLead" in resp.content

    async def test_tab_excludes_unassigned(self, admin_client, make_contact):
        """An unassigned contact does NOT appear in the asignados tab."""
        from datetime import datetime, timezone
        await make_contact(
            name="ShouldNotAppear", phone=None, source="manual",
            status="new", agent_user_id=None,
            created_at=datetime.now(timezone.utc))

        resp = await admin_client.get("/leads?tab=asignados")
        assert resp.status_code == 200
        assert b"ShouldNotAppear" not in resp.content

    async def test_asignados_tab_link_in_context(self, admin_client, db):
        """The asignados tab is reachable from the leads page chrome."""
        resp = await admin_client.get("/leads?tab=leads")
        assert resp.status_code == 200
        assert b"asignados" in resp.content or b"Asignados" in resp.content
