"""AI metrics service — token usage, latency, and estimated cost.

Queries the messages table for bot-generated rows that have ai_tokens_in /
ai_tokens_out / ai_latency_ms / ai_model populated.  Pricing constants are
defined at module level; use prefix-match so minor Anthropic/Google version
suffixes do not break lookups.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pricing constants — per 1 million tokens (USD).
# Review when Anthropic/Google update pricing.
# Keys are model prefixes; actual stored values may include date suffixes
# (e.g. "claude-haiku-4-5-20251001").  Match is prefix-based.
# ---------------------------------------------------------------------------
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5": {
        "input_usd_per_mtok": 1.00,
        "output_usd_per_mtok": 5.00,
    },
    "gemini-flash": {
        "input_usd_per_mtok": 0.075,
        "output_usd_per_mtok": 0.30,
    },
    "gemini-2.5-flash": {
        "input_usd_per_mtok": 0.075,
        "output_usd_per_mtok": 0.30,
    },
    "gemini-3-flash": {
        "input_usd_per_mtok": 0.075,
        "output_usd_per_mtok": 0.30,
    },
}


def _get_pricing(model: str) -> dict[str, float] | None:
    """Return pricing dict for *model* using prefix matching.

    Returns ``None`` for unknown models (caller logs a warning and skips cost).
    """
    for prefix, pricing in MODEL_PRICING.items():
        if model.startswith(prefix):
            return pricing
    return None


async def get_last_7_days_tokens_by_day(
    db: "AsyncSession",
    days: int = 7,
) -> list[dict]:
    """Return daily token totals for the last *days* days.

    Each entry: ``{date, tokens_in, tokens_out, messages}``.
    Los dias son dias calendario PARAGUAYOS, como el resto de las series del
    panel: bucketear en UTC mandaba al dia siguiente todo lo enviado despues
    de las 21:00 locales. La ventana sigue siendo movil (*days* x 24 h).
    Results are ordered chronologically (oldest first).
    """
    # SQLAlchemy text() does not support INTERVAL :param syntax directly —
    # use string interpolation for the integer days value (safe: int, not user string).
    sql = text(
        "SELECT "
        "  (created_at AT TIME ZONE 'America/Asuncion')::date AS day, "
        "  COALESCE(SUM(ai_tokens_in), 0)  AS tokens_in, "
        "  COALESCE(SUM(ai_tokens_out), 0) AS tokens_out, "
        "  COUNT(*) AS messages "
        "FROM messages "
        "WHERE "
        "  direction = 'outbound' "
        "  AND sender_type = 'bot' "
        "  AND ai_model IS NOT NULL "
        "  AND ai_model != '' "
        f"  AND created_at >= NOW() - INTERVAL '{int(days)} days' "
        "GROUP BY day "
        "ORDER BY day ASC"
    )
    result = await db.execute(sql)
    rows = result.fetchall()
    return [
        {
            "date": str(row.day),
            "tokens_in": int(row.tokens_in),
            "tokens_out": int(row.tokens_out),
            "messages": int(row.messages),
        }
        for row in rows
    ]


async def get_avg_latency_ms(
    db: "AsyncSession",
    days: int = 7,
) -> int:
    """Return the average ``ai_latency_ms`` over the last *days* days.

    Returns 0 when no rows with latency data exist.
    """
    sql = text(
        "SELECT COALESCE(AVG(ai_latency_ms), 0) AS avg_lat "
        "FROM messages "
        "WHERE "
        "  direction = 'outbound' "
        "  AND sender_type = 'bot' "
        "  AND ai_latency_ms IS NOT NULL "
        "  AND ai_latency_ms > 0 "
        f"  AND created_at >= NOW() - INTERVAL '{int(days)} days'"
    )
    result = await db.execute(sql)
    row = result.first()
    if row is None or row.avg_lat is None:
        return 0
    return int(round(float(row.avg_lat)))


async def get_cost_estimate_usd(
    db: "AsyncSession",
    days: int = 7,
) -> dict:
    """Return estimated cost in USD for the last *days* days.

    Result shape::

        {
            "total_usd": 1.234,
            "per_model": {
                "claude-haiku-4-5-20251001": 0.98,
                "gemini-flash": 0.25,
            },
        }

    Unknown model strings contribute zero cost and are logged as warnings.
    """
    sql = text(
        "SELECT "
        "  ai_model, "
        "  COALESCE(SUM(ai_tokens_in), 0)  AS tokens_in, "
        "  COALESCE(SUM(ai_tokens_out), 0) AS tokens_out "
        "FROM messages "
        "WHERE "
        "  direction = 'outbound' "
        "  AND sender_type = 'bot' "
        "  AND ai_model IS NOT NULL "
        "  AND ai_model != '' "
        f"  AND created_at >= NOW() - INTERVAL '{int(days)} days' "
        "GROUP BY ai_model"
    )
    result = await db.execute(sql)
    rows = result.fetchall()

    per_model: dict[str, float] = {}
    total_usd: float = 0.0

    for row in rows:
        model = row.ai_model
        pricing = _get_pricing(model)
        if pricing is None:
            logger.warning(
                "ai_metrics_service: unknown ai_model '%s' — cost set to 0", model
            )
            per_model[model] = 0.0
            continue

        cost = (
            row.tokens_in / 1_000_000 * pricing["input_usd_per_mtok"]
            + row.tokens_out / 1_000_000 * pricing["output_usd_per_mtok"]
        )
        per_model[model] = round(cost, 6)
        total_usd += cost

    return {
        "total_usd": round(total_usd, 4),
        "per_model": per_model,
    }
