"""Migration 040 — upgrade/downgrade roundtrip test (VISIT-12).

Spec: .planning/phases/114-m6.2-plan-visits/114-01-PLAN.md §1 + §7 (VISIT-12).
M6.2 — Phase 115-01.

Two scenarios:

1. ROUNDTRIP CLEAN (no rows with status='visit_scheduled'):
   - Apply 040 → visits table exists, CHECK widened.
   - Downgrade 040 → visits gone AND CHECK contracted (no 'visit_scheduled').
   - Re-apply 040 → green.

2. DOWNGRADE GUARD (rows exist):
   - Apply 040.
   - INSERT 1 contact with status='visit_scheduled'.
   - Attempt downgrade → must abort with RuntimeError containing
     "downgrade aborted" + "visit_scheduled" (per mig 040 §6 downgrade text).
   - UPDATE that contact to status='interested'.
   - Retry downgrade → succeeds.
   - Re-apply 040 → green.

After the module, leave DB at HEAD 040_visits (cleanup via finalizer).
"""
from __future__ import annotations

import pytest

from .conftest import (
    alembic_cmd,
    current_alembic_head,
    ensure_head_040,
    psql,
)


# Sentinel phone prefix so we can clean up any test rows in finalizer.
TEST_PHONE = "+595900000040"


def _has_table(name: str) -> bool:
    res = psql(
        f"SELECT 1 FROM information_schema.tables WHERE table_name = '{name}';"
    )
    return bool(res.stdout.strip())


def _check_definition(table: str, conname: str) -> str:
    res = psql(
        "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
        "JOIN pg_class t ON c.conrelid = t.oid "
        f"WHERE t.relname = '{table}' AND c.conname = '{conname}';"
    )
    return res.stdout.strip()


def _delete_test_contact() -> None:
    """Idempotent cleanup of the test contact (if it exists)."""
    psql(f"DELETE FROM contacts WHERE phone = '{TEST_PHONE}';")


@pytest.fixture(scope="module", autouse=True)
def _module_cleanup():
    """Wipe any leftover sentinel rows before and after the module runs.

    Leave the DB at HEAD 040_visits so sibling test modules can rely on
    the post-040 schema state.
    """
    _delete_test_contact()
    yield
    _delete_test_contact()
    # Ensure HEAD = 040 at module exit (e.g., after a downgrade test).
    head = current_alembic_head()
    if head != "040_visits":
        # Target 040 explicitly (NOT head): mig 041 (M6.3 bot_default_mode seed)
        # is now head, and this module's roundtrip contract is 040<->039.
        res = alembic_cmd("upgrade", "040_visits")
        if res.returncode != 0:
            raise RuntimeError(
                f"Could not restore HEAD=040 after module: "
                f"stdout={res.stdout}\nstderr={res.stderr}"
            )


class TestUpgradeDowngradeRoundtripCleanDb:

    def test_upgrade_downgrade_roundtrip_clean_db(self):
        # Start at 040.
        ensure_head_040()
        assert current_alembic_head() == "040_visits"

        # --- All 040-specific objects must be present.
        assert _has_table("visits"), "visits table missing at 040"
        check_def = _check_definition("contacts", "contacts_status_check")
        assert "'visit_scheduled'" in check_def, (
            f"contacts_status_check must include 'visit_scheduled' at 040: {check_def}"
        )

        # --- Downgrade (no 'visit_scheduled' rows → guard passes).
        _delete_test_contact()  # belt-and-suspenders
        res_down = alembic_cmd("downgrade", "-1")
        assert res_down.returncode == 0, (
            f"alembic downgrade -1 failed unexpectedly:\n"
            f"stdout={res_down.stdout}\nstderr={res_down.stderr}"
        )
        assert current_alembic_head() == "039_roles_auth_audit", (
            f"After downgrade expected HEAD=039, got {current_alembic_head()}"
        )

        # --- visits table gone; CHECK contracted (no 'visit_scheduled').
        assert not _has_table("visits"), "visits must NOT exist post-downgrade"
        check_def_after = _check_definition("contacts", "contacts_status_check")
        assert "'visit_scheduled'" not in check_def_after, (
            f"contacts_status_check must NOT include 'visit_scheduled' post-downgrade: "
            f"{check_def_after}"
        )

        # --- Re-upgrade (to 040 explicitly; mig 041 is now head, contract is 040).
        res_up = alembic_cmd("upgrade", "040_visits")
        assert res_up.returncode == 0, (
            f"Re-upgrade failed:\nstdout={res_up.stdout}\nstderr={res_up.stderr}"
        )
        assert current_alembic_head() == "040_visits", (
            f"After re-upgrade expected HEAD=040, got {current_alembic_head()}"
        )
        assert _has_table("visits"), "visits must exist after re-upgrade"


class TestDowngradeGuardBlocksWhenVisitScheduledRowsExist:

    def test_downgrade_aborts_when_visit_scheduled_row_present(self):
        # Start at 040.
        ensure_head_040()
        _delete_test_contact()

        # --- Insert a contact with status='visit_scheduled' (only valid post-040).
        ins = psql(
            "INSERT INTO contacts (phone, status, source) "
            f"VALUES ('{TEST_PHONE}', 'visit_scheduled', 'telegram');"
        )
        assert ins.returncode == 0, (
            f"Could not insert test contact:\nstdout={ins.stdout}\nstderr={ins.stderr}"
        )

        # --- Attempt downgrade — must abort.
        res_down = alembic_cmd("downgrade", "-1")
        assert res_down.returncode != 0, (
            "alembic downgrade -1 must FAIL when 'visit_scheduled' rows exist; "
            f"got returncode=0 with stdout={res_down.stdout}"
        )
        combined = (res_down.stdout or "") + (res_down.stderr or "")
        assert "downgrade aborted" in combined.lower(), (
            f"Expected 'downgrade aborted' in error output, got:\n{combined}"
        )
        assert "visit_scheduled" in combined, (
            f"Expected 'visit_scheduled' in error output, got:\n{combined}"
        )

        # --- HEAD must still be 040 (guard prevented downgrade).
        assert current_alembic_head() == "040_visits", (
            f"HEAD should still be 040 after aborted downgrade, got {current_alembic_head()}"
        )
        assert _has_table("visits"), "visits must still exist after aborted downgrade"

        # --- Reassign the contact's status; retry downgrade — must succeed now.
        upd = psql(
            f"UPDATE contacts SET status = 'interested' WHERE phone = '{TEST_PHONE}';"
        )
        assert upd.returncode == 0, (
            f"Could not update test contact:\nstdout={upd.stdout}\nstderr={upd.stderr}"
        )

        res_down_retry = alembic_cmd("downgrade", "-1")
        assert res_down_retry.returncode == 0, (
            f"Retry downgrade failed after clearing 'visit_scheduled':\n"
            f"stdout={res_down_retry.stdout}\nstderr={res_down_retry.stderr}"
        )
        assert current_alembic_head() == "039_roles_auth_audit"
        assert not _has_table("visits"), "visits must be gone after successful downgrade"

        # --- Re-apply 040 so the module finishes at HEAD=040 (per module fixture).
        # Target 040 explicitly; mig 041 (M6.3) is now head.
        res_up = alembic_cmd("upgrade", "040_visits")
        assert res_up.returncode == 0, (
            f"Final re-upgrade failed:\nstdout={res_up.stdout}\nstderr={res_up.stderr}"
        )
        assert current_alembic_head() == "040_visits"
        assert _has_table("visits")

        # Cleanup the test row (still exists with status='interested').
        _delete_test_contact()
