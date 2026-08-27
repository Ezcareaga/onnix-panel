"""FastAPI lifespan — wires up the scheduler with all registered tasks.

CRITICAL: scheduler failure must NEVER crash the panel.  All startup
logic is wrapped in try/except so the web application remains available
even if APScheduler or the DB is temporarily unreachable.

Plan 67-03.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.bot.config import bot_settings
from app.bot.logging_config import setup_bot_logging, setup_db_event_logging
from app.bot.scheduler.scheduler_service import SchedulerService
from app.bot.scheduler.settings_manager import SettingsManager
from app.bot.scheduler.tasks.cold_lead_check import run_cold_lead_check
from app.bot.scheduler.tasks.daily_report import run_daily_report
from app.bot.scheduler.tasks.heartbeat import run_heartbeat
from app.bot.scheduler.tasks.cleanup_inactive_refs import run_cleanup_inactive_refs

logger = logging.getLogger(__name__)


@asynccontextmanager
async def scheduler_lifespan(app: FastAPI):
    """Start the scheduler on startup, stop on shutdown.

    Stores the ``SchedulerService`` on ``app.state.scheduler`` so the
    health endpoint can inspect it.  If ``SCHEDULER_ENABLED`` is False,
    the scheduler is not created at all.
    """
    # --- Logging must be configured before anything else ---
    setup_bot_logging()
    try:
        from app.database import engine
        setup_db_event_logging(engine)
    except Exception:
        logger.warning("Could not attach DB event logging", exc_info=True)

    # --- Security: abort boot if signing secrets missing in production ---
    from app.config import validate_required_secrets
    validate_required_secrets()  # raises RuntimeError on production + missing secret

    scheduler = None

    try:
        if not bot_settings.SCHEDULER_ENABLED:
            logger.info("Scheduler disabled (SCHEDULER_ENABLED=false)")
        else:
            scheduler = SchedulerService()
            settings_mgr = SettingsManager()

            # --- Register cold_lead_check (daily at 09:00 PYT) ---
            if await settings_mgr.is_task_enabled("cold_lead_check"):
                scheduler.add_cron_task(
                    "cold_lead_check",
                    run_cold_lead_check,
                    hour=9,
                    minute=0,
                )
                logger.info("Task registered: cold_lead_check (cron 09:00)")
            else:
                logger.info("Task skipped (disabled): cold_lead_check")

            # --- Register daily_report (08:00 PYT) ---
            # El `hour` es hora de Paraguay, no UTC: el scheduler se
            # construye con `timezone=PYT` explicito. Decia «= 12:00 UTC» y
            # ponia `hour=12`, asi que el reporte «de las 08:00» salia a las
            # 12:00 — medido en el log: 12:00:01 el 22/08 y 12:00:00 el 23/08.
            if await settings_mgr.is_task_enabled("daily_report"):
                scheduler.add_cron_task(
                    "daily_report",
                    run_daily_report,
                    hour=8,
                    minute=0,
                )
                logger.info("Task registered: daily_report (cron 08:00 PYT)")
            else:
                logger.info("Task skipped (disabled): daily_report")

            # --- Register heartbeat (every 3600s) ---
            if await settings_mgr.is_task_enabled("heartbeat"):
                scheduler.add_interval_task(
                    "heartbeat",
                    run_heartbeat,
                    seconds=3600,
                )
                logger.info("Task registered: heartbeat (interval 3600s)")
            else:
                logger.info("Task skipped (disabled): heartbeat")


            # followup_sender se fue con el bot: mandaba plantillas de
            # seguimiento solo, a las 10:00. Onnix manda las plantillas a mano
            # desde el hilo. El toggle sigue en `bot_settings` para no romper
            # la fila que ya existe en la base; no lo lee nadie.

            # --- Register cleanup_inactive_refs (02:00 PYT) ---
            # Este es el unico de los cuatro donde NO se movio la hora: ya
            # corria 02:00 de Paraguay y para una limpieza de madrugada da
            # igual el huso. Lo que estaba mal era el comentario.
            if await settings_mgr.is_task_enabled("cleanup_inactive_refs"):
                scheduler.add_cron_task(
                    "cleanup_inactive_refs",
                    run_cleanup_inactive_refs,
                    hour=2,
                    minute=0,
                )
                logger.info("Task registered: cleanup_inactive_refs (cron 02:00 PYT)")
            else:
                logger.info("Task skipped (disabled): cleanup_inactive_refs")


            scheduler.start()
            app.state.scheduler = scheduler
            logger.info(
                "Scheduler started with %d task(s)",
                len(scheduler.list_tasks()),
            )

    except Exception:
        logger.exception(
            "Scheduler startup failed — panel continues without scheduler"
        )
        scheduler = None

    # Ensure app.state.scheduler is always set (may be None)
    if not hasattr(app.state, "scheduler"):
        app.state.scheduler = None

    yield

    # --- Shutdown ---
    if scheduler is not None and scheduler.is_running:
        try:
            scheduler.stop()
            logger.info("Scheduler stopped during shutdown")
        except Exception:
            logger.exception("Error stopping scheduler (non-fatal)")
