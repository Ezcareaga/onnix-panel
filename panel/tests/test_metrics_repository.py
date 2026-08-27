"""Tests for app/repositories/metrics_repository.py

All DB calls are mocked — no real database connection needed.
Uses the same AsyncMock + MagicMock pattern established across this test suite.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta, date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories.metrics_repository import MetricsRepository
from app.schemas.metrics import AiCost, DailyAiCost, Latency, MessageVolume, ProviderMix, SourceCost, ToolIterations
from app.tz import PYT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(**kwargs):
    """Build a mock DB row with attribute access."""
    row = MagicMock()
    for k, v in kwargs.items():
        setattr(row, k, v)
    return row


def _make_db_scalar(scalar_value):
    """Return an AsyncMock session whose execute().scalar() returns scalar_value."""
    db = AsyncMock()
    result = MagicMock()
    result.scalar.return_value = scalar_value
    db.execute = AsyncMock(return_value=result)
    return db


def _make_db_first(first_row):
    """Return an AsyncMock session whose execute().first() returns first_row."""
    db = AsyncMock()
    result = MagicMock()
    result.first.return_value = first_row
    db.execute = AsyncMock(return_value=result)
    return db


def _make_db_all(rows):
    """Return an AsyncMock session whose execute().all() returns rows."""
    db = AsyncMock()
    result = MagicMock()
    result.all.return_value = rows
    db.execute = AsyncMock(return_value=result)
    return db


def _make_db_scalar_one(first_row):
    """AsyncMock whose execute() returns a result supporting both .scalar() and .first()."""
    db = AsyncMock()
    result = MagicMock()
    result.scalar.return_value = first_row
    result.first.return_value = first_row
    db.execute = AsyncMock(return_value=result)
    return db


# ---------------------------------------------------------------------------
# count_stuck_conversations
# ---------------------------------------------------------------------------

class TestCountStuckConversations:
    async def test_returns_count_from_db(self):
        """Scalar result from DB is returned as-is."""
        db = _make_db_scalar(3)
        repo = MetricsRepository(db)
        result = await repo.count_stuck_conversations()
        assert result == 3

    async def test_returns_zero_when_none(self):
        """None scalar (no rows) returns 0, not None."""
        db = _make_db_scalar(None)
        repo = MetricsRepository(db)
        result = await repo.count_stuck_conversations()
        assert result == 0

    async def test_execute_called(self):
        """DB execute is called exactly once."""
        db = _make_db_scalar(0)
        repo = MetricsRepository(db)
        await repo.count_stuck_conversations()
        db.execute.assert_awaited_once()


class TestCountStuckConversationsWindow:
    """M2 followup: stuck count solo cuenta conversaciones cuyo último
    mensaje inbound está dentro de la ventana 24h, consistente con el
    resto de métricas 24h del dashboard. Las conversaciones con último
    inbound de hace >24h son abandono histórico del cliente, no stuck
    actual que requiere atención.
    """

    async def test_stuck_count_excludes_conversations_older_than_24h(self, db):
        """Crea 3 conversaciones; solo la reciente-stuck debe sumar al count.

        A: último inbound hace 1h → stuck dentro de ventana → cuenta (delta +1)
        B: último inbound hace 25h → stuck HISTÓRICO → NO cuenta (el fix)
        C: último inbound hace 5 min → <10min umbral → NO cuenta
        """
        from app.models.contact import Contact
        from app.models.conversation import Conversation
        from app.models.message import Message

        now = datetime.now(timezone.utc)
        repo = MetricsRepository(db)

        baseline = await repo.count_stuck_conversations()

        # Case A — stuck reciente (1h)
        ca = Contact(
            name="Stuck Recent", phone="+595981999101",
            phone_normalized="+595981999101", source="manual", status="new",
            created_at=now - timedelta(hours=2),
        )
        db.add(ca); await db.flush()
        conv_a = Conversation(
            contact_id=ca.id, status="active", is_open=True, channel="whatsapp",
            platform="whatsapp", message_count=1,
            created_at=now - timedelta(hours=2),
        )
        db.add(conv_a); await db.flush()
        db.add(Message(
            conversation_id=conv_a.id, contact_id=ca.id,
            direction="inbound", sender_type="contact",
            body="hola A", created_at=now - timedelta(hours=1),
        ))

        # Case B — stuck histórico (25h, fuera de ventana)
        cb = Contact(
            name="Stuck Historical", phone="+595981999102",
            phone_normalized="+595981999102", source="manual", status="new",
            created_at=now - timedelta(days=3),
        )
        db.add(cb); await db.flush()
        conv_b = Conversation(
            contact_id=cb.id, status="active", is_open=True, channel="whatsapp",
            platform="whatsapp", message_count=1,
            created_at=now - timedelta(days=3),
        )
        db.add(conv_b); await db.flush()
        db.add(Message(
            conversation_id=conv_b.id, contact_id=cb.id,
            direction="inbound", sender_type="contact",
            body="hola B", created_at=now - timedelta(hours=25),
        ))

        # Case C — reciente pero <10min (no stuck)
        cc = Contact(
            name="Recent Fresh", phone="+595981999103",
            phone_normalized="+595981999103", source="manual", status="new",
            created_at=now - timedelta(hours=1),
        )
        db.add(cc); await db.flush()
        conv_c = Conversation(
            contact_id=cc.id, status="active", is_open=True, channel="whatsapp",
            platform="whatsapp", message_count=1,
            created_at=now - timedelta(hours=1),
        )
        db.add(conv_c); await db.flush()
        db.add(Message(
            conversation_id=conv_c.id, contact_id=cc.id,
            direction="inbound", sender_type="contact",
            body="hola C", created_at=now - timedelta(minutes=5),
        ))

        await db.flush()

        final = await repo.count_stuck_conversations()
        delta = final - baseline

        assert delta == 1, (
            f"Expected delta=1 (only Case A within 24h window); got {delta}. "
            f"Pre-fix delta would be 2 (A + B both stuck but B is historical)."
        )


# ---------------------------------------------------------------------------
# message_volume_24h
# ---------------------------------------------------------------------------

class TestMessageVolume24h:
    async def test_separates_by_direction_and_sender(self):
        """Row values are mapped to the correct MessageVolume fields."""
        row = _make_row(inbound=5, bot_out=3, agent_out=2, total=10)
        db = _make_db_first(row)
        repo = MetricsRepository(db)
        result = await repo.message_volume_24h()
        assert isinstance(result, MessageVolume)
        assert result.inbound == 5
        assert result.bot_out == 3
        assert result.agent_out == 2
        assert result.total == 10

    async def test_returns_zeros_when_no_data(self):
        """None first row returns all-zero MessageVolume."""
        db = _make_db_first(None)
        repo = MetricsRepository(db)
        result = await repo.message_volume_24h()
        assert result.inbound == 0
        assert result.bot_out == 0
        assert result.agent_out == 0
        assert result.total == 0

    async def test_handles_none_fields(self):
        """Individual None fields on the row are treated as 0."""
        row = _make_row(inbound=None, bot_out=2, agent_out=None, total=None)
        db = _make_db_first(row)
        repo = MetricsRepository(db)
        result = await repo.message_volume_24h()
        assert result.inbound == 0
        assert result.bot_out == 2
        assert result.agent_out == 0
        assert result.total == 0


# ---------------------------------------------------------------------------
# bot_latency_24h
# ---------------------------------------------------------------------------

class TestBotLatency24h:
    async def test_returns_zeros_when_no_data(self):
        """None first row → Latency with all zeros and n=0."""
        db = _make_db_first(None)
        repo = MetricsRepository(db)
        result = await repo.bot_latency_24h()
        assert isinstance(result, Latency)
        assert result.avg_ms == 0
        assert result.p95_ms == 0
        assert result.worst_ms == 0
        assert result.n == 0

    async def test_percentile_correct(self):
        """p95 value is taken directly from the DB row (percentile_cont is in SQL)."""
        # Simulate DB returning pre-computed percentile for [100,200,300,400,500]
        # percentile_cont(0.95) of 5 values ≈ 490
        row = _make_row(avg_ms=300.0, p95_ms=490.0, worst_ms=500, n=5)
        db = _make_db_first(row)
        repo = MetricsRepository(db)
        result = await repo.bot_latency_24h()
        assert result.avg_ms == 300
        assert result.p95_ms == 490
        assert result.worst_ms == 500
        assert result.n == 5

    async def test_rounds_float_avg(self):
        """avg_ms is rounded to nearest integer."""
        row = _make_row(avg_ms=123.7, p95_ms=200.0, worst_ms=300, n=10)
        db = _make_db_first(row)
        repo = MetricsRepository(db)
        result = await repo.bot_latency_24h()
        assert result.avg_ms == 124

    async def test_returns_zeros_on_none_fields(self):
        """None fields on a present row are treated as 0."""
        row = _make_row(avg_ms=None, p95_ms=None, worst_ms=None, n=0)
        db = _make_db_first(row)
        repo = MetricsRepository(db)
        result = await repo.bot_latency_24h()
        assert result.avg_ms == 0
        assert result.p95_ms == 0
        assert result.worst_ms == 0


# ---------------------------------------------------------------------------
# provider_mix_24h
# ---------------------------------------------------------------------------

class TestProviderMix24h:
    async def test_separates_claude_and_gemini(self):
        """Claude and gemini counts are mapped correctly."""
        row = _make_row(claude=8, gemini=2)
        db = _make_db_first(row)
        repo = MetricsRepository(db)
        result = await repo.provider_mix_24h()
        assert isinstance(result, ProviderMix)
        assert result.claude == 8
        assert result.gemini == 2
        assert result.pct_fallback == pytest.approx(20.0)

    async def test_pct_fallback_zero_when_no_data(self):
        """No AI messages → pct_fallback is 0.0 (no division by zero)."""
        db = _make_db_first(None)
        repo = MetricsRepository(db)
        result = await repo.provider_mix_24h()
        assert result.claude == 0
        assert result.gemini == 0
        assert result.pct_fallback == 0.0

    async def test_pct_fallback_zero_when_row_zeros(self):
        """Row with claude=0, gemini=0 → pct_fallback is 0.0."""
        row = _make_row(claude=0, gemini=0)
        db = _make_db_first(row)
        repo = MetricsRepository(db)
        result = await repo.provider_mix_24h()
        assert result.pct_fallback == 0.0

    async def test_pct_fallback_100_when_all_gemini(self):
        """All gemini → pct_fallback = 100.0."""
        row = _make_row(claude=0, gemini=5)
        db = _make_db_first(row)
        repo = MetricsRepository(db)
        result = await repo.provider_mix_24h()
        assert result.pct_fallback == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# tool_iterations_24h
# ---------------------------------------------------------------------------

class TestToolIterations24h:
    async def test_computes_avg_max_bucket_counts(self):
        """Buckets and avg/max are taken from DB row (SQL computed the aggregates)."""
        # Represents tool_iterations = [0, 1, 3, 4, 5]
        # avg = 2.6, max = 5, zero_tools = 1, high_iters = 2
        row = _make_row(avg=2.6, max=5, zero_tools=1, high_iters=2, n=5)
        db = _make_db_first(row)
        repo = MetricsRepository(db)
        result = await repo.tool_iterations_24h()
        assert isinstance(result, ToolIterations)
        assert result.avg == pytest.approx(2.6)
        assert result.max == 5
        assert result.zero_tools == 1
        assert result.high_iters == 2
        assert result.n == 5

    async def test_handles_all_null(self):
        """No rows with tool_iterations NOT NULL → all zeros."""
        db = _make_db_first(None)
        repo = MetricsRepository(db)
        result = await repo.tool_iterations_24h()
        assert result.avg == 0.0
        assert result.max == 0
        assert result.zero_tools == 0
        assert result.high_iters == 0
        assert result.n == 0

    async def test_rounds_avg_to_two_decimals(self):
        """avg is rounded to 2 decimal places."""
        row = _make_row(avg=1.66666, max=3, zero_tools=0, high_iters=0, n=3)
        db = _make_db_first(row)
        repo = MetricsRepository(db)
        result = await repo.tool_iterations_24h()
        assert result.avg == pytest.approx(1.67)


# ---------------------------------------------------------------------------
# heartbeat_last_failure
# ---------------------------------------------------------------------------

class TestHeartbeatLastFailure:
    async def test_returns_none_when_no_failures(self):
        """No heartbeat error rows → 1-tuple of (None,)."""
        db = _make_db_first(None)
        repo = MetricsRepository(db)
        result = await repo.heartbeat_last_failure()
        assert result == (None,)

    async def test_returns_most_recent(self):
        """Returns the created_at of the most recent heartbeat row (limit 1, ordered desc)."""
        recent_ts = datetime(2026, 4, 18, 10, 0, 0, tzinfo=timezone.utc)
        # Simulate a row whose index-0 is the timestamp
        row = MagicMock()
        row.__getitem__ = lambda self, i: recent_ts if i == 0 else None
        db = _make_db_first(row)
        repo = MetricsRepository(db)
        (ts,) = await repo.heartbeat_last_failure()
        assert ts == recent_ts


# ---------------------------------------------------------------------------
# errors_by_workflow_24h
# ---------------------------------------------------------------------------

class TestErrorsByWorkflow24h:
    async def test_aggregates_correctly(self):
        """Rows are grouped into a dict and total is summed."""
        rows = [
            _make_row(workflow="whatsapp", cnt=3),
            _make_row(workflow="telegram", cnt=1),
            _make_row(workflow="heartbeat", cnt=2),
        ]
        db = _make_db_all(rows)
        repo = MetricsRepository(db)
        by_workflow, total = await repo.errors_by_workflow_24h()
        assert by_workflow == {"whatsapp": 3, "telegram": 1, "heartbeat": 2}
        assert total == 6

    async def test_returns_empty_dict_when_no_errors(self):
        """No error rows → empty dict and total=0."""
        db = _make_db_all([])
        repo = MetricsRepository(db)
        by_workflow, total = await repo.errors_by_workflow_24h()
        assert by_workflow == {}
        assert total == 0

    async def test_single_workflow(self):
        """Single workflow row produces a single-key dict."""
        rows = [_make_row(workflow="whatsapp", cnt=7)]
        db = _make_db_all(rows)
        repo = MetricsRepository(db)
        by_workflow, total = await repo.errors_by_workflow_24h()
        assert by_workflow == {"whatsapp": 7}
        assert total == 7


# ---------------------------------------------------------------------------
# Helpers for anthropic_api_calls rows
# ---------------------------------------------------------------------------

def _make_tracker_row(
    model: str = "claude-haiku-4-5",
    tokens_in: int = 0,
    tokens_out: int = 0,
    cache_creation_in: int = 0,
    cache_read_in: int = 0,
    cost_usd=None,
):
    """Simulate a DB row from anthropic_api_calls."""
    from decimal import Decimal
    from app.services.cost_config import compute_cost_usd

    row = MagicMock()
    row.model = model
    row.tokens_in = tokens_in
    row.tokens_out = tokens_out
    row.cache_creation_in = cache_creation_in
    row.cache_read_in = cache_read_in
    if cost_usd is not None:
        row.cost_usd = cost_usd
    else:
        row.cost_usd = compute_cost_usd(model, tokens_in, tokens_out, cache_creation_in, cache_read_in)
    return row


# ---------------------------------------------------------------------------
# ai_cost_today / ai_cost_month_to_date  (now read from anthropic_api_calls)
# ---------------------------------------------------------------------------

class TestAiCostMethods:

    def _make_db_all_rows(self, rows):
        """AsyncMock session returning given rows from .all()."""
        db = AsyncMock()
        result = MagicMock()
        result.all.return_value = rows
        db.execute = AsyncMock(return_value=result)
        return db

    async def test_empty_calls_returns_zero_cost(self):
        """No anthropic_api_calls rows → AiCost with all zeros."""
        db = self._make_db_all_rows([])
        repo = MetricsRepository(db)
        result = await repo.ai_cost_today()
        assert isinstance(result, AiCost)
        assert result.claude_usd == 0.0
        assert result.gemini_usd == 0.0
        assert result.total_usd == 0.0
        assert result.messages == 0

    async def test_claude_calls_attributed_correctly(self):
        """Claude calls go to claude_usd, not gemini_usd."""
        rows = [_make_tracker_row("claude-haiku-4-5", tokens_in=1_000_000, tokens_out=0)]
        db = self._make_db_all_rows(rows)
        repo = MetricsRepository(db)
        result = await repo.ai_cost_today()
        assert result.claude_usd > 0.0
        assert result.gemini_usd == 0.0
        assert result.messages == 1

    async def test_gemini_calls_attributed_correctly(self):
        """Gemini calls go to gemini_usd, not claude_usd."""
        rows = [_make_tracker_row("gemini-3-flash", tokens_in=1_000_000, tokens_out=0)]
        db = self._make_db_all_rows(rows)
        repo = MetricsRepository(db)
        result = await repo.ai_cost_today()
        assert result.gemini_usd > 0.0
        assert result.claude_usd == 0.0

    async def test_mixed_providers_split_correctly(self):
        """Mixed Claude + Gemini calls are split by provider."""
        rows = [
            _make_tracker_row("claude-haiku-4-5", tokens_in=100_000, tokens_out=50_000),
            _make_tracker_row("gemini-3-flash", tokens_in=100_000, tokens_out=50_000),
        ]
        db = self._make_db_all_rows(rows)
        repo = MetricsRepository(db)
        result = await repo.ai_cost_today()
        assert result.claude_usd > 0.0
        assert result.gemini_usd > 0.0
        assert result.messages == 2
        import math
        assert math.isclose(result.total_usd, result.claude_usd + result.gemini_usd, rel_tol=1e-6)

    async def test_month_to_date_delegates_correctly(self):
        """ai_cost_month_to_date uses a broader window (start of month)."""
        rows = [_make_tracker_row("claude-haiku-4-5", tokens_in=500, tokens_out=200)]
        db = self._make_db_all_rows(rows)
        repo = MetricsRepository(db)
        result = await repo.ai_cost_month_to_date()
        assert isinstance(result, AiCost)
        assert result.messages == 1

    async def test_ai_cost_today_uses_paraguay_midnight(self):
        """La ventana de "hoy" arranca a medianoche PYT, no a medianoche UTC.

        Tanda 12: el corte del panel es uno solo y es el paraguayo. Con
        medianoche UTC, las tres primeras horas del dia UTC —que en Paraguay
        son todavia ayer de 21:00 a 23:59— se sumaban al dia equivocado.
        """
        from unittest.mock import patch

        captured_calls: list[datetime] = []

        async def capturing_tracker_window(self_inner, window_start: datetime) -> AiCost:
            captured_calls.append(window_start)
            return AiCost(claude_usd=0.0, gemini_usd=0.0, total_usd=0.0, messages=0)

        db = self._make_db_all_rows([])
        repo = MetricsRepository(db)

        with patch.object(MetricsRepository, "_ai_cost_from_tracker", capturing_tracker_window):
            await repo.ai_cost_today()

        assert len(captured_calls) == 1
        window_start = captured_calls[0]
        assert window_start.utcoffset() != timedelta(0), (
            f"La ventana sigue anclada a UTC: {window_start}"
        )
        assert (window_start.hour, window_start.minute, window_start.second) == (0, 0, 0)
        assert window_start.astimezone(PYT).date() == datetime.now(PYT).date()


# ---------------------------------------------------------------------------
# ai_cost_by_day_last_7d
# ---------------------------------------------------------------------------

class TestAiCostByDayLast7d:

    def _make_db_day_rows(self, rows):
        db = AsyncMock()
        result = MagicMock()
        result.all.return_value = rows
        db.execute = AsyncMock(return_value=result)
        return db

    def _make_day_row(self, day_date, total_usd, tokens_in, tokens_out, calls,
                      cache_creation_in=0, cache_read_in=0):
        row = MagicMock()
        row.day = day_date
        row.total_usd = Decimal(str(total_usd))
        row.tokens_in = tokens_in
        row.tokens_out = tokens_out
        row.cache_creation_in = cache_creation_in
        row.cache_read_in = cache_read_in
        row.calls = calls
        return row

    async def test_returns_7_rows_always(self):
        """Always returns exactly 7 rows even when DB has no data."""
        db = self._make_db_day_rows([])
        repo = MetricsRepository(db)
        result = await repo.ai_cost_by_day_last_7d()
        assert len(result) == 7

    async def test_rows_ordered_oldest_first(self):
        """Returned rows go from oldest to most recent."""
        db = self._make_db_day_rows([])
        repo = MetricsRepository(db)
        result = await repo.ai_cost_by_day_last_7d()
        dates = [r.date for r in result]
        assert dates == sorted(dates)

    async def test_gap_days_have_zero_values(self):
        """Days with no DB rows are filled with zeros, not omitted."""
        db = self._make_db_day_rows([])
        repo = MetricsRepository(db)
        result = await repo.ai_cost_by_day_last_7d()
        for row in result:
            assert row.total_usd == 0.0
            assert row.calls == 0
            assert row.tokens_in == 0
            assert row.tokens_out == 0

    async def test_populated_day_is_included(self):
        """A day with data shows its aggregated values, not zeros."""
        hoy_pyt = datetime.now(PYT).date()
        day_row = self._make_day_row(hoy_pyt, 0.005, 5000, 2000, 3)
        db = self._make_db_day_rows([day_row])
        repo = MetricsRepository(db)
        result = await repo.ai_cost_by_day_last_7d()
        # Find today's entry
        today_entry = next((r for r in result if r.date == hoy_pyt), None)
        assert today_entry is not None
        assert today_entry.calls == 3
        assert today_entry.tokens_in == 5000

    async def test_returns_daily_ai_cost_schema(self):
        """Each row is a DailyAiCost instance."""
        db = self._make_db_day_rows([])
        repo = MetricsRepository(db)
        result = await repo.ai_cost_by_day_last_7d()
        for row in result:
            assert isinstance(row, DailyAiCost)


# ---------------------------------------------------------------------------
# ai_cost_by_source_today / _7d / _month
# ---------------------------------------------------------------------------

class TestAiCostBySource:

    def _make_db_source_rows(self, rows):
        db = AsyncMock()
        result = MagicMock()
        result.all.return_value = rows
        db.execute = AsyncMock(return_value=result)
        return db

    def _make_source_row(self, source, total_usd, calls, tokens_in, tokens_out):
        row = MagicMock()
        row.source = source
        row.total_usd = Decimal(str(total_usd))
        row.calls = calls
        row.tokens_in = tokens_in
        row.tokens_out = tokens_out
        return row

    async def test_today_groups_by_source(self):
        """ai_cost_by_source_today returns one SourceCost per source."""
        rows = [
            self._make_source_row("bot.orchestrator", 0.01, 10, 100_000, 20_000),
            self._make_source_row("property_classifier", 0.001, 2, 5_000, 1_000),
            self._make_source_row("bot.lead_profiler", 0.003, 3, 15_000, 5_000),
        ]
        db = self._make_db_source_rows(rows)
        repo = MetricsRepository(db)
        result = await repo.ai_cost_by_source_today()
        assert len(result) == 3
        sources = {r.source for r in result}
        assert sources == {"bot.orchestrator", "property_classifier", "bot.lead_profiler"}

    async def test_empty_returns_empty_list(self):
        """No rows → empty list, not None."""
        db = self._make_db_source_rows([])
        repo = MetricsRepository(db)
        result = await repo.ai_cost_by_source_today()
        assert result == []

    async def test_source_cost_schema_type(self):
        """Each result item is a SourceCost instance."""
        rows = [self._make_source_row("bot.orchestrator", 0.01, 10, 100_000, 20_000)]
        db = self._make_db_source_rows(rows)
        repo = MetricsRepository(db)
        result = await repo.ai_cost_by_source_today()
        assert all(isinstance(r, SourceCost) for r in result)

    async def test_7d_calls_correct_window(self):
        """ai_cost_by_source_last_7d delegates to _ai_cost_by_source_for_window."""
        from unittest.mock import patch

        captured: list = []

        async def capture(self_inner, since: datetime) -> list:
            captured.append(since)
            return []

        db = self._make_db_source_rows([])
        repo = MetricsRepository(db)

        with patch.object(MetricsRepository, "_ai_cost_by_source_for_window", capture):
            await repo.ai_cost_by_source_last_7d()

        assert len(captured) == 1
        # Window should be >= 6 days ago
        six_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
        assert captured[0] >= six_days_ago

    async def test_month_calls_correct_window(self):
        """ai_cost_by_source_month arranca el 1° del mes PARAGUAYO."""
        from unittest.mock import patch

        captured: list = []

        async def capture(self_inner, since: datetime) -> list:
            captured.append(since)
            return []

        db = self._make_db_source_rows([])
        repo = MetricsRepository(db)

        with patch.object(MetricsRepository, "_ai_cost_by_source_for_window", capture):
            await repo.ai_cost_by_source_month()

        assert len(captured) == 1
        # Dia 1 del mes en curso, medianoche PYT.
        hoy_pyt = datetime.now(PYT).date()
        assert captured[0].utcoffset() != timedelta(0)
        assert (captured[0].year, captured[0].month, captured[0].day) == (
            hoy_pyt.year, hoy_pyt.month, 1
        )
