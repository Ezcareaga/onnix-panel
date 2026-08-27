"""Tests for the InfoCasas poll scheduled task.

All tests use mocked services; no real DB or network required.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from app.bot.scheduler.tasks.infocasas_poll import (
    InfocasasPollTask,
    run_infocasas_poll,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_service(*, result: dict | None = None) -> AsyncMock:
    """Return a mock InfocasasService with a configurable run_poll result."""
    svc = AsyncMock()
    svc.run_poll = AsyncMock(
        return_value=result
        or {"status": "ok", "processed": 3, "new": 1, "skipped": 2, "errors": 0}
    )
    return svc


# ---------------------------------------------------------------------------
# TestInfocasasPollTask
# ---------------------------------------------------------------------------


class TestInfocasasPollTask:
    """Unit tests for InfocasasPollTask.run()."""

    @pytest.mark.asyncio
    async def test_run_calls_service_run_poll(self):
        """run() delegates to the injected service's run_poll method."""
        mock_svc = _make_mock_service()
        task = InfocasasPollTask(service=mock_svc)

        await task.run()

        mock_svc.run_poll.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_returns_metrics(self):
        """run() returns the dict produced by run_poll."""
        expected = {"status": "ok", "processed": 5, "new": 2, "skipped": 3, "errors": 0}
        mock_svc = _make_mock_service(result=expected)
        task = InfocasasPollTask(service=mock_svc)

        result = await task.run()

        assert result == expected

    @pytest.mark.asyncio
    async def test_run_handles_exception(self):
        """run() catches unhandled exceptions and returns an error dict."""
        mock_svc = AsyncMock()
        mock_svc.run_poll = AsyncMock(side_effect=RuntimeError("network timeout"))
        task = InfocasasPollTask(service=mock_svc)

        result = await task.run()

        assert result["status"] == "error"
        assert result["processed"] == 0
        assert result["new"] == 0
        assert result["skipped"] == 0
        assert result["errors"] == 1

    @pytest.mark.asyncio
    async def test_run_logs_metrics(self, caplog):
        """run() emits an INFO log line containing task name and metrics."""
        import logging

        mock_svc = _make_mock_service(
            result={"status": "ok", "processed": 4, "new": 2, "skipped": 2, "errors": 0}
        )
        task = InfocasasPollTask(service=mock_svc)

        with caplog.at_level(logging.INFO, logger="app.bot.scheduler.tasks.infocasas_poll"):
            await task.run()

        assert any("infocasas_poll" in record.message for record in caplog.records)
        assert any("duration_ms" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_run_lazy_imports_service_when_none(self):
        """When no service is injected, run() imports and calls get_infocasas_service."""
        fake_svc = _make_mock_service()
        fake_factory = MagicMock(return_value=fake_svc)

        task = InfocasasPollTask()

        with patch(
            "app.bot.scheduler.tasks.infocasas_poll.InfocasasPollTask.run",
            wraps=task.run,
        ):
            with patch.dict(
                "sys.modules",
                {
                    "app.bot.services.infocasas.infocasas_service": MagicMock(
                        get_infocasas_service=fake_factory
                    )
                },
            ):
                result = await task.run()

        # After run(), self._service should be set (not None again)
        assert task._service is not None

    @pytest.mark.asyncio
    async def test_run_error_dict_has_all_keys(self):
        """Error fallback dict includes all expected metric keys."""
        mock_svc = AsyncMock()
        mock_svc.run_poll = AsyncMock(side_effect=ValueError("boom"))
        task = InfocasasPollTask(service=mock_svc)

        result = await task.run()

        for key in ("status", "processed", "new", "skipped", "errors"):
            assert key in result, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# TestRunInfocasasPoll
# ---------------------------------------------------------------------------


class TestRunInfocasasPoll:
    """Tests for the module-level run_infocasas_poll() factory function."""

    @pytest.mark.asyncio
    async def test_factory_creates_task_and_runs(self):
        """run_infocasas_poll() creates a fresh InfocasasPollTask and calls run()."""
        mock_result = {"status": "ok", "processed": 2, "new": 1, "skipped": 1, "errors": 0}

        with patch(
            "app.bot.scheduler.tasks.infocasas_poll.InfocasasPollTask"
        ) as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.run = AsyncMock(return_value=mock_result)
            mock_cls.return_value = mock_instance

            result = await run_infocasas_poll()

            assert result == mock_result
            mock_cls.assert_called_once_with()
            mock_instance.run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_factory_returns_result(self):
        """run_infocasas_poll() propagates the dict returned by run()."""
        expected = {"status": "ok", "processed": 7, "new": 3, "skipped": 4, "errors": 0}

        with patch(
            "app.bot.scheduler.tasks.infocasas_poll.InfocasasPollTask"
        ) as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.run = AsyncMock(return_value=expected)
            mock_cls.return_value = mock_instance

            result = await run_infocasas_poll()

        assert result == expected


# ---------------------------------------------------------------------------
# TestLifespanRegistration
# ---------------------------------------------------------------------------


def _make_mock_scheduler_svc(*, running: bool = True) -> MagicMock:
    svc = MagicMock()
    svc.start = MagicMock()
    svc.stop = MagicMock()
    type(svc).is_running = PropertyMock(return_value=running)
    svc.list_tasks.return_value = []
    svc.add_cron_task = MagicMock()
    svc.add_interval_task = MagicMock()
    return svc


def _make_mock_settings_manager(
    *,
    cold_lead_enabled: bool = True,
    daily_report_enabled: bool = True,
    heartbeat_enabled: bool = True,
    infocasas_poll_enabled: bool = True,
    cleanup_inactive_refs_enabled: bool = True,
) -> MagicMock:
    mgr = MagicMock()

    async def fake_is_task_enabled(task_id: str) -> bool:
        mapping = {
            "cold_lead_check": cold_lead_enabled,
            "daily_report": daily_report_enabled,
            "heartbeat": heartbeat_enabled,
            "infocasas_poll": infocasas_poll_enabled,
            "cleanup_inactive_refs": cleanup_inactive_refs_enabled,
        }
        return mapping.get(task_id, True)

    mgr.is_task_enabled = AsyncMock(side_effect=fake_is_task_enabled)
    return mgr


class TestLifespanRegistration:
    """Lifespan correctly registers / skips infocasas_poll based on settings."""

    @pytest.mark.asyncio
    async def test_infocasas_poll_registered_when_enabled(self):
        """add_interval_task is called for infocasas_poll when it is enabled."""
        from fastapi import FastAPI
        from app.bot.scheduler.lifespan import scheduler_lifespan

        mock_svc = _make_mock_scheduler_svc()
        mock_mgr = _make_mock_settings_manager(infocasas_poll_enabled=True)
        test_app = FastAPI()

        with (
            patch("app.bot.scheduler.lifespan.bot_settings") as mock_settings,
            patch("app.bot.scheduler.lifespan.SchedulerService", return_value=mock_svc),
            patch("app.bot.scheduler.lifespan.SettingsManager", return_value=mock_mgr),
            patch("app.bot.scheduler.lifespan.run_cold_lead_check"),
            patch("app.bot.scheduler.lifespan.run_daily_report"),
            patch("app.bot.scheduler.lifespan.run_heartbeat"),
            patch("app.bot.scheduler.lifespan.run_infocasas_poll") as mock_run,
        ):
            mock_settings.SCHEDULER_ENABLED = True
            async with scheduler_lifespan(test_app):
                pass

        # Verify infocasas_poll interval registration
        interval_calls = [
            call
            for call in mock_svc.add_interval_task.call_args_list
            if call.args and call.args[0] == "infocasas_poll"
        ]
        assert len(interval_calls) == 1
        call = interval_calls[0]
        # Second positional arg is the callable
        assert call.args[1] is mock_run
        # minutes=5 passed as kwarg
        assert call.kwargs.get("minutes") == 5

    @pytest.mark.asyncio
    async def test_infocasas_poll_skipped_when_disabled(self):
        """add_interval_task is NOT called for infocasas_poll when disabled."""
        from fastapi import FastAPI
        from app.bot.scheduler.lifespan import scheduler_lifespan

        mock_svc = _make_mock_scheduler_svc()
        mock_mgr = _make_mock_settings_manager(
            # disable only infocasas_poll; heartbeat still uses add_interval_task
            heartbeat_enabled=False,
            infocasas_poll_enabled=False,
        )
        test_app = FastAPI()

        with (
            patch("app.bot.scheduler.lifespan.bot_settings") as mock_settings,
            patch("app.bot.scheduler.lifespan.SchedulerService", return_value=mock_svc),
            patch("app.bot.scheduler.lifespan.SettingsManager", return_value=mock_mgr),
            patch("app.bot.scheduler.lifespan.run_cold_lead_check"),
            patch("app.bot.scheduler.lifespan.run_daily_report"),
            patch("app.bot.scheduler.lifespan.run_heartbeat"),
            patch("app.bot.scheduler.lifespan.run_infocasas_poll"),
        ):
            mock_settings.SCHEDULER_ENABLED = True
            async with scheduler_lifespan(test_app):
                pass

        # No interval task should be registered when both are disabled
        mock_svc.add_interval_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_existing_tasks_unaffected_by_infocasas_registration(self):
        """Adding infocasas_poll does not change cold_lead_check or daily_report."""
        from fastapi import FastAPI
        from app.bot.scheduler.lifespan import scheduler_lifespan

        mock_svc = _make_mock_scheduler_svc()
        mock_mgr = _make_mock_settings_manager()
        test_app = FastAPI()

        with (
            patch("app.bot.scheduler.lifespan.bot_settings") as mock_settings,
            patch("app.bot.scheduler.lifespan.SchedulerService", return_value=mock_svc),
            patch("app.bot.scheduler.lifespan.SettingsManager", return_value=mock_mgr),
            patch("app.bot.scheduler.lifespan.run_cold_lead_check"),
            patch("app.bot.scheduler.lifespan.run_daily_report"),
            patch("app.bot.scheduler.lifespan.run_heartbeat"),
            patch("app.bot.scheduler.lifespan.run_infocasas_poll"),
            patch("app.bot.scheduler.lifespan.run_cleanup_inactive_refs"),
        ):
            mock_settings.SCHEDULER_ENABLED = True
            async with scheduler_lifespan(test_app):
                pass

        # cold_lead_check + daily_report + cleanup_inactive_refs + verification_scraper → 4 cron calls
        assert mock_svc.add_cron_task.call_count == 5
        # heartbeat + infocasas_poll → 2 interval calls
        assert mock_svc.add_interval_task.call_count == 2
