"""Tests for the `agendar_visita` bot tool — M6.3 Plan 123-03 (TDD).

Covers (BOT-05 / BOT-13 + D-1):
  - The tool creates a `visits` row with agent_user_id IS NULL and
    source='bot' (D-1: bot visits are unassigned, source-tagged 'bot').
  - Creating a bot visit flips contacts.status -> 'visit_scheduled'
    via VisitService._sync_contact_status (BOT-13).
  - The visit_status_change lead_event metadata shows source='bot'
    (not the hardcoded 'panel') AND the visit_created action event's
    triggered_by label is 'bot' (not 'system') on the bot path
    (D-1 cosmetic fix).
  - A VisitService error is returned to Claude as {'error': ...}
    (tool result) and never raised / surfaced to the user verbatim
    (CLAUDE.md UX rule #5).

Like test_visit_service.py these run against onnix_dev. Test
contacts use the '+5959819…' phone prefix so the conftest session
cleanup removes them, and an autouse fixture clears visits +
visit_scheduled status so the mig 040 downgrade guard never fires.
"""
import random
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.bot.ai.types import ToolCall
from app.bot.core.tool_executor import ToolExecutor
from app.bot.core.types import ConversationState
from app.models.contact import Contact
from app.models.lead_event import LeadEvent
from app.models.visit import Visit


# ---------------------------------------------------------------------------
# Local factory helpers — mirror test_visit_service.py.
# ---------------------------------------------------------------------------


def _next_phone() -> str:
    # +5959819XXXXXXX — within the conftest test cleanup range.
    return f"+5959819{random.randint(0, 9_999_999):07d}"


async def _make_contact(db, *, status: str = "bot_replied") -> Contact:
    c = Contact(
        name="AgendarVisitaTest",
        phone=_next_phone(),
        source="manual",
        status=status,
        created_at=datetime.now(timezone.utc),
    )
    db.add(c)
    await db.flush()
    return c


def _make_executor() -> ToolExecutor:
    # The handler under test only touches `session` + `search_context` +
    # VisitService; a mock search_service satisfies the constructor.
    return ToolExecutor(AsyncMock())


def _ctx_for(contact_id: int) -> ConversationState:
    ctx = ConversationState(filtros={})
    ctx._contact_id = contact_id
    return ctx


@pytest.fixture(autouse=True)
async def _cleanup_agendar_visita_contacts():
    """Drop visits + revert visit_scheduled status for our phone range so the
    mig 040 downgrade guard never fires (same rationale as
    test_visit_service.py)."""
    yield
    from sqlalchemy import text
    from tests.conftest import _TestSession  # type: ignore[import-not-found]
    try:
        async with _TestSession() as s:
            await s.execute(text(
                "DELETE FROM visits WHERE contact_id IN "
                "(SELECT id FROM contacts WHERE phone LIKE '+5959819%')"
            ))
            await s.execute(text(
                "UPDATE contacts SET status = 'no_response' "
                "WHERE phone LIKE '+5959819%' AND status = 'visit_scheduled'"
            ))
            await s.commit()
    except Exception:
        pass  # Best-effort — session-scoped conftest cleanup is the safety net.


# ---------------------------------------------------------------------------
# D-1: NULL agent + source='bot'
# ---------------------------------------------------------------------------


class TestAgendarVisitaCreate:
    async def test_agendar_visita_creates_null_agent_bot_source(self, db):
        """The handler creates a visits row with agent_user_id IS NULL and
        source='bot' (D-1)."""
        c = await _make_contact(db, status="bot_replied")
        future = datetime.now(timezone.utc) + timedelta(days=2)

        executor = _make_executor()
        result = await executor._execute_agendar_visita(
            {"scheduled_at_iso": future.isoformat()},
            db,
            _ctx_for(c.id),
        )

        assert result.get("ok") is True
        assert result.get("visit_id") is not None

        rows = await db.execute(select(Visit).where(Visit.contact_id == c.id))
        visits = list(rows.scalars().all())
        assert len(visits) == 1
        visit = visits[0]
        assert visit.agent_user_id is None
        assert visit.source == "bot"
        assert visit.status == "scheduled"

    async def test_agendar_visita_syncs_status_visit_scheduled(self, db):
        """Creating a bot visit flips contacts.status -> 'visit_scheduled'
        (BOT-13)."""
        c = await _make_contact(db, status="bot_replied")
        future = datetime.now(timezone.utc) + timedelta(days=2)

        executor = _make_executor()
        await executor._execute_agendar_visita(
            {"scheduled_at_iso": future.isoformat()},
            db,
            _ctx_for(c.id),
        )

        await db.refresh(c)
        assert c.status == "visit_scheduled"

    async def test_agendar_visita_lead_event_source_bot(self, db):
        """D-1 cosmetic fix: the visit_status_change lead_event metadata shows
        source='bot' (not 'panel'), AND the visit_created action event's
        triggered_by label is 'bot' (not 'system') on the bot path."""
        c = await _make_contact(db, status="bot_replied")
        future = datetime.now(timezone.utc) + timedelta(days=2)

        executor = _make_executor()
        await executor._execute_agendar_visita(
            {"scheduled_at_iso": future.isoformat()},
            db,
            _ctx_for(c.id),
        )

        evt_q = await db.execute(
            select(LeadEvent)
            .where(LeadEvent.contact_id == c.id)
            .order_by(LeadEvent.id.asc())
        )
        events = list(evt_q.scalars().all())

        status_changes = [
            e for e in events if e.event_type == "visit_status_change"
        ]
        assert len(status_changes) == 1
        assert status_changes[0].event_metadata.get("source") == "bot", (
            "bot-path visit_status_change must label source='bot', not 'panel'"
        )

        created = [e for e in events if e.event_type == "visit_created"]
        assert len(created) == 1
        assert created[0].triggered_by == "bot", (
            "bot-path visit_created must label triggered_by='bot', not 'system'"
        )

    async def test_agendar_visita_error_returns_dict_not_raises(self, db):
        """When the requested datetime is naive (no tzinfo), the handler
        returns {'error': ...} and does NOT raise — the user never sees a
        technical error (UX rule #5)."""
        c = await _make_contact(db, status="bot_replied")
        naive = (datetime.now() + timedelta(days=2)).replace(tzinfo=None)

        executor = _make_executor()
        result = await executor._execute_agendar_visita(
            {"scheduled_at_iso": naive.isoformat()},
            db,
            _ctx_for(c.id),
        )

        assert "error" in result
        assert result.get("ok") is None
        # No visit row created on the error path.
        rows = await db.execute(select(Visit).where(Visit.contact_id == c.id))
        assert list(rows.scalars().all()) == []
