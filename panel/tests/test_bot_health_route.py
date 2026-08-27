"""Tests for GET /stats/health — unified bot health + AI metrics dashboard route.

Uses admin_client / user_client / client fixtures from conftest.py.
MetricsService and ai_metrics_service are mocked so tests do not depend on
live DB data.
"""
from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from datetime import date

from app.schemas.metrics import (
    AiCost,
    BotHealthSnapshot,
    Costs,
    CostTimeSeries,
    DailyAiCost,
    ErrorBreakdown,
    HeartbeatStatus,
    Latency,
    MessageVolume,
    ProviderMix,
    SourceCost,
    StuckConversations,
    ToolIterations,
    TwilioUsage,
)

_SVC = "app.routes.bot_health.MetricsService"
_AI_SVC = "app.routes.bot_health.ai_metrics_service"

_EMPTY_COST_ESTIMATE = {"total_usd": 0.0, "per_model": {}}


def _zero_costs() -> Costs:
    ai = AiCost(claude_usd=0.0, gemini_usd=0.0, total_usd=0.0, messages=0)
    tw = TwilioUsage(total_usd=0.0, whatsapp_usd=0.0, other_usd=0.0)
    return Costs(
        ai_today=ai, ai_month=ai,
        twilio_today=tw, twilio_month=tw,
        total_today_usd=0.0, total_month_usd=0.0,
    )


def _make_snapshot(**overrides) -> BotHealthSnapshot:
    """Return a minimal valid BotHealthSnapshot for route tests."""
    defaults = dict(
        generated_at=datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc),
        stuck_conversations=StuckConversations(count=0),
        message_volume=MessageVolume(inbound=10, bot_out=8, agent_out=2, total=20),
        latency=Latency(avg_ms=1200, p95_ms=1800, worst_ms=2400, n=8),
        provider_mix=ProviderMix(claude=8, gemini=0, pct_fallback=0.0),
        tool_iterations=ToolIterations(avg=1.5, max=3, zero_tools=1, high_iters=0, n=8),
        heartbeat=HeartbeatStatus(
            last_failure_at=None,
            last_failure_ago_seconds=None,
            next_expected_in_seconds=3600,
        ),
        errors=ErrorBreakdown(by_workflow={}, total=0),
        costs=_zero_costs(),
    )
    defaults.update(overrides)
    return BotHealthSnapshot(**defaults)


def _patch_services(
    snapshot: BotHealthSnapshot | None = None,
    tokens_by_day=None,
    avg_latency=0,
    cost_estimate=None,
):
    """Patch MetricsService and ai_metrics_service so no DB query is issued."""
    if snapshot is None:
        snapshot = _make_snapshot()

    mock_instance = MagicMock()
    mock_instance.get_bot_health = AsyncMock(return_value=snapshot)
    mock_cls = MagicMock(return_value=mock_instance)

    stack = ExitStack()
    stack.enter_context(patch(_SVC, new=mock_cls))
    stack.enter_context(
        patch(
            f"{_AI_SVC}.get_last_7_days_tokens_by_day",
            new=AsyncMock(return_value=tokens_by_day or []),
        )
    )
    stack.enter_context(
        patch(
            f"{_AI_SVC}.get_avg_latency_ms",
            new=AsyncMock(return_value=avg_latency),
        )
    )
    stack.enter_context(
        patch(
            f"{_AI_SVC}.get_cost_estimate_usd",
            new=AsyncMock(return_value=cost_estimate or _EMPTY_COST_ESTIMATE),
        )
    )
    return stack


# ---------------------------------------------------------------------------
# Auth gate — unauthenticated request must redirect
# ---------------------------------------------------------------------------

class TestBotHealthAuth:

    @pytest.mark.asyncio
    async def test_bot_health_unauth_redirects(self, client):
        with _patch_services():
            resp = await client.get("/stats/health", follow_redirects=False)
        assert resp.status_code in (303, 307)
        assert "/login" in resp.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_bot_health_non_admin_forbidden(self, user_client):
        with _patch_services():
            resp = await user_client.get("/stats/health")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Admin access
# ---------------------------------------------------------------------------

class TestBotHealthAdminAccess:

    @pytest.mark.asyncio
    async def test_bot_health_admin_gets_200(self, admin_client):
        with _patch_services():
            resp = await admin_client.get("/stats/health")
        assert resp.status_code == 200
        assert b"Salud del Bot" in resp.content

    @pytest.mark.asyncio
    async def test_all_eight_cards_rendered(self, admin_client):
        with _patch_services():
            resp = await admin_client.get("/stats/health")
        assert resp.status_code == 200
        assert b"Conversaciones trabadas" in resp.content
        assert b"Mensajes 24h" in resp.content
        assert b"Velocidad" in resp.content
        assert b"Uso de IA" in resp.content
        assert b"Costo IA" in resp.content
        assert b"Costo Twilio" in resp.content
        assert b"Heartbeat" in resp.content
        assert b"Errores" in resp.content
        # Tool-use card removed from UI
        assert b"Tool-use iters" not in resp.content
        # No jargon words like "Fallback" in the UI labels
        assert b"Fallback (Gemini)" not in resp.content

    @pytest.mark.asyncio
    async def test_polling_attributes_present(self, admin_client):
        with _patch_services():
            resp = await admin_client.get("/stats/health")
        assert resp.status_code == 200
        assert b'hx-trigger="every 30s"' in resp.content
        # El poll arrastra `days`: sin eso el route lo defaultea a 7 y la vista
        # vuelve sola de 90 a 7 dias. Ver tests/test_htmx_request_params.py.
        assert b'hx-get="/stats/health?days=7"' in resp.content

    @pytest.mark.asyncio
    async def test_base_html_included_on_full_page(self, admin_client):
        with _patch_services():
            resp = await admin_client.get("/stats/health")
        assert resp.status_code == 200
        # Full page must include the base layout
        assert b"<html" in resp.content.lower()


# ---------------------------------------------------------------------------
# Tab navigation
# ---------------------------------------------------------------------------

class TestBotHealthTabNav:

    @pytest.mark.asyncio
    async def test_dashboard_includes_tab_nav(self, admin_client):
        with _patch_services():
            resp = await admin_client.get("/stats/health")
        assert resp.status_code == 200
        assert b"Resumen" in resp.content
        assert "Detalle t\u00e9cnico".encode() in resp.content

    @pytest.mark.asyncio
    async def test_dashboard_tab_param_sets_default_resumen(self, admin_client):
        with _patch_services():
            resp = await admin_client.get("/stats/health?tab=resumen")
        assert resp.status_code == 200
        # Alpine init value should be 'resumen'
        assert b"tab: 'resumen'" in resp.content

    @pytest.mark.asyncio
    async def test_dashboard_tab_param_sets_detalle(self, admin_client):
        with _patch_services():
            resp = await admin_client.get("/stats/health?tab=detalle")
        assert resp.status_code == 200
        # Alpine init value should be 'detalle'
        assert b"tab: 'detalle'" in resp.content

    @pytest.mark.asyncio
    async def test_dashboard_invalid_tab_defaults_to_resumen(self, admin_client):
        with _patch_services():
            resp = await admin_client.get("/stats/health?tab=invalid")
        assert resp.status_code == 200
        assert b"tab: 'resumen'" in resp.content

    @pytest.mark.asyncio
    async def test_dashboard_includes_tokens_metrics(self, admin_client):
        """Tab 2 content (Detalle técnico) is present in the full page response."""
        with _patch_services():
            resp = await admin_client.get("/stats/health")
        assert resp.status_code == 200
        assert b"Tokens entrada" in resp.content


# ---------------------------------------------------------------------------
# HTMX partial detection
# ---------------------------------------------------------------------------

class TestBotHealthHtmxPartial:

    @pytest.mark.asyncio
    async def test_htmx_returns_partial_only(self, admin_client):
        with _patch_services():
            resp = await admin_client.get(
                "/stats/health", headers={"HX-Request": "true"}
            )
        assert resp.status_code == 200
        # Partial must NOT include the outer HTML document
        assert b"<html" not in resp.content.lower()

    @pytest.mark.asyncio
    async def test_htmx_partial_still_contains_cards(self, admin_client):
        with _patch_services():
            resp = await admin_client.get(
                "/stats/health", headers={"HX-Request": "true"}
            )
        assert resp.status_code == 200
        assert b"Conversaciones trabadas" in resp.content
        assert b"Mensajes 24h" in resp.content

    @pytest.mark.asyncio
    async def test_htmx_partial_contains_both_tab_panels(self, admin_client):
        """The inner partial must include content from both tabs so Alpine can switch."""
        with _patch_services():
            resp = await admin_client.get(
                "/stats/health", headers={"HX-Request": "true"}
            )
        assert resp.status_code == 200
        # Resumen tab content
        assert b"Conversaciones trabadas" in resp.content
        # Detalle tab content
        assert b"Tokens entrada" in resp.content


# ---------------------------------------------------------------------------
# Semaphore colours — spot checks
# ---------------------------------------------------------------------------

class TestBotHealthSemaphores:

    @pytest.mark.asyncio
    async def test_stuck_green_when_zero(self, admin_client):
        snap = _make_snapshot(stuck_conversations=StuckConversations(count=0))
        with _patch_services(snap):
            resp = await admin_client.get("/stats/health")
        assert b"text-green-600" in resp.content

    @pytest.mark.asyncio
    async def test_stuck_red_when_high(self, admin_client):
        snap = _make_snapshot(stuck_conversations=StuckConversations(count=5))
        with _patch_services(snap):
            resp = await admin_client.get("/stats/health")
        assert b"text-red-500" in resp.content

    @pytest.mark.asyncio
    async def test_latency_no_data_shows_dash(self, admin_client):
        snap = _make_snapshot(
            latency=Latency(avg_ms=0, p95_ms=0, worst_ms=0, n=0)
        )
        with _patch_services(snap):
            resp = await admin_client.get("/stats/health")
        assert b"Sin datos" in resp.content

    @pytest.mark.asyncio
    async def test_errors_red_when_many(self, admin_client):
        snap = _make_snapshot(
            errors=ErrorBreakdown(by_workflow={"whatsapp": 15}, total=15)
        )
        with _patch_services(snap):
            resp = await admin_client.get("/stats/health")
        assert b"text-red-500" in resp.content

    @pytest.mark.asyncio
    async def test_heartbeat_ok_label_shown(self, admin_client):
        snap = _make_snapshot(
            heartbeat=HeartbeatStatus(
                last_failure_at=None,
                last_failure_ago_seconds=None,
                next_expected_in_seconds=3600,
            )
        )
        with _patch_services(snap):
            resp = await admin_client.get("/stats/health")
        assert b"OK" in resp.content

    @pytest.mark.asyncio
    async def test_heartbeat_failure_label_shown(self, admin_client):
        snap = _make_snapshot(
            heartbeat=HeartbeatStatus(
                last_failure_at=datetime(2026, 4, 18, 11, 0, 0, tzinfo=timezone.utc),
                last_failure_ago_seconds=600,  # 10 min — below 3600 threshold
                next_expected_in_seconds=3600,
            )
        )
        with _patch_services(snap):
            resp = await admin_client.get("/stats/health")
        assert b"Fallo" in resp.content


# ---------------------------------------------------------------------------
# Time-series UI — sparkline + attribution table
# ---------------------------------------------------------------------------

def _costs_with_timeseries(days: list[DailyAiCost] | None = None) -> Costs:
    """Return a Costs object with populated timeseries."""
    ai = AiCost(claude_usd=0.0, gemini_usd=0.0, total_usd=0.0, messages=0)
    tw = TwilioUsage(total_usd=0.0, whatsapp_usd=0.0, other_usd=0.0)
    ts = CostTimeSeries(
        ai_by_day_7d=days or [],
        ai_by_source_today=[
            SourceCost(source="bot", total_usd=0.0012, calls=3, tokens_in=900, tokens_out=300),
            SourceCost(source="classifier", total_usd=0.0003, calls=1, tokens_in=200, tokens_out=50),
        ],
        ai_by_source_7d=[],
        ai_by_source_month=[],
    )
    return Costs(
        ai_today=ai, ai_month=ai,
        twilio_today=tw, twilio_month=tw,
        total_today_usd=0.0, total_month_usd=0.0,
        timeseries=ts,
    )


def _seven_days(total_usd: float = 0.05) -> list[DailyAiCost]:
    """Return 7 DailyAiCost entries ascending from 2026-04-12 to 2026-04-18."""
    return [
        DailyAiCost(
            date=date(2026, 4, 12 + i),
            total_usd=total_usd,
            tokens_in=100,
            tokens_out=50,
            calls=2,
        )
        for i in range(7)
    ]


class TestBotHealthTimeSeries:

    @pytest.mark.asyncio
    async def test_dashboard_includes_sparkline(self, admin_client):
        snap = _make_snapshot(costs=_costs_with_timeseries(_seven_days()))
        with _patch_services(snap):
            resp = await admin_client.get("/stats/health")
        assert resp.status_code == 200
        assert "Costo IA — últimos 7 días".encode() in resp.content

    @pytest.mark.asyncio
    async def test_dashboard_does_not_include_attribution_table(self, admin_client):
        """Attribution table was removed per Ez (2026-04-18) — too technical for dashboard."""
        snap = _make_snapshot(costs=_costs_with_timeseries(_seven_days()))
        with _patch_services(snap):
            resp = await admin_client.get("/stats/health")
        assert resp.status_code == 200
        assert "Dónde se queman los tokens".encode() not in resp.content

    @pytest.mark.asyncio
    async def test_dashboard_handles_empty_timeseries(self, admin_client):
        """All 7 days with total_usd=0 must not crash the template."""
        snap = _make_snapshot(costs=_costs_with_timeseries(_seven_days(total_usd=0.0)))
        with _patch_services(snap):
            resp = await admin_client.get("/stats/health")
        assert resp.status_code == 200
        # Sparkline section still renders (days list is non-empty)
        assert "Costo IA — últimos 7 días".encode() in resp.content
