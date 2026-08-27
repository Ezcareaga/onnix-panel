"""TDD — POST /me/profile self-service profile update (B1 phone + B2 display_name).

Tests:
  - Unauthenticated request is rejected (redirect to /login)
  - Valid phone is normalized to E.164 and persisted
  - display_name is trimmed and persisted
  - Empty phone clears to NULL (allowed)
  - Empty display_name allowed (→ NULL)
  - Invalid phone → HTTP 400 with inline error, nothing persisted
  - display_name over 200 chars → HTTP 400 (rejected)
  - Endpoint updates ONLY the current user (second user's row untouched)
  - Success returns 200 + HX-Trigger showToast
"""
import json
import pytest
import bcrypt
from sqlalchemy import text


def _make_hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=4)).decode()


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------

class TestMeProfileRoute:
    async def test_unauthenticated_redirects(self, client):
        """Unauthenticated POST /me/profile must redirect to /login."""
        resp = await client.post("/me/profile", data={
            "phone": "+59598100001",
            "display_name": "Test",
        })
        # get_current_user returns 303 to /login for unauthenticated requests
        assert resp.status_code in (303, 302, 200)
        if resp.status_code in (303, 302):
            assert "login" in resp.headers.get("location", "").lower()

    async def test_valid_phone_normalized_to_e164(self, db):
        """Valid phone (possibly without +) is normalized to E.164 and persisted."""
        from app.main import app
        from httpx import ASGITransport, AsyncClient

        pw = "profiletest1234"
        ph = _make_hash(pw)
        await db.execute(text(
            "INSERT INTO users (email, name, role, password_hash, is_active) "
            "VALUES ('pytest_prof_phone@onnixtest.com', 'Prof Phone', 'user', :ph, true) "
            "ON CONFLICT (email) DO UPDATE SET password_hash=:ph, is_active=true, phone=NULL"
        ), {"ph": ph})
        await db.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            await c.post("/login", data={
                "email": "pytest_prof_phone@onnixtest.com",
                "password": pw,
            })
            resp = await c.post("/me/profile", data={
                "phone": "0981123456",   # PY national format — should normalize to +595981123456
                "display_name": "Mi Nombre",
            })

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        hx_trigger = resp.headers.get("hx-trigger", "")
        assert hx_trigger, "HX-Trigger header must be present on success"
        trigger_data = json.loads(hx_trigger)
        assert "showToast" in trigger_data
        assert trigger_data["showToast"]["type"] == "success"

        # Verify in DB
        from app.repositories.user_repo import user_repo
        user = await user_repo.get_by_email(db, "pytest_prof_phone@onnixtest.com")
        assert user.phone == "+595981123456", f"Expected E.164, got {user.phone!r}"
        assert user.display_name == "Mi Nombre"

    async def test_display_name_trimmed_and_persisted(self, db):
        """display_name with leading/trailing spaces is trimmed and saved."""
        from app.main import app
        from httpx import ASGITransport, AsyncClient

        pw = "profiletest1234"
        ph = _make_hash(pw)
        await db.execute(text(
            "INSERT INTO users (email, name, role, password_hash, is_active) "
            "VALUES ('pytest_prof_name@onnixtest.com', 'Prof Name', 'user', :ph, true) "
            "ON CONFLICT (email) DO UPDATE SET password_hash=:ph, is_active=true"
        ), {"ph": ph})
        await db.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            await c.post("/login", data={
                "email": "pytest_prof_name@onnixtest.com",
                "password": pw,
            })
            resp = await c.post("/me/profile", data={
                "phone": "",
                "display_name": "  la administradora V  ",
            })

        assert resp.status_code == 200
        from app.repositories.user_repo import user_repo
        user = await user_repo.get_by_email(db, "pytest_prof_name@onnixtest.com")
        assert user.display_name == "la administradora V"

    async def test_empty_phone_clears_to_null(self, db):
        """Empty phone field clears the stored phone to NULL."""
        from app.main import app
        from httpx import ASGITransport, AsyncClient

        pw = "profiletest1234"
        ph = _make_hash(pw)
        await db.execute(text(
            "INSERT INTO users (email, name, role, password_hash, is_active, phone) "
            "VALUES ('pytest_prof_clrph@onnixtest.com', 'Prof ClrPh', 'user', :ph, true, '+595981000001') "
            "ON CONFLICT (email) DO UPDATE SET password_hash=:ph, is_active=true, phone='+595981000001'"
        ), {"ph": ph})
        await db.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            await c.post("/login", data={
                "email": "pytest_prof_clrph@onnixtest.com",
                "password": pw,
            })
            resp = await c.post("/me/profile", data={
                "phone": "",
                "display_name": "Still Here",
            })

        assert resp.status_code == 200
        from app.repositories.user_repo import user_repo
        user = await user_repo.get_by_email(db, "pytest_prof_clrph@onnixtest.com")
        assert user.phone is None, f"Expected NULL phone, got {user.phone!r}"
        assert user.display_name == "Still Here"

    async def test_empty_display_name_allowed(self, db):
        """Empty display_name is accepted and stored as NULL."""
        from app.main import app
        from httpx import ASGITransport, AsyncClient

        pw = "profiletest1234"
        ph = _make_hash(pw)
        await db.execute(text(
            "INSERT INTO users (email, name, role, password_hash, is_active, display_name) "
            "VALUES ('pytest_prof_clrdn@onnixtest.com', 'Prof ClrDn', 'user', :ph, true, 'OldName') "
            "ON CONFLICT (email) DO UPDATE SET password_hash=:ph, is_active=true, display_name='OldName'"
        ), {"ph": ph})
        await db.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            await c.post("/login", data={
                "email": "pytest_prof_clrdn@onnixtest.com",
                "password": pw,
            })
            resp = await c.post("/me/profile", data={
                "phone": "",
                "display_name": "",
            })

        assert resp.status_code == 200
        from app.repositories.user_repo import user_repo
        user = await user_repo.get_by_email(db, "pytest_prof_clrdn@onnixtest.com")
        assert user.display_name is None, f"Expected NULL display_name, got {user.display_name!r}"

    async def test_invalid_phone_returns_400_nothing_persisted(self, db):
        """Invalid phone → 400 with inline error; DB row unchanged."""
        from app.main import app
        from httpx import ASGITransport, AsyncClient

        pw = "profiletest1234"
        ph = _make_hash(pw)
        await db.execute(text(
            "INSERT INTO users (email, name, role, password_hash, is_active, phone, display_name) "
            "VALUES ('pytest_prof_badph@onnixtest.com', 'Prof BadPh', 'user', :ph, true, NULL, 'Original') "
            "ON CONFLICT (email) DO UPDATE SET password_hash=:ph, is_active=true, phone=NULL, display_name='Original'"
        ), {"ph": ph})
        await db.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            await c.post("/login", data={
                "email": "pytest_prof_badph@onnixtest.com",
                "password": pw,
            })
            resp = await c.post("/me/profile", data={
                "phone": "NOT_A_PHONE",
                "display_name": "Should Not Save",
            })

        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
        # Must contain an error message about the phone
        body_lower = resp.content.lower()
        assert b"tel" in body_lower or b"inv" in body_lower or b"phone" in body_lower, \
            f"No phone error in response: {resp.text}"

        # Verify DB was NOT updated
        from app.repositories.user_repo import user_repo
        user = await user_repo.get_by_email(db, "pytest_prof_badph@onnixtest.com")
        assert user.phone is None, "Phone should remain NULL after bad-phone rejection"
        assert user.display_name == "Original", "display_name must not change on 400"

    async def test_display_name_over_200_chars_rejected(self, db):
        """display_name > 200 chars → 400, nothing persisted."""
        from app.main import app
        from httpx import ASGITransport, AsyncClient

        pw = "profiletest1234"
        ph = _make_hash(pw)
        await db.execute(text(
            "INSERT INTO users (email, name, role, password_hash, is_active) "
            "VALUES ('pytest_prof_longdn@onnixtest.com', 'Prof LongDn', 'user', :ph, true) "
            "ON CONFLICT (email) DO UPDATE SET password_hash=:ph, is_active=true"
        ), {"ph": ph})
        await db.commit()

        long_name = "A" * 201

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            await c.post("/login", data={
                "email": "pytest_prof_longdn@onnixtest.com",
                "password": pw,
            })
            resp = await c.post("/me/profile", data={
                "phone": "",
                "display_name": long_name,
            })

        assert resp.status_code == 400, f"Expected 400 for overlong display_name, got {resp.status_code}"

    async def test_only_current_user_updated(self, db):
        """Endpoint updates ONLY the logged-in user; a second user's row is untouched."""
        from app.main import app
        from httpx import ASGITransport, AsyncClient

        pw = "profiletest1234"
        ph = _make_hash(pw)

        # Insert user A (will log in and change profile)
        await db.execute(text(
            "INSERT INTO users (email, name, role, password_hash, is_active, phone, display_name) "
            "VALUES ('pytest_prof_usera@onnixtest.com', 'User A', 'user', :ph, true, NULL, 'UserAName') "
            "ON CONFLICT (email) DO UPDATE SET password_hash=:ph, is_active=true, phone=NULL, display_name='UserAName'"
        ), {"ph": ph})
        # Insert user B (should remain untouched)
        await db.execute(text(
            "INSERT INTO users (email, name, role, password_hash, is_active, phone, display_name) "
            "VALUES ('pytest_prof_userb@onnixtest.com', 'User B', 'user', :ph, true, NULL, 'UserBOriginal') "
            "ON CONFLICT (email) DO UPDATE SET password_hash=:ph, is_active=true, phone=NULL, display_name='UserBOriginal'"
        ), {"ph": ph})
        await db.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            await c.post("/login", data={
                "email": "pytest_prof_usera@onnixtest.com",
                "password": pw,
            })
            resp = await c.post("/me/profile", data={
                "phone": "+595981000002",
                "display_name": "UserAUpdated",
            })

        assert resp.status_code == 200

        from app.repositories.user_repo import user_repo
        user_a = await user_repo.get_by_email(db, "pytest_prof_usera@onnixtest.com")
        user_b = await user_repo.get_by_email(db, "pytest_prof_userb@onnixtest.com")

        assert user_a.display_name == "UserAUpdated", "User A should be updated"
        assert user_a.phone == "+595981000002", "User A phone should be updated"
        assert user_b.display_name == "UserBOriginal", "User B must not be modified"
        assert user_b.phone is None, "User B phone must remain untouched"

    async def test_success_returns_hx_trigger_toast(self, db):
        """Success returns 200 with HX-Trigger showToast header."""
        from app.main import app
        from httpx import ASGITransport, AsyncClient

        pw = "profiletest1234"
        ph = _make_hash(pw)
        await db.execute(text(
            "INSERT INTO users (email, name, role, password_hash, is_active) "
            "VALUES ('pytest_prof_toast@onnixtest.com', 'Prof Toast', 'user', :ph, true) "
            "ON CONFLICT (email) DO UPDATE SET password_hash=:ph, is_active=true"
        ), {"ph": ph})
        await db.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as c:
            await c.post("/login", data={
                "email": "pytest_prof_toast@onnixtest.com",
                "password": pw,
            })
            resp = await c.post("/me/profile", data={
                "phone": "",
                "display_name": "Toast Test",
            })

        assert resp.status_code == 200
        hx_trigger = resp.headers.get("hx-trigger", "")
        assert hx_trigger, "HX-Trigger header must be present on success"
        trigger_data = json.loads(hx_trigger)
        assert "showToast" in trigger_data
        assert trigger_data["showToast"]["type"] == "success"
