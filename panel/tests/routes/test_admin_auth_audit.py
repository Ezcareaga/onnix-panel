"""TDD — GET /admin/auth-audit + POST /admin/auth-audit/unlock-email

Tests:
  - Auth gating (admin only; 303 sin sesión, 403 con role=user)
  - Filtros email / ip / date_from / date_to (combinables)
  - Paginación (50 filas por página vía ?page=N)
  - Orden DESC por created_at
  - POST unlock-email inserta fila result='success' ip='admin-unlock' y redirige

Tabla auth_audit creada por migración 039 (Plan 111-01). Tests crean filas
directas con SQL contra onnix_dev.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text


AUDIT_TEST_EMAIL_LIKE = "pytest_auditroute_%@onnixtest.com"


def _delete_test_audit_rows(db_session_sync_sql: str | None = None):
    """Lazy helper -- not used directly; cleanup runs via async fixture below."""


@pytest_asyncio.fixture
async def clean_audit_rows(db):
    """Remove any auth_audit rows created by these tests before & after."""
    await db.execute(text(
        "DELETE FROM auth_audit WHERE email LIKE :p"
    ), {"p": AUDIT_TEST_EMAIL_LIKE})
    await db.commit()
    yield
    await db.execute(text(
        "DELETE FROM auth_audit WHERE email LIKE :p OR ip = 'admin-unlock'"
    ), {"p": AUDIT_TEST_EMAIL_LIKE})
    await db.commit()


async def _insert_audit_row(db, *, email: str, ip: str = "1.1.1.1",
                            user_agent: str = "pytest", result: str = "success",
                            created_at: datetime | None = None) -> int:
    if created_at is None:
        sql = text("""
            INSERT INTO auth_audit (email, ip, user_agent, result)
            VALUES (:email, :ip, :ua, :result) RETURNING id
        """)
        params = {"email": email, "ip": ip, "ua": user_agent, "result": result}
    else:
        sql = text("""
            INSERT INTO auth_audit (email, ip, user_agent, result, created_at)
            VALUES (:email, :ip, :ua, :result, :created_at) RETURNING id
        """)
        params = {"email": email, "ip": ip, "ua": user_agent,
                  "result": result, "created_at": created_at}
    row = (await db.execute(sql, params)).scalar_one()
    await db.commit()
    return row


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------

class TestAuthAuditAuth:
    async def test_unauthenticated_redirects_to_login(self, client):
        resp = await client.get("/admin/auth-audit")
        assert resp.status_code == 303
        assert "/login" in resp.headers.get("location", "")

    async def test_user_role_gets_403(self, user_client):
        resp = await user_client.get("/admin/auth-audit")
        assert resp.status_code == 403

    async def test_admin_gets_301_to_settings(self, admin_client):
        """GET /admin/auth-audit now 301-redirects to /settings?tab=accesos."""
        resp = await admin_client.get("/admin/auth-audit")
        assert resp.status_code == 301
        assert "tab=accesos" in resp.headers["location"]

    async def test_settings_accesos_accessible(self, admin_client):
        """The canonical URL /settings?tab=accesos returns 200."""
        resp = await admin_client.get("/settings?tab=accesos")
        assert resp.status_code == 200
        assert b"Auditor" in resp.content


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

_AUDIT_URL = "/settings?tab=accesos"


class TestAuthAuditFilters:
    async def test_filters_by_email(self, admin_client, db, clean_audit_rows):
        alice = "pytest_auditroute_alice@onnixtest.com"
        bob = "pytest_auditroute_bob@onnixtest.com"
        for _ in range(5):
            await _insert_audit_row(db, email=alice, result="wrong_password")
        for _ in range(5):
            await _insert_audit_row(db, email=bob, result="success")

        resp = await admin_client.get(f"{_AUDIT_URL}&email={alice}")
        assert resp.status_code == 200
        body = resp.content
        assert body.count(alice.encode()) >= 5
        assert bob.encode() not in body

    async def test_filters_by_ip(self, admin_client, db, clean_audit_rows):
        email = "pytest_auditroute_ipfilter@onnixtest.com"
        await _insert_audit_row(db, email=email, ip="1.2.3.4")
        await _insert_audit_row(db, email=email, ip="9.9.9.9")

        resp = await admin_client.get(f"{_AUDIT_URL}&ip=1.2.3.4")
        assert resp.status_code == 200
        body = resp.content
        assert b"1.2.3.4" in body
        assert b"9.9.9.9" not in body

    async def test_filters_by_date_range(self, admin_client, db, clean_audit_rows):
        email = "pytest_auditroute_daterange@onnixtest.com"
        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(days=1)
        tomorrow = now + timedelta(days=1)
        await _insert_audit_row(db, email=email, ip="10.0.0.1",
                                created_at=yesterday)
        await _insert_audit_row(db, email=email, ip="10.0.0.2",
                                created_at=now)
        await _insert_audit_row(db, email=email, ip="10.0.0.3",
                                created_at=tomorrow)

        today_str = now.strftime("%Y-%m-%d")
        resp = await admin_client.get(
            f"{_AUDIT_URL}&email={email}&date_from={today_str}&date_to={today_str}"
        )
        assert resp.status_code == 200
        body = resp.content
        assert b"10.0.0.2" in body
        assert b"10.0.0.1" not in body
        assert b"10.0.0.3" not in body

    async def test_combined_email_and_ip(self, admin_client, db, clean_audit_rows):
        email = "pytest_auditroute_combo@onnixtest.com"
        other_email = "pytest_auditroute_combo2@onnixtest.com"
        await _insert_audit_row(db, email=email, ip="5.5.5.5")
        await _insert_audit_row(db, email=email, ip="6.6.6.6")
        await _insert_audit_row(db, email=other_email, ip="5.5.5.5")

        resp = await admin_client.get(
            f"{_AUDIT_URL}&email={email}&ip=5.5.5.5"
        )
        assert resp.status_code == 200
        body = resp.content
        assert b"5.5.5.5" in body
        assert b"6.6.6.6" not in body
        assert other_email.encode() not in body


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

class TestAuthAuditPagination:
    async def test_paginates_50_per_page(self, admin_client, db, clean_audit_rows):
        email = "pytest_auditroute_paginate@onnixtest.com"
        for _ in range(75):
            await _insert_audit_row(db, email=email)

        resp1 = await admin_client.get(f"{_AUDIT_URL}&email={email}&page=1")
        assert resp1.status_code == 200
        # Count occurrences of the email in the table body — 50 rows.
        page1_count = resp1.content.count(email.encode())
        # The email also appears in filters input value, so we expect 50 + 1 = 51.
        assert page1_count >= 50

        resp2 = await admin_client.get(f"{_AUDIT_URL}&email={email}&page=2")
        assert resp2.status_code == 200
        page2_count = resp2.content.count(email.encode())
        # Page 2 has 25 rows + 1 filter echo = 26.
        assert 25 <= page2_count < 50


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------

class TestAuthAuditOrdering:
    async def test_orders_by_created_at_desc(self, admin_client, db, clean_audit_rows):
        email = "pytest_auditroute_order@onnixtest.com"
        now = datetime.now(timezone.utc)
        old = now - timedelta(hours=5)
        mid = now - timedelta(hours=2)
        new = now

        await _insert_audit_row(db, email=email, ip="11.0.0.1", created_at=old)
        await _insert_audit_row(db, email=email, ip="11.0.0.2", created_at=mid)
        await _insert_audit_row(db, email=email, ip="11.0.0.3", created_at=new)

        resp = await admin_client.get(f"{_AUDIT_URL}&email={email}")
        assert resp.status_code == 200
        body = resp.content.decode("utf-8", errors="ignore")
        pos_new = body.find("11.0.0.3")
        pos_mid = body.find("11.0.0.2")
        pos_old = body.find("11.0.0.1")
        assert pos_new < pos_mid < pos_old, (
            f"Esperado DESC: new<mid<old (en posiciones del HTML), "
            f"obtenido new={pos_new} mid={pos_mid} old={pos_old}"
        )


# ---------------------------------------------------------------------------
# Unlock email
# ---------------------------------------------------------------------------

class TestUnlockEmail:
    async def test_unlock_inserts_success_row_and_redirects(
        self, admin_client, db, clean_audit_rows
    ):
        victim = "pytest_auditroute_victim@onnixtest.com"
        # Simular 5 wrong_password en ventana de 15 min
        for _ in range(5):
            await _insert_audit_row(
                db, email=victim, ip="2.2.2.2", result="wrong_password"
            )

        resp = await admin_client.post(
            "/admin/auth-audit/unlock-email",
            data={"email": victim},
        )
        assert resp.status_code == 303
        loc = resp.headers.get("location", "")
        # Redirects back to /admin/auth-audit?email=... (still the old URL — POST handler is unchanged)
        assert "audit" in loc or "accesos" in loc
        # email se URL-encodea en el redirect (@ → %40), comprobar
        # tanto la forma plana como la encodeada del local-part.
        assert "pytest_auditroute_victim" in loc

        rows = (await db.execute(
            text("""
                SELECT id FROM auth_audit
                WHERE email = :e AND result = 'success' AND ip = 'admin-unlock'
            """),
            {"e": victim},
        )).all()
        assert len(rows) == 1

    async def test_unlock_requires_admin_403_for_user(
        self, user_client, clean_audit_rows
    ):
        resp = await user_client.post(
            "/admin/auth-audit/unlock-email",
            data={"email": "x@x.com"},
        )
        assert resp.status_code == 403

    async def test_unlock_requires_session_303_for_anon(
        self, client, clean_audit_rows
    ):
        resp = await client.post(
            "/admin/auth-audit/unlock-email",
            data={"email": "x@x.com"},
        )
        assert resp.status_code == 303
        assert "/login" in resp.headers.get("location", "")

    async def test_unlock_is_idempotent_on_double_post(
        self, admin_client, db, clean_audit_rows
    ):
        """Fix 6 — idempotency: rapid double-click / duplicate HTMX POST must
        NOT insert two admin-unlock success rows. Only the first call (when
        email is locked) inserts; the second (after unlock) is a no-op."""
        email = "pytest_auditroute_idem@onnixtest.com"
        # Set up locked state: 5 wrong_password + locked marker
        for _ in range(5):
            await _insert_audit_row(db, email=email, result="wrong_password")
        await _insert_audit_row(db, email=email, result="locked")

        # First POST — email is locked → inserts success row
        resp1 = await admin_client.post(
            "/admin/auth-audit/unlock-email",
            data={"email": email},
        )
        assert resp1.status_code == 303

        # Second POST (simulating double-click) — email is now unlocked → no-op
        resp2 = await admin_client.post(
            "/admin/auth-audit/unlock-email",
            data={"email": email},
        )
        assert resp2.status_code == 303

        # Only 1 admin-unlock success row must exist
        rows = (await db.execute(
            text("""
                SELECT id FROM auth_audit
                WHERE email = :e AND result = 'success' AND ip = 'admin-unlock'
            """),
            {"e": email},
        )).all()
        assert len(rows) == 1, (
            f"Expected exactly 1 admin-unlock row, got {len(rows)} "
            "(idempotency guard must skip second POST)"
        )

    async def test_unlock_normalizes_email_to_lowercase(
        self, admin_client, db, clean_audit_rows
    ):
        # Email enviado en mayúscula debe insertarse en minúscula.
        # The idempotency guard only inserts when email is actively locked,
        # so we first create a locked state (5 wrong_password + 1 locked row).
        upper = "PYTEST_AUDITROUTE_UPPER@OnnixTEST.COM"
        lower = upper.lower()
        for _ in range(5):
            await _insert_audit_row(db, email=lower, result="wrong_password")
        await _insert_audit_row(db, email=lower, result="locked")

        resp = await admin_client.post(
            "/admin/auth-audit/unlock-email",
            data={"email": upper},
        )
        assert resp.status_code == 303
        rows = (await db.execute(
            text("""
                SELECT email FROM auth_audit
                WHERE email = :e AND ip = 'admin-unlock'
            """),
            {"e": lower},
        )).all()
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# Empty-string date params (browser submits ?date_from=&date_to= when blank)
# ---------------------------------------------------------------------------

class TestAuthAuditEmptyDateParams:
    async def test_empty_date_strings_return_200_not_422(self, admin_client):
        """Browser submits empty strings when date fields are left blank.

        Pydantic must NOT reject these with 422 — they should be treated as
        no-filter. Tests use /settings?tab=accesos (canonical URL after 301 redirect).
        """
        resp = await admin_client.get(
            f"{_AUDIT_URL}&email=foo%40bar.com&date_from=&date_to="
        )
        assert resp.status_code == 200, (
            f"Expected 200 for empty date strings, got {resp.status_code}: {resp.text[:300]}"
        )

    async def test_valid_date_strings_still_return_200(self, admin_client):
        """Positive case: real dates must still be accepted and produce 200."""
        resp = await admin_client.get(
            f"{_AUDIT_URL}&date_from=2026-05-25&date_to=2026-05-25"
        )
        assert resp.status_code == 200, (
            f"Expected 200 for valid date strings, got {resp.status_code}: {resp.text[:300]}"
        )


# ---------------------------------------------------------------------------
# Inline unlock icon per locked row (Fix 1 — UX rework)
# ---------------------------------------------------------------------------

class TestUnlockIconPerRow:
    async def test_unlock_icon_visible_on_locked_row_when_email_locked(
        self, admin_client, db, clean_audit_rows
    ):
        """Unlock icon button must appear in the Action column for a locked row
        when the email is actively locked (5+ fails in 15-min window).
        The modal overlay pattern uses unlockTarget binding per icon button."""
        email = "pytest_auditroute_icon_locked@onnixtest.com"
        # 5 wrong_password + 1 locked row → is_locked() True via path-1 (within 15min)
        for _ in range(5):
            await _insert_audit_row(db, email=email, result="wrong_password")
        await _insert_audit_row(db, email=email, result="locked")

        resp = await admin_client.get(f"{_AUDIT_URL}&email={email}")
        assert resp.status_code == 200
        # SVG lock-open icon is present (from heroicons path)
        assert "lock" in resp.text.lower() or "M13.5 10.5" in resp.text
        # Per-row icon button sets unlockTarget (modal overlay pattern)
        assert f"unlockTarget = '{email}'" in resp.text
        # Modal overlay markup present with correct structure
        assert "unlockTarget !== null" in resp.text
        assert "Desbloquear email" in resp.text

    async def test_unlock_icon_NOT_visible_on_wrong_password_row(
        self, admin_client, db, clean_audit_rows
    ):
        """wrong_password rows must NOT show an unlock icon button for this email."""
        email = "pytest_auditroute_icon_wp@onnixtest.com"
        await _insert_audit_row(db, email=email, result="wrong_password")

        resp = await admin_client.get(f"{_AUDIT_URL}&email={email}")
        assert resp.status_code == 200
        # The per-row unlock icon button must not reference this email (email not locked)
        # Modal overlay is always in DOM (x-cloak hides it); assert no @click with this email
        assert f"unlockTarget = '{email}'" not in resp.text

    async def test_unlock_icon_NOT_visible_on_locked_row_when_email_already_unlocked(
        self, admin_client, db, clean_audit_rows
    ):
        """A locked row must NOT show the per-row unlock icon when the
        email is no longer actively locked (admin-unlock row was inserted)."""
        email = "pytest_auditroute_icon_unlocked@onnixtest.com"
        for _ in range(5):
            await _insert_audit_row(db, email=email, result="wrong_password")
        await _insert_audit_row(db, email=email, result="locked")
        # Admin-unlock clears the lock
        await _insert_audit_row(db, email=email, ip="admin-unlock", result="success")

        resp = await admin_client.get(f"{_AUDIT_URL}&email={email}")
        assert resp.status_code == 200
        # No per-row icon button since email is no longer locked
        # (Modal overlay is always in DOM; the icon @click binding is the discriminator)
        assert f"unlockTarget = '{email}'" not in resp.text

    async def test_unlock_post_with_hx_request_returns_partial_with_no_icons(
        self, admin_client, db, clean_audit_rows
    ):
        """POST /admin/auth-audit/unlock-email with HX-Request: true must
        return the auth_audit_table partial (not a redirect) and the
        freshly-queried table must not contain unlock icons for this email."""
        email = "pytest_auditroute_hx_unlock@onnixtest.com"
        for _ in range(5):
            await _insert_audit_row(db, email=email, result="wrong_password")
        await _insert_audit_row(db, email=email, result="locked")

        resp = await admin_client.post(
            "/admin/auth-audit/unlock-email",
            data={"email": email},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        # Partial HTML returned — not a redirect
        assert b"<table" in resp.content or b"audit-table-zone" in resp.content
        # After unlock, no active lock → no per-row icon button for this email
        assert f"unlockTarget = '{email}'" not in resp.text

    async def test_unlock_post_with_hx_request_returns_show_toast_header(
        self, admin_client, db, clean_audit_rows
    ):
        """HX unlock POST must include HX-Trigger showToast header on success."""
        email = "pytest_auditroute_hx_toast@onnixtest.com"
        for _ in range(5):
            await _insert_audit_row(db, email=email, result="wrong_password")
        await _insert_audit_row(db, email=email, result="locked")

        resp = await admin_client.post(
            "/admin/auth-audit/unlock-email",
            data={"email": email},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        hx = resp.headers.get("hx-trigger", "")
        assert hx, "HX-Trigger must be present on HTMX unlock success"
        data = json.loads(hx)
        assert "showToast" in data
        assert data["showToast"]["type"] == "success"
        assert email in data["showToast"]["message"]
