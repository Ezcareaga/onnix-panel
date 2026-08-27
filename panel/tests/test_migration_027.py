"""Tests for Alembic migration 027: seed wa_tpl_ai_dual_fail_text in bot_settings.

Verifies that the wa_tpl_ai_dual_fail_text row is inserted with the
user-specified AI dual-fail fallback text, that re-running the migration is
idempotent (ON CONFLICT DO UPDATE produces the same result), and that
downgrade removes the row.

All tests wrap their DB work in try/finally to remove the row on exit (or
restore to its pre-test state), ensuring no pollution of the shared dev DB.
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
    / "027_seed_ai_dual_fail_text.py"
)
_spec = importlib.util.spec_from_file_location("mig027", _MIG_PATH)
_mig027 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mig027)

_KEY = "wa_tpl_ai_dual_fail_text"
_UPGRADE_SQL = _mig027._UPGRADE_SQL
_DOWNGRADE_SQL = _mig027._DOWNGRADE_SQL
_AI_DUAL_FAIL_TEXT = _mig027._AI_DUAL_FAIL_TEXT

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_SELECT_SQL = "SELECT value FROM bot_settings WHERE key = :key"
_CLEANUP_SQL = "DELETE FROM bot_settings WHERE key = :key"


class TestUpgradeSetsAiDualFailText:
    async def test_upgrade_sets_ai_dual_fail_text(self, db):
        """After upgrade SQL runs, wa_tpl_ai_dual_fail_text equals the migration text constant."""
        try:
            # Ensure a clean slate before the test
            await db.execute(text(_CLEANUP_SQL), {"key": _KEY})
            await db.flush()

            # Apply upgrade SQL
            await db.execute(text(_UPGRADE_SQL), {"value": _AI_DUAL_FAIL_TEXT, "key": _KEY})
            await db.flush()

            result = await db.execute(text(_SELECT_SQL), {"key": _KEY})
            stored = result.scalar()
            assert stored == _AI_DUAL_FAIL_TEXT, (
                f"Expected migration text, got: {stored!r}"
            )
        finally:
            await db.execute(text(_CLEANUP_SQL), {"key": _KEY})
            await db.flush()


class TestUpgradeIdempotent:
    async def test_upgrade_is_idempotent(self, db):
        """Running upgrade SQL twice produces the same final value (ON CONFLICT DO UPDATE)."""
        try:
            # First run
            await db.execute(text(_UPGRADE_SQL), {"value": _AI_DUAL_FAIL_TEXT, "key": _KEY})
            await db.flush()

            # Second run — must not raise, must not change value
            await db.execute(text(_UPGRADE_SQL), {"value": _AI_DUAL_FAIL_TEXT, "key": _KEY})
            await db.flush()

            result = await db.execute(text(_SELECT_SQL), {"key": _KEY})
            stored = result.scalar()
            assert stored == _AI_DUAL_FAIL_TEXT, (
                f"Idempotency violated: expected migration text, got: {stored!r}"
            )
        finally:
            await db.execute(text(_CLEANUP_SQL), {"key": _KEY})
            await db.flush()


class TestDowngradeRemovesRow:
    async def test_downgrade_removes_row(self, db):
        """After downgrade SQL runs, wa_tpl_ai_dual_fail_text row is gone from bot_settings."""
        try:
            # Simulate post-upgrade state
            await db.execute(text(_UPGRADE_SQL), {"value": _AI_DUAL_FAIL_TEXT, "key": _KEY})
            await db.flush()

            # Apply downgrade SQL
            await db.execute(text(_DOWNGRADE_SQL), {"key": _KEY})
            await db.flush()

            result = await db.execute(text(_SELECT_SQL), {"key": _KEY})
            stored = result.scalar()
            assert stored is None, (
                f"Downgrade failed: expected row to be deleted but got: {stored!r}"
            )
        finally:
            # Best-effort cleanup; row may already be gone after downgrade
            await db.execute(text(_CLEANUP_SQL), {"key": _KEY})
            await db.flush()
