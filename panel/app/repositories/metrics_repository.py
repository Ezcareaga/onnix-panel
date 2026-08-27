"""Metrics repository — raw data access for the bot health dashboard.

All queries are scoped to the last 24 hours unless otherwise noted.
Uses Python-side ``datetime.now(timezone.utc)`` for the cutoff so that
tests can assert exact results without depending on SQL ``NOW()``.

No business logic here — all computation lives in MetricsService.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta, date
from typing import TYPE_CHECKING

from sqlalchemy import select, func, case, text

from decimal import Decimal

from app.models.anthropic_api_call import AnthropicApiCall
from app.models.bot_error import BotError
from app.models.conversation import Conversation
from app.models.message import Message
from app.repositories.conversation_repo import ConversationRepository
from app.schemas.metrics import AiCost, DailyAiCost, Latency, MessageVolume, ProviderMix, SourceCost, ToolIterations
from app.services.cost_config import compute_cost_usd
from app.tz import PYT_SQL_ZONE, pyt_day_start, pyt_month_start

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_24H = timedelta(hours=24)


class MetricsRepository:
    """Query layer for bot health metrics.

    Accepts an ``AsyncSession`` so the service can pass in the request-scoped
    session from the dependency-injection chain.
    """

    def __init__(self, db: "AsyncSession") -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Stuck conversations
    # ------------------------------------------------------------------

    async def count_stuck_conversations(self) -> int:
        """Cuenta las conversaciones trabadas.

        El predicado NO vive aca: es ``ConversationRepository.stuck_clause()``,
        el mismo que filtra la lista de /conversations. Estaba escrito dos
        veces y un contador que dice 4 sobre una lista que muestra 6 es peor
        que no tener el contador.
        """
        result = await self.db.execute(
            select(func.count())
            .select_from(Conversation)
            .where(ConversationRepository.stuck_clause())
        )
        return result.scalar() or 0

    # ------------------------------------------------------------------
    # Message volume
    # ------------------------------------------------------------------

    async def message_volume_24h(self) -> MessageVolume:
        """Count inbound, bot-outbound, and agent-outbound messages in last 24h."""
        cutoff = datetime.now(timezone.utc) - _24H

        result = await self.db.execute(
            select(
                func.count(
                    case((Message.direction == "inbound", 1))
                ).label("inbound"),
                func.count(
                    case(
                        (
                            (Message.direction == "outbound") & (Message.sender_type == "bot"),
                            1,
                        )
                    )
                ).label("bot_out"),
                func.count(
                    case(
                        (
                            (Message.direction == "outbound") & (Message.sender_type == "agent"),
                            1,
                        )
                    )
                ).label("agent_out"),
                func.count().label("total"),
            ).where(Message.created_at >= cutoff)
        )
        row = result.first()
        if row is None:
            return MessageVolume(inbound=0, bot_out=0, agent_out=0, total=0)
        return MessageVolume(
            inbound=int(row.inbound or 0),
            bot_out=int(row.bot_out or 0),
            agent_out=int(row.agent_out or 0),
            total=int(row.total or 0),
        )

    # ------------------------------------------------------------------
    # Bot latency
    # ------------------------------------------------------------------

    async def bot_latency_24h(self) -> Latency:
        """Compute avg, p95, and worst ai_latency_ms for bot outbound messages in last 24h.

        Uses ``percentile_cont`` for the p95 calculation.
        Returns all zeros when no rows exist (no division-by-zero risk).
        """
        cutoff = datetime.now(timezone.utc) - _24H

        result = await self.db.execute(
            select(
                func.coalesce(func.avg(Message.ai_latency_ms), 0).label("avg_ms"),
                func.coalesce(
                    func.percentile_cont(0.95).within_group(
                        Message.ai_latency_ms.asc()
                    ),
                    0,
                ).label("p95_ms"),
                func.coalesce(func.max(Message.ai_latency_ms), 0).label("worst_ms"),
                func.count().label("n"),
            ).where(
                Message.direction == "outbound",
                Message.sender_type == "bot",
                Message.ai_latency_ms.is_not(None),
                Message.ai_latency_ms > 0,
                Message.created_at >= cutoff,
            )
        )
        row = result.first()
        if row is None:
            return Latency(avg_ms=0, p95_ms=0, worst_ms=0, n=0)
        return Latency(
            avg_ms=int(round(float(row.avg_ms or 0))),
            p95_ms=int(round(float(row.p95_ms or 0))),
            worst_ms=int(row.worst_ms or 0),
            n=int(row.n or 0),
        )

    # ------------------------------------------------------------------
    # Provider mix
    # ------------------------------------------------------------------

    async def provider_mix_24h(self) -> ProviderMix:
        """Count Claude vs Gemini messages in last 24h and compute fallback percentage.

        Claude is identified by ``ai_model LIKE 'claude%'``.
        Gemini is identified by ``ai_model LIKE 'gemini%'``.
        Division-by-zero is handled: returns 0.0 when total is 0.
        """
        cutoff = datetime.now(timezone.utc) - _24H

        result = await self.db.execute(
            select(
                func.count(
                    case(
                        (Message.ai_model.like("claude%"), 1),
                    )
                ).label("claude"),
                func.count(
                    case(
                        (Message.ai_model.like("gemini%"), 1),
                    )
                ).label("gemini"),
            ).where(
                Message.direction == "outbound",
                Message.sender_type == "bot",
                Message.ai_model.is_not(None),
                Message.ai_model != "",
                Message.created_at >= cutoff,
            )
        )
        row = result.first()
        claude = int(row.claude or 0) if row else 0
        gemini = int(row.gemini or 0) if row else 0
        total = claude + gemini
        pct_fallback = round(gemini / total * 100.0, 2) if total > 0 else 0.0
        return ProviderMix(claude=claude, gemini=gemini, pct_fallback=pct_fallback)

    # ------------------------------------------------------------------
    # Tool iterations
    # ------------------------------------------------------------------

    async def tool_iterations_24h(self) -> ToolIterations:
        """Aggregate tool_iterations stats for bot outbound messages in last 24h.

        Only considers rows where ``tool_iterations IS NOT NULL``.
        Returns all-zero schema when no such rows exist.
        """
        cutoff = datetime.now(timezone.utc) - _24H

        result = await self.db.execute(
            select(
                func.coalesce(func.avg(Message.tool_iterations), 0.0).label("avg"),
                func.coalesce(func.max(Message.tool_iterations), 0).label("max"),
                func.count(
                    case(
                        (Message.tool_iterations == 0, 1),
                    )
                ).label("zero_tools"),
                func.count(
                    case(
                        (Message.tool_iterations >= 4, 1),
                    )
                ).label("high_iters"),
                func.count().label("n"),
            ).where(
                Message.direction == "outbound",
                Message.sender_type == "bot",
                Message.tool_iterations.is_not(None),
                Message.created_at >= cutoff,
            )
        )
        row = result.first()
        if row is None:
            return ToolIterations(avg=0.0, max=0, zero_tools=0, high_iters=0, n=0)
        return ToolIterations(
            avg=round(float(row.avg or 0.0), 2),
            max=int(row.max or 0),
            zero_tools=int(row.zero_tools or 0),
            high_iters=int(row.high_iters or 0),
            n=int(row.n or 0),
        )

    # ------------------------------------------------------------------
    # Heartbeat last failure
    # ------------------------------------------------------------------

    async def heartbeat_last_failure(self) -> tuple[datetime | None]:
        """Return the ``created_at`` of the most recent heartbeat error row.

        Returns a 1-tuple ``(None,)`` when no heartbeat errors exist.
        """
        result = await self.db.execute(
            select(BotError.created_at)
            .where(BotError.workflow == "heartbeat")
            .order_by(BotError.created_at.desc())
            .limit(1)
        )
        row = result.first()
        last_failure: datetime | None = row[0] if row else None
        return (last_failure,)

    # ------------------------------------------------------------------
    # Errors by workflow
    # ------------------------------------------------------------------

    async def errors_by_workflow_24h(self) -> tuple[dict[str, int], int]:
        """Count bot_errors grouped by workflow for the last 24h.

        Returns ``(by_workflow_dict, total_int)``.
        """
        cutoff = datetime.now(timezone.utc) - _24H

        result = await self.db.execute(
            select(
                BotError.workflow,
                func.count().label("cnt"),
            )
            .where(BotError.created_at >= cutoff)
            .group_by(BotError.workflow)
        )
        rows = result.all()
        by_workflow: dict[str, int] = {row.workflow: int(row.cnt) for row in rows}
        total = sum(by_workflow.values())
        return by_workflow, total


    # ------------------------------------------------------------------
    # AI cost aggregations
    # ------------------------------------------------------------------

    async def _ai_cost_for_window(
        self,
        since: "datetime",
    ) -> AiCost:
        """Compute AI USD cost for bot messages created since *since* (UTC).

        Pulls (ai_model, ai_tokens_in, ai_tokens_out) rows and iterates in
        Python calling compute_cost_usd().  For ≤1000 rows/day this is fine;
        volume is a non-issue at current scale.
        """
        result = await self.db.execute(
            select(
                Message.ai_model,
                Message.ai_tokens_in,
                Message.ai_tokens_out,
            ).where(
                Message.sender_type == "bot",
                Message.ai_model.is_not(None),
                Message.created_at >= since,
            )
        )
        rows = result.all()

        claude_total = Decimal("0")
        gemini_total = Decimal("0")
        _quantize = Decimal("0.0001")

        for row in rows:
            cost = compute_cost_usd(row.ai_model, row.ai_tokens_in, row.ai_tokens_out)
            if row.ai_model and row.ai_model.lower().startswith("gemini"):
                gemini_total += cost
            else:
                claude_total += cost

        total = claude_total + gemini_total
        return AiCost(
            claude_usd=float(claude_total.quantize(_quantize)),
            gemini_usd=float(gemini_total.quantize(_quantize)),
            total_usd=float(total.quantize(_quantize)),
            messages=len(rows),
        )

    async def ai_cost_today(self) -> AiCost:
        """Sum AI USD cost from ``anthropic_api_calls`` for today (dia PYT).

        .. deprecated::
            Reads from ``anthropic_api_calls`` (single source of truth, Fase J).
            The old ``messages``-based implementation is preserved as
            ``_ai_cost_for_window`` for 1 milestone.
        """
        return await self._ai_cost_from_tracker(pyt_day_start())

    async def ai_cost_month_to_date(self) -> AiCost:
        """Sum AI USD cost desde el 1° del mes calendario PARAGUAYO.

        .. deprecated::
            Reads from ``anthropic_api_calls`` (single source of truth, Fase J).
            The old ``messages``-based implementation is preserved as
            ``_ai_cost_for_window`` for 1 milestone.
        """
        return await self._ai_cost_from_tracker(pyt_month_start())

    # ------------------------------------------------------------------
    # NEW: anthropic_api_calls — single source of truth (Fase J)
    # ------------------------------------------------------------------

    async def _ai_cost_from_tracker(self, since: datetime) -> AiCost:
        """Compute AiCost from ``anthropic_api_calls`` rows since *since* (UTC).

        Replaces the ``messages``-based ``_ai_cost_for_window`` as the primary
        cost source.  Falls back to zero gracefully when the table is empty
        (first deploy before any tracked calls).
        """
        result = await self.db.execute(
            select(
                AnthropicApiCall.model,
                AnthropicApiCall.tokens_in,
                AnthropicApiCall.tokens_out,
                AnthropicApiCall.cache_creation_in,
                AnthropicApiCall.cache_read_in,
                AnthropicApiCall.cost_usd,
            ).where(AnthropicApiCall.created_at >= since)
        )
        rows = result.all()

        claude_total = Decimal("0")
        gemini_total = Decimal("0")
        _quantize = Decimal("0.0001")

        for row in rows:
            if row.cost_usd is not None:
                cost = Decimal(str(row.cost_usd))
            else:
                cost = compute_cost_usd(
                    row.model,
                    row.tokens_in,
                    row.tokens_out,
                    cache_creation_in=row.cache_creation_in or 0,
                    cache_read_in=row.cache_read_in or 0,
                )
            model_lower = (row.model or "").lower()
            if model_lower.startswith("gemini"):
                gemini_total += cost
            else:
                claude_total += cost

        total = claude_total + gemini_total
        return AiCost(
            claude_usd=float(claude_total.quantize(_quantize)),
            gemini_usd=float(gemini_total.quantize(_quantize)),
            total_usd=float(total.quantize(_quantize)),
            messages=len(rows),
        )

    async def ai_cost_by_day_last_7d(self) -> list[DailyAiCost]:
        """Return 7 rows, one per dia calendario PYT (oldest first), con los de costo 0.

        Queries ``anthropic_api_calls``.  Days with no calls still appear with
        zeros so the dashboard time-series has a stable 7-point x-axis.

        El dia es el paraguayo, no el UTC: la card de costo y la factura de
        Anthropic —que factura en UTC— pueden no coincidir en los bordes.
        """
        since = pyt_day_start(days_ago=6)
        seven_days_ago = since.date()

        result = await self.db.execute(
            select(
                func.date(func.timezone(PYT_SQL_ZONE, AnthropicApiCall.created_at)).label("day"),
                func.sum(AnthropicApiCall.cost_usd).label("total_usd"),
                func.sum(AnthropicApiCall.tokens_in).label("tokens_in"),
                func.sum(AnthropicApiCall.tokens_out).label("tokens_out"),
                func.sum(AnthropicApiCall.cache_creation_in).label("cache_creation_in"),
                func.sum(AnthropicApiCall.cache_read_in).label("cache_read_in"),
                func.count().label("calls"),
            )
            .where(AnthropicApiCall.created_at >= since)
            .group_by(func.date(func.timezone(PYT_SQL_ZONE, AnthropicApiCall.created_at)))
            .order_by(func.date(func.timezone(PYT_SQL_ZONE, AnthropicApiCall.created_at)))
        )
        rows = result.all()

        # Build a lookup by date string so we can fill gaps
        by_day: dict[date, DailyAiCost] = {}
        for row in rows:
            # func.date() returns a Python date or a string depending on driver
            row_date = row.day if isinstance(row.day, date) else date.fromisoformat(str(row.day))
            by_day[row_date] = DailyAiCost(
                date=row_date,
                total_usd=float(row.total_usd or 0),
                tokens_in=int(row.tokens_in or 0),
                tokens_out=int(row.tokens_out or 0),
                cache_creation_in=int(row.cache_creation_in or 0),
                cache_read_in=int(row.cache_read_in or 0),
                calls=int(row.calls or 0),
            )

        # Return all 7 days, filling gaps with zeros
        result_list: list[DailyAiCost] = []
        for offset in range(7):
            day = seven_days_ago + timedelta(days=offset)
            result_list.append(
                by_day.get(
                    day,
                    DailyAiCost(
                        date=day,
                        total_usd=0.0,
                        tokens_in=0,
                        tokens_out=0,
                        cache_creation_in=0,
                        cache_read_in=0,
                        calls=0,
                    ),
                )
            )
        return result_list

    async def _ai_cost_by_source_for_window(
        self,
        since: datetime,
    ) -> list[SourceCost]:
        """Group ``anthropic_api_calls`` by source for rows created since *since*."""
        result = await self.db.execute(
            select(
                AnthropicApiCall.source,
                func.sum(AnthropicApiCall.cost_usd).label("total_usd"),
                func.count().label("calls"),
                func.sum(AnthropicApiCall.tokens_in).label("tokens_in"),
                func.sum(AnthropicApiCall.tokens_out).label("tokens_out"),
            )
            .where(AnthropicApiCall.created_at >= since)
            .group_by(AnthropicApiCall.source)
            .order_by(func.sum(AnthropicApiCall.cost_usd).desc())
        )
        rows = result.all()
        return [
            SourceCost(
                source=row.source,
                total_usd=float(row.total_usd or 0),
                calls=int(row.calls or 0),
                tokens_in=int(row.tokens_in or 0),
                tokens_out=int(row.tokens_out or 0),
            )
            for row in rows
        ]

    async def ai_cost_by_source_today(self) -> list[SourceCost]:
        """Return cost grouped by source for the current dia calendario PYT."""
        return await self._ai_cost_by_source_for_window(pyt_day_start())

    async def ai_cost_by_source_month(self) -> list[SourceCost]:
        """Return cost grouped by source for the current mes calendario PYT."""
        return await self._ai_cost_by_source_for_window(pyt_month_start())

    async def ai_cost_by_source_last_7d(self) -> list[SourceCost]:
        """Return cost grouped by source for the last 7 dias calendario PYT."""
        return await self._ai_cost_by_source_for_window(pyt_day_start(days_ago=6))


metrics_repository = MetricsRepository  # type: ignore[assignment]  # class reference for DI
