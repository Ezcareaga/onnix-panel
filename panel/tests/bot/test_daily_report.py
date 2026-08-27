"""Tests for daily report scheduler task.

All tests use mocked sessions and SMTP; no real DB or email required.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.scheduler.tasks.daily_report import (
    DailyReportGenerator,
    run_daily_report,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session_factory(
    *,
    leads_count: int = 5,
    messages_count: int = 100,
    errors_count: int = 2,
    infocasas_count: int = 3,
    heartbeat_fails: int = 0,
    active_properties: int = 19000,
):
    """Build a mock async session factory returning predictable scalars."""
    mock_session = AsyncMock()

    # leads_count query
    leads_scalar = MagicMock()
    leads_scalar.scalar_one.return_value = leads_count

    # leads_detail query (fetchall)
    leads_detail = MagicMock()
    lead_row = MagicMock()
    lead_row.name = "Maria"
    lead_row.phone = "+595981234567"
    lead_row.source = "whatsapp"
    leads_detail.fetchall.return_value = [lead_row]

    # messages_count
    messages_scalar = MagicMock()
    messages_scalar.scalar_one.return_value = messages_count

    # errors_count
    errors_scalar = MagicMock()
    errors_scalar.scalar_one.return_value = errors_count

    # error_summary (fetchall)
    error_summary = MagicMock()
    err_row = MagicMock()
    err_row.workflow = "whatsapp"
    err_row.cnt = 2
    error_summary.fetchall.return_value = [err_row]

    # infocasas_count
    ic_scalar = MagicMock()
    ic_scalar.scalar_one.return_value = infocasas_count

    # heartbeat_fails
    hb_scalar = MagicMock()
    hb_scalar.scalar_one.return_value = heartbeat_fails

    # active_properties
    props_scalar = MagicMock()
    props_scalar.scalar_one.return_value = active_properties

    mock_session.execute = AsyncMock(side_effect=[
        leads_scalar,       # 1. leads count
        leads_detail,       # 2. leads detail
        messages_scalar,    # 3. messages count
        errors_scalar,      # 4. errors count
        error_summary,      # 5. error summary
        ic_scalar,          # 6. infocasas count
        hb_scalar,          # 7. heartbeat fails
        props_scalar,       # 8. active properties
    ])

    # Match the pattern used by heartbeat tests:
    # factory() returns an async context manager
    mock_factory = MagicMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory.return_value = mock_ctx

    return mock_factory


# ---------------------------------------------------------------------------
# Tests: gather metrics
# ---------------------------------------------------------------------------

class TestGatherMetrics:
    """DailyReportGenerator._gather_metrics() queries and aggregates data."""

    @pytest.mark.asyncio
    async def test_gather_metrics_returns_expected_keys(self):
        """All expected metric keys are present."""
        notifier = AsyncMock()
        notifier.notify = AsyncMock(return_value=True)

        gen = DailyReportGenerator(
            smtp_email="", smtp_password="", report_to="",
            session_factory=_make_session_factory(),
            notifier=notifier,
        )
        metrics = await gen._gather_metrics()

        expected_keys = {
            "date", "leads_count", "leads_list", "messages_count",
            "errors_count", "error_summary", "infocasas_count",
            "heartbeat_fails", "heartbeat_status", "active_properties",
        }
        assert expected_keys.issubset(metrics.keys())

    @pytest.mark.asyncio
    async def test_gather_metrics_values(self):
        """Metrics reflect the mocked DB data."""
        notifier = AsyncMock()
        notifier.notify = AsyncMock(return_value=True)

        gen = DailyReportGenerator(
            smtp_email="", smtp_password="", report_to="",
            session_factory=_make_session_factory(
                leads_count=10, messages_count=200, errors_count=5,
            ),
            notifier=notifier,
        )
        metrics = await gen._gather_metrics()

        assert metrics["leads_count"] == 10
        assert metrics["messages_count"] == 200
        assert metrics["errors_count"] == 5

    @pytest.mark.asyncio
    async def test_heartbeat_status_ok(self):
        """heartbeat_status is 'OK' when no failures."""
        notifier = AsyncMock()
        notifier.notify = AsyncMock(return_value=True)

        gen = DailyReportGenerator(
            smtp_email="", smtp_password="", report_to="",
            session_factory=_make_session_factory(heartbeat_fails=0),
            notifier=notifier,
        )
        metrics = await gen._gather_metrics()
        assert metrics["heartbeat_status"] == "OK"

    @pytest.mark.asyncio
    async def test_heartbeat_status_fail(self):
        """heartbeat_status shows failure count when > 0."""
        notifier = AsyncMock()
        notifier.notify = AsyncMock(return_value=True)

        gen = DailyReportGenerator(
            smtp_email="", smtp_password="", report_to="",
            session_factory=_make_session_factory(heartbeat_fails=3),
            notifier=notifier,
        )
        metrics = await gen._gather_metrics()
        assert "FAIL" in metrics["heartbeat_status"]
        assert "3" in metrics["heartbeat_status"]


# ---------------------------------------------------------------------------
# Tests: send email
# ---------------------------------------------------------------------------

class TestSendEmail:
    """DailyReportGenerator._send_email() sends via SMTP."""

    def test_send_email_skips_when_no_config(self):
        """Returns False when SMTP is not configured."""
        gen = DailyReportGenerator(
            smtp_email="", smtp_password="", report_to="",
        )
        result = gen._send_email({"date": "2026-03-28", "leads_count": 0,
                                   "leads_list": [], "messages_count": 0,
                                   "errors_count": 0, "error_summary": [],
                                   "infocasas_count": 0, "heartbeat_fails": 0,
                                   "heartbeat_status": "OK",
                                   "active_properties": 0})
        assert result is False

    @patch("app.bot.scheduler.tasks.daily_report.smtplib.SMTP")
    def test_send_email_success(self, mock_smtp_cls):
        """Returns True when email sends successfully."""
        mock_smtp = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        gen = DailyReportGenerator(
            smtp_email="test@gmail.com",
            smtp_password="app_password",
            report_to="admin@example.com",
        )
        metrics = {
            "date": "2026-03-28", "leads_count": 5,
            "leads_list": [{"name": "Test", "phone": "+595", "source": "wa"}],
            "messages_count": 100, "errors_count": 2,
            "error_summary": [{"workflow": "wa", "count": 2}],
            "infocasas_count": 3, "heartbeat_fails": 0,
            "heartbeat_status": "OK", "active_properties": 19000,
        }
        result = gen._send_email(metrics)
        assert result is True
        mock_smtp.login.assert_called_once_with("test@gmail.com", "app_password")
        mock_smtp.sendmail.assert_called_once()

    @patch("app.bot.scheduler.tasks.daily_report.smtplib.SMTP")
    def test_send_email_failure_returns_false(self, mock_smtp_cls):
        """Returns False on SMTP error (never raises)."""
        mock_smtp_cls.return_value.__enter__ = MagicMock(
            side_effect=Exception("SMTP down")
        )
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        gen = DailyReportGenerator(
            smtp_email="test@gmail.com",
            smtp_password="app_password",
            report_to="admin@example.com",
        )
        result = gen._send_email({"date": "2026-03-28", "leads_count": 0,
                                   "leads_list": [], "messages_count": 0,
                                   "errors_count": 0, "error_summary": [],
                                   "infocasas_count": 0, "heartbeat_fails": 0,
                                   "heartbeat_status": "OK",
                                   "active_properties": 0})
        assert result is False


# ---------------------------------------------------------------------------
# Tests: full run
# ---------------------------------------------------------------------------

class TestRun:
    """DailyReportGenerator.run() orchestrates metrics + email + telegram."""

    @pytest.mark.asyncio
    async def test_run_returns_metrics_with_send_status(self):
        """run() returns metrics dict with email_sent and tg_sent."""
        notifier = AsyncMock()
        notifier.notify = AsyncMock(return_value=True)

        gen = DailyReportGenerator(
            smtp_email="", smtp_password="", report_to="",
            session_factory=_make_session_factory(),
            notifier=notifier,
        )
        result = await gen.run()

        assert "leads_count" in result
        assert "email_sent" in result
        assert "tg_sent" in result
        # No SMTP config → email_sent=False
        assert result["email_sent"] is False
        # Telegram notifier mocked → tg_sent=True
        assert result["tg_sent"] is True

    @pytest.mark.asyncio
    async def test_run_sends_telegram_summary(self):
        """run() calls notifier.notify() with summary text."""
        notifier = AsyncMock()
        notifier.notify = AsyncMock(return_value=True)

        gen = DailyReportGenerator(
            smtp_email="", smtp_password="", report_to="",
            session_factory=_make_session_factory(leads_count=7),
            notifier=notifier,
        )
        await gen.run()

        notifier.notify.assert_awaited_once()
        msg = notifier.notify.call_args[0][0]
        assert "Reporte Diario" in msg
        assert "7" in msg


# ---------------------------------------------------------------------------
# Tests: factory function
# ---------------------------------------------------------------------------

class TestRunDailyReport:
    """run_daily_report() reads config and creates the generator."""

    @pytest.mark.asyncio
    async def test_factory_creates_generator(self):
        """run_daily_report() calls DailyReportGenerator.run()."""
        with patch(
            "app.bot.scheduler.tasks.daily_report.DailyReportGenerator"
        ) as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.run = AsyncMock(return_value={"ok": True})
            mock_cls.return_value = mock_instance

            result = await run_daily_report()

            assert result == {"ok": True}
            mock_cls.assert_called_once()
            mock_instance.run.assert_awaited_once()
