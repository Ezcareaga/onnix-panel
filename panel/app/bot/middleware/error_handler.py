"""Catch-all error handler for the bot pipeline.

Wraps the orchestrator call so that the user NEVER sees a technical
error message. On any unhandled exception, returns a safe apology
response.

Plan 64-01: MW-04 Error handler.
"""
from __future__ import annotations

import logging
import time
from typing import Awaitable

from app.bot.observability.outcome import RequestOutcome

logger = logging.getLogger(__name__)
_request_logger = logging.getLogger("bot.request")

SAFE_ERROR_TEXT = (
    "Disculpa, tuve un problema procesando tu mensaje. "
    "Por favor intenta de nuevo en unos segundos."
)

_MAX_ERROR_MSG_LEN = 500


async def safe_handle(
    coro: Awaitable[RequestOutcome | None],
) -> RequestOutcome | None:
    """Execute *coro* and catch any exception.

    On success, returns the coroutine's ``RequestOutcome``.
    On failure, logs the full traceback, fills in the outcome with
    status="error", emits the ``request.complete`` log, and returns
    the error outcome so the caller can send a safe user-facing response.

    Always emits one ``request.complete`` log record via ``bot.request``
    regardless of outcome, with ``processing_ms`` measured here.
    """
    start = time.monotonic()
    outcome: RequestOutcome | None = None
    try:
        outcome = await coro
        return outcome
    except Exception as exc:
        logger.exception("Unhandled error in bot pipeline")
        outcome = RequestOutcome(
            status="error",
            error_type=type(exc).__name__,
            error_message=str(exc)[:_MAX_ERROR_MSG_LEN],
        )
        return outcome
    finally:
        processing_ms = int((time.monotonic() - start) * 1000)
        _emit_request_complete(outcome, processing_ms)


def _emit_request_complete(outcome: RequestOutcome | None, processing_ms: int) -> None:
    """Emit the ``request.complete`` structured log record."""
    if outcome is None:
        # Defensive: emit a minimal record if outcome was never assigned.
        _request_logger.info(
            "request.complete",
            extra={
                "contact_id": None,
                "intent": None,
                "llm_provider": None,
                "ai_model": None,
                "tool_iterations": None,
                "tokens_in": None,
                "tokens_out": None,
                "ai_latency_ms": None,
                "processing_ms": processing_ms,
                "fallback_used": False,
                "status": "error",
                "skip_reason": None,
                "error_type": "UnknownOutcome",
                "error_message": "outcome was None",
            },
        )
        return

    _request_logger.info(
        "request.complete",
        extra={
            "contact_id": outcome.contact_id,
            "intent": outcome.intent,
            "llm_provider": outcome.llm_provider,
            "ai_model": outcome.ai_model,
            "tool_iterations": outcome.tool_iterations,
            "tokens_in": outcome.tokens_in,
            "tokens_out": outcome.tokens_out,
            "ai_latency_ms": outcome.ai_latency_ms,
            "processing_ms": processing_ms,
            "fallback_used": outcome.fallback_used,
            "status": outcome.status,
            "skip_reason": outcome.skip_reason,
            "error_type": outcome.error_type,
            "error_message": outcome.error_message,
        },
    )
