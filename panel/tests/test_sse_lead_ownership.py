"""Tests for lead.created ownership filtering in SSE endpoints.

Covers the `should_forward_event` helper and both SSE endpoints:
- GET /events (global_sse in routes/events.py)
- GET /conversations/sse (conversations_sse in routes/conversations.py)

TDD: tests were written RED first, then implementation made them GREEN.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Unit tests — pure helper `should_forward_event` (sync, no asyncio mark)
# ---------------------------------------------------------------------------


class TestShouldForwardEvent:
    """Unit tests for the pure forward/drop decision helper.

    The helper is expected to live in app.routes.events.
    Signature: should_forward_event(event: dict, is_agent: bool, user_id: int | None) -> bool

    These tests are intentionally SYNC (no async def) so pytest-asyncio does
    not try to run them as coroutines. The helper itself is a pure function.
    """

    def _import_helper(self):
        from app.routes.events import should_forward_event
        return should_forward_event

    # --- admin always receives everything ---

    def test_admin_receives_lead_created_for_own_agent(self):
        """Admin (is_agent=False) always receives lead.created regardless of agent_user_id."""
        fn = self._import_helper()
        event = {"type": "lead.created", "data": {"agent_user_id": 42, "contact_id": 1}}
        assert fn(event, is_agent=False, user_id=1) is True

    def test_admin_receives_lead_created_unassigned(self):
        """Admin receives lead.created even when agent_user_id is None (unassigned)."""
        fn = self._import_helper()
        event = {"type": "lead.created", "data": {"agent_user_id": None, "contact_id": 1}}
        assert fn(event, is_agent=False, user_id=1) is True

    def test_admin_receives_other_event_types(self):
        """Admin receives all non-lead.created events."""
        fn = self._import_helper()
        event = {"type": "conversation_update", "data": {"conversation_id": 5}}
        assert fn(event, is_agent=False, user_id=1) is True

    # --- agent receives ONLY their own lead.created events ---

    def test_agent_receives_lead_created_for_themselves(self):
        """Agent receives lead.created when agent_user_id matches their own user_id."""
        fn = self._import_helper()
        event = {"type": "lead.created", "data": {"agent_user_id": 7, "contact_id": 2}}
        assert fn(event, is_agent=True, user_id=7) is True

    def test_agent_does_not_receive_lead_created_for_other_agent(self):
        """Agent does NOT receive lead.created owned by a different agent."""
        fn = self._import_helper()
        event = {"type": "lead.created", "data": {"agent_user_id": 99, "contact_id": 3}}
        assert fn(event, is_agent=True, user_id=7) is False

    def test_agent_does_not_receive_lead_created_unassigned(self):
        """Agent does NOT receive lead.created for unassigned contacts (agent_user_id=None)."""
        fn = self._import_helper()
        event = {"type": "lead.created", "data": {"agent_user_id": None, "contact_id": 4}}
        assert fn(event, is_agent=True, user_id=7) is False

    def test_agent_receives_other_event_types(self):
        """Agent receives non-lead.created events (they are filtered elsewhere if needed)."""
        fn = self._import_helper()
        event = {"type": "conversation_update", "data": {"conversation_id": 5}}
        assert fn(event, is_agent=True, user_id=7) is True

    # --- payload must contain agent_user_id field ---

    def test_lead_created_payload_must_have_agent_user_id_key(self):
        """lead.created event data must expose agent_user_id key (even if None)."""
        fn = self._import_helper()
        # When the key is missing entirely (old payload without fix), agent
        # gets None which equals False for ownership — agent should be blocked.
        event = {"type": "lead.created", "data": {"contact_id": 1}}  # no agent_user_id key
        # Missing key => treated as None => agent is blocked
        assert fn(event, is_agent=True, user_id=7) is False

    def test_lead_created_payload_agent_user_id_present_for_admin(self):
        """Admin passes even when agent_user_id key is missing (backwards compat)."""
        fn = self._import_helper()
        event = {"type": "lead.created", "data": {"contact_id": 1}}  # no agent_user_id key
        assert fn(event, is_agent=False, user_id=1) is True


# ---------------------------------------------------------------------------
# Integration tests — GET /events (global_sse) endpoint
# ---------------------------------------------------------------------------


class TestGlobalSseLeadOwnershipFiltering:
    """Integration tests for the /events endpoint ownership filter.

    We call the route handler directly (no HTTP transport) and drive the
    generator manually — identical pattern to the existing TestEventsEndpoint.
    """

    def _build_fake_request(self) -> MagicMock:
        """Return a fake Request whose is_disconnected() returns True on the first call.

        This allows the generator to check disconnection immediately after
        processing (or skipping) one event from the queue.
        """
        call_count = 0

        async def _is_disconnected():
            nonlocal call_count
            call_count += 1
            # First call: False (generator proceeds to queue.get)
            # Second call: True (generator breaks after handling the event)
            return call_count > 1

        fake_request = MagicMock()
        fake_request.is_disconnected = _is_disconnected
        return fake_request

    def _make_user(self, *, role: str, user_id: int) -> "User":
        from app.models.user import User
        return User(
            id=user_id,
            email=f"pytest_sse_{user_id}@onnixtest.com",
            name="SSE Test",
            role=role,
            is_active=True,
            username=f"pytest_sse_{user_id}",
        )

    def _make_queue_with_sentinel(self, *events: dict) -> asyncio.Queue:
        """Pre-load a queue with events + a 'disconnect' sentinel event.

        The sentinel causes the generator to process one final non-blocking
        queue.get() and then detect disconnection on the next loop iteration.
        We use a dummy 'ping' event that carries no PII data so the test can
        distinguish it from events that should/shouldn't be forwarded.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        for ev in events:
            q.put_nowait(ev)
        # Sentinel: after all real events are consumed, this unblocks queue.get()
        # so the generator reaches is_disconnected() == True on the next iteration.
        q.put_nowait({"type": "__sentinel__", "data": {}})
        return q

    async def test_agent_does_not_receive_lead_for_other_agent(self):
        """An agent connected to /events must NOT receive lead.created owned by another agent."""
        from app.routes.events import global_sse
        from app.services import event_bus as event_bus_module

        agent_user = self._make_user(role="agent", user_id=10)
        fake_request = self._build_fake_request()

        # lead.created belonging to agent_id=99, not our agent (10)
        q = self._make_queue_with_sentinel(
            {"type": "lead.created", "data": {
                "contact_id": 1, "name": "Carlos", "phone": "+595981000001",
                "source": "whatsapp", "status": "new", "agent_user_id": 99,
            }}
        )

        original_subscribe = event_bus_module.event_bus.subscribe
        event_bus_module.event_bus.subscribe = lambda: q
        try:
            response = await global_sse(request=fake_request, user=agent_user)
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
        finally:
            event_bus_module.event_bus.subscribe = original_subscribe

        # No event data should have been forwarded (only keepalive or nothing)
        combined = "".join(chunks)
        assert "lead.created" not in combined
        assert "Carlos" not in combined

    async def test_agent_does_not_receive_lead_unassigned(self):
        """An agent must NOT receive lead.created with agent_user_id=None (unassigned)."""
        from app.routes.events import global_sse
        from app.services import event_bus as event_bus_module

        agent_user = self._make_user(role="agent", user_id=10)
        fake_request = self._build_fake_request()

        q = self._make_queue_with_sentinel(
            {"type": "lead.created", "data": {
                "contact_id": 2, "name": "Maria", "phone": "+595981000002",
                "source": "telegram", "status": "new", "agent_user_id": None,
            }}
        )

        original_subscribe = event_bus_module.event_bus.subscribe
        event_bus_module.event_bus.subscribe = lambda: q
        try:
            response = await global_sse(request=fake_request, user=agent_user)
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
        finally:
            event_bus_module.event_bus.subscribe = original_subscribe

        combined = "".join(chunks)
        assert "lead.created" not in combined
        assert "Maria" not in combined

    async def test_agent_receives_own_lead_created(self):
        """An agent DOES receive lead.created when agent_user_id matches their own id."""
        from app.routes.events import global_sse
        from app.services import event_bus as event_bus_module

        agent_user = self._make_user(role="agent", user_id=10)
        fake_request = self._build_fake_request()

        q = self._make_queue_with_sentinel(
            {"type": "lead.created", "data": {
                "contact_id": 3, "name": "Pedro", "phone": "+595981000003",
                "source": "infocasas", "status": "new", "agent_user_id": 10,
            }}
        )

        original_subscribe = event_bus_module.event_bus.subscribe
        event_bus_module.event_bus.subscribe = lambda: q
        try:
            response = await global_sse(request=fake_request, user=agent_user)
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
        finally:
            event_bus_module.event_bus.subscribe = original_subscribe

        combined = "".join(chunks)
        assert "lead.created" in combined
        assert "Pedro" in combined

    async def test_admin_receives_all_lead_created(self):
        """An admin connected to /events receives ALL lead.created events."""
        from app.routes.events import global_sse
        from app.services import event_bus as event_bus_module

        admin_user = self._make_user(role="admin", user_id=1)
        # Two events: one unassigned, one for another agent — admin sees both
        # Use a fresh fake_request that disconnects after 3 iterations
        call_count = 0

        async def _is_disconnected():
            nonlocal call_count
            call_count += 1
            return call_count > 3

        fake_request = MagicMock()
        fake_request.is_disconnected = _is_disconnected

        q = self._make_queue_with_sentinel(
            {"type": "lead.created", "data": {
                "contact_id": 4, "name": "Ana", "phone": "+595981000004",
                "source": "whatsapp", "status": "new", "agent_user_id": None,
            }},
            {"type": "lead.created", "data": {
                "contact_id": 5, "name": "Luis", "phone": "+595981000005",
                "source": "telegram", "status": "new", "agent_user_id": 42,
            }},
        )

        original_subscribe = event_bus_module.event_bus.subscribe
        event_bus_module.event_bus.subscribe = lambda: q
        try:
            response = await global_sse(request=fake_request, user=admin_user)
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
        finally:
            event_bus_module.event_bus.subscribe = original_subscribe

        combined = "".join(chunks)
        assert "Ana" in combined
        assert "Luis" in combined

    async def test_lead_created_payload_contains_agent_user_id(self):
        """The forwarded lead.created payload must include the agent_user_id field."""
        from app.routes.events import global_sse
        from app.services import event_bus as event_bus_module

        # Admin sees everything — use admin to verify payload shape
        admin_user = self._make_user(role="admin", user_id=1)
        fake_request = self._build_fake_request()

        q = self._make_queue_with_sentinel(
            {"type": "lead.created", "data": {
                "contact_id": 6, "name": "Sofia", "phone": "+595981000006",
                "source": "whatsapp", "status": "new", "agent_user_id": 7,
            }}
        )

        original_subscribe = event_bus_module.event_bus.subscribe
        event_bus_module.event_bus.subscribe = lambda: q
        try:
            response = await global_sse(request=fake_request, user=admin_user)
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
        finally:
            event_bus_module.event_bus.subscribe = original_subscribe

        combined = "".join(chunks)
        assert "agent_user_id" in combined


# ---------------------------------------------------------------------------
# Integration tests — GET /conversations/sse endpoint
# ---------------------------------------------------------------------------


class TestConversationsSseLeadOwnershipFiltering:
    """Tests for lead.created filtering in /conversations/sse.

    Calls the route handler directly with a minimal fake DB that never executes
    real SQL (the lead.created filter doesn't need DB — it reads from payload).
    """

    def _make_user(self, *, role: str, user_id: int):
        from app.models.user import User
        return User(
            id=user_id,
            email=f"pytest_sse_{user_id}@onnixtest.com",
            name="SSE Test",
            role=role,
            is_active=True,
            username=f"pytest_sse_{user_id}",
        )

    def _build_fake_request(self) -> MagicMock:
        """Fake Request that disconnects on the second is_disconnected() call."""
        call_count = 0

        async def _is_disconnected():
            nonlocal call_count
            call_count += 1
            return call_count > 1

        fake_request = MagicMock()
        fake_request.is_disconnected = _is_disconnected
        return fake_request

    def _build_fake_request_n(self, n: int) -> MagicMock:
        """Fake Request that disconnects after n is_disconnected() calls."""
        call_count = 0

        async def _is_disconnected():
            nonlocal call_count
            call_count += 1
            return call_count > n

        fake_request = MagicMock()
        fake_request.is_disconnected = _is_disconnected
        return fake_request

    def _make_fake_db(self):
        """Minimal async DB mock — no SQL needed for lead.created filtering."""
        fake_db = AsyncMock()
        return fake_db

    def _make_queue_with_sentinel(self, *events: dict) -> asyncio.Queue:
        """Pre-load a queue with events + a sentinel that unblocks queue.get()."""
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        for ev in events:
            q.put_nowait(ev)
        q.put_nowait({"type": "__sentinel__", "data": {}})
        return q

    async def test_agent_does_not_receive_lead_from_other_agent_via_conv_sse(self):
        """Agent connected to /conversations/sse must NOT receive lead.created for another agent."""
        from app.routes.conversations import conversations_sse
        from app.services import event_bus as event_bus_module

        agent_user = self._make_user(role="agent", user_id=10)
        fake_request = self._build_fake_request()
        fake_db = self._make_fake_db()

        q = self._make_queue_with_sentinel(
            {"type": "lead.created", "data": {
                "contact_id": 7, "name": "Roberto", "phone": "+595981000007",
                "source": "whatsapp", "status": "new", "agent_user_id": 55,
            }}
        )

        original_subscribe = event_bus_module.event_bus.subscribe
        event_bus_module.event_bus.subscribe = lambda: q
        try:
            response = await conversations_sse(
                request=fake_request, user=agent_user, db=fake_db
            )
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
        finally:
            event_bus_module.event_bus.subscribe = original_subscribe

        combined = "".join(chunks)
        assert "lead.created" not in combined
        assert "Roberto" not in combined

    async def test_agent_does_not_receive_unassigned_lead_via_conv_sse(self):
        """Agent must NOT receive lead.created for unassigned contact via /conversations/sse."""
        from app.routes.conversations import conversations_sse
        from app.services import event_bus as event_bus_module

        agent_user = self._make_user(role="agent", user_id=10)
        fake_request = self._build_fake_request()
        fake_db = self._make_fake_db()

        q = self._make_queue_with_sentinel(
            {"type": "lead.created", "data": {
                "contact_id": 8, "name": "Elena", "phone": "+595981000008",
                "source": "infocasas", "status": "new", "agent_user_id": None,
            }}
        )

        original_subscribe = event_bus_module.event_bus.subscribe
        event_bus_module.event_bus.subscribe = lambda: q
        try:
            response = await conversations_sse(
                request=fake_request, user=agent_user, db=fake_db
            )
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
        finally:
            event_bus_module.event_bus.subscribe = original_subscribe

        combined = "".join(chunks)
        assert "lead.created" not in combined
        assert "Elena" not in combined

    async def test_agent_receives_own_lead_via_conv_sse(self):
        """Agent DOES receive lead.created matching their own id via /conversations/sse."""
        from app.routes.conversations import conversations_sse
        from app.services import event_bus as event_bus_module

        agent_user = self._make_user(role="agent", user_id=10)
        fake_request = self._build_fake_request()
        fake_db = self._make_fake_db()

        q = self._make_queue_with_sentinel(
            {"type": "lead.created", "data": {
                "contact_id": 9, "name": "Carmen", "phone": "+595981000009",
                "source": "telegram", "status": "new", "agent_user_id": 10,
            }}
        )

        original_subscribe = event_bus_module.event_bus.subscribe
        event_bus_module.event_bus.subscribe = lambda: q
        try:
            response = await conversations_sse(
                request=fake_request, user=agent_user, db=fake_db
            )
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
        finally:
            event_bus_module.event_bus.subscribe = original_subscribe

        combined = "".join(chunks)
        assert "lead.created" in combined
        assert "Carmen" in combined

    async def test_admin_receives_all_leads_via_conv_sse(self):
        """Admin receives all lead.created events via /conversations/sse."""
        from app.routes.conversations import conversations_sse
        from app.services import event_bus as event_bus_module

        admin_user = self._make_user(role="admin", user_id=1)
        # 3 iterations: Gustavo + Lucia + sentinel → then disconnect on 4th check
        fake_request = self._build_fake_request_n(3)
        fake_db = self._make_fake_db()

        q = self._make_queue_with_sentinel(
            {"type": "lead.created", "data": {
                "contact_id": 10, "name": "Gustavo", "phone": "+595981000010",
                "source": "whatsapp", "status": "new", "agent_user_id": None,
            }},
            {"type": "lead.created", "data": {
                "contact_id": 11, "name": "Lucia", "phone": "+595981000011",
                "source": "infocasas", "status": "new", "agent_user_id": 77,
            }},
        )

        original_subscribe = event_bus_module.event_bus.subscribe
        event_bus_module.event_bus.subscribe = lambda: q
        try:
            response = await conversations_sse(
                request=fake_request, user=admin_user, db=fake_db
            )
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
        finally:
            event_bus_module.event_bus.subscribe = original_subscribe

        combined = "".join(chunks)
        assert "Gustavo" in combined
        assert "Lucia" in combined
