"""In-memory idempotency guard for message deduplication.

Tracks processed message external IDs (Telegram message_id or
WhatsApp MessageSid) using an OrderedDict with LRU eviction.

Plan 64-01: MW-02 Idempotency.
"""
from __future__ import annotations

import logging
from collections import OrderedDict

logger = logging.getLogger(__name__)

DEFAULT_MAX_SIZE = 10_000


class IdempotencyGuard:
    """Deduplicates messages by their external identifier.

    Uses an OrderedDict as an LRU cache. When capacity is reached,
    the oldest entries are evicted.

    Parameters
    ----------
    max_size:
        Maximum number of external IDs to track.
    """

    def __init__(self, max_size: int = DEFAULT_MAX_SIZE) -> None:
        self._max_size = max_size
        self._seen: OrderedDict[str, bool] = OrderedDict()

    def is_duplicate(self, external_id: str | None) -> bool:
        """Return ``True`` if *external_id* was already processed.

        - ``None`` or empty string always returns ``False`` (not a
          duplicate) because we cannot deduplicate without an ID.
        - New IDs are added to the cache and ``False`` is returned.
        - Repeated IDs return ``True`` immediately.
        """
        if not external_id:
            return False

        if external_id in self._seen:
            # Move to end (most recently seen)
            self._seen.move_to_end(external_id)
            logger.info("Duplicate message detected: external_id=%s", external_id)
            return True

        # Add new entry, evict oldest if at capacity
        self._seen[external_id] = True
        if len(self._seen) > self._max_size:
            evicted_id, _ = self._seen.popitem(last=False)
            logger.debug("Evicted oldest entry: %s", evicted_id)

        return False

    @property
    def size(self) -> int:
        """Current number of tracked IDs."""
        return len(self._seen)
