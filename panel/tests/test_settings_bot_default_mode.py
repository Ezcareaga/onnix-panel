"""Tests for the bot_default_mode global setting (M6.3 / Phase 123-01).

Covers:
  * Migration 041 seed semantics (BOT-01): the seeded default is 'busqueda'
    and the seed is idempotent — re-running ON CONFLICT DO NOTHING must NEVER
    clobber a value an admin already set.
  * SettingsService.set_bot_default_mode (BOT-01 application-layer CHECK):
    rejects any value outside {recepcionista, busqueda}, upserts valid values,
    and surfaces the current value via get_all_settings.

All tests run against onnix_dev (conftest) and never touch production.
bot_settings is NEVER truncated by conftest cleanup, so the seeded row persists
across the suite; these tests restore the seed value to 'busqueda' when done so
they remain order-independent.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.repositories.bot_setting_repo import bot_setting_repo
from app.services.settings_service import settings_service

pytestmark = pytest.mark.asyncio

_KEY = "bot_default_mode"

_SEED_SQL = (
    "INSERT INTO bot_settings (key, value, description, updated_at) "
    "VALUES (:key, 'busqueda', "
    "'Modo global del bot: recepcionista | busqueda', NOW()) "
    "ON CONFLICT (key) DO NOTHING"
)


async def _ensure_seed(db) -> None:
    """Apply migration 041's idempotent seed (safe to call repeatedly)."""
    await db.execute(text(_SEED_SQL), {"key": _KEY})
    await db.commit()


async def _reset_to_busqueda(db) -> None:
    await bot_setting_repo.upsert(db, _KEY, "busqueda", user_id=1)
    await db.commit()


class TestMigration041Seed:
    async def test_bot_default_mode_busqueda_default(self, db):
        """BOT-01 (§604): the seeded default is 'busqueda' — deploying the
        recepcionista must NOT auto-switch live conversations."""
        # Start from a clean slate so we observe the SEED value, not a prior
        # test's mutation.
        await db.execute(text("DELETE FROM bot_settings WHERE key = :k"), {"k": _KEY})
        await db.commit()

        await _ensure_seed(db)

        value = await bot_setting_repo.get_value(db, _KEY)
        assert value == "busqueda"

    async def test_seed_is_idempotent_never_clobbers(self, db):
        """BOT-01: re-running the seed (ON CONFLICT DO NOTHING) must NOT reset
        an admin-set value back to 'busqueda' — critical if Phase 124 has
        already flipped prod to 'recepcionista'."""
        await _ensure_seed(db)
        # Admin flips it.
        await bot_setting_repo.upsert(db, _KEY, "recepcionista", user_id=1)
        await db.commit()

        # Re-run the migration seed — must be a no-op.
        await _ensure_seed(db)

        value = await bot_setting_repo.get_value(db, _KEY)
        assert value == "recepcionista"

        await _reset_to_busqueda(db)


class TestSetBotDefaultMode:
    async def test_set_bot_default_mode_validates(self, db):
        """BOT-01 application-layer CHECK: any value not in
        {recepcionista, busqueda} raises ValueError."""
        with pytest.raises(ValueError):
            await settings_service.set_bot_default_mode(db, "garbage", user_id=1)

    async def test_set_bot_default_mode_persists(self, db):
        """Valid values upsert and are returned."""
        await _ensure_seed(db)

        for mode in ("recepcionista", "busqueda"):
            returned = await settings_service.set_bot_default_mode(db, mode, user_id=1)
            await db.commit()
            assert returned == mode
            assert await bot_setting_repo.get_value(db, _KEY) == mode

        await _reset_to_busqueda(db)

    async def test_get_all_settings_exposes_bot_default_mode(self, db):
        """get_all_settings surfaces the current value so the template can
        render the active toggle option."""
        await _ensure_seed(db)
        await settings_service.set_bot_default_mode(db, "recepcionista", user_id=1)
        await db.commit()

        data = await settings_service.get_all_settings(db)
        assert data["bot_default_mode"] == "recepcionista"

        await _reset_to_busqueda(db)
