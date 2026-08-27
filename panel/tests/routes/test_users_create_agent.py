"""GET /users/create-agent — el 301 y su gating (Phase 111-06, podado en el carril D).

Spec original: .planning/phases/110-m6.1-plan-roles-auth/110-01-PLAN.md §7.2.

El POST que acompañaba a este GET se borró: ninguna plantilla del panel
posteaba ahí, y el alta real es POST /users desde el modal de
/settings?tab=usuarios, con su propia suite en tests/test_routes_users.py.
Lo que queda acá es el redirect —que sigue siendo la puerta que un bookmark
viejo encuentra— y su gating por rol.
"""
from __future__ import annotations

import os

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text


_TEST_PASSWORD = os.environ.get("TEST_ADMIN_PASSWORD", "test-fallback-only")


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def agent_client(db):
    """HTTP client with an active session for a NON-admin agent user.

    Inserts a temp users row with role='agent' (bcrypt cost 12 hash of 'test123').
    Cleanup via cleanup_test_data fixture (covers pytest_%@onnixtest.com).
    """
    await db.execute(text(
        "INSERT INTO users (email, name, role, password_hash, is_active) "
        "VALUES ('pytest_agentauth@onnixtest.com', 'Agent Auth', 'agent', "
        "'$2b$12$q7iHvYIHneLdjGsG2AZFLeKKEyerDlRc4wkIvfPFPgVja403Ue7Xu', true) "
        "ON CONFLICT (email) DO UPDATE "
        "SET password_hash = EXCLUDED.password_hash, role='agent', is_active=true"
    ))
    await db.commit()

    from app.main import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        login_resp = await c.post("/login", data={
            "email": "pytest_agentauth@onnixtest.com",
            "password": "test123",
        })
        assert login_resp.status_code == 303, (
            f"agent_client login failed: {login_resp.status_code} "
            f"body={login_resp.content[:200]!r}"
        )
        yield c


# ---------------------------------------------------------------------------
# GET /users/create-agent — auth gating + form
# ---------------------------------------------------------------------------

class TestGetCreateAgentAuth:
    async def test_unauthenticated_redirects_to_login(self, client):
        resp = await client.get("/users/create-agent")
        assert resp.status_code == 303
        assert "/login" in resp.headers["location"]

    async def test_user_role_gets_403(self, user_client):
        resp = await user_client.get("/users/create-agent")
        assert resp.status_code == 403

    async def test_agent_role_gets_403(self, agent_client):
        resp = await agent_client.get("/users/create-agent")
        assert resp.status_code == 403

    async def test_admin_gets_301_redirect(self, admin_client):
        """GET /users/create-agent now 301-redirects to /settings?tab=usuarios."""
        resp = await admin_client.get("/users/create-agent")
        assert resp.status_code == 301
        assert resp.headers["location"] == "/settings?tab=usuarios"


# ---------------------------------------------------------------------------
# Nav discoverability — Phase 112-04
# Ensures admin-only links added in sidebar/users page are reachable via UI.
# ---------------------------------------------------------------------------

class TestNavDiscoverability:
    async def test_settings_page_is_accessible(self, admin_client):
        """GET /settings as admin → 200 (settings page accessible)."""
        resp = await admin_client.get("/settings")
        assert resp.status_code == 200

    async def test_dashboard_admin_has_no_auth_audit_sidebar_link(self, admin_client):
        """GET /dashboard as admin → sidebar no longer has /admin/auth-audit directly."""
        resp = await admin_client.get("/dashboard")
        assert resp.status_code == 200
        assert b"/admin/auth-audit" not in resp.content, "/admin/auth-audit should not be in sidebar"

    async def test_users_page_non_admin_no_crear_agente_link(self, user_client):
        """GET /users as non-admin → should 403 (admin-only route, no leak)."""
        resp = await user_client.get("/users")
        # /users is admin-only; non-admin gets 403 before any template renders
        assert resp.status_code == 403
