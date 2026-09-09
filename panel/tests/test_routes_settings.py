"""
Tests for app/routes/settings.py

Covers: acceso admin-only, que pestañas ve cada rol, y que la pantalla
no vuelque `bot_settings` al HTML.
"""
import pytest
from sqlalchemy import text
from app.repositories.bot_setting_repo import bot_setting_repo


class TestGetSettings:
    async def test_unauthenticated_redirects(self, client):
        resp = await client.get("/settings")
        assert resp.status_code == 303

    async def test_admin_gets_200(self, admin_client):
        resp = await admin_client.get("/settings")
        assert resp.status_code == 200

    async def test_user_role_can_access_for_mi_cuenta(self, user_client):
        # Post UX refactor (commit 05b08c8): /settings GET is accessible to all
        # auth users; template gates admin-only tabs internally. Non-admins
        # only see the Mi Cuenta tab — the page itself loads 200.
        resp = await user_client.get("/settings")
        assert resp.status_code == 200

    async def test_user_role_does_NOT_see_admin_tabs(self, user_client):
        # Verify the role-gating IN the template — non-admin must NOT see
        # the admin-only tabs (Configuración del Bot, Accesos, Usuarios).
        resp = await user_client.get("/settings")
        assert resp.status_code == 200
        # Mi Cuenta IS visible
        assert b"Mi Cuenta" in resp.content
        # The admin tab labels are NOT in the tab bar
        assert b"Configuraci\xc3\xb3n del Bot" not in resp.content
        assert b"Auditor\xc3\xada de Login" not in resp.content

    async def test_la_pagina_carga(self, admin_client):
        """Antes buscaba el switch del bot. El bot se fue; la página no."""
        resp = await admin_client.get("/settings")
        assert resp.status_code == 200


class TestNoHayVolcadoDeBotSettings:
    """SEC-01, despues de que se fuera la pestaña del bot.

    Antes la pantalla volcaba `bot_settings` entera en una tabla y el test
    verificaba que dos claves credenciales quedaran afuera de ese volcado. La
    tabla se fue con el bot, asi que el test de la lista negra pasaria por
    ausencia del sujeto —verde decorativo—. Lo que se assertea ahora es lo que
    de verdad manda: **ninguna** fila de `bot_settings` llega al HTML, ni las
    sensibles ni las operativas. Si alguien vuelve a poner un volcado, aunque
    sea filtrado, esto se cae.
    """

    _MARKER = "pytest-bot-settings-marker-xk9"
    # Una sensible y una operativa: la regla ya no distingue.
    _KEYS = ("infocasas_phpsessid", "bot_off_message")

    async def test_ninguna_fila_de_bot_settings_llega_al_html(self, admin_client, db):
        originals: dict[str, str | None] = {}
        for key in self._KEYS:
            originals[key] = await bot_setting_repo.get_value(db, key)
            await bot_setting_repo.upsert(db, key, self._MARKER)
        await db.commit()
        try:
            resp = await admin_client.get("/settings")
            assert resp.status_code == 200
            html = resp.content.decode()
            for key in self._KEYS:
                assert key not in html, f"la clave {key} volvio al HTML"
            assert self._MARKER not in html, "un value de bot_settings volvio al HTML"
        finally:
            for key, original in originals.items():
                if original is not None:
                    await bot_setting_repo.upsert(db, key, original)
                else:
                    await db.execute(
                        text("DELETE FROM bot_settings WHERE key = :key"),
                        {"key": key},
                    )
            await db.commit()
