"""In-memory sliding-window rate limiter.

Tracks message timestamps per contact and rejects messages that
exceed the configured threshold within the sliding window.

Plan 64-01: MW-01 Rate limiter.
"""
from __future__ import annotations

import time
import logging

logger = logging.getLogger(__name__)


class RateLimiter:
    """Sliding-window rate limiter keyed by contact identifier.

    Parameters
    ----------
    max_messages:
        Maximum messages allowed within the window.
    window_seconds:
        Duration of the sliding window in seconds.
    """

    def __init__(
        self,
        max_messages: int = 5,
        window_seconds: int = 60,
    ) -> None:
        self._max_messages = max_messages
        self._window_seconds = window_seconds
        self._timestamps: dict[str, list[float]] = {}

    def is_rate_limited(self, contact_id: str) -> bool:
        """Return ``True`` if *contact_id* has exceeded the rate limit.

        If the contact is within the limit, the current timestamp is
        recorded. If over the limit, the message is rejected and no
        timestamp is added.
        """
        now = time.monotonic()
        cutoff = now - self._window_seconds

        # Get or create timestamp list
        if contact_id not in self._timestamps:
            self._timestamps[contact_id] = []

        # Prune expired timestamps
        self._timestamps[contact_id] = [
            ts for ts in self._timestamps[contact_id] if ts > cutoff
        ]

        if len(self._timestamps[contact_id]) >= self._max_messages:
            logger.warning(
                "Rate limited: contact=%s count=%d limit=%d",
                contact_id, len(self._timestamps[contact_id]), self._max_messages,
            )
            return True

        # Record this message
        self._timestamps[contact_id].append(now)
        return False

    def cleanup(self) -> int:
        """Remove contacts with no recent activity.

        Returns the number of contacts purged. Call periodically
        to prevent unbounded memory growth.
        """
        now = time.monotonic()
        cutoff = now - self._window_seconds
        to_remove = []

        for contact_id, timestamps in self._timestamps.items():
            active = [ts for ts in timestamps if ts > cutoff]
            if not active:
                to_remove.append(contact_id)
            else:
                self._timestamps[contact_id] = active

        for contact_id in to_remove:
            del self._timestamps[contact_id]

        return len(to_remove)
