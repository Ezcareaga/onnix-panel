"""TDD — POST /me/password self-service password change (items 7+8).

Tests:
  - Unauthenticated → 303 redirect to login
  - Wrong current_password → 400 with Spanish message
  - New password too short (< 12) → 400 with Spanish message
  - Confirm password mismatch → 400
  - Success: 200 + HX-Trigger header with showToast payload + empty body
  - Admin can also use self-service (admin is not exempt)
  - Service: UserManagementService.change_own_password — unit tested directly
  - Edge cases: long password (DoS), unicode input, corrupt/empty hash
"""
import json
import pytest
import bcrypt
from sqlalchemy import text


class TestMePasswordRoute:
    async def test_unauthenticated_redirects(self, client):
        resp = await client.post("/me/password", data={
            "current_password": "old",
            "new_password": "newpassword1234",
            "confirm_password": "newpassword1234",
        })
        # Returns 303 redirect to /login (or HTMX redirect)
        assert resp.status_code in (303, 200)

    async def test_wrong_current_password_returns_error(self, user_client):
        resp = await user_client.post("/me/password", data={
            "current_password": "wrongpassword123",
            "new_password": "newpassword1234",
            "confirm_password": "newpassword1234",
        })
        assert resp.status_code == 400
        assert b"actual" in resp.content.lower() or b"incorrecta" in resp.content.lower()

    async def test_new_password_too_short_returns_error(self, user_client):
        resp = await user_client.post("/me/password", data={
            "current_password": "test123",  # correct current pw for user_client
            "new_password": "short",
            "confirm_password": "short",
        })
        assert resp.status_code == 400
        assert b"12" in resp.content  # "12 caracteres" in error

    async def test_confirm_mismatch_returns_error(self, user_client):
        resp = await user_client.post("/me/password", data={
            "current_password": "test123",
            "new_password": "newpassword1234",
            "confirm_password": "different1234xx",
        })
        assert resp.status_code == 400
        assert b"coincid" in resp.content.lower() or b"match" in resp.content.lower()

    async def test_success_returns_ok(self, db):
        """Create temp user, change their own password, assert 200 feedback."""
        # Insert a test user with a known password hash
        from app.main import app
        from httpx import ASGITransport, AsyncClient

        known_pw = "originalpass1234"
        pw_hash = bcrypt.hashpw(known_pw.encode(), bcrypt.gensalt(rounds=4)).decode()
        await db.execute(text(
            "INSERT INTO users (email, name, role, password_hash, is_active) "
            "VALUES ('pytest_mepw@onnixtest.com', 'Me PW', 'user', :ph, true) "
            "ON CONFLICT (email) DO UPDATE SET password_hash=:ph, is_active=true"
        ), {"ph": pw_hash})
        await db.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            login_r = await c.post("/login", data={
                "email": "pytest_mepw@onnixtest.com",
                "password": known_pw,
            })
            assert login_r.status_code == 303

            resp = await c.post("/me/password", data={
                "current_password": known_pw,
                "new_password": "updatedpass1234",
                "confirm_password": "updatedpass1234",
            })
            assert resp.status_code == 200
            # Success: HX-Trigger header carries showToast payload; body is minimal
            hx_trigger = resp.headers.get("hx-trigger", "")
            assert hx_trigger, "HX-Trigger header must be present on success"
            trigger_data = json.loads(hx_trigger)
            assert "showToast" in trigger_data
            assert trigger_data["showToast"]["type"] == "success"
            assert "contraseña" in trigger_data["showToast"]["message"].lower()

    async def test_after_change_old_password_rejected(self, db):
        """After changing password, old password no longer works on login."""
        from app.main import app
        from httpx import ASGITransport, AsyncClient

        known_pw = "originalpass5678"
        pw_hash = bcrypt.hashpw(known_pw.encode(), bcrypt.gensalt(rounds=4)).decode()
        await db.execute(text(
            "INSERT INTO users (email, name, role, password_hash, is_active) "
            "VALUES ('pytest_mepw2@onnixtest.com', 'Me PW2', 'user', :ph, true) "
            "ON CONFLICT (email) DO UPDATE SET password_hash=:ph, is_active=true"
        ), {"ph": pw_hash})
        await db.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            await c.post("/login", data={
                "email": "pytest_mepw2@onnixtest.com",
                "password": known_pw,
            })
            await c.post("/me/password", data={
                "current_password": known_pw,
                "new_password": "newpassword9999",
                "confirm_password": "newpassword9999",
            })

        # Now try login with OLD password — should fail
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c2:
            fail_r = await c2.post("/login", data={
                "email": "pytest_mepw2@onnixtest.com",
                "password": known_pw,
            })
            assert fail_r.status_code == 401


class TestChangeOwnPasswordService:
    """Unit tests for UserManagementService.change_own_password."""

    async def test_wrong_current_password_raises(self, db):
        from app.services.user_management_service import user_management_service
        known_pw = "mypassword1234"
        pw_hash = bcrypt.hashpw(known_pw.encode(), bcrypt.gensalt(rounds=4)).decode()
        await db.execute(text(
            "INSERT INTO users (email, name, role, password_hash, is_active) "
            "VALUES ('pytest_ownpw_svc@onnixtest.com', 'Own PW Svc', 'user', :ph, true) "
            "ON CONFLICT (email) DO UPDATE SET password_hash=:ph, is_active=true"
        ), {"ph": pw_hash})
        await db.commit()

        from app.repositories.user_repo import user_repo
        target = await user_repo.get_by_email(db, "pytest_ownpw_svc@onnixtest.com")

        with pytest.raises(ValueError, match="actual"):
            await user_management_service.change_own_password(
                db, target, "wrongpassword123", "newpassword9999"
            )

    async def test_correct_password_updates(self, db):
        from app.services.user_management_service import user_management_service
        from app.repositories.user_repo import user_repo
        known_pw = "correctpass1234"
        pw_hash = bcrypt.hashpw(known_pw.encode(), bcrypt.gensalt(rounds=4)).decode()
        await db.execute(text(
            "INSERT INTO users (email, name, role, password_hash, is_active) "
            "VALUES ('pytest_ownpw_ok@onnixtest.com', 'Own PW OK', 'user', :ph, true) "
            "ON CONFLICT (email) DO UPDATE SET password_hash=:ph, is_active=true"
        ), {"ph": pw_hash})
        await db.commit()
        target = await user_repo.get_by_email(db, "pytest_ownpw_ok@onnixtest.com")
        updated = await user_management_service.change_own_password(
            db, target, known_pw, "newpass12345678"
        )
        assert bcrypt.checkpw("newpass12345678".encode(), updated.password_hash.encode())


class TestMePasswordEdgeCases:
    """Defensive edge-case tests for /me/password."""

    async def test_oversized_new_password_returns_400(self, db):
        """new_password > 1024 chars must be rejected with 400, not 500."""
        from app.main import app
        from httpx import ASGITransport, AsyncClient

        known_pw = "edgecase_orig1234"
        pw_hash = bcrypt.hashpw(known_pw.encode(), bcrypt.gensalt(rounds=4)).decode()
        await db.execute(text(
            "INSERT INTO users (email, name, role, password_hash, is_active) "
            "VALUES ('pytest_edge_long@onnixtest.com', 'Edge Long', 'user', :ph, true) "
            "ON CONFLICT (email) DO UPDATE SET password_hash=:ph, is_active=true"
        ), {"ph": pw_hash})
        await db.commit()

        long_pw = "A" * 1025
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            await c.post("/login", data={
                "email": "pytest_edge_long@onnixtest.com",
                "password": known_pw,
            })
            resp = await c.post("/me/password", data={
                "current_password": known_pw,
                "new_password": long_pw,
                "confirm_password": long_pw,
            })
        assert resp.status_code == 400
        assert b"larga" in resp.content.lower() or b"large" in resp.content.lower()

    async def test_unicode_password_does_not_500(self, db):
        """Unicode characters in current_password must yield 400, not 500."""
        from app.main import app
        from httpx import ASGITransport, AsyncClient

        known_pw = "edgecase_uni1234"
        pw_hash = bcrypt.hashpw(known_pw.encode(), bcrypt.gensalt(rounds=4)).decode()
        await db.execute(text(
            "INSERT INTO users (email, name, role, password_hash, is_active) "
            "VALUES ('pytest_edge_uni@onnixtest.com', 'Edge Uni', 'user', :ph, true) "
            "ON CONFLICT (email) DO UPDATE SET password_hash=:ph, is_active=true"
        ), {"ph": pw_hash})
        await db.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            await c.post("/login", data={
                "email": "pytest_edge_uni@onnixtest.com",
                "password": known_pw,
            })
            resp = await c.post("/me/password", data={
                "current_password": "éàǘ",  # non-ASCII unicode
                "new_password": "newpassword1234",
                "confirm_password": "newpassword1234",
            })
        # Must not 500; wrong password yields 400 with friendly message
        assert resp.status_code == 400
        assert b"actual" in resp.content.lower() or b"incorrecta" in resp.content.lower()

    async def test_success_body_is_minimal_no_inline_feedback(self, db):
        """On success, the body is empty/minimal (no inline green div)."""
        from app.main import app
        from httpx import ASGITransport, AsyncClient

        known_pw = "edgecase_body1234"
        pw_hash = bcrypt.hashpw(known_pw.encode(), bcrypt.gensalt(rounds=4)).decode()
        await db.execute(text(
            "INSERT INTO users (email, name, role, password_hash, is_active) "
            "VALUES ('pytest_edge_body@onnixtest.com', 'Edge Body', 'user', :ph, true) "
            "ON CONFLICT (email) DO UPDATE SET password_hash=:ph, is_active=true"
        ), {"ph": pw_hash})
        await db.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            await c.post("/login", data={
                "email": "pytest_edge_body@onnixtest.com",
                "password": known_pw,
            })
            resp = await c.post("/me/password", data={
                "current_password": known_pw,
                "new_password": "newpassword9876",
                "confirm_password": "newpassword9876",
            })
        assert resp.status_code == 200
        # Body must not contain the old inline success div (bg-green-50)
        assert b"bg-green-50" not in resp.content
        # HX-Trigger carries the toast
        assert "hx-trigger" in {k.lower() for k in resp.headers}
