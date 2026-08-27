"""Tests for app/services/ai_metrics_service.py

All DB calls are mocked — no real database connection needed.
Uses the same AsyncMock + MagicMock pattern established in test_stats_service.py.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import ai_metrics_service
from app.services.ai_metrics_service import _get_pricing, MODEL_PRICING


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(**kwargs):
    """Build a mock DB row with attribute access."""
    row = MagicMock()
    for k, v in kwargs.items():
        setattr(row, k, v)
    return row


def _make_db(rows=None, first_row=None):
    """Return an AsyncMock session whose execute().fetchall() / .first() returns given data.

    Always sets first.return_value (including None) so MagicMock's default
    auto-generated attribute does not slip through.
    """
    db = AsyncMock()
    result = MagicMock()
    result.fetchall.return_value = rows if rows is not None else []
    result.first.return_value = first_row  # explicitly set — may be None
    db.execute = AsyncMock(return_value=result)
    return db


# ---------------------------------------------------------------------------
# _get_pricing — internal helper
# ---------------------------------------------------------------------------

class TestGetPricing:
    """Prefix-match lookup for MODEL_PRICING."""

    def test_exact_prefix_match(self):
        pricing = _get_pricing("claude-haiku-4-5")
        assert pricing is not None
        assert pricing["input_usd_per_mtok"] == 1.00

    def test_prefix_match_with_version_suffix(self):
        """'claude-haiku-4-5-20251001' must match 'claude-haiku-4-5' prefix."""
        pricing = _get_pricing("claude-haiku-4-5-20251001")
        assert pricing is not None
        assert pricing["output_usd_per_mtok"] == 5.00

    def test_gemini_flash_prefix_match(self):
        pricing = _get_pricing("gemini-flash")
        assert pricing is not None
        assert pricing["input_usd_per_mtok"] == 0.075

    def test_unknown_model_returns_none(self):
        pricing = _get_pricing("gpt-4o-unknown")
        assert pricing is None

    def test_empty_string_returns_none(self):
        assert _get_pricing("") is None


# ---------------------------------------------------------------------------
# get_last_7_days_tokens_by_day
# ---------------------------------------------------------------------------

class TestGetLast7DaysTokensByDay:
    """Groups token usage by day with correct sums."""

    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self):
        rows = [
            _make_row(day="2026-04-11", tokens_in=100, tokens_out=50, messages=3),
            _make_row(day="2026-04-12", tokens_in=200, tokens_out=80, messages=5),
        ]
        db = _make_db(rows=rows)
        result = await ai_metrics_service.get_last_7_days_tokens_by_day(db)
        assert len(result) == 2
        assert result[0]["date"] == "2026-04-11"
        assert result[0]["tokens_in"] == 100
        assert result[0]["tokens_out"] == 50
        assert result[0]["messages"] == 3

    @pytest.mark.asyncio
    async def test_groups_by_date(self):
        """Three rows across three days all appear in the result."""
        rows = [
            _make_row(day="2026-04-10", tokens_in=10, tokens_out=5, messages=1),
            _make_row(day="2026-04-11", tokens_in=20, tokens_out=10, messages=2),
            _make_row(day="2026-04-12", tokens_in=30, tokens_out=15, messages=3),
        ]
        db = _make_db(rows=rows)
        result = await ai_metrics_service.get_last_7_days_tokens_by_day(db)
        assert len(result) == 3
        dates = [r["date"] for r in result]
        assert "2026-04-10" in dates
        assert "2026-04-12" in dates

    @pytest.mark.asyncio
    async def test_empty_rows_returns_empty_list(self):
        db = _make_db(rows=[])
        result = await ai_metrics_service.get_last_7_days_tokens_by_day(db)
        assert result == []

    @pytest.mark.asyncio
    async def test_values_are_ints(self):
        rows = [_make_row(day="2026-04-17", tokens_in=1000, tokens_out=500, messages=10)]
        db = _make_db(rows=rows)
        result = await ai_metrics_service.get_last_7_days_tokens_by_day(db)
        assert isinstance(result[0]["tokens_in"], int)
        assert isinstance(result[0]["tokens_out"], int)
        assert isinstance(result[0]["messages"], int)


# ---------------------------------------------------------------------------
# get_avg_latency_ms
# ---------------------------------------------------------------------------

class TestGetAvgLatencyMs:
    """Average latency computation."""

    @pytest.mark.asyncio
    async def test_returns_zero_if_no_data(self):
        row = _make_row(avg_lat=None)
        db = _make_db(first_row=row)
        result = await ai_metrics_service.get_avg_latency_ms(db)
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_when_first_is_none(self):
        db = _make_db(first_row=None)
        result = await ai_metrics_service.get_avg_latency_ms(db)
        assert result == 0

    @pytest.mark.asyncio
    async def test_computes_mean_correctly(self):
        """avg_lat=1500 → returns 1500."""
        row = _make_row(avg_lat=1500.0)
        db = _make_db(first_row=row)
        result = await ai_metrics_service.get_avg_latency_ms(db)
        assert result == 1500

    @pytest.mark.asyncio
    async def test_rounds_to_int(self):
        """avg_lat=1234.7 rounds to 1235."""
        row = _make_row(avg_lat=1234.7)
        db = _make_db(first_row=row)
        result = await ai_metrics_service.get_avg_latency_ms(db)
        assert result == 1235

    @pytest.mark.asyncio
    async def test_returns_int_type(self):
        row = _make_row(avg_lat=800.0)
        db = _make_db(first_row=row)
        result = await ai_metrics_service.get_avg_latency_ms(db)
        assert type(result) is int


# ---------------------------------------------------------------------------
# get_cost_estimate_usd
# ---------------------------------------------------------------------------

class TestGetCostEstimateUsd:
    """Cost estimation from token counts and pricing constants."""

    @pytest.mark.asyncio
    async def test_known_model_cost_matches_pricing(self):
        """1000 input + 500 output tokens for claude-haiku-4-5.

        Cost = (1000/1M * 1.00) + (500/1M * 5.00) = 0.000001 + 0.0000025 = 0.0000035
        """
        rows = [
            _make_row(ai_model="claude-haiku-4-5", tokens_in=1000, tokens_out=500),
        ]
        db = _make_db(rows=rows)
        result = await ai_metrics_service.get_cost_estimate_usd(db)
        assert "claude-haiku-4-5" in result["per_model"]
        expected = (1000 / 1_000_000 * 1.00) + (500 / 1_000_000 * 5.00)
        assert abs(result["per_model"]["claude-haiku-4-5"] - expected) < 1e-9
        assert abs(result["total_usd"] - expected) < 1e-4

    @pytest.mark.asyncio
    async def test_prefix_match_for_versioned_model(self):
        """claude-haiku-4-5-20251001 must use claude-haiku-4-5 pricing."""
        rows = [
            _make_row(ai_model="claude-haiku-4-5-20251001", tokens_in=1_000_000, tokens_out=0),
        ]
        db = _make_db(rows=rows)
        result = await ai_metrics_service.get_cost_estimate_usd(db)
        assert result["total_usd"] == pytest.approx(1.00, abs=0.001)

    @pytest.mark.asyncio
    async def test_unknown_model_costs_zero_no_crash(self):
        """Unknown ai_model → cost 0, total_usd 0, no exception."""
        rows = [
            _make_row(ai_model="gpt-4o-unknown", tokens_in=500_000, tokens_out=100_000),
        ]
        db = _make_db(rows=rows)
        result = await ai_metrics_service.get_cost_estimate_usd(db)
        assert result["total_usd"] == 0.0
        assert result["per_model"]["gpt-4o-unknown"] == 0.0

    @pytest.mark.asyncio
    async def test_multiple_models_summed_correctly(self):
        """Two known models — per_model and total_usd are both correct."""
        rows = [
            _make_row(ai_model="claude-haiku-4-5", tokens_in=1_000_000, tokens_out=0),
            _make_row(ai_model="gemini-flash", tokens_in=1_000_000, tokens_out=0),
        ]
        db = _make_db(rows=rows)
        result = await ai_metrics_service.get_cost_estimate_usd(db)
        # Claude: 1M input * $1.00/MTok = $1.00
        # Gemini: 1M input * $0.075/MTok = $0.075
        assert result["per_model"]["claude-haiku-4-5"] == pytest.approx(1.00, abs=1e-6)
        assert result["per_model"]["gemini-flash"] == pytest.approx(0.075, abs=1e-6)
        assert result["total_usd"] == pytest.approx(1.075, abs=0.001)

    @pytest.mark.asyncio
    async def test_empty_rows_returns_zero(self):
        db = _make_db(rows=[])
        result = await ai_metrics_service.get_cost_estimate_usd(db)
        assert result == {"total_usd": 0.0, "per_model": {}}

    @pytest.mark.asyncio
    async def test_result_shape(self):
        db = _make_db(rows=[])
        result = await ai_metrics_service.get_cost_estimate_usd(db)
        assert "total_usd" in result
        assert "per_model" in result
        assert isinstance(result["per_model"], dict)
