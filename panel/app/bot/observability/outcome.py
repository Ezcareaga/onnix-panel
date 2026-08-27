"""RequestOutcome dataclass — captures all observable fields for one bot request.

Used by safe_handle to emit the ``request.complete`` structured log.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RequestOutcome:
    """All observable fields for a single handled bot request.

    Produced by _handle_inner and consumed by safe_handle to emit
    the ``request.complete`` log record.
    """

    # LLM info
    contact_id: int | None = None
    intent: str | None = None
    llm_provider: str | None = None       # "claude" | "gemini" | None
    ai_model: str | None = None
    tool_iterations: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    ai_latency_ms: int | None = None

    # Request-level
    fallback_used: bool = False
    status: str = "ok"                    # "ok" | "skipped" | "error" | "send_failed"
    skip_reason: str | None = None        # "duplicate" | "rate_limited" | "bot_disabled" | "manual_mode"
    error_type: str | None = None
    error_message: str | None = None      # always <= 500 chars

    # processing_ms is set by safe_handle, not _handle_inner
    processing_ms: int = field(default=0, init=False)
