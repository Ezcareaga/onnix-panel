"""Tests for Alembic migration 026: drop 12 dead DB columns.

Verifies that after upgrade DDL, each dead column is absent from
information_schema.columns, and that after downgrade DDL, the columns are
restored.

_DROPPED_COLUMNS is imported directly from the migration module (drift guard).

DDL strategy: ALTER TABLE requires ACCESS EXCLUSIVE lock and blocks if any
concurrent transaction holds even a row-level lock. All DDL and schema
assertions in this file use psql via docker exec (synchronous, auto-commit,
no async session pool). Upgrade and downgrade DDL are each batched into a
single psql call via a heredoc to minimise round-trips and avoid pytest-timeout.

Only TestConversationsUpdatedAtPreserved uses the async ``db`` fixture (pure
read — no DDL, no lock contention).

conversations.updated_at is intentionally NOT in _DROPPED_COLUMNS — it has
live reads at bot/webhooks/telegram.py:155 and bot/webhooks/whatsapp.py:202.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import text

# ---------------------------------------------------------------------------
# STAB-05 (119-07) attnum-bloat fix — scratch DB for the column-churn DDL.
#
# This file pre-dates STAB-03 (119-05) and lives OUTSIDE tests/migrations/, so
# STAB-03's scratch-DB redirect never covered it. Each upgrade/downgrade cycle
# DROPs + re-ADDs 9 ``contacts`` columns; run against onnix_dev that bloats
# its pg_attribute attnum high-water permanently (+~36/run), violating the
# post-suite "dev contacts.attnum <= baseline" criterion. The DDL is now routed
# to a throwaway ``onnix_test_mig_026_<pid>`` DB (schema-only clone of dev,
# zero rows), so the column churn — and its attnum bloat — dies with the DB.
# ---------------------------------------------------------------------------
_SCRATCH_DB = f"onnix_test_mig_026_{os.getpid()}"

# ---------------------------------------------------------------------------
# Bind to migration source-of-truth via importlib (drift guard).
# ---------------------------------------------------------------------------
_MIG_PATH = (
    Path(__file__).parent.parent
    / "alembic"
    / "versions"
    / "026_drop_dead_columns.py"
)
_spec = importlib.util.spec_from_file_location("mig026", _MIG_PATH)
_mig026 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mig026)

_DROPPED_COLUMNS: list[tuple[str, str]] = _mig026._DROPPED_COLUMNS  # drift guard


# ---------------------------------------------------------------------------
# psql batch helper — sends all SQL in a single connection.
# ---------------------------------------------------------------------------

def _admin_psql(db: str, sql: str, *, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a single SQL statement against an explicit DB (postgres container)."""
    return subprocess.run(
        [
            "docker", "exec", "onnix-postgres",
            "psql", "-U", "onnix", "-d", db, "--no-psqlrc", "-tA", "-c", sql,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _drop_scratch_db(name: str) -> None:
    """Terminate backends on `name`, then DROP DATABASE IF EXISTS. Safe-by-name."""
    assert name.startswith("onnix_test_mig_"), (
        f"refusing to drop non-scratch DB: {name!r}"
    )
    _admin_psql(
        "postgres",
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = '{name}' AND pid <> pg_backend_pid();",
    )
    res = _admin_psql("postgres", f"DROP DATABASE IF EXISTS {name};")
    if res.returncode != 0:
        raise RuntimeError(
            f"Failed to drop scratch DB {name}:\nstdout={res.stdout}\nstderr={res.stderr}"
        )


@pytest.fixture(scope="module", autouse=True)
def _scratch_db():
    """STAB-05 (119-07) — run this file's column-churn DDL on a throwaway DB.

    A schema-only clone of onnix_dev's public schema (zero rows) brought to
    the same head, so the DROP/ADD COLUMN roundtrips here never touch — or bloat
    the attnum of — onnix_dev. Dropped on teardown; bloat dies with it.
    """
    _drop_scratch_db(_SCRATCH_DB)
    create = _admin_psql("postgres", f"CREATE DATABASE {_SCRATCH_DB} TEMPLATE template0;")
    if create.returncode != 0:
        raise RuntimeError(
            f"Failed to create scratch DB {_SCRATCH_DB}:\n"
            f"stdout={create.stdout}\nstderr={create.stderr}"
        )
    ext = _admin_psql(
        _SCRATCH_DB,
        "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"; "
        "CREATE EXTENSION IF NOT EXISTS pgcrypto; "
        "CREATE EXTENSION IF NOT EXISTS pg_trgm; "
        "CREATE EXTENSION IF NOT EXISTS unaccent; "
        "CREATE EXTENSION IF NOT EXISTS vector;",
    )
    if ext.returncode != 0:
        _drop_scratch_db(_SCRATCH_DB)
        raise RuntimeError(
            f"Failed to create extensions on scratch DB {_SCRATCH_DB}:\n"
            f"stdout={ext.stdout}\nstderr={ext.stderr}"
        )
    seed = subprocess.run(
        [
            "docker", "exec", "onnix-postgres", "bash", "-c",
            f"pg_dump -U onnix --schema-only --no-owner --no-privileges "
            f"-n public onnix_dev | psql -U onnix -d {_SCRATCH_DB}",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if seed.returncode != 0:
        _drop_scratch_db(_SCRATCH_DB)
        raise RuntimeError(
            f"Failed to seed scratch DB {_SCRATCH_DB} from onnix_dev schema:\n"
            f"stdout={seed.stdout}\nstderr={seed.stderr}"
        )
    try:
        yield _SCRATCH_DB
    finally:
        _drop_scratch_db(_SCRATCH_DB)


def _psql_batch(sql_block: str, *, timeout: int = 120) -> subprocess.CompletedProcess:
    """Execute a multi-statement SQL block via psql stdin in one docker exec call."""
    return subprocess.run(
        [
            "docker", "exec", "-i", "onnix-postgres",
            "psql", "-U", "onnix", "-d", _SCRATCH_DB, "--no-psqlrc",
        ],
        input=sql_block,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _column_exists_sync(table: str, column: str) -> bool:
    """Check column presence via a single psql call."""
    result = subprocess.run(
        [
            "docker", "exec", "onnix-postgres",
            "psql", "-U", "onnix", "-d", _SCRATCH_DB,
            "--no-psqlrc", "-t", "-A", "-c",
            f"SELECT COUNT(*) FROM information_schema.columns "
            f"WHERE table_schema = 'public' "
            f"AND table_name = '{table}' AND column_name = '{column}'",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.stdout.strip() == "1"


def _terminate_blocking() -> None:
    """Kill idle-in-transaction sessions to unblock DDL."""
    subprocess.run(
        [
            "docker", "exec", "onnix-postgres",
            "psql", "-U", "onnix", "-d", _SCRATCH_DB,
            "--no-psqlrc", "-c",
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{_SCRATCH_DB}' "
            "AND state IN ('idle in transaction', 'idle in transaction (aborted)') "
            "AND pid <> pg_backend_pid()",
        ],
        capture_output=True,
        timeout=10,
    )


# ---------------------------------------------------------------------------
# Batched DDL blocks — each is sent as a single psql connection.
# ---------------------------------------------------------------------------

_UPGRADE_SQL = """
ALTER TABLE contacts DROP CONSTRAINT IF EXISTS contacts_assigned_to_fkey;
ALTER TABLE lead_events DROP CONSTRAINT IF EXISTS lead_events_assigned_to_fkey;
ALTER TABLE contacts DROP COLUMN IF EXISTS interest_operation;
ALTER TABLE contacts DROP COLUMN IF EXISTS interest_type;
ALTER TABLE contacts DROP COLUMN IF EXISTS interest_city;
ALTER TABLE contacts DROP COLUMN IF EXISTS interest_min_price;
ALTER TABLE contacts DROP COLUMN IF EXISTS interest_max_price;
ALTER TABLE contacts DROP COLUMN IF EXISTS interest_bedrooms;
ALTER TABLE contacts DROP COLUMN IF EXISTS original_data;
ALTER TABLE contacts DROP COLUMN IF EXISTS last_contact_at;
ALTER TABLE contacts DROP COLUMN IF EXISTS assigned_to;
ALTER TABLE messages DROP COLUMN IF EXISTS error_code;
ALTER TABLE messages DROP COLUMN IF EXISTS error_message;
ALTER TABLE lead_events DROP COLUMN IF EXISTS assigned_to;
"""

_DOWNGRADE_SQL = """
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS interest_operation VARCHAR(20);
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS interest_type VARCHAR(50);
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS interest_city VARCHAR(100);
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS interest_min_price NUMERIC(15,2);
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS interest_max_price NUMERIC(15,2);
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS interest_bedrooms SMALLINT;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS original_data JSONB;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS last_contact_at TIMESTAMPTZ;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS assigned_to INTEGER;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'contacts_assigned_to_fkey'
  ) THEN
    ALTER TABLE contacts ADD CONSTRAINT contacts_assigned_to_fkey
    FOREIGN KEY (assigned_to) REFERENCES users(id);
  END IF;
END $$;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS error_code VARCHAR(20);
ALTER TABLE messages ADD COLUMN IF NOT EXISTS error_message TEXT;
ALTER TABLE lead_events ADD COLUMN IF NOT EXISTS assigned_to INTEGER;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'lead_events_assigned_to_fkey'
  ) THEN
    ALTER TABLE lead_events ADD CONSTRAINT lead_events_assigned_to_fkey
    FOREIGN KEY (assigned_to) REFERENCES users(id);
  END IF;
END $$;
"""

_IS_NULLABLE_CHECK_SQL = (
    "SELECT table_name || '.' || column_name "
    "FROM information_schema.columns "
    "WHERE table_schema = 'public' AND is_nullable = 'NO' AND ("
    + " OR ".join(
        f"(table_name = '{t}' AND column_name = '{c}')"
        for t, c in [
            ("contacts", "interest_operation"),
            ("contacts", "interest_type"),
            ("contacts", "interest_city"),
            ("contacts", "interest_min_price"),
            ("contacts", "interest_max_price"),
            ("contacts", "interest_bedrooms"),
            ("contacts", "original_data"),
            ("contacts", "last_contact_at"),
            ("contacts", "assigned_to"),
            ("messages", "error_code"),
            ("messages", "error_message"),
            ("lead_events", "assigned_to"),
        ]
    )
    + ")"
)


def _apply_upgrade() -> None:
    _terminate_blocking()
    _psql_batch(_UPGRADE_SQL)


def _apply_downgrade() -> None:
    _terminate_blocking()
    _psql_batch(_DOWNGRADE_SQL)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.timeout(240)
class TestUpgradeDropsAllColumns:
    def test_upgrade_drops_all_columns(self) -> None:
        """After upgrade DDL, all 12 dead columns are absent from information_schema."""
        _apply_downgrade()
        try:
            _apply_upgrade()

            still_present = [
                f"{t}.{c}"
                for t, c in _DROPPED_COLUMNS
                if _column_exists_sync(t, c)
            ]
            assert not still_present, (
                f"Upgrade failed — these columns still exist: {still_present}"
            )
        finally:
            _apply_downgrade()


@pytest.mark.timeout(240)
class TestUpgradeIdempotent:
    def test_upgrade_idempotent(self) -> None:
        """Running upgrade DDL twice (IF EXISTS) must not error and columns stay absent."""
        _apply_downgrade()
        try:
            _apply_upgrade()
            _apply_upgrade()  # second run — IF EXISTS prevents error

            still_present = [
                f"{t}.{c}"
                for t, c in _DROPPED_COLUMNS
                if _column_exists_sync(t, c)
            ]
            assert not still_present, (
                f"After idempotent upgrade, columns still exist: {still_present}"
            )
        finally:
            _apply_downgrade()


@pytest.mark.timeout(240)
class TestDowngradeRestoresAllColumns:
    def test_downgrade_restores_all_columns(self) -> None:
        """After downgrade DDL, all 12 columns are present."""
        _apply_downgrade()
        _apply_upgrade()
        try:
            _apply_downgrade()

            missing = [
                f"{t}.{c}"
                for t, c in _DROPPED_COLUMNS
                if not _column_exists_sync(t, c)
            ]
            assert not missing, (
                f"Downgrade failed — these columns are still absent: {missing}"
            )
        finally:
            pass  # DB left in downgraded/restored state — correct baseline


@pytest.mark.timeout(240)
class TestDowngradeColumnsAreNullable:
    def test_downgrade_columns_are_nullable(self) -> None:
        """After downgrade, all restored columns are nullable (is_nullable = YES)."""
        _apply_downgrade()
        _apply_upgrade()
        try:
            _apply_downgrade()

            result = subprocess.run(
                [
                    "docker", "exec", "onnix-postgres",
                    "psql", "-U", "onnix", "-d", _SCRATCH_DB,
                    "--no-psqlrc", "-t", "-A", "-c",
                    _IS_NULLABLE_CHECK_SQL,
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            non_nullable = [
                line.strip()
                for line in result.stdout.strip().splitlines()
                if line.strip()
            ]
            assert not non_nullable, (
                f"Downgrade restored non-nullable columns: {non_nullable}"
            )
        finally:
            pass


class TestConversationsUpdatedAtPreserved:
    async def test_conversations_updated_at_not_dropped(self, db) -> None:
        """conversations.updated_at must NOT be in _DROPPED_COLUMNS.

        This column has live reads at:
          - bot/webhooks/telegram.py:155  ORDER BY c.updated_at DESC LIMIT 1
          - bot/webhooks/whatsapp.py:202  ORDER BY c.updated_at DESC LIMIT 1
        """
        dropped_set = set(_DROPPED_COLUMNS)
        assert ("conversations", "updated_at") not in dropped_set, (
            "conversations.updated_at must not be dropped — it has live reads"
        )
        result = await db.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'conversations' AND column_name = 'updated_at'"
            )
        )
        exists = result.scalar() > 0
        assert exists, (
            "conversations.updated_at is missing from the DB — was incorrectly dropped"
        )
