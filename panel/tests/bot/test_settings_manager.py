"""Tests for SettingsManager — bot_settings integration for scheduler."""
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import pytest_asyncio

from app.bot.scheduler.settings_manager import SettingsManager


@pytest.fixture
def manager() -> SettingsManager:
    """Fresh SettingsManager instance."""
    return SettingsManager()


def _mock_session_factory(get_value_return: str | None = None):
    """Create a mock async_session_factory context manager.

    Returns a mock session whose BotSettingRepository.get_value returns
    the given value.
    """
    mock_session = AsyncMock()

    async def _get_value(session, key):
        return get_value_return

    return mock_session, _get_value


class TestIsTaskEnabled:
    """is_task_enabled reads scheduler_{task_id}_enabled."""

    @pytest.mark.asyncio
    async def test_enabled_true(self, manager: SettingsManager) -> None:
        """Returns True when setting value is 'true'."""
        with patch(
            "app.bot.scheduler.settings_manager.async_session_factory"
        ) as mock_factory, patch(
            "app.bot.scheduler.settings_manager.BotSettingRepository"
        ) as mock_repo:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_repo.get_value = AsyncMock(return_value="true")

            result = await manager.is_task_enabled("cold_lead_check")
            assert result is True
            mock_repo.get_value.assert_called_once_with(
                mock_session, "scheduler_cold_lead_check_enabled"
            )

    @pytest.mark.asyncio
    async def test_enabled_one(self, manager: SettingsManager) -> None:
        """Returns True when setting value is '1'."""
        with patch(
            "app.bot.scheduler.settings_manager.async_session_factory"
        ) as mock_factory, patch(
            "app.bot.scheduler.settings_manager.BotSettingRepository"
        ) as mock_repo:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_repo.get_value = AsyncMock(return_value="1")

            result = await manager.is_task_enabled("heartbeat")
            assert result is True

    @pytest.mark.asyncio
    async def test_enabled_false(self, manager: SettingsManager) -> None:
        """Returns False when setting value is 'false'."""
        with patch(
            "app.bot.scheduler.settings_manager.async_session_factory"
        ) as mock_factory, patch(
            "app.bot.scheduler.settings_manager.BotSettingRepository"
        ) as mock_repo:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_repo.get_value = AsyncMock(return_value="false")

            result = await manager.is_task_enabled("cold_lead_check")
            assert result is False

    @pytest.mark.asyncio
    async def test_enabled_not_found_defaults_true(
        self, manager: SettingsManager
    ) -> None:
        """Returns True (fail-open) when setting key does not exist."""
        with patch(
            "app.bot.scheduler.settings_manager.async_session_factory"
        ) as mock_factory, patch(
            "app.bot.scheduler.settings_manager.BotSettingRepository"
        ) as mock_repo:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_repo.get_value = AsyncMock(return_value=None)

            result = await manager.is_task_enabled("nonexistent")
            assert result is True

    @pytest.mark.asyncio
    async def test_enabled_db_error_defaults_true(
        self, manager: SettingsManager
    ) -> None:
        """Returns True (fail-open) when DB raises an exception."""
        with patch(
            "app.bot.scheduler.settings_manager.async_session_factory"
        ) as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(
                side_effect=ConnectionError("DB down")
            )
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await manager.is_task_enabled("heartbeat")
            assert result is True


class TestGetTaskConfig:
    """get_task_config reads scheduler_{task_id}_{setting}."""

    @pytest.mark.asyncio
    async def test_returns_value(self, manager: SettingsManager) -> None:
        """Returns the stored value when the key exists."""
        with patch(
            "app.bot.scheduler.settings_manager.async_session_factory"
        ) as mock_factory, patch(
            "app.bot.scheduler.settings_manager.BotSettingRepository"
        ) as mock_repo:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_repo.get_value = AsyncMock(return_value="0 3 * * *")

            result = await manager.get_task_config("backup", "cron_expr", "0 4 * * *")
            assert result == "0 3 * * *"
            mock_repo.get_value.assert_called_once_with(
                mock_session, "scheduler_backup_cron_expr"
            )

    @pytest.mark.asyncio
    async def test_not_found_returns_default(
        self, manager: SettingsManager
    ) -> None:
        """Returns default when the key does not exist."""
        with patch(
            "app.bot.scheduler.settings_manager.async_session_factory"
        ) as mock_factory, patch(
            "app.bot.scheduler.settings_manager.BotSettingRepository"
        ) as mock_repo:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_repo.get_value = AsyncMock(return_value=None)

            result = await manager.get_task_config("backup", "cron_expr", "0 4 * * *")
            assert result == "0 4 * * *"

    @pytest.mark.asyncio
    async def test_db_error_returns_default(
        self, manager: SettingsManager
    ) -> None:
        """Returns default (fail-open) when DB raises an exception."""
        with patch(
            "app.bot.scheduler.settings_manager.async_session_factory"
        ) as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(
                side_effect=ConnectionError("DB down")
            )
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await manager.get_task_config(
                "backup", "cron_expr", "0 4 * * *"
            )
            assert result == "0 4 * * *"
