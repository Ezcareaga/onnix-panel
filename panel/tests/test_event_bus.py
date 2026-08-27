"""Tests for EventBus SSE singleton."""
import asyncio

import pytest

pytestmark = pytest.mark.asyncio


class TestEventBusSubscribe:
    async def test_subscribe_returns_queue(self):
        from app.services.event_bus import EventBus

        bus = EventBus()
        q = bus.subscribe()
        assert q is not None
        assert isinstance(q, asyncio.Queue)

    async def test_subscribe_increases_subscriber_count(self):
        from app.services.event_bus import EventBus

        bus = EventBus()
        assert len(bus._subscribers) == 0
        bus.subscribe()
        assert len(bus._subscribers) == 1
        bus.subscribe()
        assert len(bus._subscribers) == 2

    async def test_subscribe_queue_has_maxsize_50(self):
        from app.services.event_bus import EventBus

        bus = EventBus()
        q = bus.subscribe()
        assert q.maxsize == 50


class TestEventBusPublish:
    async def test_publish_delivers_to_single_subscriber(self):
        from app.services.event_bus import EventBus

        bus = EventBus()
        q = bus.subscribe()
        await bus.publish("test_event", {"conversation_id": 42})
        event = q.get_nowait()
        assert event["type"] == "test_event"
        assert event["data"]["conversation_id"] == 42

    async def test_publish_to_multiple_subscribers(self):
        from app.services.event_bus import EventBus

        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        await bus.publish("test_event", {"x": 1})
        assert q1.qsize() == 1
        assert q2.qsize() == 1

    async def test_publish_event_structure(self):
        from app.services.event_bus import EventBus

        bus = EventBus()
        q = bus.subscribe()
        await bus.publish("conversation_update", {"conversation_id": 99})
        event = q.get_nowait()
        assert "type" in event
        assert "data" in event
        assert event["type"] == "conversation_update"
        assert event["data"]["conversation_id"] == 99

    async def test_publish_queue_full_is_skipped_no_raise(self):
        from app.services.event_bus import EventBus

        bus = EventBus()
        q = bus.subscribe()
        # Fill the queue to maxsize
        for i in range(50):
            q.put_nowait({"type": "filler", "data": {}})
        # Publish when full — should not raise
        await bus.publish("overflow_event", {"x": 1})
        # Queue remains at maxsize (overflow event was skipped)
        assert q.qsize() == 50

    async def test_publish_after_unsubscribe_no_delivery(self):
        from app.services.event_bus import EventBus

        bus = EventBus()
        q = bus.subscribe()
        bus.unsubscribe(q)
        await bus.publish("test_event", {"x": 1})
        assert q.qsize() == 0

    async def test_publish_no_subscribers_no_error(self):
        from app.services.event_bus import EventBus

        bus = EventBus()
        # No subscribers — should not raise
        await bus.publish("test_event", {"x": 1})

    async def test_publish_multiple_events_ordered(self):
        from app.services.event_bus import EventBus

        bus = EventBus()
        q = bus.subscribe()
        await bus.publish("event_a", {"seq": 1})
        await bus.publish("event_b", {"seq": 2})
        e1 = q.get_nowait()
        e2 = q.get_nowait()
        assert e1["type"] == "event_a"
        assert e2["type"] == "event_b"


class TestEventBusUnsubscribe:
    async def test_unsubscribe_removes_subscriber(self):
        from app.services.event_bus import EventBus

        bus = EventBus()
        q = bus.subscribe()
        assert len(bus._subscribers) == 1
        bus.unsubscribe(q)
        assert len(bus._subscribers) == 0

    async def test_unsubscribe_nonexistent_no_error(self):
        from app.services.event_bus import EventBus

        bus = EventBus()
        q = asyncio.Queue()
        bus.unsubscribe(q)  # Should not raise

    async def test_unsubscribe_one_of_multiple(self):
        from app.services.event_bus import EventBus

        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        bus.unsubscribe(q1)
        assert len(bus._subscribers) == 1
        assert q2 in bus._subscribers
        assert q1 not in bus._subscribers

    async def test_unsubscribe_twice_no_error(self):
        from app.services.event_bus import EventBus

        bus = EventBus()
        q = bus.subscribe()
        bus.unsubscribe(q)
        bus.unsubscribe(q)  # Second call should not raise


class TestEventBusSingleton:
    async def test_singleton_exists(self):
        from app.services.event_bus import event_bus

        assert event_bus is not None

    async def test_singleton_is_event_bus_instance(self):
        from app.services.event_bus import EventBus, event_bus

        assert isinstance(event_bus, EventBus)

    async def test_singleton_is_same_object(self):
        from app.services.event_bus import event_bus as bus1
        from app.services.event_bus import event_bus as bus2

        assert bus1 is bus2


class TestSSERoute:
    async def test_sse_endpoint_route_is_registered(self):
        """Verify /conversations/sse route is registered in the router."""
        from app.routes.conversations import router

        sse_routes = [
            r
            for r in router.routes
            if hasattr(r, "path") and r.path == "/conversations/sse"
        ]
        assert len(sse_routes) == 1

    async def test_sse_route_method_is_get(self):
        """SSE endpoint must be a GET route."""
        from app.routes.conversations import router

        sse_routes = [
            r
            for r in router.routes
            if hasattr(r, "path") and r.path == "/conversations/sse"
        ]
        assert len(sse_routes) == 1
        route = sse_routes[0]
        assert "GET" in route.methods
