"""Tests for GET /stats/ai — now a 301 redirect to /stats/health?tab=detalle.

Legacy tests that validated the old page content have been migrated to
test_bot_health_route.py (TestBotHealthTabNav / TestBotHealthHtmxPartial).
This file retains the redirect contract and the auth-gate behaviour.
"""
from __future__ import annotations

import pytest


class TestAiMetricsAuth:
    """Unauthenticated requests still get a 301 (redirect is auth-free)."""

    @pytest.mark.asyncio
    async def test_ai_metrics_page_redirects(self, client):
        resp = await client.get("/stats/ai", follow_redirects=False)
        assert resp.status_code == 301
        assert "/stats/health" in resp.headers.get("location", "")


class TestAiMetricsRedirect:
    """The endpoint permanently redirects all callers to the unified page."""

    @pytest.mark.asyncio
    async def test_admin_gets_301(self, admin_client):
        resp = await admin_client.get("/stats/ai", follow_redirects=False)
        assert resp.status_code == 301

    @pytest.mark.asyncio
    async def test_redirect_location_includes_tab_detalle(self, admin_client):
        resp = await admin_client.get("/stats/ai", follow_redirects=False)
        location = resp.headers.get("location", "")
        assert "tab=detalle" in location

    @pytest.mark.asyncio
    async def test_following_redirect_lands_on_health_page(self, admin_client):
        """Following the redirect with mocked services returns 200 on /stats/health."""
        from contextlib import ExitStack
        from unittest.mock import AsyncMock, MagicMock, patch
        from datetime import datetime, timezone
        from app.schemas.metrics import (
            AiCost, BotHealthSnapshot, Costs, ErrorBreakdown,
            HeartbeatStatus, Latency, MessageVolume, ProviderMix,
            StuckConversations, ToolIterations, TwilioUsage,
        )

        ai = AiCost(claude_usd=0.0, gemini_usd=0.0, total_usd=0.0, messages=0)
        tw = TwilioUsage(total_usd=0.0, whatsapp_usd=0.0, other_usd=0.0)
        snap = BotHealthSnapshot(
            generated_at=datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc),
            stuck_conversations=StuckConversations(count=0),
            message_volume=MessageVolume(inbound=0, bot_out=0, agent_out=0, total=0),
            latency=Latency(avg_ms=0, p95_ms=0, worst_ms=0, n=0),
            provider_mix=ProviderMix(claude=0, gemini=0, pct_fallback=0.0),
            tool_iterations=ToolIterations(avg=0.0, max=0, zero_tools=0, high_iters=0, n=0),
            heartbeat=HeartbeatStatus(
                last_failure_at=None,
                last_failure_ago_seconds=None,
                next_expected_in_seconds=3600,
            ),
            errors=ErrorBreakdown(by_workflow={}, total=0),
            costs=Costs(
                ai_today=ai, ai_month=ai,
                twilio_today=tw, twilio_month=tw,
                total_today_usd=0.0, total_month_usd=0.0,
            ),
        )

        mock_instance = MagicMock()
        mock_instance.get_bot_health = AsyncMock(return_value=snap)
        mock_cls = MagicMock(return_value=mock_instance)

        with ExitStack() as stack:
            stack.enter_context(patch("app.routes.bot_health.MetricsService", new=mock_cls))
            stack.enter_context(
                patch(
                    "app.routes.bot_health.ai_metrics_service.get_last_7_days_tokens_by_day",
                    new=AsyncMock(return_value=[]),
                )
            )
            stack.enter_context(
                patch(
                    "app.routes.bot_health.ai_metrics_service.get_avg_latency_ms",
                    new=AsyncMock(return_value=0),
                )
            )
            stack.enter_context(
                patch(
                    "app.routes.bot_health.ai_metrics_service.get_cost_estimate_usd",
                    new=AsyncMock(return_value={"total_usd": 0.0, "per_model": {}}),
                )
            )
            resp = await admin_client.get("/stats/ai", follow_redirects=True)

        assert resp.status_code == 200
        assert b"Salud del Bot" in resp.content
