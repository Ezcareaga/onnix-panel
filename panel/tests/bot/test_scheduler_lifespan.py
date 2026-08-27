"""Tests for scheduler lifespan and /health/scheduler endpoint.

Plan 67-03 — lifespan wiring tests.
All tests use mocked SchedulerService and SettingsManager; no real DB/APScheduler.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import AsyncClient, ASGITransport

from app.bot.scheduler.lifespan import scheduler_lifespan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_scheduler(*, running: bool = True):
    """Create a mock SchedulerService."""
    svc = MagicMock()
    svc.start = MagicMock()
    svc.stop = MagicMock()
    type(svc).is_running = PropertyMock(return_value=running)
    svc.list_tasks.return_value = [
        {"id": "cold_lead_check", "name": "cold_lead_check", "trigger": "cron", "next_run_time": "2026-03-28T09:00:00"},
        {"id": "heartbeat", "name": "heartbeat", "trigger": "interval", "next_run_time": "2026-03-27T13:00:00"},
    ]
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
    verification_scraper_enabled: bool = True,
):
    """Create a mock SettingsManager."""
    mgr = MagicMock()

    async def fake_is_task_enabled(task_id: str) -> bool:
        if task_id == "cold_lead_check":
            return cold_lead_enabled
        if task_id == "daily_report":
            return daily_report_enabled
        if task_id == "heartbeat":
            return heartbeat_enabled
        if task_id == "infocasas_poll":
            return infocasas_poll_enabled
        if task_id == "cleanup_inactive_refs":
            return cleanup_inactive_refs_enabled
        if task_id == "verification_scraper":
            return verification_scraper_enabled
        return True

    mgr.is_task_enabled = AsyncMock(side_effect=fake_is_task_enabled)
    return mgr


# ---------------------------------------------------------------------------
# Tests: Scheduler starts and stops
# ---------------------------------------------------------------------------

class TestSchedulerStartsStops:
    """Lifespan starts and stops the scheduler."""

    @pytest.mark.asyncio
    async def test_scheduler_starts_on_lifespan(self):
        """SchedulerService.start() is called during startup."""
        mock_svc = _make_mock_scheduler()
        mock_mgr = _make_mock_settings_manager()
        test_app = FastAPI()

        with (
            patch("app.bot.scheduler.lifespan.bot_settings") as mock_bot_settings,
            patch("app.bot.scheduler.lifespan.SchedulerService", return_value=mock_svc),
            patch("app.bot.scheduler.lifespan.SettingsManager", return_value=mock_mgr),
            patch("app.bot.scheduler.lifespan.run_cold_lead_check"),
            patch("app.bot.scheduler.lifespan.run_daily_report"),
            patch("app.bot.scheduler.lifespan.run_heartbeat"),
        ):
            mock_bot_settings.SCHEDULER_ENABLED = True
            async with scheduler_lifespan(test_app):
                mock_svc.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_scheduler_stops_on_shutdown(self):
        """SchedulerService.stop() is called during shutdown."""
        mock_svc = _make_mock_scheduler(running=True)
        mock_mgr = _make_mock_settings_manager()
        test_app = FastAPI()

        with (
            patch("app.bot.scheduler.lifespan.bot_settings") as mock_bot_settings,
            patch("app.bot.scheduler.lifespan.SchedulerService", return_value=mock_svc),
            patch("app.bot.scheduler.lifespan.SettingsManager", return_value=mock_mgr),
            patch("app.bot.scheduler.lifespan.run_cold_lead_check"),
            patch("app.bot.scheduler.lifespan.run_daily_report"),
            patch("app.bot.scheduler.lifespan.run_heartbeat"),
        ):
            mock_bot_settings.SCHEDULER_ENABLED = True
            async with scheduler_lifespan(test_app):
                pass  # lifespan entered

        mock_svc.stop.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: Disabled when SCHEDULER_ENABLED=False
# ---------------------------------------------------------------------------

class TestSchedulerDisabled:
    """Scheduler is not created when SCHEDULER_ENABLED=False."""

    @pytest.mark.asyncio
    async def test_disabled_no_scheduler_created(self):
        """When SCHEDULER_ENABLED=False, no SchedulerService is created."""
        test_app = FastAPI()

        with patch("app.bot.scheduler.lifespan.bot_settings") as mock_bot_settings:
            mock_bot_settings.SCHEDULER_ENABLED = False
            async with scheduler_lifespan(test_app):
                assert test_app.state.scheduler is None


# ---------------------------------------------------------------------------
# Tests: Respects per-task settings
# ---------------------------------------------------------------------------

class TestPerTaskSettings:
    """SettingsManager per-task enabled/disabled is respected."""

    @pytest.mark.asyncio
    async def test_cold_lead_disabled_heartbeat_enabled(self):
        """When cold_lead_check is disabled, the other three crons are registered."""
        mock_svc = _make_mock_scheduler()
        mock_mgr = _make_mock_settings_manager(
            cold_lead_enabled=False,
            heartbeat_enabled=True,
        )
        test_app = FastAPI()

        with (
            patch("app.bot.scheduler.lifespan.bot_settings") as mock_bot_settings,
            patch("app.bot.scheduler.lifespan.SchedulerService", return_value=mock_svc),
            patch("app.bot.scheduler.lifespan.SettingsManager", return_value=mock_mgr),
            patch("app.bot.scheduler.lifespan.run_cold_lead_check"),
            patch("app.bot.scheduler.lifespan.run_daily_report"),
            patch("app.bot.scheduler.lifespan.run_heartbeat"),
            patch("app.bot.scheduler.lifespan.run_infocasas_poll"),
            patch("app.bot.scheduler.lifespan.run_cleanup_inactive_refs"),
        ):
            mock_bot_settings.SCHEDULER_ENABLED = True
            async with scheduler_lifespan(test_app):
                pass

        # cold_lead disabled → daily_report + cleanup_inactive_refs + verification_scraper
        assert mock_svc.add_cron_task.call_count == 3
        # heartbeat + infocasas_poll are both interval tasks (both enabled by default)
        assert mock_svc.add_interval_task.call_count == 2

    @pytest.mark.asyncio
    async def test_both_tasks_disabled(self):
        """When all tasks disabled, scheduler starts with no tasks."""
        mock_svc = _make_mock_scheduler()
        mock_mgr = _make_mock_settings_manager(
            cold_lead_enabled=False,
            daily_report_enabled=False,
            heartbeat_enabled=False,
            infocasas_poll_enabled=False,
            cleanup_inactive_refs_enabled=False,
            verification_scraper_enabled=False,
        )
        test_app = FastAPI()

        with (
            patch("app.bot.scheduler.lifespan.bot_settings") as mock_bot_settings,
            patch("app.bot.scheduler.lifespan.SchedulerService", return_value=mock_svc),
            patch("app.bot.scheduler.lifespan.SettingsManager", return_value=mock_mgr),
            patch("app.bot.scheduler.lifespan.run_cold_lead_check"),
            patch("app.bot.scheduler.lifespan.run_daily_report"),
            patch("app.bot.scheduler.lifespan.run_heartbeat"),
            patch("app.bot.scheduler.lifespan.run_infocasas_poll"),
            patch("app.bot.scheduler.lifespan.run_cleanup_inactive_refs"),
        ):
            mock_bot_settings.SCHEDULER_ENABLED = True
            async with scheduler_lifespan(test_app):
                pass

        mock_svc.add_cron_task.assert_not_called()
        mock_svc.add_interval_task.assert_not_called()
        mock_svc.start.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: Stored on app.state
# ---------------------------------------------------------------------------

class TestAppState:
    """SchedulerService is stored on app.state.scheduler."""

    @pytest.mark.asyncio
    async def test_scheduler_on_app_state(self):
        """app.state.scheduler is the SchedulerService instance."""
        mock_svc = _make_mock_scheduler()
        mock_mgr = _make_mock_settings_manager()
        test_app = FastAPI()

        with (
            patch("app.bot.scheduler.lifespan.bot_settings") as mock_bot_settings,
            patch("app.bot.scheduler.lifespan.SchedulerService", return_value=mock_svc),
            patch("app.bot.scheduler.lifespan.SettingsManager", return_value=mock_mgr),
            patch("app.bot.scheduler.lifespan.run_cold_lead_check"),
            patch("app.bot.scheduler.lifespan.run_daily_report"),
            patch("app.bot.scheduler.lifespan.run_heartbeat"),
        ):
            mock_bot_settings.SCHEDULER_ENABLED = True
            async with scheduler_lifespan(test_app):
                assert test_app.state.scheduler is mock_svc


# ---------------------------------------------------------------------------
# Tests: Startup failure is non-fatal
# ---------------------------------------------------------------------------

class TestStartupFailureNonFatal:
    """Scheduler startup failure must not crash the panel."""

    @pytest.mark.asyncio
    async def test_startup_exception_non_fatal(self):
        """Panel continues when SchedulerService() raises."""
        test_app = FastAPI()

        with (
            patch("app.bot.scheduler.lifespan.bot_settings") as mock_bot_settings,
            patch(
                "app.bot.scheduler.lifespan.SchedulerService",
                side_effect=Exception("APScheduler exploded"),
            ),
            patch("app.bot.scheduler.lifespan.SettingsManager"),
            patch("app.bot.scheduler.lifespan.run_cold_lead_check"),
            patch("app.bot.scheduler.lifespan.run_daily_report"),
            patch("app.bot.scheduler.lifespan.run_heartbeat"),
        ):
            mock_bot_settings.SCHEDULER_ENABLED = True
            # Should NOT raise — scheduler failure is caught
            async with scheduler_lifespan(test_app):
                assert test_app.state.scheduler is None


# ---------------------------------------------------------------------------
# Tests: /health/scheduler endpoint
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    """GET /health/scheduler returns scheduler status."""

    @pytest.mark.asyncio
    async def test_health_disabled(self):
        """Returns status=disabled when scheduler is None."""
        test_app = FastAPI()

        @test_app.get("/health/scheduler")
        async def health_scheduler(request: Request):
            scheduler = getattr(request.app.state, "scheduler", None)
            if scheduler is None or not scheduler.is_running:
                return JSONResponse({"status": "disabled", "tasks": []})
            return JSONResponse({"status": "running", "tasks": scheduler.list_tasks()})

        # Set state directly — no lifespan needed for endpoint test
        test_app.state.scheduler = None

        async with AsyncClient(
            transport=ASGITransport(app=test_app),
            base_url="http://test",
        ) as client:
            resp = await client.get("/health/scheduler")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "disabled"
            assert data["tasks"] == []

    @pytest.mark.asyncio
    async def test_health_running(self):
        """Returns status=running with tasks when scheduler is active."""
        mock_svc = _make_mock_scheduler(running=True)
        test_app = FastAPI()

        @test_app.get("/health/scheduler")
        async def health_scheduler(request: Request):
            scheduler = getattr(request.app.state, "scheduler", None)
            if scheduler is None or not scheduler.is_running:
                return JSONResponse({"status": "disabled", "tasks": []})
            return JSONResponse({"status": "running", "tasks": scheduler.list_tasks()})

        # Set state directly with mock scheduler
        test_app.state.scheduler = mock_svc

        async with AsyncClient(
            transport=ASGITransport(app=test_app),
            base_url="http://test",
        ) as client:
            resp = await client.get("/health/scheduler")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "running"
            assert len(data["tasks"]) == 2
