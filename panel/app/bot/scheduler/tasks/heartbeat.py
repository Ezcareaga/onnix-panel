"""Heartbeat health check — verifies DB connectivity and emits a health snapshot.

Runs at a configurable interval (default: every hour) and:
- Sends a best-effort Telegram failure notification if the DB is unreachable.
- On DB success, pulls MetricsService.get_bot_health() and sends a compact
  Telegram HTML summary via AdminNotifier.notify_heartbeat_snapshot.
- On DB failure, writes a row to bot_errors so daily_report counters work
  (fixes the gap documented in the M1 Observabilidad audit 2026-04-18).

Plan 67-03: SCHED-TASK-02.
Refactored in 71-03: Task 6 — replaced inline httpx with AdminNotifier.
Enriched in M1 Fase F — health snapshot + bot_errors on failure.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import text

from app.bot.config import bot_settings
from app.bot.services.admin_notifier import AdminNotifier
from app.database import async_session_factory

logger = logging.getLogger(__name__)


class HeartbeatChecker:
    """Checks DB health, emits metrics snapshot, and notifies on failure.

    Parameters
    ----------
    notification_chat_id:
        Telegram chat ID for failure notifications.
    telegram_bot_token:
        Telegram Bot API token for sending notifications.
    session_factory:
        Optional async session factory override (for testing).
    notifier:
        Optional AdminNotifier override (for testing).
    """

    def __init__(
        self,
        notification_chat_id: str,
        telegram_bot_token: str,
        *,
        session_factory=None,
        notifier: AdminNotifier | None = None,
    ) -> None:
        self.notification_chat_id = notification_chat_id
        self.telegram_bot_token = telegram_bot_token
        self._session_factory = session_factory or async_session_factory
        self._notifier = notifier or AdminNotifier(
            chat_id=notification_chat_id,
            bot_token=telegram_bot_token,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> dict:
        """Execute the heartbeat check.

        Returns a dict with ``db_healthy`` (bool) and ``timestamp`` (ISO).
        """
        start = time.monotonic()
        timestamp = datetime.now(timezone.utc).isoformat()
        db_healthy = await self._check_db()

        if not db_healthy:
            await self._notifier.notify_heartbeat_failure(timestamp)
            await self._record_db_failure(timestamp)
        else:
            await self._emit_snapshot()

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            'Job executed — {"task": "heartbeat", "duration_ms": %.0f, "db_healthy": %s}',
            elapsed_ms, "true" if db_healthy else "false",
        )
        return {"db_healthy": db_healthy, "timestamp": timestamp}

    # ------------------------------------------------------------------
    # Private: DB check
    # ------------------------------------------------------------------

    async def _check_db(self) -> bool:
        """Run ``SELECT 1`` to verify DB connectivity."""
        try:
            async with self._session_factory() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception:
            logger.exception("heartbeat: DB health check failed")
            return False

    # ------------------------------------------------------------------
    # Private: emit health snapshot on success
    # ------------------------------------------------------------------

    async def _emit_snapshot(self) -> None:
        """Pull a BotHealthSnapshot and send a compact Telegram summary.

        Never raises — all failures are logged and swallowed so the
        heartbeat task itself remains non-fatal.
        """
        try:
            from app.services.metrics_service import MetricsService

            async with self._session_factory() as session:
                snapshot = await MetricsService(session).get_bot_health()

            # Structured log for JSON observability pipeline
            logger.info(
                "heartbeat.success",
                extra={
                    "stuck_conversations": snapshot.stuck_conversations.count,
                    "msgs_24h": snapshot.message_volume.total,
                    "latency_p95_ms": snapshot.latency.p95_ms,
                    "pct_fallback": round(snapshot.provider_mix.pct_fallback, 2),
                    "errors_24h": snapshot.errors.total,
                    "total_today_usd": round(snapshot.costs.total_today_usd, 2),
                    "total_month_usd": round(snapshot.costs.total_month_usd, 2),
                },
            )

            # Best-effort Telegram summary — silent if creds empty (staging guardrail)
            await self._notifier.notify_heartbeat_snapshot(snapshot)
        except Exception:
            logger.exception("heartbeat: failed to emit snapshot (non-fatal)")

    # ------------------------------------------------------------------
    # Private: record DB failure to bot_errors
    # ------------------------------------------------------------------

    async def _record_db_failure(self, timestamp: str) -> None:
        """Write a bot_errors row so daily_report counters include this failure.

        Best-effort: if DB is still unreachable, swallow silently.
        """
        from app.bot.services.error_service import BotErrorService

        try:
            async with self._session_factory() as session:
                svc = BotErrorService(workflow="heartbeat")
                await svc.record_error(
                    session,
                    error_message=f"DB health check failed at {timestamp}",
                    node="db_check",
                    execution_id=None,
                    chat_id=None,
                )
        except Exception:
            logger.exception("heartbeat: could not record error to bot_errors")


# ------------------------------------------------------------------
# Module-level factory
# ------------------------------------------------------------------


async def run_heartbeat() -> dict:
    """Factory function invoked by the scheduler.

    Reads configuration from ``bot_settings`` and runs the check.
    """
    checker = HeartbeatChecker(
        notification_chat_id=bot_settings.TELEGRAM_EZ_CHAT_ID,
        telegram_bot_token=bot_settings.TELEGRAM_BOT_TOKEN,
    )
    return await checker.run()
