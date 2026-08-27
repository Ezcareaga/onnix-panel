"""Metrics service — assembles the BotHealthSnapshot from repository queries.

No SQL here; all data access is delegated to MetricsRepository.
Business logic (e.g. computing last_failure_ago_seconds) lives here.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.repositories.metrics_repository import MetricsRepository
from app.schemas.metrics import (
    BotHealthSnapshot,
    Costs,
    CostTimeSeries,
    ErrorBreakdown,
    HeartbeatStatus,
    StuckConversations,
)
from app.services.twilio_usage_service import TwilioUsageService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class MetricsService:
    """Orchestrates repository calls and assembles a :class:`BotHealthSnapshot`."""

    def __init__(
        self,
        db: "AsyncSession",
        twilio: TwilioUsageService | None = None,
    ) -> None:
        self.repo = MetricsRepository(db)
        self.twilio = twilio or TwilioUsageService()

    async def get_bot_health(self) -> BotHealthSnapshot:
        """Return a complete bot health snapshot for the current moment.

        All time-window queries cover the last 24 hours.
        Safe to call with an empty database — all sub-schemas default to zeros.
        """
        now = datetime.now(timezone.utc)

        msg_vol = await self.repo.message_volume_24h()
        latency = await self.repo.bot_latency_24h()
        provider = await self.repo.provider_mix_24h()
        tools = await self.repo.tool_iterations_24h()
        (hb_last_failure,) = await self.repo.heartbeat_last_failure()
        by_workflow, errs_total = await self.repo.errors_by_workflow_24h()
        stuck = await self.repo.count_stuck_conversations()

        ai_today = await self.repo.ai_cost_today()
        ai_month = await self.repo.ai_cost_month_to_date()
        tw_today = await self.twilio.today_usd()
        tw_month = await self.twilio.this_month_usd()

        # Time-series: per-source attribution (Fase J)
        ai_by_day_7d = await self.repo.ai_cost_by_day_last_7d()
        ai_by_source_today = await self.repo.ai_cost_by_source_today()
        ai_by_source_7d = await self.repo.ai_cost_by_source_last_7d()
        ai_by_source_month = await self.repo.ai_cost_by_source_month()

        timeseries = CostTimeSeries(
            ai_by_day_7d=ai_by_day_7d,
            ai_by_source_today=ai_by_source_today,
            ai_by_source_7d=ai_by_source_7d,
            ai_by_source_month=ai_by_source_month,
        )

        costs = Costs(
            ai_today=ai_today,
            ai_month=ai_month,
            twilio_today=tw_today,
            twilio_month=tw_month,
            total_today_usd=round(ai_today.total_usd + tw_today.total_usd, 2),
            total_month_usd=round(ai_month.total_usd + tw_month.total_usd, 2),
            timeseries=timeseries,
        )

        last_failure_ago: int | None = (
            int((now - hb_last_failure).total_seconds())
            if hb_last_failure is not None
            else None
        )

        snapshot = BotHealthSnapshot(
            generated_at=now,
            stuck_conversations=StuckConversations(count=stuck),
            message_volume=msg_vol,
            latency=latency,
            provider_mix=provider,
            tool_iterations=tools,
            heartbeat=HeartbeatStatus(
                last_failure_at=hb_last_failure,
                last_failure_ago_seconds=last_failure_ago,
                next_expected_in_seconds=3600,
            ),
            errors=ErrorBreakdown(by_workflow=by_workflow, total=errs_total),
            costs=costs,
        )
        logger.info(
            "metrics_service: snapshot generated stuck=%d errors=%d "
            "ai_today_usd=%.4f twilio_today_usd=%.2f",
            stuck,
            errs_total,
            ai_today.total_usd,
            tw_today.total_usd,
        )
        return snapshot


metrics_service = MetricsService  # type: ignore[assignment]  # class reference for DI
