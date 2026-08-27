"""
Shared fixtures for auth tests (login audit + lockout).

Phase 111-02 (M6.1). Tests run on host against onnix_dev.

Per-test cleanup: deletes pytest_*@onnixtest.com users and any auth_audit rows
for our test email pattern so each test starts clean (lockout has no state
spill-over between tests).
"""
from __future__ import annotations

import os
import subprocess

import pytest
import pytest_asyncio

# Email used by lockout/audit tests. Cleanup matches LIKE 'pytest_lockout_%'.
TEST_AUDIT_EMAIL_PATTERN = "pytest_lockout_%@onnixtest.com"


def _psql(sql: str) -> None:
    """Run SQL inside the onnix-postgres container against onnix_dev (sync)."""
    try:
        proc = subprocess.Popen(
            ["docker", "exec", "onnix-postgres",
             "psql", "-U", "onnix", "-d", os.environ["POSTGRES_DB"], "-c", sql],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        proc.wait(timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass


def _cleanup_audit_state() -> None:
    """Wipe auth_audit rows + test users used by these tests."""
    _psql(
        f"DELETE FROM auth_audit WHERE email LIKE '{TEST_AUDIT_EMAIL_PATTERN}'; "
        f"DELETE FROM users WHERE email LIKE '{TEST_AUDIT_EMAIL_PATTERN}';"
    )


@pytest.fixture(autouse=True)
def cleanup_audit_between_tests():
    """Clean before AND after every auth test. Lockout state must not leak."""
    _cleanup_audit_state()
    yield
    _cleanup_audit_state()


# bcrypt hash for password 'test123' (same hash as user_client fixture in panel/tests/conftest.py)
_TEST_BCRYPT_HASH = "$2b$12$q7iHvYIHneLdjGsG2AZFLeKKEyerDlRc4wkIvfPFPgVja403Ue7Xu"
TEST_PASSWORD = "test123"


@pytest.fixture
def test_user_email() -> str:
    """Insert a test user with known password and return its email."""
    email = "pytest_lockout_user@onnixtest.com"
    _psql(
        "INSERT INTO users (email, name, role, password_hash, is_active) "
        f"VALUES ('{email}', 'Lockout Test User', 'user', "
        f"'{_TEST_BCRYPT_HASH}', true) "
        "ON CONFLICT (email) DO UPDATE SET "
        f"password_hash = EXCLUDED.password_hash, is_active = true"
    )
    return email


@pytest.fixture
def test_user_inactive_email() -> str:
    """Insert an inactive test user with known password and return its email."""
    email = "pytest_lockout_inactive@onnixtest.com"
    _psql(
        "INSERT INTO users (email, name, role, password_hash, is_active) "
        f"VALUES ('{email}', 'Inactive Test User', 'user', "
        f"'{_TEST_BCRYPT_HASH}', false) "
        "ON CONFLICT (email) DO UPDATE SET "
        f"password_hash = EXCLUDED.password_hash, is_active = false"
    )
    return email
