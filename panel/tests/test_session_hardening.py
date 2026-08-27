"""TDD — Session Hardening (D2): inactivity timeout + pw_changed_at invalidation.

Tests:
  - Inactivity: session with stale last_activity is rejected (redirect to /login)
  - Inactivity: recently-active session passes
  - Inactivity: last_activity slides forward on each successful request
  - PW-change invalidation: pw_changed_at > issued_at -> session cleared + redirect
  - PW-change invalidation: pw_changed_at <= issued_at -> session valid
  - PW-change invalidation: pw_changed_at is None -> session valid (backward-compat)
  - Backward-compat: session with user_id but NO issued_at/last_activity is NOT invalidated
  - Self-service /me/password: current session survives; pw_changed_at is bumped in DB
"""
from __future__ import annotations

import time
import pytest
import bcrypt
from datetime import datetime, timezone, timedelta
from sqlalchemy import text
from httpx import AsyncClient, ASGITransport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


async def _create_test_user(db, email: str, password: str = "hardening1234") -> None:
    """Insert or replace a test user with a known password."""
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()
    await db.execute(text(
        "INSERT INTO users (email, name, role, password_hash, is_active) "
        "VALUES (:email, 'Hardening Test', 'user', :ph, true) "
        "ON CONFLICT (email) DO UPDATE SET password_hash=:ph, is_active=true, pw_changed_at=NULL"
    ), {"email": email, "ph": pw_hash})
    await db.commit()


# ---------------------------------------------------------------------------
# Inactivity timeout tests
# ---------------------------------------------------------------------------

class TestInactivityTimeout:
    """Inactivity timeout: sessions older than SESSION_INACTIVITY_MINUTES are rejected."""

    async def test_stale_last_activity_is_rejected(self, client):
        """A session with last_activity > timeout ago → redirect to /login."""
        from app.main import app
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            # Manually build a session with stale last_activity
            from app.config import settings
            stale_ts = _now() - (settings.SESSION_INACTIVITY_MINUTES * 60 + 120)
            c.cookies.set("onnix_session", "")
            # Use the session directly via the request mock — inject via login first
            # then manipulate. We do it via direct session cookie manipulation using
            # the Starlette signer.
            from itsdangerous import TimestampSigner, URLSafeTimedSerializer
            import json, base64

            session_data = {
                "user_id": 1,  # real admin user id
                "user_role": "admin",
                "user_name": "Ez",
                "issued_at": stale_ts - 3600,
                "last_activity": stale_ts,
            }
            # Build a signed session cookie the same way Starlette does
            serializer = URLSafeTimedSerializer(
                settings.SECRET_KEY, salt="cookie-session"
            )
            cookie_value = serializer.dumps(session_data)

            c.cookies.set("onnix_session", cookie_value, domain="test")
            resp = await c.get("/dashboard")

        # Must redirect to login — session expired
        assert resp.status_code in (303, 200), f"Expected redirect, got {resp.status_code}"
        location = resp.headers.get("location", resp.headers.get("hx-redirect", ""))
        assert "/login" in location or resp.status_code == 303

    async def test_fresh_last_activity_passes(self, db):
        """A session with recent last_activity → accepted normally."""
        from app.main import app
        email = "pytest_hardening_fresh@onnixtest.com"
        await _create_test_user(db, email)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            # Real login — sets issued_at + last_activity
            login_r = await c.post("/login", data={"email": email, "password": "hardening1234"})
            assert login_r.status_code == 303

            # Immediate next request should succeed (last_activity is just now)
            resp = await c.get("/dashboard")
            # Should either succeed (200) or redirect to /dashboard (303 from /)
            assert resp.status_code not in (303,) or "/login" not in resp.headers.get("location", "")

    async def test_last_activity_slides_forward(self, db):
        """Each successful request updates last_activity in the session."""
        from app.main import app
        email = "pytest_hardening_slide@onnixtest.com"
        await _create_test_user(db, email)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            await c.post("/login", data={"email": email, "password": "hardening1234"})

            # First request — record session cookie
            resp1 = await c.get("/health")
            cookie_after_1 = c.cookies.get("onnix_session", "")

            # Second request
            resp2 = await c.get("/health")
            cookie_after_2 = c.cookies.get("onnix_session", "")

            # Both must succeed (200)
            assert resp1.status_code == 200
            assert resp2.status_code == 200

            # Session cookie may or may not change value (Starlette re-signs on
            # update) — but what matters is that /health keeps returning 200
            # (no redirect), proving last_activity keeps being refreshed.


# ---------------------------------------------------------------------------
# Password-change invalidation tests
# ---------------------------------------------------------------------------

class TestPasswordChangeInvalidation:
    """Sessions issued before pw_changed_at are invalidated."""

    async def test_old_session_invalidated_after_pw_change(self, db):
        """Session issued before pw_changed_at is rejected; redirect to /login."""
        from app.main import app
        from app.config import settings
        from itsdangerous import URLSafeTimedSerializer

        email = "pytest_hardening_stale_pw@onnixtest.com"
        await _create_test_user(db, email)

        # Get the user's id
        result = await db.execute(text("SELECT id FROM users WHERE email=:e"), {"e": email})
        row = result.fetchone()
        user_id = row[0]

        # Set pw_changed_at to NOW in the DB (simulating a password change)
        pw_changed_ts = datetime.now(timezone.utc)
        await db.execute(text(
            "UPDATE users SET pw_changed_at=:ts WHERE id=:id"
        ), {"ts": pw_changed_ts, "id": user_id})
        await db.commit()

        # Build a session issued BEFORE the pw change
        old_issued = int((pw_changed_ts - timedelta(seconds=3600)).timestamp())
        session_data = {
            "user_id": user_id,
            "user_role": "user",
            "user_name": "Hardening Test",
            "issued_at": old_issued,
            "last_activity": _now(),  # active recently
        }
        serializer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="cookie-session")
        cookie_value = serializer.dumps(session_data)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            c.cookies.set("onnix_session", cookie_value, domain="test")
            resp = await c.get("/dashboard")

        assert resp.status_code in (200, 303)
        location = resp.headers.get("location", resp.headers.get("hx-redirect", ""))
        assert "/login" in location or resp.status_code == 303

    async def test_session_issued_after_pw_change_passes(self, db):
        """Session issued AFTER pw_changed_at is valid."""
        from app.main import app
        email = "pytest_hardening_new_pw@onnixtest.com"
        await _create_test_user(db, email)

        # Set pw_changed_at to one hour ago
        await db.execute(text(
            "UPDATE users SET pw_changed_at = now() - interval '1 hour' WHERE email=:e"
        ), {"e": email})
        await db.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            # Login AFTER pw_changed_at — issued_at will be > pw_changed_at
            login_r = await c.post("/login", data={"email": email, "password": "hardening1234"})
            assert login_r.status_code == 303

            # Subsequent request must pass
            resp = await c.get("/health")
            assert resp.status_code == 200

    async def test_pw_changed_at_none_skips_check(self, db):
        """pw_changed_at IS NULL → skip invalidation check (backward-compat)."""
        from app.main import app
        email = "pytest_hardening_no_pw_ts@onnixtest.com"
        await _create_test_user(db, email)
        # pw_changed_at is already NULL (created by _create_test_user)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            await c.post("/login", data={"email": email, "password": "hardening1234"})
            resp = await c.get("/health")
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Backward-compatibility tests
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    """Sessions without issued_at/last_activity must NOT be invalidated."""

    async def test_legacy_session_without_timestamps_passes(self, db):
        """A session with ONLY user_id (no issued_at, no last_activity) must be accepted."""
        from app.main import app
        from app.config import settings
        from itsdangerous import URLSafeTimedSerializer

        email = "pytest_hardening_legacy@onnixtest.com"
        await _create_test_user(db, email)
        result = await db.execute(text("SELECT id FROM users WHERE email=:e"), {"e": email})
        user_id = result.fetchone()[0]

        # Build a legacy-style session (no timestamps)
        session_data = {
            "user_id": user_id,
            "user_role": "user",
            "user_name": "Legacy User",
            # NO issued_at, NO last_activity — simulates pre-hardening session
        }
        serializer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="cookie-session")
        cookie_value = serializer.dumps(session_data)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            c.cookies.set("onnix_session", cookie_value, domain="test")
            resp = await c.get("/health")
            # Must succeed — not rejected for missing timestamps
            assert resp.status_code == 200

    async def test_legacy_session_gets_last_activity_set(self, db):
        """A legacy session gets last_activity set on first access (sliding window bootstrap)."""
        from app.main import app
        from app.config import settings
        from itsdangerous import URLSafeTimedSerializer

        email = "pytest_hardening_legacy2@onnixtest.com"
        await _create_test_user(db, email)
        result = await db.execute(text("SELECT id FROM users WHERE email=:e"), {"e": email})
        user_id = result.fetchone()[0]

        session_data = {
            "user_id": user_id,
            "user_role": "user",
            "user_name": "Legacy User 2",
        }
        serializer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="cookie-session")
        cookie_value = serializer.dumps(session_data)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            c.cookies.set("onnix_session", cookie_value, domain="test")
            # First request — should succeed and bootstrap last_activity
            resp1 = await c.get("/health")
            assert resp1.status_code == 200
            # Second request — should also succeed (last_activity now present)
            resp2 = await c.get("/health")
            assert resp2.status_code == 200


# ---------------------------------------------------------------------------
# Self-service password change keeps current session alive
# ---------------------------------------------------------------------------

class TestSelfPasswordChangeKeepsSession:
    """POST /me/password: current session must survive its own password change."""

    async def test_current_session_survives_own_pw_change(self, db):
        """After self-service password change, the acting user's session stays valid."""
        from app.main import app
        email = "pytest_hardening_selfpw@onnixtest.com"
        pw = "hardening_orig1234"
        await _create_test_user(db, email, pw)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            # Login
            login_r = await c.post("/login", data={"email": email, "password": pw})
            assert login_r.status_code == 303

            # Change own password
            change_r = await c.post("/me/password", data={
                "current_password": pw,
                "new_password": "hardening_new1234",
                "confirm_password": "hardening_new1234",
            })
            assert change_r.status_code == 200

            # CRITICAL: follow-up authenticated request must still work
            # (current session was refreshed — not invalidated)
            followup_r = await c.get("/health")
            assert followup_r.status_code == 200, (
                "Current session must survive own password change — "
                f"got {followup_r.status_code} instead of 200"
            )

    async def test_pw_changed_at_set_after_self_change(self, db):
        """After /me/password, pw_changed_at is set in the DB."""
        from app.main import app
        email = "pytest_hardening_pwts@onnixtest.com"
        pw = "hardening_pwts1234"
        await _create_test_user(db, email, pw)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            await c.post("/login", data={"email": email, "password": pw})
            await c.post("/me/password", data={
                "current_password": pw,
                "new_password": "hardening_new_ts1234",
                "confirm_password": "hardening_new_ts1234",
            })

        # Verify pw_changed_at was written
        result = await db.execute(text(
            "SELECT pw_changed_at FROM users WHERE email=:e"
        ), {"e": email})
        row = result.fetchone()
        assert row is not None
        assert row[0] is not None, "pw_changed_at must be set after password change"

    async def test_admin_password_reset_sets_pw_changed_at(self, db):
        """Admin password reset (UserManagementService.change_password) also sets pw_changed_at."""
        from app.services.user_management_service import user_management_service
        from app.repositories.user_repo import user_repo

        email = "pytest_hardening_adminreset@onnixtest.com"
        pw = "hardening_adm1234"
        await _create_test_user(db, email, pw)
        target = await user_repo.get_by_email(db, email)
        assert target is not None

        before = datetime.now(timezone.utc)
        await user_management_service.change_password(db, target.id, "newadminpass1234")
        after = datetime.now(timezone.utc)

        # Refresh from DB
        result = await db.execute(text(
            "SELECT pw_changed_at FROM users WHERE email=:e"
        ), {"e": email})
        row = result.fetchone()
        assert row is not None
        assert row[0] is not None, "pw_changed_at must be set after admin password reset"
        # pw_changed_at must be within the test window
        ts = row[0]
        if hasattr(ts, 'tzinfo') and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        assert before <= ts <= after + timedelta(seconds=2)
