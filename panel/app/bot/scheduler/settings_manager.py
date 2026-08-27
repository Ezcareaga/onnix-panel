"""SettingsManager — reads scheduler task config from bot_settings table.

Each task has two kinds of settings in bot_settings:
  - scheduler_{task_id}_enabled  (true/false/1/0)
  - scheduler_{task_id}_{setting} (arbitrary config value)

All reads use short-lived sessions and fail-open: if the DB is unreachable
or the key does not exist, the task is assumed enabled and defaults are used.
"""
from __future__ import annotations

import logging

from app.database import async_session_factory
from app.repositories.bot_setting_repo import BotSettingRepository

logger = logging.getLogger(__name__)


class SettingsManager:
    """Read scheduler-related settings from bot_settings table."""

    _ENABLED_TRUTHY = {"true", "1", "yes"}

    async def is_task_enabled(self, task_id: str) -> bool:
        """Check if a scheduled task is enabled.

        Reads ``scheduler_{task_id}_enabled`` from bot_settings.
        Fail-open: returns True if the key is missing or on DB error.
        """
        key = f"scheduler_{task_id}_enabled"
        try:
            async with async_session_factory() as session:
                value = await BotSettingRepository.get_value(session, key)
            if value is None:
                logger.debug("Setting %s not found — defaulting to enabled", key)
                return True
            return value.strip().lower() in self._ENABLED_TRUTHY
        except Exception:
            logger.exception("DB error reading %s — defaulting to enabled", key)
            return True

    async def get_task_config(
        self,
        task_id: str,
        setting: str,
        default: str | None = None,
    ) -> str | None:
        """Read an arbitrary config value for a scheduled task.

        Reads ``scheduler_{task_id}_{setting}`` from bot_settings.
        Returns *default* if the key is missing or on DB error.
        """
        key = f"scheduler_{task_id}_{setting}"
        try:
            async with async_session_factory() as session:
                value = await BotSettingRepository.get_value(session, key)
            if value is None:
                logger.debug("Setting %s not found — using default %r", key, default)
                return default
            return value
        except Exception:
            logger.exception("DB error reading %s — using default %r", key, default)
            return default
