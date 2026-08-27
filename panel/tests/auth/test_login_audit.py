"""
ROLE-03 — Login audit persist tests (Phase 111-02, spec §8 test #4).

Every POST /login (success or failure) must INSERT exactly one row in
auth_audit with the correct `result` value:
  - 'success'        → user authenticated
  - 'wrong_password' → email exists + active + bad password
  - 'inactive'       → email exists but is_active=False
  - 'not_found'      → email does not exist
  - 'locked'         → email is currently locked (set of fail rows in window)

Audit row must carry ip + user_agent extracted from request.
"""
from __future__ import annotations

import os
import subprocess

import pytest


def _count_audit(email: str, result: str | None = None) -> int:
    """Return number of auth_audit rows for email (optionally filtered by result)."""
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


def _last_audit_row(email: str) -> dict:
    """Return the most recent auth_audit row for email as a dict."""
    proc = subprocess.run(
        ["docker", "exec", "onnix-postgres",
         "psql", "-U", "onnix", "-d", os.environ["POSTGRES_DB"],
         "-tA", "-F", "|", "-c",
         f"SELECT email, ip, user_agent, result "
         f"FROM auth_audit WHERE email = '{email}' "
         f"ORDER BY created_at DESC, id DESC LIMIT 1;"],
        capture_output=True, text=True, timeout=10,
    )
    line = proc.stdout.strip()
    if not line:
        return {}
    parts = line.split("|")
    return {
        "email": parts[0],
        "ip": parts[1] or None,
        "user_agent": parts[2] or None,
        "result": parts[3],
    }


@pytest.mark.asyncio
async def test_successful_login_writes_audit_row_with_result_success(
    client, test_user_email
):
    """ROLE-03: POST /login with valid credentials → 1 row, result='success'."""
    resp = await client.post(
        "/login",
        data={"email": test_user_email, "password": "test123"},
        headers={"User-Agent": "pytest-audit-success/1.0"},
    )
    assert resp.status_code == 303, f"expected redirect, got {resp.status_code}"

    assert _count_audit(test_user_email) == 1
    row = _last_audit_row(test_user_email)
    assert row["result"] == "success"
    assert row["email"] == test_user_email


@pytest.mark.asyncio
async def test_unknown_email_writes_audit_row_with_result_not_found(client):
    """ROLE-03: POST /login with unknown email → 1 row, result='not_found'."""
    email = "pytest_lockout_ghost@onnixtest.com"
    resp = await client.post(
        "/login",
        data={"email": email, "password": "anything"},
        headers={"User-Agent": "pytest-audit-notfound/1.0"},
    )
    assert resp.status_code == 401

    assert _count_audit(email) == 1
    row = _last_audit_row(email)
    assert row["result"] == "not_found"


@pytest.mark.asyncio
async def test_wrong_password_writes_audit_row_with_result_wrong_password(
    client, test_user_email
):
    """ROLE-03: POST /login with bad password → 1 row, result='wrong_password'."""
    resp = await client.post(
        "/login",
        data={"email": test_user_email, "password": "totally-wrong-password"},
        headers={"User-Agent": "pytest-audit-badpw/1.0"},
    )
    assert resp.status_code == 401

    assert _count_audit(test_user_email) == 1
    row = _last_audit_row(test_user_email)
    assert row["result"] == "wrong_password"


@pytest.mark.asyncio
async def test_inactive_user_writes_audit_row_with_result_inactive(
    client, test_user_inactive_email
):
    """ROLE-03: POST /login with inactive user → 1 row, result='inactive'."""
    resp = await client.post(
        "/login",
        data={"email": test_user_inactive_email, "password": "test123"},
        headers={"User-Agent": "pytest-audit-inactive/1.0"},
    )
    assert resp.status_code == 401

    assert _count_audit(test_user_inactive_email) == 1
    row = _last_audit_row(test_user_inactive_email)
    assert row["result"] == "inactive"


@pytest.mark.asyncio
async def test_audit_row_includes_ip_and_user_agent(client, test_user_email):
    """ROLE-03: audit row must capture request ip + user_agent."""
    resp = await client.post(
        "/login",
        data={"email": test_user_email, "password": "test123"},
        headers={"User-Agent": "pytest-audit-headers/2.0"},
    )
    assert resp.status_code == 303

    row = _last_audit_row(test_user_email)
    # user_agent persisted exactly as sent
    assert row["user_agent"] == "pytest-audit-headers/2.0"
    # ASGITransport puts a non-null client host into request.client
    # (typically '127.0.0.1' or 'testclient'); just assert non-empty.
    assert row["ip"] is not None and row["ip"] != ""


@pytest.mark.asyncio
async def test_email_is_normalized_to_lowercase_before_audit_and_auth(
    client, test_user_email
):
    """ROLE-03 + §10.4: email is stripped+lowercased before audit + lookup."""
    # User stored as 'pytest_lockout_user@onnixtest.com'; submit uppercase + spaces.
    resp = await client.post(
        "/login",
        data={"email": f"  {test_user_email.upper()}  ", "password": "test123"},
        headers={"User-Agent": "pytest-audit-norm/1.0"},
    )
    assert resp.status_code == 303, "uppercase+spaces email must still authenticate"

    # Audit row must use the normalized lowercase form, not the raw input.
    assert _count_audit(test_user_email, result="success") == 1
    # The uppercase form should NOT appear as a separate row.
    assert _count_audit(test_user_email.upper()) == 0
