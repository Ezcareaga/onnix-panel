"""
Tests for app/services/settings_service.py

Covers: get_all_settings, toggle_bot, update_setting.
"""
import pytest
from app.services.settings_service import settings_service
from app.repositories.bot_setting_repo import bot_setting_repo


class TestGetAllSettings:
    async def test_returns_settings_list(self, db):
        data = await settings_service.get_all_settings(db)
        assert "settings" in data
        assert isinstance(data["settings"], list)

    async def test_returns_bot_enabled_bool(self, db):
        data = await settings_service.get_all_settings(db)
        assert "bot_enabled" in data
        assert isinstance(data["bot_enabled"], bool)

    async def test_has_at_least_10_settings(self, db):
        data = await settings_service.get_all_settings(db)
        assert len(data["settings"]) >= 10

    async def test_bot_enabled_key_present(self, db):
        data = await settings_service.get_all_settings(db)
        keys = [s.key for s in data["settings"]]
        assert "bot_enabled" in keys


class TestSensitiveKeyFiltering:
    """SEC-01: get_all_settings filtra keys credenciales (phpsessid/JWT/tokens)."""

    async def test_filters_sensitive_keys(self, db):
        """Keys que matchean el patron sensible NO se devuelven en settings."""
        # Garantizar que las keys sensibles existen en DB (dev snapshot las tiene,
        # pero el test no debe depender de eso).
        originals = {}
        for key in ("infocasas_phpsessid", "infocasas_frontend_token"):
            originals[key] = await bot_setting_repo.get_value(db, key)
            await bot_setting_repo.upsert(db, key, "pytest-temp-sensitive")
        await db.commit()
        try:
            data = await settings_service.get_all_settings(db)
            keys = [s.key for s in data["settings"]]
            assert "infocasas_phpsessid" not in keys
            assert "infocasas_frontend_token" not in keys
        finally:
            for key, original in originals.items():
                if original is not None:
                    await bot_setting_repo.upsert(db, key, original)
            await db.commit()

    async def test_non_sensitive_keys_pass_through(self, db):
        """Las demas keys (operativas + SIDs Twilio wa_tpl_*) siguen pasando."""
        data = await settings_service.get_all_settings(db)
        keys = [s.key for s in data["settings"]]
        assert "bot_off_message" in keys
        assert "bot_enabled" in keys
        assert any(k.startswith("wa_tpl_") for k in keys)

    def test_sensitive_pattern(self):
        """El regex matchea credenciales y NO matchea keys operativas."""
        from app.services.settings_service import _SENSITIVE_KEY_RE

        for key in (
            "infocasas_phpsessid",
            "infocasas_frontend_token",
            "some_api_key",
            "my_secret",
            "JWT_REFRESH",
            "admin_password",
        ):
            assert _SENSITIVE_KEY_RE.search(key), f"deberia matchear: {key}"
        for key in ("bot_off_message", "wa_tpl_ic_welcome_v3", "whatsapp_mode",
                    "working_hours_start"):
            assert not _SENSITIVE_KEY_RE.search(key), f"NO deberia matchear: {key}"


class TestToggleBot:
    async def test_toggles_from_true_to_false(self, db):
        # Ensure bot is ON first
        await bot_setting_repo.update_value(db, "bot_enabled", "true", 1)
        await db.commit()

        result = await settings_service.toggle_bot(db, user_id=1)
        assert result is False

        # Restore
        await bot_setting_repo.update_value(db, "bot_enabled", "true", 1)
        await db.commit()

    async def test_toggles_from_false_to_true(self, db):
        await bot_setting_repo.update_value(db, "bot_enabled", "false", 1)
        await db.commit()

        result = await settings_service.toggle_bot(db, user_id=1)
        assert result is True

        # Restore
        await bot_setting_repo.update_value(db, "bot_enabled", "true", 1)
        await db.commit()

    async def test_returns_bool(self, db):
        result = await settings_service.toggle_bot(db, user_id=1)
        assert isinstance(result, bool)
        # Restore original state
        await settings_service.toggle_bot(db, user_id=1)


class TestUpdateSetting:
    async def test_updates_value_in_db(self, db):
        await settings_service.update_setting(
            db, "infocasas_poll_interval_min", "10", user_id=1,
        )
        value = await bot_setting_repo.get_value(db, "infocasas_poll_interval_min")
        assert value == "10"

        # Restore original
        await settings_service.update_setting(
            db, "infocasas_poll_interval_min", "5", user_id=1,
        )
