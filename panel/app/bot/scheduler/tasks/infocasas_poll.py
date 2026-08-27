"""InfoCasas poll — scheduled task for lead capture.

Polls InfoCasas GraphQL API for new notifications, processes new leads,
and sends Telegram/WhatsApp notifications. Designed to run every 5 minutes
via APScheduler, in parallel with N8N during migration.

Guard: set INFOCASAS_POLL_ENABLED=false to disable without redeploying
(use in staging to prevent real WA sends from non-prod environments).
"""
from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)


class InfocasasPollTask:
    """Wrapper that creates and runs the InfocasasService.

    Parameters
    ----------
    session_factory:
        Optional async session factory override (for testing).
    service:
        Optional InfocasasService override (for testing).
    """

    def __init__(
        self,
        *,
        session_factory=None,
        service=None,  # For testing — inject a mock InfocasasService
    ) -> None:
        self._session_factory = session_factory
        self._service = service

    async def run(self) -> dict:
        """Execute one polling cycle and return metrics."""
        start = time.monotonic()

        if self._service is None:
            from app.bot.services.infocasas.infocasas_service import (
                get_infocasas_service,
            )

            self._service = get_infocasas_service()

        try:
            result = await self._service.run_poll()
        except Exception:
            logger.exception("InfocasasPollTask: unhandled error in run_poll")
            result = {"status": "error", "processed": 0, "new": 0, "skipped": 0, "errors": 1}

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            'Job executed — {"task": "infocasas_poll", "duration_ms": %.0f, '
            '"status": "%s", "processed": %d, "new": %d, "skipped": %d, "errors": %d}',
            elapsed_ms,
            result.get("status", "unknown"),
            result.get("processed", 0),
            result.get("new", 0),
            result.get("skipped", 0),
            result.get("errors", 0),
        )
        return result


# ------------------------------------------------------------------
# Module-level factory
# ------------------------------------------------------------------


async def run_infocasas_poll() -> dict:
    """Factory function invoked by the scheduler.

    Creates a fresh InfocasasPollTask each invocation.
    Skips silently when INFOCASAS_POLL_ENABLED=false (staging guard).
    """
    if os.getenv("INFOCASAS_POLL_ENABLED", "true").lower() == "false":
        logger.info("InfocasasPollTask: poll disabled via INFOCASAS_POLL_ENABLED=false — skipping")
        return {"status": "disabled", "processed": 0, "new": 0, "skipped": 0, "errors": 0}
    task = InfocasasPollTask()
    return await task.run()
