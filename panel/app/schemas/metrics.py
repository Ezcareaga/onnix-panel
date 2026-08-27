"""Pydantic v2 schemas for the bot health dashboard snapshot."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class StuckConversations(BaseModel):
    """Conversations where the last message was inbound and is older than 10 minutes."""

    count: int = Field(ge=0)


class MessageVolume(BaseModel):
    """Message counts split by direction/sender over the last 24 hours."""

    inbound: int = Field(ge=0)
    bot_out: int = Field(ge=0)
    agent_out: int = Field(ge=0)
    total: int = Field(ge=0)


class Latency(BaseModel):
    """AI response latency statistics over the last 24 hours (bot outbound messages)."""

    avg_ms: int = Field(ge=0, description="Average ai_latency_ms over last 24h outbound bot messages")
    p95_ms: int = Field(ge=0)
    worst_ms: int = Field(ge=0)
    n: int = Field(ge=0, description="Sample size")


class ProviderMix(BaseModel):
    """LLM provider usage split over the last 24 hours."""

    claude: int = Field(ge=0)
    gemini: int = Field(ge=0)
    pct_fallback: float = Field(
        ge=0.0,
        le=100.0,
        description="gemini / (claude + gemini) * 100",
    )


class ToolIterations(BaseModel):
    """Tool-use iteration statistics for bot messages over the last 24 hours."""

    avg: float = Field(ge=0.0)
    max: int = Field(ge=0)
    zero_tools: int = Field(ge=0, description="Count of bot messages with tool_iterations = 0")
    high_iters: int = Field(ge=0, description="Count of bot messages with tool_iterations >= 4")
    n: int = Field(ge=0)


class HeartbeatStatus(BaseModel):
    """Status of the most recent heartbeat scheduler failure."""

    last_failure_at: datetime | None
    last_failure_ago_seconds: int | None = Field(default=None, ge=0)
    next_expected_in_seconds: int = Field(default=3600, ge=0)


class ErrorBreakdown(BaseModel):
    """Bot error counts grouped by workflow over the last 24 hours."""

    by_workflow: dict[str, int] = Field(default_factory=dict)
    total: int = Field(ge=0)


class AiCost(BaseModel):
    """USD cost breakdown for AI calls (Claude + Gemini) over a time window."""

    claude_usd: float = Field(ge=0.0, description="Claude cost in USD, rounded to 4 decimals")
    gemini_usd: float = Field(ge=0.0, description="Gemini cost in USD, rounded to 4 decimals")
    total_usd: float = Field(ge=0.0)
    messages: int = Field(ge=0, description="Number of messages covered by this window")


class TwilioUsage(BaseModel):
    """Twilio billing aggregated from Usage Records API."""

    total_usd: float = Field(ge=0.0)
    whatsapp_usd: float = Field(ge=0.0)
    other_usd: float = Field(ge=0.0)
    categories: dict[str, float] = Field(default_factory=dict)
    currency: str = "usd"


class DailyAiCost(BaseModel):
    """AI cost aggregated for one UTC calendar day."""

    date: date
    total_usd: float = Field(ge=0.0)
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    cache_creation_in: int = Field(ge=0, default=0)
    cache_read_in: int = Field(ge=0, default=0)
    calls: int = Field(ge=0)


class SourceCost(BaseModel):
    """AI cost aggregated by source (caller attribution)."""

    source: str
    total_usd: float = Field(ge=0.0)
    calls: int = Field(ge=0)
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)


class CostTimeSeries(BaseModel):
    """Time-series cost breakdowns for the observability dashboard."""

    ai_by_day_7d: list[DailyAiCost] = Field(default_factory=list)
    ai_by_source_today: list[SourceCost] = Field(default_factory=list)
    ai_by_source_7d: list[SourceCost] = Field(default_factory=list)
    ai_by_source_month: list[SourceCost] = Field(default_factory=list)


class Costs(BaseModel):
    """Combined AI + Twilio cost snapshot for today and month-to-date."""

    ai_today: AiCost
    ai_month: AiCost
    twilio_today: TwilioUsage
    twilio_month: TwilioUsage
    total_today_usd: float = Field(ge=0.0)
    total_month_usd: float = Field(ge=0.0)
    timeseries: CostTimeSeries = Field(default_factory=CostTimeSeries)


class BotHealthSnapshot(BaseModel):
    """Composite snapshot of bot health metrics for the dashboard."""

    generated_at: datetime
    stuck_conversations: StuckConversations
    message_volume: MessageVolume
    latency: Latency
    provider_mix: ProviderMix
    tool_iterations: ToolIterations
    heartbeat: HeartbeatStatus
    errors: ErrorBreakdown
    costs: Costs
