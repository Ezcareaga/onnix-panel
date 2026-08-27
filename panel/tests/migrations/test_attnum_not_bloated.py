"""STAB-03 (TD-115-04) — migration roundtrips must NOT bloat onnix_dev.

The Alembic upgrade/downgrade roundtrip tests drop/re-add columns on
`contacts` each cycle, permanently incrementing `pg_attribute.attnum`
(dead column slots are never reclaimed). Run against onnix_dev, this
inflated dev's `contacts` attnum high-water from ~9 (prod baseline) to
500+ over many test runs.

The fix (STAB-03): the migration roundtrips run against a throwaway
scratch DB (`onnix_test_mig_NNN`), so the bloat dies in the scratch DB
and `onnix_dev` is never mutated by these tests.

This test PROVES that property: it reads dev's `contacts` attnum
high-water BEFORE and AFTER driving a full migration roundtrip
(downgrade then re-upgrade) through the SAME mechanism the roundtrip
tests use (`alembic_cmd` via the migration conftest), and asserts the
dev value did NOT increase.

RED (before the scratch fixture lands): `alembic_cmd` targets
onnix_dev, so the roundtrip drops/re-adds columns on the live dev
`contacts` table → attnum climbs → `after <= before` FAILS.

GREEN (after the scratch fixture lands): `alembic_cmd` is redirected to
the scratch DB, so the roundtrip mutates the scratch `contacts` table
instead → dev attnum is untouched → `after == before` PASSES.
"""
from __future__ import annotations

import subprocess

from .conftest import alembic_cmd, current_alembic_head, ensure_head_039

PG_CONTAINER = "onnix-postgres"
PG_USER = "onnix"
DEV_DB = "onnix_dev"


def _dev_contacts_attnum_highwater() -> int:
    """Read the contacts pg_attribute attnum high-water on onnix_dev.

    Pinned to onnix_dev explicitly (NOT the migration conftest's
    redirectable TARGET_DB) — this is the measurement we want to protect.
    """
    res = subprocess.run(
        [
            "docker", "exec", PG_CONTAINER,
            "psql", "-U", PG_USER, "-d", DEV_DB, "-tA", "-c",
            "SELECT COALESCE(max(attnum), 0) FROM pg_attribute a "
            "JOIN pg_class c ON a.attrelid = c.oid "
            "WHERE c.relname = 'contacts';",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert res.returncode == 0, f"attnum query failed: {res.stderr}"
    return int((res.stdout or "0").strip())


def _drive_roundtrip() -> None:
    """Drive a full migration roundtrip (downgrade 039->038 then re-upgrade).

    Uses the SAME mechanism the roundtrip tests use (`alembic_cmd`, which
    Task 2 redirects to the scratch DB). Dropping + re-adding the 039
    columns (agent_seen_at, agent_assigned_at) is exactly what bloats
    `contacts.attnum`.
    """
    # Land at 039 first so a downgrade->038->039 cycle is well-defined.
    ensure_head_039()
    assert current_alembic_head() == "039_roles_auth_audit"

    # 039 -> 038: drops contacts.agent_seen_at / agent_assigned_at.
    down = alembic_cmd("downgrade", "-1")
    assert down.returncode == 0, (
        f"downgrade 039->038 failed:\nstdout={down.stdout}\nstderr={down.stderr}"
    )
    assert current_alembic_head() == "038_seed_chatbot_flag"

    # 038 -> 039: re-adds those columns at FRESH attnum slots (bloat).
    up = alembic_cmd("upgrade", "039_roles_auth_audit")
    assert up.returncode == 0, (
        f"re-upgrade 038->039 failed:\nstdout={up.stdout}\nstderr={up.stderr}"
    )
    assert current_alembic_head() == "039_roles_auth_audit"


def test_migration_roundtrip_does_not_bloat_dev_contacts_attnum():
    before = _dev_contacts_attnum_highwater()

    _drive_roundtrip()

    after = _dev_contacts_attnum_highwater()

    assert after <= before, (
        "Migration roundtrip bloated onnix_dev contacts.attnum "
        f"(before={before}, after={after}). The roundtrip must run against a "
        "throwaway scratch DB, not onnix_dev (STAB-03 / TD-115-04)."
    )
