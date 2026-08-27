"""Tests for EventBus (unit) and GET /events SSE endpoint (integration).

Unit tests (TestEventBus*) use a fresh EventBus() instance — no app, no DB.
Integration tests (TestEventsEndpoint) use the app via dependency_overrides
to bypass auth, matching the pattern used by test_routes_dashboard.py.
"""
import asyncio
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Unit tests — EventBus pure asyncio logic
# ---------------------------------------------------------------------------


class TestEventBusSubscribe:
    async def test_subscribe_adds_queue(self):
        """subscribe() returns a Queue and registers it in _subscribers."""
        from app.services.event_bus import EventBus

        bus = EventBus()
        assert len(bus._subscribers) == 0

        q = bus.subscribe()

        assert isinstance(q, asyncio.Queue)
        assert q in bus._subscribers
        assert len(bus._subscribers) == 1


class TestEventBusUnsubscribe:
    async def test_unsubscribe_removes_queue(self):
        """unsubscribe() removes the exact queue that was registered."""
        from app.services.event_bus import EventBus

        bus = EventBus()
        q = bus.subscribe()
        assert q in bus._subscribers

        bus.unsubscribe(q)

        assert q not in bus._subscribers
        assert len(bus._subscribers) == 0

    async def test_unsubscribe_nonexistent_is_safe(self):
        """Calling unsubscribe() with a queue that was never registered does not raise."""
        from app.services.event_bus import EventBus

        bus = EventBus()
        alien_queue = asyncio.Queue()

        # Must not raise ValueError or any other exception
        bus.unsubscribe(alien_queue)


class TestEventBusPublish:
    async def test_publish_delivers_to_subscriber(self):
        """publish() puts the correctly shaped event dict onto the subscriber queue."""
        from app.services.event_bus import EventBus

        bus = EventBus()
        q = bus.subscribe()

        await bus.publish("lead_update", {"lead_id": 7})

        assert q.qsize() == 1
        event = q.get_nowait()
        assert event == {"type": "lead_update", "data": {"lead_id": 7}}

    async def test_publish_delivers_to_multiple_subscribers(self):
        """All N subscribers receive the same event when publish() is called once."""
        from app.services.event_bus import EventBus

        bus = EventBus()
        queues = [bus.subscribe() for _ in range(4)]

        await bus.publish("contact_update", {"contact_id": 99})

        for q in queues:
            assert q.qsize() == 1
            event = q.get_nowait()
            assert event["type"] == "contact_update"
            assert event["data"]["contact_id"] == 99

    async def test_publish_queue_full_skips_subscriber(self):
        """A full queue is silently skipped; other subscribers still receive the event."""
        from app.services.event_bus import EventBus

        bus = EventBus()
        full_q = bus.subscribe()
        good_q = bus.subscribe()

        # Saturate full_q to its maxsize (50)
        for i in range(50):
            full_q.put_nowait({"type": "filler", "data": {"i": i}})
        assert full_q.full()

        # publish() must not raise even though full_q is at capacity
        await bus.publish("overflow_event", {"x": 1})

        # full_q is unchanged (overflow event was skipped)
        assert full_q.qsize() == 50
        # good_q received the event normally
        assert good_q.qsize() == 1
        event = good_q.get_nowait()
        assert event["type"] == "overflow_event"


class TestEventBusPublishFromThread:
    async def test_publish_from_thread_schedules_on_loop(self):
        """publish_from_thread() calls call_soon_threadsafe on the supplied loop."""
        from app.services.event_bus import EventBus

        bus = EventBus()
        mock_loop = MagicMock(spec=asyncio.AbstractEventLoop)

        bus.publish_from_thread("thread_event", {"value": 42}, mock_loop)

        mock_loop.call_soon_threadsafe.assert_called_once()
        # The first positional arg must be a callable (the lambda wrapping ensure_future)
        callback_arg = mock_loop.call_soon_threadsafe.call_args[0][0]
        assert callable(callback_arg)


# ---------------------------------------------------------------------------
# Integration tests — GET /events SSE endpoint
# ---------------------------------------------------------------------------


class TestEventsEndpoint:
    async def test_events_endpoint_requires_auth(self, client):
        """Unauthenticated request to /events must be rejected (302 or 303 redirect)."""
        resp = await client.get("/events")

        # get_current_user raises HTTPException(303) for browser clients
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers.get("location", "")

    async def test_events_endpoint_returns_sse_headers(self):
        """Authenticated request to /events returns text/event-stream with required headers.

        Strategy: call the route handler function directly (no HTTP transport)
        to obtain the StreamingResponse object and inspect its status code,
        media_type, and headers — without ever driving the streaming generator.
        A minimal fake Request and fake User bypass auth and DB entirely.
        """
        from unittest.mock import AsyncMock, MagicMock
        from fastapi.responses import StreamingResponse
        from app.routes.events import global_sse
        from app.models.user import User
        from app.services import event_bus as event_bus_module

        fake_user = User(
            id=9999,
            email="pytest_sse@onnixtest.com",
            name="SSE Test",
            role="user",
            is_active=True,
            username="pytest_sse",
        )

        # Minimal Starlette Request stub — is_disconnected() must be awaitable
        fake_request = MagicMock()
        fake_request.is_disconnected = AsyncMock(return_value=False)

        # Pre-load queue so the generator would yield immediately if driven
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        q.put_nowait({"type": "ping", "data": {"ok": True}})

        original_subscribe = event_bus_module.event_bus.subscribe
        event_bus_module.event_bus.subscribe = lambda: q

        try:
            response = await global_sse(request=fake_request, user=fake_user)
        finally:
            event_bus_module.event_bus.subscribe = original_subscribe

        assert isinstance(response, StreamingResponse)
        assert response.status_code == 200
        assert response.media_type == "text/event-stream"
        assert response.headers.get("x-accel-buffering", "").lower() == "no"
        assert response.headers.get("cache-control") == "no-cache"


# ---------------------------------------------------------------------------
# Route registration sanity check
# ---------------------------------------------------------------------------


class TestEventsRouteRegistration:
    async def test_events_route_is_registered(self):
        """GET /events must be registered in the events router."""
        from app.routes.events import router

        event_routes = [
            r
            for r in router.routes
            if hasattr(r, "path") and r.path == "/events"
        ]
        assert len(event_routes) == 1

    async def test_events_route_method_is_get(self):
        """The /events route must accept GET requests."""
        from app.routes.events import router

        event_routes = [
            r
            for r in router.routes
            if hasattr(r, "path") and r.path == "/events"
        ]
        assert len(event_routes) == 1
        assert "GET" in event_routes[0].methods
