"""Migration 039 — round-trip 038 → 039 → 038 → 039 sanity test (§8.1).

Spec: .planning/phases/110-m6.1-plan-roles-auth/110-01-PLAN.md §2 + §9.

Validates the migration upgrades + downgrades cleanly on a DB without any
role='agent' rows. After downgrade:
  - auth_audit table does NOT exist
  - idx_contacts_agent_user_id does NOT exist
  - agent_seen_at / agent_assigned_at columns do NOT exist
  - FK contacts_agent_user_id_fkey is back to NO ACTION ('a')
  - CHECK users.role contracted to ('admin','user')

Then re-upgrade and assert HEAD = 039 again (idempotency).

This is a sanity test for infra (NOT a ROLE-* numbered test).
"""
from __future__ import annotations

import pytest

from .conftest import (
    alembic_cmd,
    current_alembic_head,
    ensure_head_039,
    psql,
)


def _has_table(name: str) -> bool:
    res = psql(
        f"SELECT 1 FROM information_schema.tables WHERE table_name = '{name}';"
    )
    return bool(res.stdout.strip())


def _has_index(table: str, name: str) -> bool:
    res = psql(
        f"SELECT 1 FROM pg_indexes WHERE tablename = '{table}' AND indexname = '{name}';"
    )
    return bool(res.stdout.strip())


def _has_column(table: str, col: str) -> bool:
    res = psql(
        f"SELECT 1 FROM information_schema.columns "
        f"WHERE table_name = '{table}' AND column_name = '{col}';"
    )
    return bool(res.stdout.strip())


def _fk_confdeltype(conname: str) -> str:
    res = psql(f"SELECT confdeltype FROM pg_constraint WHERE conname = '{conname}';")
    return res.stdout.strip()


def _check_definition(table: str, conname: str) -> str:
    res = psql(
        "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
        "JOIN pg_class t ON c.conrelid = t.oid "
        f"WHERE t.relname = '{table}' AND c.conname = '{conname}';"
    )
    return res.stdout.strip()


class TestUpgradeDowngradeRoundtripCleanDb:

    @pytest.fixture(autouse=True)
    def _cleanup_agents_around_test(self):
        # Defensive: null any contacts.agent_user_id referencing a test agent
        # (FK is ON DELETE RESTRICT — must clear references before DELETE),
        # then remove the agent rows. This isolates the roundtrip test from
        # leftover agents created by other suites in the same DB session
        # (test_users_create_agent, test_leads_tabs, test_agent_assign, etc.).
        psql(
            "UPDATE contacts SET agent_user_id = NULL WHERE agent_user_id IN "
            "(SELECT id FROM users WHERE role = 'agent' AND email LIKE 'pytest_%');"
        )
        psql("DELETE FROM users WHERE role = 'agent' AND email LIKE 'pytest_%';")
        yield
        psql(
            "UPDATE contacts SET agent_user_id = NULL WHERE agent_user_id IN "
            "(SELECT id FROM users WHERE role = 'agent' AND email LIKE 'pytest_%');"
        )
        psql("DELETE FROM users WHERE role = 'agent' AND email LIKE 'pytest_%';")
        # Leave the DB at 040 so subsequent randomly-ordered tests find the
        # schema they expect. Was `ensure_head_039()` pre-M6.2, which silently
        # stranded the DB at 039 after this test ran and broke any later test
        # that needed the 040 visits table. Target 040 explicitly (NOT head):
        # mig 041 (M6.3 bot_default_mode seed) is now head, and the migration
        # suite's stable schema baseline is 040.
        res = alembic_cmd("upgrade", "040_visits")
        if res.returncode != 0:
            raise RuntimeError(
                f"teardown upgrade 040 failed:\nstdout={res.stdout}\nstderr={res.stderr}"
            )

    def test_upgrade_downgrade_roundtrip_clean_db(self):
        # Start at 039 (or upgrade if needed).
        ensure_head_039()
        assert current_alembic_head() == "039_roles_auth_audit"

        # --- All 039-specific objects must be present.
        assert _has_table("auth_audit"), "auth_audit table missing at 039"
        assert _has_index("auth_audit", "idx_auth_audit_email_created_desc"), \
            "idx_auth_audit_email_created_desc missing at 039"
        assert _has_index("contacts", "idx_contacts_agent_user_id"), \
            "idx_contacts_agent_user_id missing at 039"
        assert _has_column("contacts", "agent_seen_at"), "agent_seen_at missing at 039"
        assert _has_column("contacts", "agent_assigned_at"), "agent_assigned_at missing at 039"
        assert _fk_confdeltype("contacts_agent_user_id_fkey") == "r", \
            "FK is not RESTRICT at 039"
        check_def = _check_definition("users", "users_role_check")
        assert "'agent'" in check_def, f"CHECK does not allow 'agent' at 039: {check_def}"

        # --- Downgrade (no role='agent' rows → guard passes).
        res_down = alembic_cmd("downgrade", "-1")
        assert res_down.returncode == 0, (
            f"alembic downgrade -1 failed unexpectedly:\n"
            f"stdout={res_down.stdout}\nstderr={res_down.stderr}"
        )
        assert current_alembic_head() == "038_seed_chatbot_flag", (
            f"After downgrade expected HEAD=038, got {current_alembic_head()}"
        )

        # --- All 039 objects must be gone.
        assert not _has_table("auth_audit"), "auth_audit must NOT exist post-downgrade"
        assert not _has_index("contacts", "idx_contacts_agent_user_id"), \
            "idx_contacts_agent_user_id must NOT exist post-downgrade"
        assert not _has_column("contacts", "agent_seen_at"), \
            "agent_seen_at must NOT exist post-downgrade"
        assert not _has_column("contacts", "agent_assigned_at"), \
            "agent_assigned_at must NOT exist post-downgrade"
        # FK back to NO ACTION ('a').
        assert _fk_confdeltype("contacts_agent_user_id_fkey") == "a", (
            "FK must revert to NO ACTION (confdeltype='a') post-downgrade, "
            f"got {_fk_confdeltype('contacts_agent_user_id_fkey')!r}"
        )
        check_def_after = _check_definition("users", "users_role_check")
        assert "'agent'" not in check_def_after, (
            f"CHECK must NOT allow 'agent' post-downgrade, got: {check_def_after}"
        )
        assert "'admin'" in check_def_after and "'user'" in check_def_after, (
            f"CHECK must allow admin+user post-downgrade, got: {check_def_after}"
        )

        # --- Re-upgrade — idempotency / clean re-apply.
        # Pin to 039 specifically (not `head`) so this test stays decoupled
        # from later migrations like 040 that shifted the head forward.
        res_up = alembic_cmd("upgrade", "039_roles_auth_audit")
        assert res_up.returncode == 0, (
            f"Re-upgrade failed:\nstdout={res_up.stdout}\nstderr={res_up.stderr}"
        )
        assert current_alembic_head() == "039_roles_auth_audit", (
            f"After re-upgrade expected HEAD=039, got {current_alembic_head()}"
        )
        # Sanity: auth_audit table back.
        assert _has_table("auth_audit"), "auth_audit must exist after re-upgrade"
