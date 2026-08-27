"""Tests for SchedulerService — APScheduler wrapper."""
import pytest

from app.bot.scheduler.scheduler_service import SchedulerService


def _make_service() -> SchedulerService:
    """Create a fresh SchedulerService instance."""
    return SchedulerService()


class TestSchedulerLifecycle:
    """Start/stop and is_running property."""

    def test_is_running_false_before_start(self) -> None:
        """Scheduler is not running until start() is called."""
        svc = _make_service()
        assert svc.is_running is False

    @pytest.mark.asyncio
    async def test_start_makes_running_true(self) -> None:
        """After start(), is_running should be True."""
        svc = _make_service()
        svc.start()
        try:
            assert svc.is_running is True
        finally:
            svc.stop()

    @pytest.mark.asyncio
    async def test_stop_makes_running_false(self) -> None:
        """After stop(), is_running should be False."""
        svc = _make_service()
        svc.start()
        svc.stop()
        assert svc.is_running is False

    @pytest.mark.asyncio
    async def test_start_idempotent(self) -> None:
        """Calling start() twice does not raise."""
        svc = _make_service()
        svc.start()
        try:
            svc.start()  # should not raise
            assert svc.is_running is True
        finally:
            svc.stop()

    def test_stop_idempotent(self) -> None:
        """Calling stop() when not running does not raise."""
        svc = _make_service()
        svc.stop()  # should not raise


class TestCronTask:
    """Adding and listing cron tasks."""

    @pytest.mark.asyncio
    async def test_add_cron_task_appears_in_list(self) -> None:
        """A cron task is visible in list_tasks after adding."""
        svc = _make_service()
        svc.start()
        try:
            svc.add_cron_task("test_cron", lambda: None, hour=3, minute=0)
            tasks = svc.list_tasks()
            assert len(tasks) == 1
            assert tasks[0]["id"] == "test_cron"
        finally:
            svc.stop()

    @pytest.mark.asyncio
    async def test_add_cron_task_replace_existing(self) -> None:
        """Adding a cron task with the same id replaces the old one."""
        svc = _make_service()
        svc.start()
        try:
            svc.add_cron_task("dup_cron", lambda: None, hour=1)
            svc.add_cron_task("dup_cron", lambda: None, hour=5)
            tasks = svc.list_tasks()
            assert len(tasks) == 1
        finally:
            svc.stop()


class TestIntervalTask:
    """Adding interval tasks."""

    @pytest.mark.asyncio
    async def test_add_interval_task_appears_in_list(self) -> None:
        """An interval task is visible in list_tasks after adding."""
        svc = _make_service()
        svc.start()
        try:
            svc.add_interval_task("test_interval", lambda: None, seconds=60)
            tasks = svc.list_tasks()
            assert len(tasks) == 1
            assert tasks[0]["id"] == "test_interval"
        finally:
            svc.stop()


class TestRemovePauseResume:
    """Remove, pause, and resume tasks."""

    @pytest.mark.asyncio
    async def test_remove_existing_task(self) -> None:
        """remove_task returns True for an existing task."""
        svc = _make_service()
        svc.start()
        try:
            svc.add_interval_task("rm_me", lambda: None, seconds=60)
            assert svc.remove_task("rm_me") is True
            assert svc.list_tasks() == []
        finally:
            svc.stop()

    @pytest.mark.asyncio
    async def test_remove_missing_task(self) -> None:
        """remove_task returns False for a non-existent task."""
        svc = _make_service()
        svc.start()
        try:
            assert svc.remove_task("ghost") is False
        finally:
            svc.stop()

    @pytest.mark.asyncio
    async def test_pause_existing_task(self) -> None:
        """pause_task returns True and sets next_run_time to None."""
        svc = _make_service()
        svc.start()
        try:
            svc.add_interval_task("pause_me", lambda: None, seconds=60)
            assert svc.pause_task("pause_me") is True
            tasks = svc.list_tasks()
            assert tasks[0]["next_run_time"] is None
        finally:
            svc.stop()

    @pytest.mark.asyncio
    async def test_resume_paused_task(self) -> None:
        """resume_task returns True and restores next_run_time."""
        svc = _make_service()
        svc.start()
        try:
            svc.add_interval_task("resume_me", lambda: None, seconds=60)
            svc.pause_task("resume_me")
            assert svc.resume_task("resume_me") is True
            tasks = svc.list_tasks()
            assert tasks[0]["next_run_time"] is not None
        finally:
            svc.stop()

    @pytest.mark.asyncio
    async def test_pause_missing_task(self) -> None:
        """pause_task returns False for a non-existent task."""
        svc = _make_service()
        svc.start()
        try:
            assert svc.pause_task("ghost") is False
        finally:
            svc.stop()

    @pytest.mark.asyncio
    async def test_resume_missing_task(self) -> None:
        """resume_task returns False for a non-existent task."""
        svc = _make_service()
        svc.start()
        try:
            assert svc.resume_task("ghost") is False
        finally:
            svc.stop()
