"""Tests for BotSettingRepository."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.repositories.bot_setting_repo import BotSettingRepository


pytestmark = pytest.mark.asyncio


class TestBotSettingRepository:
    """Tests for BotSettingRepository using mocked AsyncSession."""

    async def test_upsert_executes_insert_statement(self):
        db = MagicMock()
        db.execute = AsyncMock(return_value=MagicMock())
        await BotSettingRepository.upsert(db, key="test_key", value="HXabc123")
        db.execute.assert_called_once()

    async def test_upsert_with_description(self):
        db = MagicMock()
        db.execute = AsyncMock(return_value=MagicMock())
        await BotSettingRepository.upsert(
            db, key="test_key", value="HXabc123", description="Test description"
        )
        db.execute.assert_called_once()

    async def test_upsert_with_user_id(self):
        db = MagicMock()
        db.execute = AsyncMock(return_value=MagicMock())
        await BotSettingRepository.upsert(
            db, key="test_key", value="HXabc123", user_id=1
        )
        db.execute.assert_called_once()

    async def test_upsert_raises_on_none_value(self):
        db = MagicMock()
        db.execute = AsyncMock(return_value=MagicMock())
        with pytest.raises(ValueError, match="value cannot be None"):
            await BotSettingRepository.upsert(db, key="test_key", value=None)
        db.execute.assert_not_called()

    async def test_get_value_returns_scalar(self):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = "HXabc123"
        db = MagicMock()
        db.execute = AsyncMock(return_value=mock_result)
        result = await BotSettingRepository.get_value(db, "test_key")
        assert result == "HXabc123"

    async def test_get_value_returns_none_when_not_found(self):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db = MagicMock()
        db.execute = AsyncMock(return_value=mock_result)
        result = await BotSettingRepository.get_value(db, "missing_key")
        assert result is None

    async def test_update_value_executes_update(self):
        db = MagicMock()
        db.execute = AsyncMock(return_value=MagicMock())
        await BotSettingRepository.update_value(db, key="test_key", value="new_val", user_id=1)
        db.execute.assert_called_once()
