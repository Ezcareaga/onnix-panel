"""Plan 111-03 (Test #11 §8) — RED tests for require_agent_or_admin dependency.

Verifies the dependency:
    - allows admin (role='admin')
    - allows agent (role='agent')
    - blocks user (role='user') with 403
    - redirects unauthenticated to /login (303)
"""
from __future__ import annotations

import os
import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import Depends, FastAPI

# conftest.py at <raíz del repo>/panel/tests/conftest.py is auto-discovered
# by pytest via rootdir lookup; nothing to import here.


@pytest.fixture
async def role_app(db):
    """Mount the real require_agent_or_admin dependency on a minimal test route.

    Reuses the production app dependency stack (get_db override is already
    applied in conftest.py). Returns an ASGI client bound to a test app
    that exposes /test-roles-route which depends on require_agent_or_admin.
    """
    from app.dependencies import require_agent_or_admin
    from app.main import app as main_app

    # Add a temporary test route to the main app so session + DB overrides
    # already configured in conftest.py keep working transparently.
    if not any(getattr(r, "path", None) == "/test-roles-route" for r in main_app.routes):
        @main_app.get("/test-roles-route")
        async def _test_roles_route(user=Depends(require_agent_or_admin)):
            return {"ok": True, "role": user.role}

    yield  # nothing returned — clients are constructed by caller


def _psql(sql: str) -> None:
    """Run SQL inside the postgres container against onnix_dev."""
    import subprocess
    subprocess.run(
        ["docker", "exec", "onnix-postgres",
         "psql", "-U", "onnix", "-d", os.environ["POSTGRES_DB"], "-c", sql],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=10,
    )


@pytest.fixture
async def agent_client():
    """HTTP client authenticated as a temp user with role='agent'."""
    # bcrypt hash for "test123" — same hash used in user_client fixture
    _psql(
        "INSERT INTO users (email, name, role, password_hash, is_active) "
        "VALUES ('pytest_agent@onnixtest.com', 'Test Agent', 'agent', "
        "'$2b$12$q7iHvYIHneLdjGsG2AZFLeKKEyerDlRc4wkIvfPFPgVja403Ue7Xu', true) "
        "ON CONFLICT (email) DO UPDATE SET "
        "password_hash = EXCLUDED.password_hash, "
        "role = 'agent', is_active = true"
    )
    from app.main import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        await c.post("/login", data={
            "email": "pytest_agent@onnixtest.com",
            "password": "test123",
        })
        yield c


class TestRequireAgentOrAdmin:
    async def test_admin_allowed(self, role_app, admin_client):
        resp = await admin_client.get("/test-roles-route")
        assert resp.status_code == 200
        assert resp.json()["role"] == "admin"

    async def test_agent_allowed(self, role_app, agent_client):
        resp = await agent_client.get("/test-roles-route")
        assert resp.status_code == 200
        assert resp.json()["role"] == "agent"

    async def test_user_blocked_with_403(self, role_app, user_client):
        resp = await user_client.get("/test-roles-route")
        assert resp.status_code == 403
        # Error message per Plan 110-01 §6.2 / Plan 111-03 must_haves
        assert b"administradores" in resp.content.lower() or \
               b"asesores" in resp.content.lower()

    async def test_unauthenticated_redirects(self, role_app, client):
        resp = await client.get("/test-roles-route")
        assert resp.status_code == 303
        assert "/login" in resp.headers.get("location", "")
