"""Event bus for SSE broadcast to connected panel clients."""
import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class EventBus:
    """In-process pub/sub for SSE events.

    One asyncio.Queue per connected client (subscriber). publish() puts an event
    on every live queue. QueueFull is tolerated — slow clients are skipped.
    """

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        """Register a new SSE client. Returns the queue it should read from."""
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._subscribers.append(q)
        logger.debug("EventBus: subscriber added (total=%d)", len(self._subscribers))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Remove a subscriber queue (called on disconnect)."""
        try:
            self._subscribers.remove(q)
            logger.debug(
                "EventBus: subscriber removed (total=%d)", len(self._subscribers)
            )
        except ValueError:
            pass  # Already removed

    async def publish(self, event_type: str, data: dict[str, Any]) -> None:
        """Broadcast an event to all subscribers. QueueFull -> skip that subscriber."""
        event = {"type": event_type, "data": data}
        dead: list[asyncio.Queue] = []
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.debug("EventBus: QueueFull for a subscriber — skipped")
            except Exception as exc:
                logger.warning(
                    "EventBus: unexpected error putting to queue: %s", exc
                )
                dead.append(q)
        for q in dead:
            self.unsubscribe(q)

    def publish_from_thread(
        self, event_type: str, data: dict[str, Any], loop: asyncio.AbstractEventLoop
    ) -> None:
        """Thread-safe publish for use from non-async threads.

        Use this when calling from a sync thread (e.g. ThreadPoolExecutor,
        sync APScheduler). For async contexts, use await publish() instead.
        Schedules publish() on the given event loop via call_soon_threadsafe.
        """
        loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self.publish(event_type, data), loop=loop)
        )


# Module-level singleton
event_bus = EventBus()
