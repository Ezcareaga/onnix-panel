"""Tests for heartbeat health check scheduled task.

Plan 67-03: SCHED-TASK-02 — HeartbeatChecker unit tests.
Updated in 71-03: Task 9 — adapted notification tests for AdminNotifier.
All tests use mocked sessions; no real DB required.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.scheduler.tasks.heartbeat import (
    HeartbeatChecker,
    run_heartbeat,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session_factory(*, healthy: bool = True):
    """Build a mock async session factory.

    When *healthy* is True, execute(SELECT 1) succeeds.
    When False, execute raises an exception simulating DB failure.
    """
    mock_session = AsyncMock()
    if healthy:
        mock_session.execute = AsyncMock(return_value=MagicMock())
    else:
        mock_session.execute = AsyncMock(
            side_effect=Exception("connection refused"),
        )

    mock_factory = MagicMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory.return_value = mock_ctx

    return mock_factory


def _make_mock_notifier():
    """Build a mock AdminNotifier for testing."""
    notifier = AsyncMock()
    notifier.notify_heartbeat_failure = AsyncMock(return_value=True)
    return notifier


# ---------------------------------------------------------------------------
# Tests: DB healthy
# ---------------------------------------------------------------------------

class TestDBHealthy:
    """When the DB responds to SELECT 1."""

    @pytest.mark.asyncio
    async def test_db_healthy_returns_true(self):
        """Result has db_healthy=True when SELECT 1 succeeds."""
        factory = _make_session_factory(healthy=True)
        checker = HeartbeatChecker(
            notification_chat_id="123",
            telegram_bot_token="tok",
            session_factory=factory,
        )
        result = await checker.run()
        assert result["db_healthy"] is True
        assert "timestamp" in result

    @pytest.mark.asyncio
    async def test_no_notification_on_success(self):
        """No notification when DB is healthy."""
        factory = _make_session_factory(healthy=True)
        mock_notifier = _make_mock_notifier()

        checker = HeartbeatChecker(
            notification_chat_id="123",
            telegram_bot_token="tok",
            session_factory=factory,
            notifier=mock_notifier,
        )
        await checker.run()
        mock_notifier.notify_heartbeat_failure.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests: DB unhealthy
# ---------------------------------------------------------------------------

class TestDBUnhealthy:
    """When the DB is unreachable."""

    @pytest.mark.asyncio
    async def test_db_unhealthy_returns_false(self):
        """Result has db_healthy=False when SELECT 1 raises."""
        factory = _make_session_factory(healthy=False)
        checker = HeartbeatChecker(
            notification_chat_id="",
            telegram_bot_token="",
            session_factory=factory,
        )
        result = await checker.run()
        assert result["db_healthy"] is False
        assert "timestamp" in result

    @pytest.mark.asyncio
    async def test_notification_sent_on_failure(self):
        """AdminNotifier.notify_heartbeat_failure is called when DB check fails."""
        factory = _make_session_factory(healthy=False)
        mock_notifier = _make_mock_notifier()

        checker = HeartbeatChecker(
            notification_chat_id="999",
            telegram_bot_token="bot_token_123",
            session_factory=factory,
            notifier=mock_notifier,
        )
        await checker.run()

        mock_notifier.notify_heartbeat_failure.assert_awaited_once()
        call_args = mock_notifier.notify_heartbeat_failure.call_args
        # Timestamp is passed as the first positional argument
        assert isinstance(call_args[0][0], str)


# ---------------------------------------------------------------------------
# Tests: notification failure is non-fatal
# ---------------------------------------------------------------------------

class TestNotificationFailureNonFatal:
    """Notification failure must not break the heartbeat task."""

    @pytest.mark.asyncio
    async def test_notification_returns_false_non_fatal(self):
        """Task completes even when notification returns False."""
        factory = _make_session_factory(healthy=False)
        mock_notifier = _make_mock_notifier()
        mock_notifier.notify_heartbeat_failure = AsyncMock(return_value=False)

        checker = HeartbeatChecker(
            notification_chat_id="999",
            telegram_bot_token="tok",
            session_factory=factory,
            notifier=mock_notifier,
        )
        result = await checker.run()
        # Task still returns valid result despite notification failure
        assert result["db_healthy"] is False
        assert "timestamp" in result


# ---------------------------------------------------------------------------
# Tests: factory function
# ---------------------------------------------------------------------------

class TestFactoryFunction:
    """Module-level run_heartbeat() factory."""

    @pytest.mark.asyncio
    async def test_factory_creates_checker_and_runs(self):
        """run_heartbeat() reads bot_settings and calls run()."""
        mock_result = {"db_healthy": True, "timestamp": "2026-03-27T12:00:00+00:00"}

        with patch(
            "app.bot.scheduler.tasks.heartbeat.HeartbeatChecker"
        ) as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.run = AsyncMock(return_value=mock_result)
            mock_cls.return_value = mock_instance

            result = await run_heartbeat()

            assert result == mock_result
            mock_cls.assert_called_once()
            mock_instance.run.assert_awaited_once()
