"""SchedulerService — thin wrapper around APScheduler AsyncIOScheduler.

Provides a clean interface for adding/removing cron and interval tasks
with safe defaults: coalesce=True, max_instances=1, misfire_grace_time=300.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.tz import PYT

logger = logging.getLogger(__name__)


class SchedulerService:
    """Manages scheduled tasks via APScheduler AsyncIOScheduler."""

    def __init__(self) -> None:
        # El huso se pasa EXPLICITO y no se hereda del sistema.
        #
        # Sin este argumento APScheduler toma la zona del proceso, que hoy es
        # `America/Asuncion` — y ahi nacio el bug: cuatro tareas estaban
        # escritas con la hora UTC («08:00 PYT = 12:00 UTC», `hour=12`) sobre un
        # scheduler que interpretaba ese 12 como hora local. Medido en el log de
        # produccion: `daily_report` corrio a las **12:00:01 -0300** el 22/08 y
        # a las **12:00:00 -0300** el 23/08. Cuatro horas tarde, todos los dias,
        # durante meses.
        #
        # Con el huso explicito los horarios dejan de depender de la zona del
        # servidor: si manana alguien la cambia, las tareas siguen corriendo a
        # la hora de Paraguay, que es donde vive el negocio. Postgres se queda
        # en UTC a proposito — ver `panel/app/tz.py`.
        self._scheduler = AsyncIOScheduler(
            timezone=PYT,
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 300,
            },
        )
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the scheduler if not already running."""
        if not self._running:
            self._scheduler.start()
            self._running = True
            logger.info("Scheduler started")

    def stop(self) -> None:
        """Gracefully stop the scheduler.

        Note: AsyncIOScheduler.shutdown() is asynchronous internally
        (uses call_soon_threadsafe), so we track state ourselves to
        provide a reliable synchronous is_running check.
        """
        if self._running:
            self._scheduler.shutdown(wait=False)
            self._running = False
            logger.info("Scheduler stopped")

    @property
    def is_running(self) -> bool:
        """Return True if the scheduler is currently running."""
        return self._running

    # ------------------------------------------------------------------
    # Task management
    # ------------------------------------------------------------------

    def add_cron_task(
        self,
        task_id: str,
        func: Callable[..., Any],
        **cron_kwargs: Any,
    ) -> None:
        """Add a cron-triggered task.

        Parameters
        ----------
        task_id:
            Unique identifier for the job.
        func:
            The callable (sync or async) to execute.
        **cron_kwargs:
            APScheduler CronTrigger arguments (hour, minute, day_of_week, etc.).
        """
        self._scheduler.add_job(
            func,
            trigger="cron",
            id=task_id,
            replace_existing=True,
            **cron_kwargs,
        )
        logger.info("Cron task added: %s", task_id)

    def add_interval_task(
        self,
        task_id: str,
        func: Callable[..., Any],
        **interval_kwargs: Any,
    ) -> None:
        """Add an interval-triggered task.

        Parameters
        ----------
        task_id:
            Unique identifier for the job.
        func:
            The callable (sync or async) to execute.
        **interval_kwargs:
            APScheduler IntervalTrigger arguments (seconds, minutes, hours, etc.).
        """
        self._scheduler.add_job(
            func,
            trigger="interval",
            id=task_id,
            replace_existing=True,
            **interval_kwargs,
        )
        logger.info("Interval task added: %s", task_id)

    def remove_task(self, task_id: str) -> bool:
        """Remove a task by id. Returns True if removed, False if not found."""
        try:
            self._scheduler.remove_job(task_id)
            logger.info("Task removed: %s", task_id)
            return True
        except JobLookupError:
            logger.warning("Task not found for removal: %s", task_id)
            return False

    def pause_task(self, task_id: str) -> bool:
        """Pause a task by id. Returns True if paused, False if not found."""
        try:
            self._scheduler.pause_job(task_id)
            logger.info("Task paused: %s", task_id)
            return True
        except JobLookupError:
            logger.warning("Task not found for pause: %s", task_id)
            return False

    def resume_task(self, task_id: str) -> bool:
        """Resume a paused task. Returns True if resumed, False if not found."""
        try:
            self._scheduler.resume_job(task_id)
            logger.info("Task resumed: %s", task_id)
            return True
        except JobLookupError:
            logger.warning("Task not found for resume: %s", task_id)
            return False

    def list_tasks(self) -> list[dict[str, Any]]:
        """Return a summary of all scheduled tasks."""
        jobs = self._scheduler.get_jobs()
        return [
            {
                "id": job.id,
                "name": job.name,
                "trigger": str(job.trigger),
                "next_run_time": (
                    job.next_run_time.isoformat() if job.next_run_time else None
                ),
            }
            for job in jobs
        ]
