"""Circuit breaker for AI provider failover.

Tracks failures and opens the circuit when the threshold is reached,
preventing further calls to a failing provider until a reset timeout
elapses.
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Callable
import logging

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Possible states of a circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """State machine: CLOSED -> OPEN -> HALF_OPEN -> CLOSED.

    Parameters
    ----------
    failure_threshold:
        Number of consecutive failures before opening the circuit.
    reset_timeout:
        Seconds to wait in OPEN before transitioning to HALF_OPEN.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        reset_timeout: int = 300,
        on_open: Callable[[int], None] | None = None,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._reset_timeout = reset_timeout
        self._on_open = on_open
        self._state = CircuitState.CLOSED
        self._failure_count: int = 0
        self._last_failure_time: float = 0.0

    # -- properties -----------------------------------------------------------

    @property
    def state(self) -> CircuitState:
        """Current circuit state."""
        return self._state

    @property
    def is_open(self) -> bool:
        """True when calls should be blocked.

        Side-effect: transitions OPEN -> HALF_OPEN when the reset
        timeout has elapsed.
        """
        if self._state == CircuitState.OPEN:
            elapsed = time.time() - self._last_failure_time
            if elapsed >= self._reset_timeout:
                self._state = CircuitState.HALF_OPEN
                logger.info(
                    "CircuitBreaker -> HALF_OPEN after %.1fs", elapsed
                )
                return False
            return True
        # HALF_OPEN and CLOSED both allow a trial call
        return False

    # -- recording outcomes ---------------------------------------------------

    def record_success(self) -> None:
        """Record a successful call. Resets the breaker to CLOSED."""
        self._failure_count = 0
        self._state = CircuitState.CLOSED
        logger.info("CircuitBreaker -> CLOSED (success)")

    def record_failure(self) -> None:
        """Record a failed call. Opens the circuit when threshold is met."""
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._failure_count >= self._failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning(
                "CircuitBreaker -> OPEN after %d failures",
                self._failure_count,
            )
            if self._on_open:
                try:
                    self._on_open(self._failure_count)
                except Exception:
                    logger.warning("on_open callback failed (non-fatal)", exc_info=True)
