"""
Tests for app/routes/leads.py

Covers: GET /leads auth guard, POST /leads/{id}/status for status changes
        with distinct error codes (400 for invalid status, 404 for not found).
"""
import pytest


class TestGetLeads:
    async def test_unauthenticated_redirects_to_login(self, client):
        resp = await client.get("/leads")
        assert resp.status_code == 303
        assert "/login" in resp.headers["location"]

    async def test_admin_gets_200(self, admin_client):
        resp = await admin_client.get("/leads")
        assert resp.status_code == 200

    async def test_user_role_gets_403(self, user_client):
        """Plan 111-03 — /leads now requires role admin|agent (require_agent_or_admin).

        Regular users (role='user') get 403 instead of the previous 200.
        """
        resp = await user_client.get("/leads")
        assert resp.status_code == 403

    async def test_no_excel_contacts_shown(self, admin_client):
        resp = await admin_client.get("/leads")
        assert b"import:excel" not in resp.content

    async def test_htmx_returns_partial(self, admin_client):
        resp = await admin_client.get("/leads", headers={"HX-Request": "true"})
        assert resp.status_code == 200


class TestChangeStatus:
    async def test_nonexistent_contact_returns_404(self, admin_client):
        resp = await admin_client.post("/leads/999999/status", data={"status": "bot_replied"})
        assert resp.status_code == 404

    async def test_invalid_status_returns_400(self, admin_client):
        resp = await admin_client.post("/leads/1/status", data={"status": "bogus_status"})
        assert resp.status_code == 400

    async def test_unauthenticated_redirects(self, client):
        resp = await client.post("/leads/1/status", data={"status": "contacted"})
        assert resp.status_code == 303


class TestAssignableUsersDropdownAfterSwap:
    """STAB-08 (TD-116-02): both HTMX swap handlers (update_lead_status,
    agent_assign) must render partials/lead_item.html with a NON-EMPTY
    assignable_users dropdown for admins, not the hardcoded `[]`.

    The template gate (lead_item.html:137) is
    `{% if user.role == 'admin' and assignable_users %}` — with `[]` the
    "Asignar a…" dropdown header never renders. The marker below
    ("Asignar a") appears in the rendered partial ONLY when
    assignable_users is truthy.
    """

    # Marker present ONLY inside the {% if … and assignable_users %} block
    # (button title at lead_item.html:148). "Asignar a asesor" never appears in
    # the false branch, and is distinct from the WA button's
    # "Asignar agente y abrir WhatsApp" title.
    DROPDOWN_MARKER = "Asignar a asesor"

    async def _make_contact(self, db, phone):
        """Create a pytest contact (test phone prefix → cleaned up by conftest)."""
        from app.models.contact import Contact
        from datetime import datetime, timezone
        c = Contact(
            name="STAB08 DropdownProbe",
            phone=phone,
            source="manual",
            status="new",
            created_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.commit()
        await db.refresh(c)
        return c

    async def test_status_swap_renders_nonempty_dropdown(self, admin_client, db):
        """After POST /leads/{id}/status the returned partial contains the
        non-empty assignable_users dropdown (RED today: handler passes []).
        """
        contact = await self._make_contact(db, "+595981999008")
        resp = await admin_client.post(
            f"/leads/{contact.id}/status",
            data={"status": "bot_replied"},
        )
        assert resp.status_code == 200
        assert self.DROPDOWN_MARKER in resp.text, (
            "assignable_users dropdown absent after status swap — "
            "handler is passing a hardcoded empty list"
        )

    async def test_agent_assign_swap_renders_nonempty_dropdown(self, admin_client, db, agent_client):
        """After POST /leads/{id}/agent-assign the returned partial contains
        the non-empty assignable_users dropdown (RED today: handler passes []).

        agent_client fixture seeds an active 'agent' user; we look it up so we
        assign to a valid active assignable target.
        """
        from app.repositories.user_repo import user_repo
        contact = await self._make_contact(db, "+595981999009")
        agent = await user_repo.get_by_email(db, "pytest_agent@onnixtest.com")
        assert agent is not None, "agent_client fixture should have seeded the test agent"

        resp = await admin_client.post(
            f"/leads/{contact.id}/agent-assign",
            data={"target_user_id": str(agent.id)},
        )
        assert resp.status_code == 200
        assert self.DROPDOWN_MARKER in resp.text, (
            "assignable_users dropdown absent after agent-assign swap — "
            "handler is passing a hardcoded empty list"
        )


class TestUpdateLeadStatusPersistsExplicitly:
    """STAB-01 (TD-115-01): update_lead_status mutates lead status via
    lead_service.change_status but the WRITE PATH relies SOLELY on get_db's
    commit-on-yield-exit (database.py:23). If anything after the mutation but
    before the generator close raises (or the connection is returned to the
    pool mid-transaction), the change can leave an idle-in-transaction backend.

    The fix makes the write path self-contained: explicit `await db.commit()`
    on success / `await db.rollback()` on error in the handler itself.

    Two assertions:
      1. SOURCE-level regression guard (the true RED→GREEN signal): the handler
         source must contain explicit `db.commit()` AND `db.rollback()`. This is
         RED today (handler has neither) and documents the exact STAB-01 change.
      2. BEHAVIOR guard: the new status persists and is readable from a SEPARATE
         fresh DB session.

    Per OQ-3 we do NOT attempt to reproduce a live idle-in-transaction leak in
    pytest; runtime acceptance is the OQ-3 query (usename='onnix') in the
    plan's Task 3, recorded in the SUMMARY.
    """

    async def _make_contact(self, db, phone):
        from app.models.contact import Contact
        from datetime import datetime, timezone
        c = Contact(
            name="STAB01 PersistProbe",
            phone=phone,
            source="manual",
            status="new",
            created_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.commit()
        await db.refresh(c)
        return c

    def test_handler_has_explicit_commit_and_rollback(self):
        """RED today: update_lead_status relies solely on get_db's
        commit-on-yield. After STAB-01 the handler must commit on success and
        roll back on error explicitly.
        """
        import inspect
        from app.routes import leads

        src = inspect.getsource(leads.update_lead_status)
        assert "db.commit()" in src, (
            "update_lead_status must explicitly `await db.commit()` on the "
            "success path (STAB-01) — currently relies SOLELY on get_db "
            "commit-on-yield-exit"
        )
        assert "db.rollback()" in src, (
            "update_lead_status must explicitly `await db.rollback()` on the "
            "error paths (STAB-01) so the transaction is not left open"
        )

    async def test_status_change_persists_in_fresh_session(self, admin_client, db):
        """After POST /leads/{id}/status the new status is persisted and
        readable from a SEPARATE fresh DB session (not the request session).
        """
        from app.models.contact import Contact
        from sqlalchemy import select
        from tests.conftest import _TestSession

        contact = await self._make_contact(db, "+595981999010")

        resp = await admin_client.post(
            f"/leads/{contact.id}/status",
            data={"status": "bot_replied"},
        )
        assert resp.status_code == 200

        # Fresh, independent session — proves the write was committed, not just
        # visible within the request's own (uncommitted) transaction.
        async with _TestSession() as fresh:
            row = await fresh.execute(
                select(Contact.status).where(Contact.id == contact.id)
            )
            persisted = row.scalar_one()
        assert persisted == "bot_replied", (
            f"lead status did not persist across sessions (got {persisted!r})"
        )


class TestLeadsCacheHeaders:
    """Mismo fix back-button que dashboard: /leads sirve full page y
    partial HTMX en la misma URL → Vary: HX-Request obligatorio y
    no-store en el partial."""

    async def test_full_page_has_vary_hx_request(self, admin_client):
        resp = await admin_client.get("/leads")
        assert "HX-Request" in resp.headers.get("vary", "")

    async def test_partial_has_cache_control_no_store(self, admin_client):
        resp = await admin_client.get(
            "/leads", headers={"HX-Request": "true"},
        )
        assert "HX-Request" in resp.headers.get("vary", "")
        assert resp.headers.get("cache-control") == "no-store"
