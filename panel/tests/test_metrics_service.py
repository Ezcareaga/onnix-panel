"""Tests for app/services/metrics_service.py

All repository calls are mocked — no real database connection needed.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.metrics_service import MetricsService
from app.schemas.metrics import (
    AiCost,
    BotHealthSnapshot,
    Costs,
    CostTimeSeries,
    ErrorBreakdown,
    HeartbeatStatus,
    Latency,
    MessageVolume,
    ProviderMix,
    StuckConversations,
    ToolIterations,
    TwilioUsage,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _empty_snapshot_kwargs() -> dict:
    """Keyword args for a fully-zeroed BotHealthSnapshot."""
    return dict(
        msg_vol=MessageVolume(inbound=0, bot_out=0, agent_out=0, total=0),
        latency=Latency(avg_ms=0, p95_ms=0, worst_ms=0, n=0),
        provider=ProviderMix(claude=0, gemini=0, pct_fallback=0.0),
        tools=ToolIterations(avg=0.0, max=0, zero_tools=0, high_iters=0, n=0),
        hb_failure=None,
        by_workflow={},
        errs_total=0,
        stuck=0,
    )


def _zero_ai_cost() -> AiCost:
    return AiCost(claude_usd=0.0, gemini_usd=0.0, total_usd=0.0, messages=0)


def _zero_twilio() -> TwilioUsage:
    return TwilioUsage(total_usd=0.0, whatsapp_usd=0.0, other_usd=0.0)


def _patch_repo(
    msg_vol: MessageVolume,
    latency: Latency,
    provider: ProviderMix,
    tools: ToolIterations,
    hb_failure: datetime | None,
    by_workflow: dict,
    errs_total: int,
    stuck: int,
    ai_cost: AiCost | None = None,
):
    """Build a mock MetricsRepository instance with given return values."""
    repo = MagicMock()
    repo.message_volume_24h = AsyncMock(return_value=msg_vol)
    repo.bot_latency_24h = AsyncMock(return_value=latency)
    repo.provider_mix_24h = AsyncMock(return_value=provider)
    repo.tool_iterations_24h = AsyncMock(return_value=tools)
    repo.heartbeat_last_failure = AsyncMock(return_value=(hb_failure,))
    repo.errors_by_workflow_24h = AsyncMock(return_value=(by_workflow, errs_total))
    repo.count_stuck_conversations = AsyncMock(return_value=stuck)
    repo.ai_cost_today = AsyncMock(return_value=ai_cost or _zero_ai_cost())
    repo.ai_cost_month_to_date = AsyncMock(return_value=ai_cost or _zero_ai_cost())
    # Time-series (Fase J)
    repo.ai_cost_by_day_last_7d = AsyncMock(return_value=[])
    repo.ai_cost_by_source_today = AsyncMock(return_value=[])
    repo.ai_cost_by_source_last_7d = AsyncMock(return_value=[])
    repo.ai_cost_by_source_month = AsyncMock(return_value=[])
    return repo


def _patch_twilio(
    today: TwilioUsage | None = None,
    month: TwilioUsage | None = None,
) -> MagicMock:
    """Build a mock TwilioUsageService."""
    svc = MagicMock()
    svc.today_usd = AsyncMock(return_value=today or _zero_twilio())
    svc.this_month_usd = AsyncMock(return_value=month or _zero_twilio())
    return svc


# ---------------------------------------------------------------------------
# Snapshot structure
# ---------------------------------------------------------------------------

def _make_svc(kwargs: dict, twilio_today=None, twilio_month=None) -> MetricsService:
    """Build a MetricsService with all dependencies mocked."""
    repo = _patch_repo(**kwargs)
    twilio = _patch_twilio(
        today=twilio_today or _zero_twilio(),
        month=twilio_month or _zero_twilio(),
    )
    db = AsyncMock()
    svc = MetricsService(db, twilio=twilio)
    svc.repo = repo
    return svc


class TestGetBotHealthReturnsCompleteSnapshot:
    async def test_returns_bot_health_snapshot_type(self):
        """get_bot_health() returns a BotHealthSnapshot instance."""
        result = await _make_svc(_empty_snapshot_kwargs()).get_bot_health()
        assert isinstance(result, BotHealthSnapshot)

    async def test_all_sub_schemas_are_present(self):
        """Every sub-schema on the snapshot is populated (not None)."""
        result = await _make_svc(_empty_snapshot_kwargs()).get_bot_health()
        assert isinstance(result.stuck_conversations, StuckConversations)
        assert isinstance(result.message_volume, MessageVolume)
        assert isinstance(result.latency, Latency)
        assert isinstance(result.provider_mix, ProviderMix)
        assert isinstance(result.tool_iterations, ToolIterations)
        assert isinstance(result.heartbeat, HeartbeatStatus)
        assert isinstance(result.errors, ErrorBreakdown)
        assert isinstance(result.costs, Costs)
        assert isinstance(result.costs.timeseries, CostTimeSeries)

    async def test_generated_at_is_utc_datetime(self):
        """generated_at is a UTC-aware datetime close to now."""
        before = datetime.now(timezone.utc)
        result = await _make_svc(_empty_snapshot_kwargs()).get_bot_health()
        after = datetime.now(timezone.utc)
        assert before <= result.generated_at <= after


# ---------------------------------------------------------------------------
# Empty database
# ---------------------------------------------------------------------------

class TestGetBotHealthWithEmptyDb:
    async def test_all_zeros_no_panic(self):
        """Empty DB produces all-zero sub-schemas without errors."""
        result = await _make_svc(_empty_snapshot_kwargs()).get_bot_health()
        assert result.stuck_conversations.count == 0
        assert result.message_volume.total == 0
        assert result.latency.n == 0
        assert result.latency.avg_ms == 0
        assert result.provider_mix.pct_fallback == 0.0
        assert result.tool_iterations.n == 0
        assert result.heartbeat.last_failure_at is None
        assert result.heartbeat.last_failure_ago_seconds is None
        assert result.errors.total == 0
        assert result.errors.by_workflow == {}
        assert result.costs.total_today_usd == 0.0
        assert result.costs.total_month_usd == 0.0

    async def test_heartbeat_none_when_no_failures(self):
        """last_failure_at and last_failure_ago_seconds are None when no failures."""
        result = await _make_svc(_empty_snapshot_kwargs()).get_bot_health()
        assert result.heartbeat.last_failure_at is None
        assert result.heartbeat.last_failure_ago_seconds is None


# ---------------------------------------------------------------------------
# Populated data
# ---------------------------------------------------------------------------

class TestGetBotHealthWithData:
    async def test_heartbeat_ago_computed_correctly(self):
        """last_failure_ago_seconds is the integer delta from now to the failure time."""
        failure_time = datetime.now(timezone.utc) - timedelta(seconds=120)
        kwargs = _empty_snapshot_kwargs()
        kwargs["hb_failure"] = failure_time
        result = await _make_svc(kwargs).get_bot_health()
        assert result.heartbeat.last_failure_at == failure_time
        assert abs(result.heartbeat.last_failure_ago_seconds - 120) <= 2

    async def test_errors_breakdown_propagated(self):
        """Error breakdown dict and total are passed through correctly."""
        kwargs = _empty_snapshot_kwargs()
        kwargs["by_workflow"] = {"whatsapp": 4, "telegram": 1}
        kwargs["errs_total"] = 5
        result = await _make_svc(kwargs).get_bot_health()
        assert result.errors.by_workflow == {"whatsapp": 4, "telegram": 1}
        assert result.errors.total == 5

    async def test_stuck_conversations_propagated(self):
        """stuck count is placed in stuck_conversations.count."""
        kwargs = _empty_snapshot_kwargs()
        kwargs["stuck"] = 7
        result = await _make_svc(kwargs).get_bot_health()
        assert result.stuck_conversations.count == 7

    async def test_next_expected_in_seconds_always_3600(self):
        """next_expected_in_seconds is always 3600 (1 heartbeat per hour)."""
        result = await _make_svc(_empty_snapshot_kwargs()).get_bot_health()
        assert result.heartbeat.next_expected_in_seconds == 3600

    async def test_costs_block_totals_are_summed(self):
        """total_today_usd = round(ai_today + twilio_today, 2); month likewise."""
        # Use round numbers to avoid banker's rounding edge cases in the assertion
        ai_cost = AiCost(claude_usd=0.01, gemini_usd=0.00, total_usd=0.01, messages=5)
        twilio_today = TwilioUsage(total_usd=1.50, whatsapp_usd=1.20, other_usd=0.30)
        twilio_month = TwilioUsage(total_usd=12.00, whatsapp_usd=10.00, other_usd=2.00)
        kwargs = _empty_snapshot_kwargs()
        kwargs["ai_cost"] = ai_cost
        result = await _make_svc(kwargs, twilio_today=twilio_today, twilio_month=twilio_month).get_bot_health()
        assert result.costs.total_today_usd == pytest.approx(1.51, abs=0.001)
        assert result.costs.twilio_today.total_usd == 1.50
        assert result.costs.twilio_month.total_usd == 12.00


# ---------------------------------------------------------------------------
# JSON serialisability
# ---------------------------------------------------------------------------

class TestGetBotHealthJsonSerializable:
    async def test_model_dump_json_succeeds(self):
        """model_dump_json() must not raise."""
        result = await _make_svc(_empty_snapshot_kwargs()).get_bot_health()
        json_str = result.model_dump_json()
        assert isinstance(json_str, str)
        assert len(json_str) > 10

    async def test_model_dump_json_with_heartbeat_failure(self):
        """model_dump_json() succeeds when last_failure_at is a real datetime."""
        failure_time = datetime(2026, 4, 18, 9, 0, 0, tzinfo=timezone.utc)
        kwargs = _empty_snapshot_kwargs()
        kwargs["hb_failure"] = failure_time
        result = await _make_svc(kwargs).get_bot_health()
        json_str = result.model_dump_json()
        assert "2026-04-18" in json_str
