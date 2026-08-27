"""Anthropic API call interceptor for per-source token/cost attribution.

Usage::

    from app.bot.observability.anthropic_tracker import track_anthropic_call

    async with track_anthropic_call("bot.orchestrator") as tracker:
        resp = await client.messages.create(...)
        tracker.set_response(resp)

The context manager writes one row to ``anthropic_api_calls`` on exit (success
or exception).  DB write failures are logged as warnings and never propagated —
the interceptor must NEVER break the caller.

``request_id`` and ``conversation_id`` are picked up automatically from the
``contextvars`` set by the bot request pipeline.
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any, AsyncGenerator

logger = logging.getLogger(__name__)


class _CallTracker:
    """Mutable holder that the caller can populate with the API response."""

    def __init__(self, source: str) -> None:
        self.source = source
        self._response: Any = None
        self._error: str | None = None

    def set_response(self, resp: Any) -> None:
        """Record the Anthropic response object so the context manager can extract tokens."""
        self._response = resp

    def set_error(self, exc: BaseException) -> None:
        self._error = str(exc)[:200]


@asynccontextmanager
async def track_anthropic_call(source: str) -> AsyncGenerator[_CallTracker, None]:
    """Async context manager that records one Anthropic API call row on exit.

    Args:
        source: Dot-namespaced label, e.g. ``"bot.orchestrator"``,
                ``"property_classifier"``, ``"bot.lead_profiler"``.

    Yields:
        A :class:`_CallTracker` instance.  Call ``.set_response(resp)`` inside
        the ``async with`` block after the Anthropic call completes.

    Guarantees:
        - On ``__aexit__`` (success or exception), one DB row is inserted.
        - If the DB write itself fails, a WARNING is logged but the exception
          is NOT propagated (fire-and-forget).
        - If an exception escapes the ``async with`` block, it is re-raised
          after the DB write attempt.
    """
    tracker = _CallTracker(source)
    start = time.monotonic()
    exc_caught: BaseException | None = None

    try:
        yield tracker
    except BaseException as exc:
        exc_caught = exc
        tracker.set_error(exc)
    finally:
        duration_ms = int((time.monotonic() - start) * 1000)
        await _persist(tracker, duration_ms)
        if exc_caught is not None:
            raise exc_caught


async def _persist(tracker: _CallTracker, duration_ms: int) -> None:
    """Write one row to ``anthropic_api_calls``.  Never raises."""
    from app.bot.observability.context import get_request_context
    from app.database import async_session_factory
    from app.models.anthropic_api_call import AnthropicApiCall
    from app.services.cost_config import compute_cost_usd

    ctx = get_request_context()
    request_id: str | None = ctx.get("request_id")
    conversation_id: int | None = ctx.get("conversation_id")

    resp = tracker._response
    model = "unknown"
    tokens_in = 0
    tokens_out = 0
    cache_creation_in = 0
    cache_read_in = 0
    cost_usd = Decimal("0")

    if resp is not None:
        try:
            model = getattr(resp, "model", "unknown") or "unknown"
            usage = getattr(resp, "usage", None)
            if usage is not None:
                tokens_in = int(getattr(usage, "input_tokens", 0) or 0)
                tokens_out = int(getattr(usage, "output_tokens", 0) or 0)
                cache_creation_in = int(
                    getattr(usage, "cache_creation_input_tokens", 0) or 0
                )
                cache_read_in = int(
                    getattr(usage, "cache_read_input_tokens", 0) or 0
                )
            cost_usd = compute_cost_usd(
                model,
                tokens_in,
                tokens_out,
                cache_creation_in=cache_creation_in,
                cache_read_in=cache_read_in,
            )
        except Exception as parse_exc:
            logger.warning(
                "anthropic_tracker: failed to parse response for source=%s — %s",
                tracker.source,
                parse_exc,
            )

    row = AnthropicApiCall(
        source=tracker.source,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cache_creation_in=cache_creation_in,
        cache_read_in=cache_read_in,
        cost_usd=cost_usd,
        request_id=request_id,
        conversation_id=conversation_id,
        duration_ms=duration_ms,
        error=tracker._error,
    )

    try:
        async with async_session_factory() as session:
            session.add(row)
            await session.commit()
    except Exception as db_exc:
        logger.warning(
            "anthropic_tracker: DB write failed for source=%s — %s (non-fatal)",
            tracker.source,
            db_exc,
        )
