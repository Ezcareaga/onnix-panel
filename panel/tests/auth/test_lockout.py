"""
ROLE-04 — Lockout service tests (Phase 111-02, spec §8 tests #5–#8).

Rules (D-2 email-only):
  - 5 failures from same email within 15 min window → 30 min lock.
  - 6th attempt (even correct pw) → 401 + auth_audit row result='locked'.
  - Telegram alert fired exactly once per 30 min window (idempotent).
  - After 30 min window expires, email auto-unlocks.
  - Scope is email-only: 5 IPs × 1 email = same lockout.
  - Admin-unlock success row clears the failure window (fix 112-01).
"""
from __future__ import annotations

import os
import subprocess
from unittest.mock import AsyncMock

import pytest


def _count_audit(email: str, result: str | None = None) -> int:
    where = f"email = '{email}'"
    if result is not None:
        where += f" AND result = '{result}'"
    proc = subprocess.run(
        ["docker", "exec", "onnix-postgres",
         "psql", "-U", "onnix", "-d", os.environ["POSTGRES_DB"],
         "-tA", "-c", f"SELECT COUNT(*) FROM auth_audit WHERE {where};"],
        capture_output=True, text=True, timeout=10,
    )
    return int(proc.stdout.strip() or "0")


@pytest.fixture
def mock_notify_login_locked(monkeypatch):
    """Patch AdminNotifier.notify_login_locked with an AsyncMock.

    Note: lockout_service must call `get_admin_notifier().notify_login_locked(...)`.
    We patch on the class so every instance returned by the factory uses the mock.
    """
    mock = AsyncMock(return_value=True)
    # Import here to avoid circular import at collection time
    from app.bot.services import admin_notifier as an_module

    monkeypatch.setattr(
        an_module.AdminNotifier, "notify_login_locked", mock, raising=False
    )
    return mock


@pytest.mark.asyncio
async def test_lockout_after_5_failures_blocks_and_fires_alert_once(
    client, test_user_email, mock_notify_login_locked
):
    """Spec test #5: 5 fails → 6th attempt locked + alert fired once."""
    # 5 failed attempts with bad password (each returns 401, no lock yet)
    for i in range(5):
        resp = await client.post(
            "/login",
            data={"email": test_user_email, "password": "wrong-pw"},
            headers={"User-Agent": f"pytest-lockout-fail-{i}"},
        )
        assert resp.status_code == 401

    # 6th attempt with CORRECT password — must still be blocked (lock active)
    resp = await client.post(
        "/login",
        data={"email": test_user_email, "password": "test123"},
        headers={"User-Agent": "pytest-lockout-6th"},
    )
    assert resp.status_code == 401, "lock must override correct password"

    # 5 wrong_password rows + at least 1 locked row written by 6th attempt
    assert _count_audit(test_user_email, result="wrong_password") == 5
    assert _count_audit(test_user_email, result="locked") >= 1

    # Alert fired EXACTLY once across these 6 attempts (threshold crossing event)
    assert mock_notify_login_locked.await_count == 1, (
        f"expected exactly 1 lockout alert, got {mock_notify_login_locked.await_count}"
    )
    # Sanity: alert was called with this email
    args, kwargs = mock_notify_login_locked.await_args
    all_kwargs = {**kwargs}
    # First positional arg (if any) or kwarg 'email' must equal our test email
    email_in_call = kwargs.get("email") if "email" in kwargs else (args[0] if args else None)
    assert email_in_call == test_user_email


@pytest.mark.asyncio
async def test_lockout_alert_is_idempotent_within_30min_window(
    client, test_user_email, mock_notify_login_locked
):
    """Spec test #6: subsequent fails inside lock window do NOT re-fire alert."""
    # 5 fails → triggers alert + lock
    for i in range(5):
        resp = await client.post(
            "/login",
            data={"email": test_user_email, "password": "wrong-pw"},
            headers={"User-Agent": f"pytest-idem-{i}"},
        )
        assert resp.status_code == 401

    # 3 more attempts while locked — each writes 'locked' row but NO new alert
    for i in range(3):
        resp = await client.post(
            "/login",
            data={"email": test_user_email, "password": "wrong-pw"},
            headers={"User-Agent": f"pytest-idem-after-{i}"},
        )
        assert resp.status_code == 401

    # Still only ONE alert call across all 8 attempts
    assert mock_notify_login_locked.await_count == 1, (
        f"alert must be idempotent within 30min window; "
        f"got {mock_notify_login_locked.await_count} calls"
    )
    # Multiple 'locked' rows are fine (we audit every blocked attempt)
    assert _count_audit(test_user_email, result="locked") >= 3


@pytest.mark.asyncio
async def test_lockout_auto_expires_after_30_minutes(
    client, test_user_email, mock_notify_login_locked
):
    """Spec test #7: after 31 min, correct password unlocks (no 'locked' row).

    Note: lockout_service uses Postgres `now()` in its window queries, so
    Python-side time mocking (freezegun) does not affect the SQL. We simulate
    the passage of 31 minutes by back-dating the existing auth_audit rows
    via a direct UPDATE. This tests the same invariant: the SQL window
    correctly drops out-of-range rows.
    """
    # 5 fails at "real now" → lock active
    for i in range(5):
        resp = await client.post(
            "/login",
            data={"email": test_user_email, "password": "wrong-pw"},
            headers={"User-Agent": f"pytest-expire-{i}"},
        )
        assert resp.status_code == 401

    # Confirm we are locked right now
    assert _count_audit(test_user_email, result="wrong_password") == 5

    # Back-date every row for this email by 31 minutes — outside the 30 min
    # lock window AND the 15 min fail window.
    subprocess.run(
        ["docker", "exec", "onnix-postgres",
         "psql", "-U", "onnix", "-d", os.environ["POSTGRES_DB"], "-c",
         f"UPDATE auth_audit SET created_at = created_at - INTERVAL '31 minutes' "
         f"WHERE email = '{test_user_email}';"],
        capture_output=True, text=True, timeout=10,
    )

    # Correct password must now succeed (lock window passed)
    resp = await client.post(
        "/login",
        data={"email": test_user_email, "password": "test123"},
        headers={"User-Agent": "pytest-expire-after"},
    )
    assert resp.status_code == 303, "lock must have expired after 31 min"

    # Verify the new attempt was audited as 'success', not 'locked'
    assert _count_audit(test_user_email, result="success") == 1


@pytest.mark.asyncio
async def test_lockout_is_email_scoped_d2(
    client, test_user_email, mock_notify_login_locked
):
    """Spec test #8: D-2 — 5 fails from 5 distinct IPs to same email → lock.

    httpx ASGITransport always reports the same `request.client.host`; rotating
    IPs requires monkeypatching at the request scope level. We achieve the
    'distinct IPs' semantics by overriding the `X-Forwarded-For` header (the
    common deployment shape) and asserting that the lock is keyed on email
    regardless. Even with route v1 ignoring X-Forwarded-For, the email-only
    scope is what we are testing: 5 attempts to the same email lock the email
    period, no matter which client made them.
    """
    fake_ips = ["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4", "10.0.0.5"]
    for ip in fake_ips:
        resp = await client.post(
            "/login",
            data={"email": test_user_email, "password": "wrong-pw"},
            headers={
                "User-Agent": f"pytest-ipscope-{ip}",
                "X-Forwarded-For": ip,
            },
        )
        assert resp.status_code == 401

    # 6th attempt from yet another IP with CORRECT password — still locked
    resp = await client.post(
        "/login",
        data={"email": test_user_email, "password": "test123"},
        headers={
            "User-Agent": "pytest-ipscope-newip",
            "X-Forwarded-For": "10.0.0.99",
        },
    )
    assert resp.status_code == 401, "lock is email-only; new IP must NOT bypass"

    # Audit reflects the email-scoped lock
    assert _count_audit(test_user_email, result="locked") >= 1
    # Alert fired once (threshold crossing)
    assert mock_notify_login_locked.await_count == 1


@pytest.mark.asyncio
async def test_lockout_persists_in_minutes_15_to_30_after_last_fail(db):
    """Fix bug5 — path-2: lock must still return True at minute 16 and 25
    after the fail burst, even though path-1 (15-min window) has expired.
    Lock must return False at minute 31.

    Technique: insert 5 wrong_password + 1 locked row with backdated
    created_at to simulate different points in the lock timeline.
    """
    import subprocess as _sp

    from app.services.lockout_service import is_locked

    email = "pytest_lockout_path2@onnixtest.com"

    def _psql_local(sql: str) -> None:
        proc = _sp.Popen(
            ["docker", "exec", "onnix-postgres",
             "psql", "-U", "onnix", "-d", os.environ["POSTGRES_DB"], "-c", sql],
            stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, start_new_session=True,
        )
        proc.wait(timeout=10)

    _psql_local(f"DELETE FROM auth_audit WHERE email = '{email}';")

    # Insert fail burst backdated 16 minutes ago — path-1 (15-min window) won't
    # count them, but path-2 (30-min window with locked marker) should still fire.
    _psql_local(
        f"INSERT INTO auth_audit (email, ip, result, created_at) VALUES "
        f"('{email}', '1.1.1.1', 'wrong_password', now() - INTERVAL '16 minutes'), "
        f"('{email}', '1.1.1.2', 'wrong_password', now() - INTERVAL '16 minutes'), "
        f"('{email}', '1.1.1.3', 'wrong_password', now() - INTERVAL '16 minutes'), "
        f"('{email}', '1.1.1.4', 'wrong_password', now() - INTERVAL '16 minutes'), "
        f"('{email}', '1.1.1.5', 'wrong_password', now() - INTERVAL '16 minutes'), "
        f"('{email}', '1.1.1.6', 'locked',         now() - INTERVAL '16 minutes');"
    )

    # At minute 16 after burst → path-1 expired but path-2 active → True
    assert await is_locked(db, email) is True, (
        "expected is_locked=True at minute 16 (path-2 should cover minutes 15-30)"
    )

    # Simulate minute 25: rows are 25 minutes old
    _psql_local(
        f"UPDATE auth_audit SET created_at = now() - INTERVAL '25 minutes' "
        f"WHERE email = '{email}';"
    )
    assert await is_locked(db, email) is True, (
        "expected is_locked=True at minute 25 (still within 30-min lock duration)"
    )

    # Simulate minute 31: rows are 31 minutes old → both paths expired → False
    _psql_local(
        f"UPDATE auth_audit SET created_at = now() - INTERVAL '31 minutes' "
        f"WHERE email = '{email}';"
    )
    assert await is_locked(db, email) is False, (
        "expected is_locked=False at minute 31 (lock duration 30 min expired)"
    )


@pytest.mark.asyncio
async def test_admin_unlock_clears_path2(db):
    """Fix bug5 — path-2 unlock: admin-unlock row AFTER a locked marker must
    clear path-2 so is_locked() returns False even within the 30-min window."""
    import subprocess as _sp

    from app.services.lockout_service import is_locked

    email = "pytest_lockout_path2_unlock@onnixtest.com"

    def _psql_local(sql: str) -> None:
        proc = _sp.Popen(
            ["docker", "exec", "onnix-postgres",
             "psql", "-U", "onnix", "-d", os.environ["POSTGRES_DB"], "-c", sql],
            stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, start_new_session=True,
        )
        proc.wait(timeout=10)

    _psql_local(f"DELETE FROM auth_audit WHERE email = '{email}';")

    # Insert backdated burst (16 min ago) so path-1 is expired but path-2 active.
    _psql_local(
        f"INSERT INTO auth_audit (email, ip, result, created_at) VALUES "
        f"('{email}', '2.2.2.1', 'wrong_password', now() - INTERVAL '16 minutes'), "
        f"('{email}', '2.2.2.2', 'wrong_password', now() - INTERVAL '16 minutes'), "
        f"('{email}', '2.2.2.3', 'wrong_password', now() - INTERVAL '16 minutes'), "
        f"('{email}', '2.2.2.4', 'wrong_password', now() - INTERVAL '16 minutes'), "
        f"('{email}', '2.2.2.5', 'wrong_password', now() - INTERVAL '16 minutes'), "
        f"('{email}', '2.2.2.6', 'locked',         now() - INTERVAL '16 minutes');"
    )

    # Confirm locked via path-2
    assert await is_locked(db, email) is True, "path-2 should report locked"

    # Admin-unlock row written NOW (after the locked marker) → clears path-2
    _psql_local(
        f"INSERT INTO auth_audit (email, ip, result) "
        f"VALUES ('{email}', 'admin-unlock', 'success');"
    )

    assert await is_locked(db, email) is False, (
        "admin-unlock after locked marker must clear path-2"
    )


@pytest.mark.asyncio
async def test_admin_unlock_clears_lockout_window(db):
    """Fix 112-01: admin-unlock success row must reset the failure window.

    Scenario (mirrors staging smoke — scenario 6):
      1. INSERT 5 wrong_password rows for a test email  → is_locked() True.
      2. INSERT result='success', ip='admin-unlock'     → is_locked() False.

    Before the fix, _fail_count_in_window counted all 5 wrong_password rows
    regardless of the admin-unlock row, so step 2 left the email locked.
    """
    import subprocess as _sp

    from app.services.lockout_service import is_locked

    email = "pytest_lockout_adminunlock@onnixtest.com"

    def _psql_local(sql: str) -> None:
        proc = _sp.Popen(
            ["docker", "exec", "onnix-postgres",
             "psql", "-U", "onnix", "-d", os.environ["POSTGRES_DB"], "-c", sql],
            stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, start_new_session=True,
        )
        proc.wait(timeout=10)

    # Clean slate (cleanup_audit_between_tests fixture handles email pattern
    # pytest_lockout_% so this email is already clean, but be explicit).
    _psql_local(f"DELETE FROM auth_audit WHERE email = '{email}';")

    # Step 1: insert 5 wrong_password rows — email becomes locked.
    for _ in range(5):
        _psql_local(
            f"INSERT INTO auth_audit (email, ip, result) "
            f"VALUES ('{email}', '1.2.3.4', 'wrong_password');"
        )

    assert await is_locked(db, email) is True, (
        "expected is_locked=True after 5 wrong_password rows"
    )

    # Step 2: insert admin-unlock success row — email must become unlocked.
    _psql_local(
        f"INSERT INTO auth_audit (email, ip, result) "
        f"VALUES ('{email}', 'admin-unlock', 'success');"
    )

    assert await is_locked(db, email) is False, (
        "expected is_locked=False after admin-unlock success row "
        "(fix 112-01: failures before admin-unlock must be excluded from count)"
    )
