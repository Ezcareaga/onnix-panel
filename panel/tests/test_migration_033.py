"""Tests for Alembic migration 033: add construction_state to properties + M5 flags.

Validates the post-migration schema contract (column, check constraint, index)
and the bot_settings seed (M5 feature flags).

All tests run against onnix_dev and assume migration 033 is already applied
(i.e., tests are read-only schema contracts, not up/down runners).  This follows
the project pattern for migrations that touch DDL — running DDL inside an async
test session causes lock contention on asyncpg.

DDL-level tests (column exists, index exists, constraint) use docker exec psql
(synchronous, auto-commit, no async pool).  DML tests (constraint acceptance /
rejection, flag presence) use the async ``db`` fixture.

M5_FLAG_KEYS is imported from the migration module itself (drift guard) so this
test file never duplicates the source-of-truth list.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

# ---------------------------------------------------------------------------
# Bind to migration source-of-truth via importlib (drift guard)
# ---------------------------------------------------------------------------
_MIG_PATH = (
    Path(__file__).parent.parent
    / "alembic"
    / "versions"
    / "033_m5_construction_state.py"
)
_spec = importlib.util.spec_from_file_location("mig033", _MIG_PATH)
_mig033 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mig033)

M5_FLAG_KEYS: list[tuple[str, str]] = _mig033.M5_FLAG_KEYS  # drift guard

# ---------------------------------------------------------------------------
# Valid and invalid construction_state values (mirrors CHECK constraint)
# ---------------------------------------------------------------------------
VALID_VALUES = ["en_pozo", "en_construccion", "a_estrenar", "terminado", None]
INVALID_VALUES = ["pozo", "construido", "new", "", "EN_POZO"]

# ---------------------------------------------------------------------------
# psql helper (synchronous, auto-commit)
# ---------------------------------------------------------------------------

def _psql(sql: str, *, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "docker", "exec", "onnix-postgres",
            "psql", "-U", "onnix", "-d", os.environ["POSTGRES_DB"],
            "--no-psqlrc", "-t", "-A", "-c", sql,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Test 1: Column exists after upgrade
# ---------------------------------------------------------------------------

class TestColumnExistsAfterUpgrade:
    def test_033_column_exists_after_upgrade(self) -> None:
        """After migration 033, properties.construction_state must exist in information_schema."""
        result = _psql(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            "AND table_name = 'properties' "
            "AND column_name = 'construction_state'"
        )
        assert result.returncode == 0, f"psql error: {result.stderr}"
        count = int(result.stdout.strip())
        assert count == 1, (
            "Column properties.construction_state not found in information_schema. "
            "Migration 033 may not have been applied."
        )

    def test_033_column_is_nullable(self) -> None:
        """construction_state must be nullable (NULL is valid for unknown state)."""
        result = _psql(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            "AND table_name = 'properties' "
            "AND column_name = 'construction_state'"
        )
        assert result.returncode == 0, f"psql error: {result.stderr}"
        is_nullable = result.stdout.strip()
        assert is_nullable == "YES", (
            f"construction_state should be nullable, got is_nullable={is_nullable!r}"
        )

    def test_033_column_max_length_is_20(self) -> None:
        """construction_state must be VARCHAR(20) — all 4 enum values fit with room."""
        result = _psql(
            "SELECT character_maximum_length FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            "AND table_name = 'properties' "
            "AND column_name = 'construction_state'"
        )
        assert result.returncode == 0, f"psql error: {result.stderr}"
        length = int(result.stdout.strip())
        assert length == 20, (
            f"construction_state should be VARCHAR(20), got {length}"
        )


# ---------------------------------------------------------------------------
# Test 2: CHECK constraint rejects invalid values
# ---------------------------------------------------------------------------

class TestCheckConstraintRejectsInvalidValues:
    async def test_033_check_constraint_rejects_invalid_values(self, db) -> None:
        """Inserting an invalid construction_state value must raise IntegrityError."""
        # Pick a real property id to use as a reference for the UPDATE test
        result = await db.execute(
            text(
                "SELECT id FROM properties WHERE is_active = TRUE LIMIT 1"
            )
        )
        row = result.fetchone()
        if row is None:
            pytest.skip("No active properties in test DB — cannot test constraint")
        prop_id = row[0]

        for bad_value in INVALID_VALUES:
            with pytest.raises(IntegrityError, match="ck_properties_construction_state"):
                await db.execute(
                    text(
                        "UPDATE properties SET construction_state = :val WHERE id = :id"
                    ),
                    {"val": bad_value, "id": prop_id},
                )
                await db.flush()
            await db.rollback()


# ---------------------------------------------------------------------------
# Test 3: CHECK constraint accepts valid values
# ---------------------------------------------------------------------------

class TestCheckConstraintAcceptsValidValues:
    async def test_033_check_constraint_accepts_valid_values(self, db) -> None:
        """Each of the 4 canonical values and NULL must be accepted by the constraint."""
        result = await db.execute(
            text(
                "SELECT id FROM properties WHERE is_active = TRUE LIMIT 1"
            )
        )
        row = result.fetchone()
        if row is None:
            pytest.skip("No active properties in test DB — cannot test constraint")
        prop_id = row[0]

        # Save original value to restore after
        orig_result = await db.execute(
            text("SELECT construction_state FROM properties WHERE id = :id"),
            {"id": prop_id},
        )
        original_value = orig_result.scalar()

        try:
            for good_value in VALID_VALUES:
                if good_value is None:
                    await db.execute(
                        text(
                            "UPDATE properties SET construction_state = NULL WHERE id = :id"
                        ),
                        {"id": prop_id},
                    )
                else:
                    await db.execute(
                        text(
                            "UPDATE properties SET construction_state = :val WHERE id = :id"
                        ),
                        {"val": good_value, "id": prop_id},
                    )
                await db.flush()
                # Verify value was stored
                check = await db.execute(
                    text("SELECT construction_state FROM properties WHERE id = :id"),
                    {"id": prop_id},
                )
                stored = check.scalar()
                assert stored == good_value, (
                    f"Expected construction_state={good_value!r}, got {stored!r}"
                )
        finally:
            # Restore original value
            await db.execute(
                text(
                    "UPDATE properties SET construction_state = :val WHERE id = :id"
                ),
                {"val": original_value, "id": prop_id},
            )
            await db.flush()


# ---------------------------------------------------------------------------
# Test 4: Index exists
# ---------------------------------------------------------------------------

class TestIndexExists:
    def test_033_index_exists(self) -> None:
        """ix_properties_construction_state btree index must exist after migration 033."""
        result = _psql(
            "SELECT COUNT(*) FROM pg_indexes "
            "WHERE tablename = 'properties' "
            "AND indexname = 'ix_properties_construction_state'"
        )
        assert result.returncode == 0, f"psql error: {result.stderr}"
        count = int(result.stdout.strip())
        assert count == 1, (
            "Index ix_properties_construction_state not found. "
            "Migration 033 may not have been applied or index was dropped."
        )

    def test_033_index_is_btree(self) -> None:
        """Index must be a btree (optimal for equality lookups on low-cardinality enum)."""
        result = _psql(
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename = 'properties' "
            "AND indexname = 'ix_properties_construction_state'"
        )
        assert result.returncode == 0, f"psql error: {result.stderr}"
        indexdef = result.stdout.strip()
        assert "btree" in indexdef.lower(), (
            f"Expected btree index, got: {indexdef!r}"
        )


# ---------------------------------------------------------------------------
# Test 5: M5 feature flags seeded in bot_settings
# ---------------------------------------------------------------------------

class TestSeedFlagsPresent:
    async def test_033_seed_flags_present(self, db) -> None:
        """After migration 033, both M5 feature flag keys must exist with a valid boolean-string value.

        Note: migration 033 seeds value='false' by default. The value may later be
        flipped to 'true' in a specific environment (e.g. staging during Fase J
        rollout of M5). This test validates presence + shape, not the runtime value.
        """
        expected_keys = [k for k, _ in M5_FLAG_KEYS]
        result = await db.execute(
            text(
                "SELECT key, value FROM bot_settings "
                "WHERE key = ANY(:keys)"
            ),
            {"keys": expected_keys},
        )
        rows = {row[0]: row[1] for row in result.fetchall()}

        # All keys must be present
        missing = [k for k in expected_keys if k not in rows]
        assert not missing, (
            f"M5 feature flag keys missing from bot_settings: {missing}. "
            "Migration 033 seed may not have run."
        )

        # All must have a valid boolean-string value (seed is 'false', may be 'true' post-activation)
        bad_value = [
            f"{k}={rows[k]!r}" for k in expected_keys
            if rows[k] not in ("true", "false")
        ]
        assert not bad_value, (
            f"M5 feature flags must be 'true' or 'false', got: {bad_value}"
        )

    async def test_033_seed_flags_have_descriptions(self, db) -> None:
        """M5 feature flag rows must have non-empty descriptions (auditable)."""
        expected_keys = [k for k, _ in M5_FLAG_KEYS]
        result = await db.execute(
            text(
                "SELECT key, description FROM bot_settings "
                "WHERE key = ANY(:keys)"
            ),
            {"keys": expected_keys},
        )
        rows = {row[0]: row[1] for row in result.fetchall()}

        empty_desc = [
            k for k in expected_keys
            if not rows.get(k)
        ]
        assert not empty_desc, (
            f"M5 feature flags missing description: {empty_desc}"
        )

    async def test_033_seed_idempotent(self, db) -> None:
        """Re-running seed INSERT ON CONFLICT DO NOTHING must not change existing values."""
        key = "m5_zero_results_alternatives_enabled"
        # Record current value
        result = await db.execute(
            text("SELECT value FROM bot_settings WHERE key = :k"),
            {"k": key},
        )
        original_value = result.scalar()

        # Simulate re-run of seed (ON CONFLICT DO NOTHING)
        await db.execute(
            text(
                "INSERT INTO bot_settings (key, value, description, updated_at) "
                "VALUES (:key, 'SENTINEL_RERUN', 'test', NOW()) "
                "ON CONFLICT (key) DO NOTHING"
            ),
            {"key": key},
        )
        await db.flush()

        # Value must be unchanged
        result2 = await db.execute(
            text("SELECT value FROM bot_settings WHERE key = :k"),
            {"k": key},
        )
        after_value = result2.scalar()
        assert after_value == original_value, (
            f"Seed re-run changed value from {original_value!r} to {after_value!r}. "
            "ON CONFLICT DO NOTHING should have preserved original."
        )
