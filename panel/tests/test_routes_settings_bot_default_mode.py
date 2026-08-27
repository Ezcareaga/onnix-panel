"""Tests for POST /settings/bot-default-mode (M6.3 / Phase 123-01, BOT-02).

Admin-only toggle for the global bot_default_mode setting. Mirrors the M6.1
dedicated-toggle route tests. Runs against onnix_dev (conftest).
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.repositories.bot_setting_repo import bot_setting_repo

pytestmark = pytest.mark.asyncio

_KEY = "bot_default_mode"


async def _reset(db) -> None:
    await bot_setting_repo.upsert(db, _KEY, "busqueda", user_id=1)
    await db.commit()


class TestBotDefaultModeRoute:
    async def test_unauthenticated_redirects(self, client):
        resp = await client.post("/settings/bot-default-mode", data={"mode": "recepcionista"})
        assert resp.status_code == 303

    async def test_non_admin_forbidden(self, user_client):
        resp = await user_client.post(
            "/settings/bot-default-mode", data={"mode": "recepcionista"}
        )
        assert resp.status_code in (403, 303)

    async def test_admin_flips_value(self, admin_client, db):
        resp = await admin_client.post(
            "/settings/bot-default-mode", data={"mode": "recepcionista"}
        )
        assert resp.status_code == 200
        # DB reflects the flip
        value = await bot_setting_repo.get_value(db, _KEY)
        assert value == "recepcionista"
        # Partial rendered, reflects new active value
        assert b"recepcionista" in resp.content.lower()

        await _reset(db)

    async def test_admin_invalid_mode_rejected(self, admin_client, db):
        resp = await admin_client.post(
            "/settings/bot-default-mode", data={"mode": "garbage"}
        )
        # Service raises ValueError -> route returns 422 (no DB change)
        assert resp.status_code == 422
        value = await bot_setting_repo.get_value(db, _KEY)
        assert value in ("busqueda", "recepcionista")  # never 'garbage'

        await _reset(db)

    async def test_settings_page_includes_mode_toggle(self, admin_client):
        resp = await admin_client.get("/settings")
        assert resp.status_code == 200
        assert b"bot-default-mode" in resp.content
        assert b"Modo del bot" in resp.content
