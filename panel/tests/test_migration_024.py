"""Tests for Alembic migration 024: seed wa_tpl_opt_out text in bot_settings.

Verifies that the wa_tpl_opt_out row is updated (or inserted) with the
user-specified opt-out text, that re-running the migration is idempotent
(ON CONFLICT DO UPDATE produces the same result), and that downgrade reverts
the value to empty string.

All tests wrap their DB work in try/finally to restore wa_tpl_opt_out to
value='' on exit, ensuring no pollution of the shared dev DB.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Bind to migration source-of-truth (importlib — numeric prefix not importable)
# ---------------------------------------------------------------------------
_MIG_PATH = (
    Path(__file__).parent.parent
    / "alembic"
    / "versions"
    / "024_seed_opt_out_text.py"
)
_spec = importlib.util.spec_from_file_location("mig024", _MIG_PATH)
_mig024 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mig024)

_KEY = "wa_tpl_opt_out"
_UPGRADE_SQL = _mig024._UPGRADE_SQL
_DOWNGRADE_SQL = _mig024._DOWNGRADE_SQL
_OPT_OUT_TEXT = _mig024._OPT_OUT_TEXT

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_RESTORE_SQL = "UPDATE bot_settings SET value = '' WHERE key = :key"
_SELECT_SQL = "SELECT value FROM bot_settings WHERE key = :key"


class TestUpgradeSetsOptOutText:
    async def test_upgrade_sets_opt_out_text(self, db):
        """After upgrade SQL runs, wa_tpl_opt_out equals the migration text constant."""
        try:
            # Start from empty (pre-Fase-6 state)
            await db.execute(text(_RESTORE_SQL), {"key": _KEY})
            await db.flush()

            # Apply upgrade SQL
            await db.execute(text(_UPGRADE_SQL), {"value": _OPT_OUT_TEXT, "key": _KEY})
            await db.flush()

            result = await db.execute(text(_SELECT_SQL), {"key": _KEY})
            stored = result.scalar()
            assert stored == _OPT_OUT_TEXT, (
                f"Expected migration text, got: {stored!r}"
            )
        finally:
            await db.execute(text(_RESTORE_SQL), {"key": _KEY})
            await db.flush()


class TestUpgradeIdempotent:
    async def test_upgrade_is_idempotent(self, db):
        """Running upgrade SQL twice produces the same final value (ON CONFLICT DO UPDATE)."""
        try:
            # First run
            await db.execute(text(_UPGRADE_SQL), {"value": _OPT_OUT_TEXT, "key": _KEY})
            await db.flush()

            # Second run — must not raise, must not change value
            await db.execute(text(_UPGRADE_SQL), {"value": _OPT_OUT_TEXT, "key": _KEY})
            await db.flush()

            result = await db.execute(text(_SELECT_SQL), {"key": _KEY})
            stored = result.scalar()
            assert stored == _OPT_OUT_TEXT, (
                f"Idempotency violated: expected migration text, got: {stored!r}"
            )
        finally:
            await db.execute(text(_RESTORE_SQL), {"key": _KEY})
            await db.flush()


class TestDowngradeResetsToEmpty:
    async def test_downgrade_resets_value_to_empty(self, db):
        """After downgrade SQL runs, wa_tpl_opt_out.value is empty string."""
        try:
            # Simulate post-upgrade state
            await db.execute(text(_UPGRADE_SQL), {"value": _OPT_OUT_TEXT, "key": _KEY})
            await db.flush()

            # Apply downgrade SQL
            await db.execute(text(_DOWNGRADE_SQL), {"key": _KEY})
            await db.flush()

            result = await db.execute(text(_SELECT_SQL), {"key": _KEY})
            stored = result.scalar()
            assert stored == "", (
                f"Downgrade failed: expected '' but got: {stored!r}"
            )
        finally:
            await db.execute(text(_RESTORE_SQL), {"key": _KEY})
            await db.flush()
