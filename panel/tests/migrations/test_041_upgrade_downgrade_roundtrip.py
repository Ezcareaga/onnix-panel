"""Migration 041 — upgrade/downgrade/idempotency roundtrip test (VER-02).

Spec: .planning/phases/124-m6.3-verify-bot-recepcionista/124-01-PLAN.md §Task 2.
M6.3 — Phase 124-01.

Migration 041 (`041_seed_bot_default_mode`, down_revision `040_visits`) seeds a
single bot_settings row (key='bot_default_mode', value='busqueda') via
INSERT ... ON CONFLICT (key) DO NOTHING. Downgrade DELETEs the row. There is no
schema change — it is a pure data seed above 040.

Four behaviors are pinned (all against the scratch DB from conftest — never
onnix_dev, never onnix_prod):

1. UPGRADE      → head == 041_seed_bot_default_mode AND bot_default_mode='busqueda'.
2. DOWNGRADE    → head == 040_visits AND the bot_default_mode row is GONE (count 0).
3. RE-UPGRADE   → row present again with 'busqueda' (clean re-application).
4. IDEMPOTENCY  → at 041, flip the value to 'recepcionista', re-run the upgrade,
   and assert it STAYS 'recepcionista' (ON CONFLICT DO NOTHING). This proves a
   prod rebuild won't reset an operator's deliberate flip (Phase 124).

After the module, leave DB at HEAD 040_visits (cleanup via finalizer + the
shared `_restore_head_040_after_migration_test` autouse fixture in conftest).
"""
from __future__ import annotations

import pytest

from .conftest import (
    alembic_cmd,
    current_alembic_head,
    ensure_head_040,
    psql,
)


_KEY = "bot_default_mode"


def _mode_value() -> str:
    """Return value of the bot_default_mode row, or '' if absent."""
    res = psql(f"SELECT value FROM bot_settings WHERE key = '{_KEY}';")
    return (res.stdout or "").strip()


def _mode_count() -> int:
    res = psql(f"SELECT COUNT(*) FROM bot_settings WHERE key = '{_KEY}';")
    return int((res.stdout or "0").strip() or "0")


def _delete_mode_row() -> None:
    """Idempotent cleanup of the seeded row (if it exists)."""
    psql(f"DELETE FROM bot_settings WHERE key = '{_KEY}';")


@pytest.fixture(scope="module", autouse=True)
def _module_cleanup():
    """Leave the DB at HEAD 040_visits so sibling test modules rely on the
    post-040 schema state. Wipe the seeded row before and after the module.
    """
    ensure_head_040()
    _delete_mode_row()
    yield
    _delete_mode_row()
    head = current_alembic_head()
    if head != "040_visits":
        # Target 040 explicitly (NOT head): this module's contract is 040<->041.
        res = alembic_cmd("upgrade", "040_visits")
        if res.returncode != 0:
            raise RuntimeError(
                f"Could not restore HEAD=040 after module: "
                f"stdout={res.stdout}\nstderr={res.stderr}"
            )
    _delete_mode_row()


class TestUpgradeDowngradeRoundtrip041:

    def test_upgrade_seeds_busqueda(self):
        # Start at 040 (no seed row), then upgrade to 041.
        ensure_head_040()
        _delete_mode_row()
        assert current_alembic_head() == "040_visits"

        res_up = alembic_cmd("upgrade", "041_seed_bot_default_mode")
        assert res_up.returncode == 0, (
            f"upgrade to 041 failed:\nstdout={res_up.stdout}\nstderr={res_up.stderr}"
        )
        assert current_alembic_head() == "041_seed_bot_default_mode", (
            f"After upgrade expected HEAD=041, got {current_alembic_head()}"
        )
        assert _mode_value() == "busqueda", (
            f"bot_default_mode must be 'busqueda' after 041 upgrade, "
            f"got {_mode_value()!r}"
        )

    def test_downgrade_removes_row_lands_on_040(self):
        # Ensure we are at 041 with the seed present.
        ensure_head_040()
        _delete_mode_row()
        res_up = alembic_cmd("upgrade", "041_seed_bot_default_mode")
        assert res_up.returncode == 0
        assert _mode_value() == "busqueda"

        # Downgrade 041 -> 040.
        res_down = alembic_cmd("downgrade", "040_visits")
        assert res_down.returncode == 0, (
            f"downgrade 041->040 failed:\n"
            f"stdout={res_down.stdout}\nstderr={res_down.stderr}"
        )
        assert current_alembic_head() == "040_visits", (
            f"After downgrade expected HEAD=040, got {current_alembic_head()}"
        )
        assert _mode_count() == 0, (
            "bot_default_mode row must be GONE after 041 downgrade, "
            f"count={_mode_count()}"
        )

    def test_reupgrade_restores_busqueda(self):
        # From 040 (no seed), re-upgrade and confirm the row returns.
        ensure_head_040()
        _delete_mode_row()
        assert _mode_count() == 0

        res_up = alembic_cmd("upgrade", "041_seed_bot_default_mode")
        assert res_up.returncode == 0, (
            f"re-upgrade to 041 failed:\n"
            f"stdout={res_up.stdout}\nstderr={res_up.stderr}"
        )
        assert current_alembic_head() == "041_seed_bot_default_mode"
        assert _mode_value() == "busqueda", (
            f"bot_default_mode must be 'busqueda' after re-upgrade, "
            f"got {_mode_value()!r}"
        )


class TestUpgradeIsIdempotentOverFlippedValue:

    def test_reupgrade_does_not_overwrite_operator_flip(self):
        """ON CONFLICT (key) DO NOTHING — re-running upgrade over a flipped
        'recepcionista' value must NOT reset it to 'busqueda'.

        This is the load-bearing assertion: a prod rebuild (which re-runs
        `alembic upgrade head`) must never clobber a deliberate Phase-124 flip.
        """
        # Arrive at 041 with the seed present.
        ensure_head_040()
        _delete_mode_row()
        res_up = alembic_cmd("upgrade", "041_seed_bot_default_mode")
        assert res_up.returncode == 0
        assert _mode_value() == "busqueda"

        # Operator flips the mode from the panel.
        flip = psql(
            f"UPDATE bot_settings SET value = 'recepcionista' WHERE key = '{_KEY}';"
        )
        assert flip.returncode == 0, (
            f"could not flip bot_default_mode:\n"
            f"stdout={flip.stdout}\nstderr={flip.stderr}"
        )
        assert _mode_value() == "recepcionista"

        # Re-run the 041 upgrade (simulates a prod rebuild re-applying head).
        # Stamp down then up so alembic actually re-executes upgrade() — the
        # INSERT ... ON CONFLICT DO NOTHING must leave the flipped value intact.
        stamp_down = alembic_cmd("stamp", "040_visits")
        assert stamp_down.returncode == 0, (
            f"stamp 040 failed:\nstdout={stamp_down.stdout}\nstderr={stamp_down.stderr}"
        )
        res_reupgrade = alembic_cmd("upgrade", "041_seed_bot_default_mode")
        assert res_reupgrade.returncode == 0, (
            f"re-running 041 upgrade failed:\n"
            f"stdout={res_reupgrade.stdout}\nstderr={res_reupgrade.stderr}"
        )
        assert current_alembic_head() == "041_seed_bot_default_mode"

        # The critical assertion: ON CONFLICT DO NOTHING preserved the flip.
        assert _mode_value() == "recepcionista", (
            "041 upgrade must NOT overwrite a flipped 'recepcionista' value "
            f"(ON CONFLICT DO NOTHING); got {_mode_value()!r}"
        )
