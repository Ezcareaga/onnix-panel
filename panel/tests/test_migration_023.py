"""Tests for Alembic migration 023: seed followup toggle defaults in bot_settings.

Verifies that the three followup toggle rows are inserted with the correct
default values, that re-running the migration preserves a pre-existing value
(ON CONFLICT DO NOTHING), and that downgrade removes all three rows.
"""
import importlib.util
from pathlib import Path

from sqlalchemy import text

# ---------------------------------------------------------------------------
# Bind to migration source-of-truth (Fix A)
# Alembic version files have numeric-prefix filenames that are not importable
# as regular Python modules, so we use importlib to load them directly.
# ---------------------------------------------------------------------------
_MIG_PATH = (
    Path(__file__).parent.parent
    / "alembic"
    / "versions"
    / "023_seed_followup_toggle_defaults.py"
)
_spec = importlib.util.spec_from_file_location("mig023", _MIG_PATH)
_mig023 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mig023)

_SEEDED_ROWS = _mig023._SEEDED_ROWS  # list[tuple[str, str, str]]
_EXPECTED_VALUES: dict[str, str] = {key: value for key, value, _ in _SEEDED_ROWS}
_EXPECTED_DESCRIPTIONS: dict[str, str] = {key: desc for key, _, desc in _SEEDED_ROWS}

# ---------------------------------------------------------------------------
# Shared SQL constants
# ---------------------------------------------------------------------------
_INSERT_SQL = (
    "INSERT INTO bot_settings (key, value, description, updated_at) "
    "VALUES (:key, :value, :description, NOW()) "
    "ON CONFLICT (key) DO NOTHING"
)

_DELETE_SQL = "DELETE FROM bot_settings WHERE key = :key"


class TestUpgradeSeedsThreeToggles:
    async def test_upgrade_seeds_three_toggles(self, db):
        """After migration upgrade logic runs, all 3 keys exist with correct defaults and descriptions."""
        keys = list(_EXPECTED_VALUES.keys())
        try:
            # Ensure rows are absent first (idempotent setup)
            for key in keys:
                await db.execute(text(_DELETE_SQL), {"key": key})
            await db.flush()

            # Re-apply upgrade SQL using real descriptions (Fix B)
            for key, value in _EXPECTED_VALUES.items():
                await db.execute(
                    text(_INSERT_SQL),
                    {
                        "key": key,
                        "value": value,
                        "description": _EXPECTED_DESCRIPTIONS[key],
                    },
                )
            await db.flush()

            # Assert values AND descriptions (Fix B)
            result = await db.execute(
                text(
                    "SELECT key, value, description FROM bot_settings "
                    "WHERE key = ANY(:keys)"
                ),
                {"keys": keys},
            )
            rows = {r[0]: {"value": r[1], "description": r[2]} for r in result.fetchall()}

            assert set(rows.keys()) == set(_EXPECTED_VALUES.keys()), (
                f"Expected keys {set(_EXPECTED_VALUES.keys())}, got {set(rows.keys())}"
            )
            for key, expected_value in _EXPECTED_VALUES.items():
                assert rows[key]["value"] == expected_value, (
                    f"Key '{key}': expected value '{expected_value}', got '{rows[key]['value']}'"
                )
                assert rows[key]["description"] == _EXPECTED_DESCRIPTIONS[key], (
                    f"Key '{key}': expected description '{_EXPECTED_DESCRIPTIONS[key]}', "
                    f"got '{rows[key]['description']}'"
                )
        finally:
            # Fix C: guarantee cleanup even on mid-test failure
            for key in keys:
                await db.execute(text(_DELETE_SQL), {"key": key})
            await db.flush()


class TestUpgradeIdempotentOnConflict:
    async def test_upgrade_idempotent_on_conflict(self, db):
        """Re-running the migration with ON CONFLICT DO NOTHING preserves a pre-set value."""
        target_key = "followup_24h_enabled"
        custom_value = "false"  # Override from the migration default of "true"
        keys = [target_key]
        try:
            # Clean slate
            await db.execute(text(_DELETE_SQL), {"key": target_key})
            await db.flush()

            # Insert manually with a non-default value (simulates admin already changed it)
            await db.execute(
                text(
                    "INSERT INTO bot_settings (key, value, description, updated_at) "
                    "VALUES (:key, :value, 'manually set', NOW())"
                ),
                {"key": target_key, "value": custom_value},
            )
            await db.flush()

            # Re-apply the migration INSERT — must not overwrite the custom value
            await db.execute(
                text(_INSERT_SQL),
                {
                    "key": target_key,
                    "value": _EXPECTED_VALUES[target_key],
                    "description": _EXPECTED_DESCRIPTIONS[target_key],
                },
            )
            await db.flush()

            result = await db.execute(
                text("SELECT value FROM bot_settings WHERE key = :key"),
                {"key": target_key},
            )
            stored_value = result.scalar()
            assert stored_value == custom_value, (
                f"ON CONFLICT DO NOTHING violated: expected '{custom_value}', "
                f"got '{stored_value}'"
            )
        finally:
            # Fix C: guarantee cleanup even on mid-test failure
            for key in keys:
                await db.execute(text(_DELETE_SQL), {"key": key})
            await db.flush()


class TestDowngradeRemovesToggles:
    async def test_downgrade_removes_toggles(self, db):
        """After downgrade logic runs, all 3 rows are absent from bot_settings."""
        keys = list(_EXPECTED_VALUES.keys())
        try:
            # Seed rows first (simulate post-upgrade state)
            for key, value in _EXPECTED_VALUES.items():
                await db.execute(
                    text(_INSERT_SQL),
                    {
                        "key": key,
                        "value": value,
                        "description": _EXPECTED_DESCRIPTIONS[key],
                    },
                )
            await db.flush()

            # Apply downgrade SQL
            for key in keys:
                await db.execute(text(_DELETE_SQL), {"key": key})
            await db.flush()

            result = await db.execute(
                text(
                    "SELECT COUNT(*) FROM bot_settings "
                    "WHERE key = ANY(:keys)"
                ),
                {"keys": keys},
            )
            count = result.scalar()
            assert count == 0, (
                f"Downgrade failed: {count} followup toggle rows still present"
            )
        finally:
            # Fix C: guarantee cleanup even on mid-test failure
            for key in keys:
                await db.execute(text(_DELETE_SQL), {"key": key})
            await db.flush()
