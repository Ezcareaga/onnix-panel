"""Tests for BotErrorService.

Plan 71-03: Task 7 — unit tests for error recording, counting, and
auto-disable logic.  All tests use mocked sessions; no real DB required.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.services.error_service import BotErrorService, _MAX_ERROR_MESSAGE_LENGTH


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_session(*, commit_ok: bool = True):
    """Build a mock AsyncSession.

    If *commit_ok* is False, commit will raise.
    """
    session = AsyncMock()
    session.add = MagicMock()
    if not commit_ok:
        session.commit = AsyncMock(side_effect=Exception("db write failed"))
    else:
        session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.execute = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# Tests: record_error inserts row
# ---------------------------------------------------------------------------

class TestRecordError:
    """record_error inserts a BotError into the session."""

    @pytest.mark.asyncio
    async def test_record_error_adds_and_commits(self):
        """A BotError is added to the session and committed."""
        session = _make_mock_session()
        svc = BotErrorService(workflow="telegram")

        await svc.record_error(
            session,
            "something went wrong",
            node="webhook_process",
            chat_id="12345",
        )

        session.add.assert_called_once()
        error_obj = session.add.call_args[0][0]
        assert error_obj.workflow == "telegram"
        assert error_obj.node == "webhook_process"
        assert error_obj.error_message == "something went wrong"
        assert error_obj.chat_id == "12345"
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_record_error_truncates_long_messages(self):
        """Error messages longer than _MAX_ERROR_MESSAGE_LENGTH are truncated."""
        session = _make_mock_session()
        svc = BotErrorService(workflow="whatsapp")

        long_msg = "x" * 5000
        await svc.record_error(session, long_msg)

        error_obj = session.add.call_args[0][0]
        assert len(error_obj.error_message) == _MAX_ERROR_MESSAGE_LENGTH

    @pytest.mark.asyncio
    async def test_record_error_none_message(self):
        """None error_message is stored as None."""
        session = _make_mock_session()
        svc = BotErrorService(workflow="telegram")

        await svc.record_error(session, None)

        error_obj = session.add.call_args[0][0]
        assert error_obj.error_message is None

    @pytest.mark.asyncio
    async def test_record_error_failure_is_non_fatal(self):
        """If the DB write fails, record_error does not raise."""
        session = _make_mock_session(commit_ok=False)
        svc = BotErrorService(workflow="telegram")

        # Should not raise
        await svc.record_error(session, "boom")

        session.rollback.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: count_recent
# ---------------------------------------------------------------------------

class TestCountRecent:
    """count_recent returns error count within the time window."""

    @pytest.mark.asyncio
    async def test_count_recent_returns_count(self):
        """Returns the scalar count from the query."""
        session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 5
        session.execute = AsyncMock(return_value=mock_result)

        svc = BotErrorService(workflow="telegram")
        count = await svc.count_recent(session, window_minutes=15)

        assert count == 5
        session.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_count_recent_zero(self):
        """Returns 0 when no recent errors."""
        session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 0
        session.execute = AsyncMock(return_value=mock_result)

        svc = BotErrorService(workflow="whatsapp")
        count = await svc.count_recent(session, window_minutes=30)

        assert count == 0
