"""Human-agent cooldown check.

When a human agent takes over a conversation, the bot should stay
silent for a configurable period (default 30 minutes).

Plan 64-01: MW-03 Cooldown.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_MINUTES = 30


class CooldownChecker:
    """Checks whether a human agent replied recently.

    Parameters
    ----------
    cooldown_minutes:
        Minutes of silence after a human reply before the bot
        resumes auto-responses.
    """

    def __init__(self, cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES) -> None:
        self._cooldown_minutes = cooldown_minutes

    def is_in_cooldown(self, last_human_reply_at: datetime | None) -> bool:
        """Return ``True`` if a human replied within the cooldown window.

        Returns ``False`` if *last_human_reply_at* is ``None`` (no
        human has ever replied in this conversation).
        """
        if last_human_reply_at is None:
            return False

        # Ensure timezone-aware comparison
        now = datetime.now(timezone.utc)
        if last_human_reply_at.tzinfo is None:
            last_human_reply_at = last_human_reply_at.replace(tzinfo=timezone.utc)

        elapsed = now - last_human_reply_at
        in_cooldown = elapsed < timedelta(minutes=self._cooldown_minutes)

        if in_cooldown:
            logger.info(
                "Cooldown active: human replied %.1f min ago (limit=%d min)",
                elapsed.total_seconds() / 60, self._cooldown_minutes,
            )

        return in_cooldown
