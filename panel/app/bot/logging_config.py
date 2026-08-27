"""Centralized logging configuration for the bot engine.

Call ``setup_bot_logging()`` once at application startup (in the FastAPI
lifespan) to wire all ``app.bot.*`` loggers to stdout + rotating file.

Logger namespaces (shown in log output):
    bot.webhook     — inbound requests, webhook parsing
    bot.middleware   — rate limit, idempotency, cooldown, error handler
    bot.orchestrator — message flow, contact/conversation resolution
    bot.ai           — LLM calls (Claude, Gemini), circuit breaker
    bot.search       — property search, filters, vector, relaxation
    bot.sender       — outbound messages (Twilio, Telegram API)
    bot.scheduler    — cron tasks, heartbeat, cold lead check
    bot.db           — slow queries (>1s), connection errors
"""
from __future__ import annotations

import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler

# Full module path → short alias for log output.
_MODULE_ALIASES: dict[str, str] = {
    "app.bot.webhooks": "bot.webhook",
    "app.bot.handlers": "bot.webhook",
    "app.bot.middleware.injection_guard": "bot.guard",
    "app.bot.middleware": "bot.middleware",
    "app.bot.core.orchestrator": "bot.orchestrator",
    "app.bot.core.conversation": "bot.orchestrator",
    "app.bot.core.response_builder": "bot.orchestrator",
    "app.bot.core.tool_executor": "bot.orchestrator",
    "app.bot.ai": "bot.ai",
    "app.bot.search": "bot.search",
    "app.bot.channels": "bot.sender",
    "app.bot.scheduler": "bot.scheduler",
    "app.bot.db": "bot.db",
    "app.bot": "bot",
}

# Sorted longest-prefix-first for greedy matching.
_SORTED_ALIASES = sorted(_MODULE_ALIASES.items(), key=lambda x: -len(x[0]))


class _BotDedupFilter(logging.Filter):
    """Suppress ``app.bot.*`` records on the root logger.

    Our ``app.bot`` logger already has its own stdout + file handlers.
    This filter prevents the root logger from duplicating those records
    when propagation is enabled (needed for pytest caplog to work).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith("app.bot")


class _BotFormatter(logging.Formatter):
    """Replaces ``app.bot.*`` logger names with short ``bot.*`` aliases."""

    def format(self, record: logging.LogRecord) -> str:
        saved = record.name
        for prefix, alias in _SORTED_ALIASES:
            if saved.startswith(prefix):
                record.name = alias
                break
        out = super().format(record)
        record.name = saved
        return out


def setup_bot_logging(
    level: str | None = None,
    log_file: str | None = None,
) -> None:
    """Configure handlers for all ``app.bot.*`` loggers.

    Parameters
    ----------
    level
        Log level (DEBUG / INFO / WARNING …).
        Falls back to ``BOT_LOG_LEVEL`` env var, then ``"INFO"``.
    log_file
        Path to the rotating log file.
        Falls back to ``BOT_LOG_FILE`` env var, then ``/app/logs/bot_v7.log``.
    """
    log_format = os.getenv("LOG_FORMAT", "text").lower()
    level_str = (level or os.environ.get("BOT_LOG_LEVEL", "INFO")).upper()
    level_int = getattr(logging, level_str, logging.INFO)
    file_path = log_file or os.environ.get(
        "BOT_LOG_FILE", "/app/logs/bot_v7.log",
    )

    if log_format == "json":
        from app.bot.observability.json_formatter import JsonFormatter  # lazy, avoid circular
        formatter: logging.Formatter = JsonFormatter()
    else:
        fmt = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
        datefmt = "%Y-%m-%dT%H:%M:%S%z"
        formatter = _BotFormatter(fmt, datefmt=datefmt)

    bot_logger = logging.getLogger("app.bot")
    bot_logger.setLevel(level_int)
    bot_logger.handlers.clear()

    # Keep propagate=True so pytest caplog can capture records.
    # Prevent double-logging by suppressing the root logger's default handler
    # for our namespace (root handler uses stderr with a different format,
    # our handlers use stdout with the bot format).
    root = logging.getLogger()
    if not any(isinstance(f, _BotDedupFilter) for f in root.filters):
        root.addFilter(_BotDedupFilter())

    # --- stdout handler (for docker logs) ---
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    sh.setLevel(level_int)
    bot_logger.addHandler(sh)

    # --- Rotating file handler ---
    try:
        log_dir = os.path.dirname(file_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        fh = RotatingFileHandler(
            file_path,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        fh.setFormatter(formatter)
        fh.setLevel(level_int)
        bot_logger.addHandler(fh)
    except OSError as exc:
        bot_logger.warning(
            "Cannot create log file %s: %s — file logging disabled",
            file_path,
            exc,
        )

    bot_logger.info(
        "Bot logging configured — format=%s, file=%s",
        log_format,
        file_path,
    )


def setup_db_event_logging(engine) -> None:
    """Attach slow-query (>1 s) and error logging to a SQLAlchemy engine.

    Call after ``setup_bot_logging()`` so ``bot.db`` inherits handlers.
    """
    from sqlalchemy import event

    db_logger = logging.getLogger("app.bot.db")

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _before_cursor(conn, cursor, stmt, params, context, executemany):
        conn.info.setdefault("_query_start", []).append(time.monotonic())

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def _after_cursor(conn, cursor, stmt, params, context, executemany):
        starts = conn.info.get("_query_start")
        if not starts:
            return
        elapsed = time.monotonic() - starts.pop(-1)
        if elapsed > 1.0:
            db_logger.warning("Slow query (%.3fs): %.200s", elapsed, stmt)

    @event.listens_for(engine.sync_engine, "handle_error")
    def _on_error(ctx):
        db_logger.error(
            "DB error: %s — sql: %.200s",
            ctx.original_exception,
            ctx.statement or "N/A",
        )

    db_logger.debug("DB event logging attached")
