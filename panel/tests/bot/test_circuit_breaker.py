"""Tests for the CircuitBreaker state machine.

RED phase — all tests that verify state transitions MUST fail against stubs.
"""

from unittest.mock import patch, MagicMock

import pytest

from app.bot.ai.circuit_breaker import CircuitBreaker, CircuitState


class TestCircuitBreaker:
    """CircuitBreaker state machine behaviour."""

    # 1. Initial state
    def test_initial_state_closed(self):
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED
        assert cb.is_open is False

    # 2. Single failure stays closed
    def test_single_failure_stays_closed(self):
        cb = CircuitBreaker()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        assert cb.is_open is False

    # 3. Threshold failures opens circuit
    def test_threshold_failures_opens(self):
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.is_open is True

    # 4. Below threshold stays closed
    def test_below_threshold_stays_closed(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        assert cb.is_open is False

    # 5. Success resets failure count
    def test_success_resets_failure_count(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        # After reset, 2 more failures should NOT open (only 2 < 3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        assert cb.is_open is False

    # 6. OPEN → HALF_OPEN after timeout
    @patch("app.bot.ai.circuit_breaker.time")
    def test_open_transitions_to_half_open_after_timeout(self, mock_time):
        mock_time.time = MagicMock(side_effect=[
            1000.0,  # record_failure 1
            1000.0,  # record_failure 2
            1000.0,  # record_failure 3 (opens circuit, sets _last_failure_time)
            1301.0,  # is_open check → 301s elapsed > 300s → HALF_OPEN
        ])
        cb = CircuitBreaker(failure_threshold=3, reset_timeout=300)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is False
        assert cb.state == CircuitState.HALF_OPEN

    # 7. OPEN stays open before timeout
    @patch("app.bot.ai.circuit_breaker.time")
    def test_open_stays_open_before_timeout(self, mock_time):
        mock_time.time = MagicMock(side_effect=[
            1000.0,  # record_failure 1
            1000.0,  # record_failure 2
            1000.0,  # record_failure 3
            1100.0,  # is_open check → 100s < 300s → still OPEN
        ])
        cb = CircuitBreaker(failure_threshold=3, reset_timeout=300)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is True
        assert cb.state == CircuitState.OPEN

    # 8. HALF_OPEN + success → CLOSED
    @patch("app.bot.ai.circuit_breaker.time")
    def test_half_open_success_closes(self, mock_time):
        mock_time.time = MagicMock(side_effect=[
            1000.0,  # record_failure 1
            1000.0,  # record_failure 2
            1000.0,  # record_failure 3
            1301.0,  # is_open check → HALF_OPEN
            1302.0,  # record_success (timestamp, if needed)
        ])
        cb = CircuitBreaker(failure_threshold=3, reset_timeout=300)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        # Trigger transition to HALF_OPEN
        assert cb.is_open is False
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.is_open is False

    # 9. HALF_OPEN + failure → OPEN
    @patch("app.bot.ai.circuit_breaker.time")
    def test_half_open_failure_reopens(self, mock_time):
        mock_time.time = MagicMock(side_effect=[
            1000.0,  # record_failure 1
            1000.0,  # record_failure 2
            1000.0,  # record_failure 3
            1301.0,  # is_open check → HALF_OPEN
            1302.0,  # record_failure in HALF_OPEN
            1302.0,  # is_open check after re-open
        ])
        cb = CircuitBreaker(failure_threshold=3, reset_timeout=300)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        # Trigger transition to HALF_OPEN
        assert cb.is_open is False
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.is_open is True

    # 10. on_open callback fires when circuit opens
    def test_on_open_callback_called(self):
        callback = MagicMock()
        cb = CircuitBreaker(failure_threshold=3, on_open=callback)
        for _ in range(3):
            cb.record_failure()
        callback.assert_called_once_with(3)

    # 11. on_open callback failure does not crash
    def test_on_open_callback_exception_swallowed(self):
        callback = MagicMock(side_effect=RuntimeError("boom"))
        cb = CircuitBreaker(failure_threshold=3, on_open=callback)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN
        callback.assert_called_once()

    # 12. on_open not called below threshold
    def test_on_open_not_called_below_threshold(self):
        callback = MagicMock()
        cb = CircuitBreaker(failure_threshold=3, on_open=callback)
        cb.record_failure()
        cb.record_failure()
        callback.assert_not_called()

    # 13. Custom threshold and timeout
    @patch("app.bot.ai.circuit_breaker.time")
    def test_custom_threshold_and_timeout(self, mock_time):
        mock_time.time = MagicMock(side_effect=[
            500.0,  # record_failure 1
            500.0,  # record_failure 2
            500.0,  # record_failure 3
            500.0,  # record_failure 4
            500.0,  # record_failure 5 (opens)
            561.0,  # is_open check → 61s > 60s → HALF_OPEN
        ])
        cb = CircuitBreaker(failure_threshold=5, reset_timeout=60)
        # 4 failures should NOT open
        for _ in range(4):
            cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        # 5th failure opens
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        # After 61s → HALF_OPEN
        assert cb.is_open is False
        assert cb.state == CircuitState.HALF_OPEN
