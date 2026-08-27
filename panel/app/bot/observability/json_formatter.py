"""JSON log formatter for structured observability output.

Produces one compact JSON object per log record, enriched with:
- Standard fields: ts, level, logger (aliased), msg
- Request context from contextvars (non-None values only)
- Whitelisted extra fields passed via ``extra={}`` on log calls
- Optional ``exc`` key when exc_info is present

Enable via ``LOG_FORMAT=json`` environment variable (read by
``setup_bot_logging()`` in ``logging_config.py``).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.bot.logging_config import _SORTED_ALIASES
from app.bot.observability.context import get_request_context

# Fields allowed from ``extra={}`` on log calls.  Anything not in this
# tuple is silently dropped.
ALLOWED_EXTRA_FIELDS: tuple[str, ...] = (
    "contact_id",
    "intent",
    "llm_provider",
    "ai_model",
    "tool_iterations",
    "tokens_in",
    "tokens_out",
    "ai_latency_ms",
    "processing_ms",
    "fallback_used",
    "status",
    "skip_reason",
    "error_type",
    "error_message",
    # Heartbeat structured counters (Fase F — M1 Observabilidad)
    "stuck_conversations",
    "msgs_24h",
    "latency_p95_ms",
    "pct_fallback",
    "errors_24h",
    # Cost metrics (Fase I — M1 Observabilidad)
    "total_today_usd",
    "total_month_usd",
)

# Keys that are part of the standard LogRecord and must NOT be confused
# with user-supplied extra fields.
_STDLIB_RECORD_KEYS: frozenset[str] = frozenset(
    (
        "name", "msg", "args", "levelname", "levelno", "pathname",
        "filename", "module", "exc_info", "exc_text", "stack_info",
        "lineno", "funcName", "created", "msecs", "relativeCreated",
        "thread", "threadName", "process", "processName", "message",
        "taskName",
    )
)


def _resolve_alias(name: str) -> str:
    """Apply ``_MODULE_ALIASES`` longest-prefix matching to *name*."""
    for prefix, alias in _SORTED_ALIASES:
        if name.startswith(prefix):
            return alias
    return name


class JsonFormatter(logging.Formatter):
    """Emit each log record as a single compact JSON line.

    Fields always present: ``ts``, ``level``, ``logger``, ``msg``.
    Fields merged from context vars: only non-None values.
    Fields merged from ``extra``: only keys in ``ALLOWED_EXTRA_FIELDS``.
    Optional field: ``exc`` when ``record.exc_info`` is truthy.
    """

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        """Format *record* as a JSON string."""
        try:
            return self._build_json(record)
        except Exception:  # pragma: no cover — last-resort guard
            return "{}-bot-logger-serialize-failed"

    def _build_json(self, record: logging.LogRecord) -> str:
        # --- ts: ISO 8601 UTC with milliseconds ---
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        ts = dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"

        payload: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname,
            "logger": _resolve_alias(record.name),
            "msg": record.getMessage(),
        }

        # --- context vars (non-None only) ---
        ctx = get_request_context()
        for key, value in ctx.items():
            if value is not None:
                payload[key] = value

        # --- whitelisted extra fields ---
        for field in ALLOWED_EXTRA_FIELDS:
            if field in record.__dict__ and field not in _STDLIB_RECORD_KEYS:
                payload[field] = record.__dict__[field]

        # --- exception info ---
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False, separators=(",", ":"))
