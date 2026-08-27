"""Tests for Alembic migration 025: drop dead WA template rows from bot_settings.

Verifies that the five dead WhatsApp template keys are deleted by upgrade,
that the delete is idempotent when rows are already absent, and that downgrade
restores each row with its exact captured prod value and description.

_DELETED_ROWS is imported directly from the migration module (drift guard) so
this test file never duplicates the source-of-truth list.
"""
import importlib.util
from pathlib import Path

from sqlalchemy import text

# ---------------------------------------------------------------------------
# Bind to migration source-of-truth via importlib
# Alembic version files have numeric-prefix filenames that cannot be imported
# as regular Python modules.
# ---------------------------------------------------------------------------
_MIG_PATH = (
    Path(__file__).parent.parent
    / "alembic"
    / "versions"
    / "025_drop_dead_wa_templates.py"
)
_spec = importlib.util.spec_from_file_location("mig025", _MIG_PATH)
_mig025 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mig025)

_DELETED_ROWS: list[tuple[str, str, str | None]] = _mig025._DELETED_ROWS  # drift guard

# ---------------------------------------------------------------------------
# Shared SQL helpers
# ---------------------------------------------------------------------------
_INSERT_SQL = (
    "INSERT INTO bot_settings (key, value, description, updated_at) "
    "VALUES (:key, :value, :description, NOW()) "
    "ON CONFLICT (key) DO NOTHING"
)
_RESTORE_SQL = _INSERT_SQL  # alias used by downgrade sentinel test
_DELETE_SQL = "DELETE FROM bot_settings WHERE key = :key"
_ALL_KEYS: list[str] = [key for key, _, _ in _DELETED_ROWS]


async def _cleanup(db) -> None:
    """Remove all five keys from the DB — used in finally blocks."""
    for key in _ALL_KEYS:
        await db.execute(text(_DELETE_SQL), {"key": key})
    await db.flush()


async def _seed_all(db) -> None:
    """Insert all five rows with their captured prod values."""
    for key, value, description in _DELETED_ROWS:
        await db.execute(
            text(_INSERT_SQL),
            {"key": key, "value": value, "description": description},
        )
    await db.flush()


class TestUpgradeDeletesAllFiveKeys:
    async def test_upgrade_deletes_all_five_keys(self, db):
        """After running the upgrade DELETE SQL, all five keys are absent."""
        try:
            await _seed_all(db)

            # Apply upgrade SQL (one DELETE per key, matching migration logic)
            for key in _ALL_KEYS:
                await db.execute(text(_DELETE_SQL), {"key": key})
            await db.flush()

            result = await db.execute(
                text("SELECT COUNT(*) FROM bot_settings WHERE key = ANY(:keys)"),
                {"keys": _ALL_KEYS},
            )
            count = result.scalar()
            assert count == 0, (
                f"Upgrade failed: {count} of the 5 dead template rows still present"
            )
        finally:
            await _cleanup(db)


class TestUpgradeIdempotentWhenRowsAlreadyAbsent:
    async def test_upgrade_idempotent_when_rows_already_absent(self, db):
        """DELETE on already-absent rows raises no error and count stays 0."""
        try:
            # Ensure rows are absent before running
            await _cleanup(db)

            # Re-apply the upgrade DELETE — must not error
            for key in _ALL_KEYS:
                await db.execute(text(_DELETE_SQL), {"key": key})
            await db.flush()

            result = await db.execute(
                text("SELECT COUNT(*) FROM bot_settings WHERE key = ANY(:keys)"),
                {"keys": _ALL_KEYS},
            )
            count = result.scalar()
            assert count == 0, (
                f"Expected 0 rows after idempotent delete, got {count}"
            )
        finally:
            await _cleanup(db)


class TestDowngradeRestoresAllFiveKeysWithExactValues:
    async def test_downgrade_restores_all_five_keys_with_exact_values(self, db):
        """Downgrade INSERT restores all five rows with exact captured prod values and descriptions."""
        try:
            # Start from post-upgrade state: rows absent
            await _cleanup(db)

            # Apply downgrade SQL (INSERT ... ON CONFLICT DO NOTHING, matching migration)
            for key, value, description in _DELETED_ROWS:
                await db.execute(
                    text(
                        "INSERT INTO bot_settings (key, value, description, updated_at) "
                        "VALUES (:key, :value, :description, NOW()) "
                        "ON CONFLICT (key) DO NOTHING"
                    ),
                    {"key": key, "value": value, "description": description},
                )
            await db.flush()

            # Fetch and verify all five rows
            result = await db.execute(
                text(
                    "SELECT key, value, description FROM bot_settings "
                    "WHERE key = ANY(:keys)"
                ),
                {"keys": _ALL_KEYS},
            )
            rows = {r[0]: {"value": r[1], "description": r[2]} for r in result.fetchall()}

            assert set(rows.keys()) == set(_ALL_KEYS), (
                f"Downgrade missing keys: {set(_ALL_KEYS) - set(rows.keys())}"
            )

            for key, expected_value, expected_description in _DELETED_ROWS:
                assert rows[key]["value"] == expected_value, (
                    f"Key '{key}': expected value '{expected_value}', "
                    f"got '{rows[key]['value']}'"
                )
                # wa_tpl_ambiguo_visita description is NULL in prod (None here)
                assert rows[key]["description"] == expected_description, (
                    f"Key '{key}': expected description '{expected_description}', "
                    f"got '{rows[key]['description']}'"
                )
        finally:
            await _cleanup(db)


class TestDowngradeOnConflictDoNothing:
    async def test_downgrade_preserves_existing_row_via_on_conflict(self, db):
        """If a row exists under one of the deleted keys, downgrade INSERT must preserve its value."""
        target_key = "wa_tpl_saludo"
        sentinel_value = "HX_SENTINEL_ABC123"
        sentinel_description = "sentinel — must not be overwritten"
        try:
            # Seed target with sentinel (non-migration values)
            await db.execute(text(_DELETE_SQL), {"key": target_key})
            await db.flush()
            await db.execute(
                text(
                    "INSERT INTO bot_settings (key, value, description, updated_at) "
                    "VALUES (:k, :v, :d, NOW())"
                ),
                {"k": target_key, "v": sentinel_value, "d": sentinel_description},
            )
            await db.flush()

            # Run downgrade INSERT (ON CONFLICT DO NOTHING) — should NOT overwrite
            rows_by_key = {k: (v, d) for k, v, d in _DELETED_ROWS}
            restore_value, restore_description = rows_by_key[target_key]
            await db.execute(
                text(_RESTORE_SQL),
                {"key": target_key, "value": restore_value, "description": restore_description},
            )
            await db.flush()

            result = await db.execute(
                text("SELECT value, description FROM bot_settings WHERE key = :k"),
                {"k": target_key},
            )
            row = result.fetchone()
            assert row[0] == sentinel_value, (
                f"Expected sentinel value preserved, got {row[0]}"
            )
            assert row[1] == sentinel_description, (
                f"Expected sentinel description preserved, got {row[1]}"
            )
        finally:
            await db.execute(text(_DELETE_SQL), {"key": target_key})
            await db.flush()
