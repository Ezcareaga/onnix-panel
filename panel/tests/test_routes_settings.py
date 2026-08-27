"""
Tests for app/routes/settings.py

Covers: admin-only access, bot toggle changes DB, update setting, allowlist.
"""
import pytest
from sqlalchemy import text
from app.repositories.bot_setting_repo import bot_setting_repo
from app.services.settings_service import ALLOWED_SETTINGS


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

    async def test_contains_bot_toggle(self, admin_client):
        resp = await admin_client.get("/settings")
        assert b"bot" in resp.content.lower()


class TestSensitiveKeysHidden:
    """SEC-01: claves credenciales (phpsessid/JWT) nunca viajan al HTML."""

    _MARKERS = {
        "infocasas_phpsessid": "pytest-sensitive-sessid-marker-xk9",
        "infocasas_frontend_token": "pytest-sensitive-jwt-marker-xk9",
    }

    async def test_settings_hides_sensitive_keys(self, admin_client, db):
        """GET /settings: ni las keys sensibles ni sus VALUES aparecen en el HTML."""
        originals: dict[str, str | None] = {}
        for key, marker in self._MARKERS.items():
            originals[key] = await bot_setting_repo.get_value(db, key)
            await bot_setting_repo.upsert(db, key, marker)
        await db.commit()
        try:
            resp = await admin_client.get("/settings")
            assert resp.status_code == 200
            html = resp.content.decode()
            for key, marker in self._MARKERS.items():
                assert key not in html, f"key sensible expuesta en HTML: {key}"
                assert marker not in html, f"value sensible expuesto en HTML: {key}"
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

    async def test_settings_normal_keys_still_visible(self, admin_client):
        """Las keys operativas (bot_off_message, wa_tpl_*) siguen visibles."""
        resp = await admin_client.get("/settings")
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "bot_off_message" in html
        # wa_tpl_* (SIDs de Twilio) intencionalmente visibles — no matchean el patron
        assert "wa_tpl_ic_welcome" in html
        assert "working_hours_start" in html


class TestBotToggle:
    async def test_user_role_gets_403(self, user_client):
        resp = await user_client.post("/settings/bot-toggle")
        assert resp.status_code == 403

    async def test_admin_gets_200(self, admin_client, db):
        before_raw = await bot_setting_repo.get_value(db, "bot_enabled")
        try:
            resp = await admin_client.post("/settings/bot-toggle")
            assert resp.status_code == 200
        finally:
            await bot_setting_repo.update_value(db, "bot_enabled", before_raw, None)

    async def test_actually_toggles_in_db(self, admin_client, db):
        before_raw = await bot_setting_repo.get_value(db, "bot_enabled")
        try:
            await admin_client.post("/settings/bot-toggle")
            after_raw = await bot_setting_repo.get_value(db, "bot_enabled")
            after = after_raw == "true"
            assert after != (before_raw == "true")
        finally:
            await bot_setting_repo.update_value(db, "bot_enabled", before_raw, None)


class TestUpdateSetting:
    """Tests for /settings/update endpoint and ALLOWED_SETTINGS allowlist."""

    async def test_allowed_key_succeeds(self, admin_client, db):
        """Updating an allowed key (bot_off_message) works and restores original."""
        original = await bot_setting_repo.get_value(db, "bot_off_message")
        try:
            resp = await admin_client.post("/settings/update", data={
                "key": "bot_off_message",
                "value": "pytest-temp-value",
            })
            assert resp.status_code == 200
            updated = await bot_setting_repo.get_value(db, "bot_off_message")
            assert updated == "pytest-temp-value"
        finally:
            await bot_setting_repo.update_value(db, "bot_off_message", original, None)

    async def test_disallowed_key_returns_422(self, admin_client):
        """Trying to update bot_enabled via /settings/update returns 422."""
        resp = await admin_client.post("/settings/update", data={
            "key": "bot_enabled",
            "value": "false",
        })
        assert resp.status_code == 422

    async def test_unknown_key_returns_422(self, admin_client):
        """Unknown key returns 422."""
        resp = await admin_client.post("/settings/update", data={
            "key": "nonexistent_evil_key",
            "value": "hacked",
        })
        assert resp.status_code == 422

    def test_allowlist_excludes_sensitive_keys(self):
        """bot_enabled and whatsapp_mode must NOT be in ALLOWED_SETTINGS."""
        assert "bot_enabled" not in ALLOWED_SETTINGS
        assert "whatsapp_mode" not in ALLOWED_SETTINGS

    def test_allowlist_has_expected_size(self):
        """ALLOWED_SETTINGS contains exactly 10 editable keys."""
        assert len(ALLOWED_SETTINGS) == 10
