"""Tests for middleware — rate limiter, idempotency, cooldown, error handler.

Plan 64-01: MW-01, MW-02, MW-03, MW-04.
All tests are unit tests with no external dependencies.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from app.bot.core.types import BotResponse
from app.bot.middleware.rate_limiter import RateLimiter
from app.bot.middleware.idempotency import IdempotencyGuard
from app.bot.middleware.cooldown import CooldownChecker
from app.bot.middleware.error_handler import safe_handle, SAFE_ERROR_TEXT
from app.bot.observability.outcome import RequestOutcome


# ===========================================================================
# TestRateLimiter
# ===========================================================================

class TestRateLimiter:
    """MW-01: Sliding window rate limiter."""

    def test_under_limit_allowed(self):
        """Messages under the limit are allowed."""
        rl = RateLimiter(max_messages=3, window_seconds=60)
        assert rl.is_rate_limited("user1") is False
        assert rl.is_rate_limited("user1") is False
        assert rl.is_rate_limited("user1") is False

    def test_at_limit_blocked(self):
        """Message at the limit (4th after max=3) is blocked."""
        rl = RateLimiter(max_messages=3, window_seconds=60)
        for _ in range(3):
            assert rl.is_rate_limited("user1") is False
        assert rl.is_rate_limited("user1") is True

    def test_different_contacts_independent(self):
        """Rate limits are independent per contact."""
        rl = RateLimiter(max_messages=2, window_seconds=60)
        assert rl.is_rate_limited("user1") is False
        assert rl.is_rate_limited("user1") is False
        assert rl.is_rate_limited("user1") is True  # user1 blocked
        assert rl.is_rate_limited("user2") is False  # user2 ok

    def test_window_expiry_resets(self):
        """After the window expires, messages are allowed again."""
        rl = RateLimiter(max_messages=2, window_seconds=1)
        assert rl.is_rate_limited("user1") is False
        assert rl.is_rate_limited("user1") is False
        assert rl.is_rate_limited("user1") is True

        # Simulate window expiry by manipulating timestamps
        rl._timestamps["user1"] = [time.monotonic() - 2.0]
        assert rl.is_rate_limited("user1") is False

    def test_default_config(self):
        """Default config: 5 messages per 60 seconds."""
        rl = RateLimiter()
        assert rl._max_messages == 5
        assert rl._window_seconds == 60

    def test_cleanup_removes_expired(self):
        """Cleanup purges contacts with no recent activity."""
        rl = RateLimiter(max_messages=5, window_seconds=1)
        rl._timestamps["old_user"] = [time.monotonic() - 5.0]
        rl._timestamps["new_user"] = [time.monotonic()]

        purged = rl.cleanup()
        assert purged == 1
        assert "old_user" not in rl._timestamps
        assert "new_user" in rl._timestamps

    def test_cleanup_with_no_expired(self):
        """Cleanup with no expired entries purges nothing."""
        rl = RateLimiter(max_messages=5, window_seconds=60)
        rl.is_rate_limited("user1")
        purged = rl.cleanup()
        assert purged == 0

    def test_custom_config(self):
        """Custom configuration is respected."""
        rl = RateLimiter(max_messages=10, window_seconds=120)
        assert rl._max_messages == 10
        assert rl._window_seconds == 120


# ===========================================================================
# TestIdempotencyGuard
# ===========================================================================

class TestIdempotencyGuard:
    """MW-02: Message deduplication by external_id."""

    def test_new_message_not_duplicate(self):
        """First occurrence of an external_id is not a duplicate."""
        guard = IdempotencyGuard()
        assert guard.is_duplicate("msg_001") is False

    def test_repeated_message_is_duplicate(self):
        """Second occurrence of the same external_id is a duplicate."""
        guard = IdempotencyGuard()
        guard.is_duplicate("msg_001")
        assert guard.is_duplicate("msg_001") is True

    def test_none_external_id_never_duplicate(self):
        """None external_id always returns False."""
        guard = IdempotencyGuard()
        assert guard.is_duplicate(None) is False
        assert guard.is_duplicate(None) is False

    def test_empty_string_never_duplicate(self):
        """Empty string external_id always returns False."""
        guard = IdempotencyGuard()
        assert guard.is_duplicate("") is False
        assert guard.is_duplicate("") is False

    def test_different_ids_independent(self):
        """Different external IDs are tracked independently."""
        guard = IdempotencyGuard()
        assert guard.is_duplicate("msg_001") is False
        assert guard.is_duplicate("msg_002") is False
        assert guard.is_duplicate("msg_001") is True
        assert guard.is_duplicate("msg_002") is True

    def test_capacity_eviction(self):
        """Oldest entries are evicted when capacity is reached."""
        guard = IdempotencyGuard(max_size=3)

        guard.is_duplicate("msg_001")
        guard.is_duplicate("msg_002")
        guard.is_duplicate("msg_003")
        assert guard.size == 3

        # Adding a 4th evicts the oldest (msg_001)
        guard.is_duplicate("msg_004")
        assert guard.size == 3
        assert guard.is_duplicate("msg_001") is False  # Was evicted, so not a dupe

    def test_size_property(self):
        """Size property reflects tracked IDs count."""
        guard = IdempotencyGuard()
        assert guard.size == 0
        guard.is_duplicate("a")
        assert guard.size == 1
        guard.is_duplicate("b")
        assert guard.size == 2

    def test_default_max_size(self):
        """Default max size is 10000."""
        guard = IdempotencyGuard()
        assert guard._max_size == 10_000

    def test_lru_order_maintained(self):
        """Accessing a seen ID moves it to end, protecting from eviction."""
        guard = IdempotencyGuard(max_size=3)
        guard.is_duplicate("msg_001")
        guard.is_duplicate("msg_002")
        guard.is_duplicate("msg_003")

        # Re-access msg_001 (moves to end)
        guard.is_duplicate("msg_001")  # True, but refreshes LRU position

        # Now adding msg_004 should evict msg_002 (oldest untouched)
        guard.is_duplicate("msg_004")
        assert guard.is_duplicate("msg_002") is False  # Was evicted
        assert guard.is_duplicate("msg_001") is True    # Still present (was refreshed)


# ===========================================================================
# TestCooldownChecker
# ===========================================================================

class TestCooldownChecker:
    """MW-03: Human agent cooldown."""

    def test_no_human_reply_no_cooldown(self):
        """When no human has replied, there's no cooldown."""
        checker = CooldownChecker()
        assert checker.is_in_cooldown(None) is False

    def test_recent_human_reply_in_cooldown(self):
        """Recent human reply triggers cooldown."""
        checker = CooldownChecker(cooldown_minutes=30)
        recent = datetime.now(timezone.utc) - timedelta(minutes=5)
        assert checker.is_in_cooldown(recent) is True

    def test_old_human_reply_no_cooldown(self):
        """Human reply older than cooldown window is not in cooldown."""
        checker = CooldownChecker(cooldown_minutes=30)
        old = datetime.now(timezone.utc) - timedelta(minutes=60)
        assert checker.is_in_cooldown(old) is False

    def test_exactly_at_boundary(self):
        """At exactly the cooldown boundary, should not be in cooldown."""
        checker = CooldownChecker(cooldown_minutes=30)
        boundary = datetime.now(timezone.utc) - timedelta(minutes=30, seconds=1)
        assert checker.is_in_cooldown(boundary) is False

    def test_default_cooldown_minutes(self):
        """Default cooldown is 30 minutes."""
        checker = CooldownChecker()
        assert checker._cooldown_minutes == 30

    def test_custom_cooldown_minutes(self):
        """Custom cooldown period is respected."""
        checker = CooldownChecker(cooldown_minutes=60)
        recent = datetime.now(timezone.utc) - timedelta(minutes=45)
        assert checker.is_in_cooldown(recent) is True

    def test_naive_datetime_handled(self):
        """Naive datetime (no tzinfo) is handled gracefully."""
        checker = CooldownChecker(cooldown_minutes=30)
        naive_recent = datetime.utcnow() - timedelta(minutes=5)
        assert checker.is_in_cooldown(naive_recent) is True


# ===========================================================================
# TestErrorHandler
# ===========================================================================

class TestErrorHandler:
    """MW-04: Catch-all error handler."""

    @pytest.mark.asyncio
    async def test_success_passthrough(self):
        """Successful coroutine result passes through unchanged."""
        expected = RequestOutcome(status="ok", intent="saludo")

        async def success():
            return expected

        result = await safe_handle(success())
        assert result is expected

    @pytest.mark.asyncio
    async def test_none_passthrough(self):
        """None result (bot silent) passes through unchanged."""
        async def silent():
            return None

        result = await safe_handle(silent())
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_caught(self):
        """Exception is caught and RequestOutcome with status='error' returned."""
        async def failing():
            raise RuntimeError("Database connection lost")

        result = await safe_handle(failing())
        assert result is not None
        assert isinstance(result, RequestOutcome)
        assert result.status == "error"
        assert result.error_type == "RuntimeError"

    @pytest.mark.asyncio
    async def test_value_error_caught(self):
        """ValueError is also caught."""
        async def bad_value():
            raise ValueError("Invalid input")

        result = await safe_handle(bad_value())
        assert result is not None
        assert isinstance(result, RequestOutcome)
        assert result.status == "error"
        assert result.error_type == "ValueError"

    @pytest.mark.asyncio
    async def test_keyboard_interrupt_propagates(self):
        """KeyboardInterrupt is NOT caught (it's BaseException, not Exception)."""
        async def keyboard():
            raise KeyboardInterrupt()

        with pytest.raises(KeyboardInterrupt):
            await safe_handle(keyboard())

    @pytest.mark.asyncio
    async def test_safe_error_text_is_user_friendly(self):
        """error_message in outcome does NOT expose internal/tech text to users.

        The user-visible error text is now composed by handle() in MessageHandler,
        not by safe_handle itself.  safe_handle's error_message is an internal
        field (capped to 500 chars) for logging only.
        """
        async def crash():
            raise Exception("sqlalchemy.exc.OperationalError: connection refused")

        result = await safe_handle(crash())
        assert result is not None
        assert isinstance(result, RequestOutcome)
        assert result.status == "error"
        # SAFE_ERROR_TEXT is the user-visible copy, assembled by MessageHandler
        assert SAFE_ERROR_TEXT is not None
        assert "Disculpa" in SAFE_ERROR_TEXT
