"""Migration 039 — CHECK users.role tests (ROLE-01).

Spec: .planning/phases/110-m6.1-plan-roles-auth/110-01-PLAN.md §1.2 + §2.1 step 1.

Validates:
  - After upgrade: CHECK accepts ('admin','agent','user'); rejects others with
    IntegrityError mentioning `users_role_check`.
  - Before downgrade: pre-condition guard aborts with descriptive RuntimeError
    if any users.role = 'agent' exists.
"""
from __future__ import annotations

import pytest

from .conftest import (
    alembic_cmd,
    current_alembic_head,
    ensure_head_039,
    ensure_head_038,
    psql,
)


TEST_EMAIL_AGENT = "pytest_role_check_agent@onnixtest.com"
TEST_EMAIL_ADMIN = "pytest_role_check_admin@onnixtest.com"
TEST_EMAIL_USER = "pytest_role_check_user@onnixtest.com"
TEST_EMAIL_BAD = "pytest_role_check_bad@onnixtest.com"


@pytest.fixture(autouse=True)
def _cleanup_test_users():
    """Remove any leftover test users before and after each test."""
    emails = [TEST_EMAIL_AGENT, TEST_EMAIL_ADMIN, TEST_EMAIL_USER, TEST_EMAIL_BAD]
    where = " OR ".join(f"email = '{e}'" for e in emails)
    psql(f"DELETE FROM users WHERE {where};")
    yield
    psql(f"DELETE FROM users WHERE {where};")


def _insert_user(email: str, role: str):
    """Try to insert a user with the given role; return CompletedProcess from psql."""
    sql = (
        "INSERT INTO users (email, password_hash, name, role, is_active) "
        f"VALUES ('{email}', 'x', 'Test', '{role}', true);"
    )
    return psql(sql)


class TestCheckConstraintAcceptsAdminAgentUser:
    """ROLE-01 happy path: post-039 CHECK accepts the 3 canonical roles
    and rejects an unknown role with an IntegrityError citing the constraint."""

    def test_check_constraint_accepts_admin_agent_user(self):
        ensure_head_039()

        # The 3 canonical roles must succeed.
        for email, role in [
            (TEST_EMAIL_ADMIN, "admin"),
            (TEST_EMAIL_AGENT, "agent"),
            (TEST_EMAIL_USER, "user"),
        ]:
            res = _insert_user(email, role)
            assert res.returncode == 0, (
                f"Expected INSERT users role='{role}' to succeed, but failed.\n"
                f"stdout={res.stdout}\nstderr={res.stderr}"
            )

        # An unknown role must be rejected by users_role_check.
        res_bad = _insert_user(TEST_EMAIL_BAD, "supervisor")
        assert res_bad.returncode != 0, (
            "Expected INSERT users role='supervisor' to fail, but it succeeded."
        )
        combined = (res_bad.stdout or "") + (res_bad.stderr or "")
        assert "users_role_check" in combined, (
            f"Expected error to mention 'users_role_check', got:\n{combined}"
        )


class TestCheckConstraintDowngradeBlocksIfAgentsExist:
    """ROLE-01 guard: downgrade aborts with descriptive RuntimeError if any
    rows have role='agent' (mig 018:51-69 pattern replicated)."""

    def test_check_constraint_downgrade_blocks_if_agents_exist(self):
        ensure_head_039()

        # Seed: insert an agent user. Guard must fire.
        ins = _insert_user(TEST_EMAIL_AGENT, "agent")
        assert ins.returncode == 0, f"Setup failed: {ins.stderr}"

        try:
            res = alembic_cmd("downgrade", "-1")
            combined = (res.stdout or "") + (res.stderr or "")

            assert res.returncode != 0, (
                f"Expected alembic downgrade -1 to FAIL while role='agent' rows "
                f"exist, but it succeeded.\nstdout={res.stdout}\nstderr={res.stderr}"
            )
            assert "Migration 039 downgrade aborted" in combined, (
                f"Expected guard message 'Migration 039 downgrade aborted', got:\n{combined}"
            )
            assert "role='agent'" in combined or "user(s) with role" in combined, (
                f"Expected guard message to mention agent users, got:\n{combined}"
            )

            # DB should still be at 039 (the failed downgrade rolled back).
            assert current_alembic_head() == "039_roles_auth_audit", (
                f"After failed downgrade, expected HEAD=039_roles_auth_audit, got {current_alembic_head()}"
            )
        finally:
            # Clean: remove the agent so the next test starts from a clean state
            # and so that other tests / a later round-trip can downgrade.
            psql(f"DELETE FROM users WHERE email = '{TEST_EMAIL_AGENT}';")
            # Make sure we leave the DB at HEAD 039 for subsequent tests.
            ensure_head_039()
