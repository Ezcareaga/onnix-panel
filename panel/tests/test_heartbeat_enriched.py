"""Tests for the enriched heartbeat — health snapshot + bot_errors on failure.

Fase F of M1 Observabilidad plan.
All tests use mocked sessions; no real DB or network required.

Design note: BotErrorService and MetricsService are imported *inside* private
methods of HeartbeatChecker (deferred imports).  We patch them at their
canonical module paths:
  - app.bot.services.error_service.BotErrorService
  - app.services.metrics_service.MetricsService
which is the correct target for deferred imports.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.scheduler.tasks.heartbeat import HeartbeatChecker, run_heartbeat
from app.bot.services.admin_notifier import AdminNotifier
from app.schemas.metrics import (
    AiCost,
    BotHealthSnapshot,
    Costs,
    ErrorBreakdown,
    HeartbeatStatus,
    Latency,
    MessageVolume,
    ProviderMix,
    StuckConversations,
    ToolIterations,
    TwilioUsage,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_snapshot() -> BotHealthSnapshot:
    """Return a canned BotHealthSnapshot for testing."""
    now = datetime(2026, 4, 18, 10, 0, 0, tzinfo=timezone.utc)
    ai = AiCost(claude_usd=0.0012, gemini_usd=0.0003, total_usd=0.0015, messages=45)
    tw = TwilioUsage(total_usd=1.50, whatsapp_usd=1.20, other_usd=0.30)
    costs = Costs(
        ai_today=ai, ai_month=ai,
        twilio_today=tw, twilio_month=tw,
        total_today_usd=round(0.0015 + 1.50, 2),
        total_month_usd=round(0.0015 + 1.50, 2),
    )
    return BotHealthSnapshot(
        generated_at=now,
        stuck_conversations=StuckConversations(count=2),
        message_volume=MessageVolume(inbound=50, bot_out=45, agent_out=5, total=100),
        latency=Latency(avg_ms=120, p95_ms=350, worst_ms=900, n=45),
        provider_mix=ProviderMix(claude=40, gemini=5, pct_fallback=11.1),
        tool_iterations=ToolIterations(avg=1.8, max=4, zero_tools=3, high_iters=2, n=45),
        heartbeat=HeartbeatStatus(
            last_failure_at=None,
            last_failure_ago_seconds=None,
            next_expected_in_seconds=3600,
        ),
        errors=ErrorBreakdown(by_workflow={"telegram": 1}, total=1),
        costs=costs,
    )


def _make_healthy_session_factory():
    """Async session factory where SELECT 1 succeeds."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock())
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=mock_ctx)
    return factory


def _make_unhealthy_session_factory():
    """Async session factory where SELECT 1 raises."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=Exception("connection refused"))
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=mock_ctx)
    return factory


def _make_mock_notifier(*, snapshot_return: bool = True) -> MagicMock:
    """Build a mock AdminNotifier that tracks all async calls."""
    notifier = MagicMock(spec=AdminNotifier)
    notifier.notify_heartbeat_failure = AsyncMock(return_value=True)
    notifier.notify_heartbeat_snapshot = AsyncMock(return_value=snapshot_return)
    notifier.chat_id = "999"
    notifier.bot_token = "tok"
    return notifier


# ---------------------------------------------------------------------------
# Test F.2a — snapshot obtained and Telegram notified on success
# ---------------------------------------------------------------------------

class TestHeartbeatSuccessObtainsSnapshotAndNotifies:
    """On DB success, heartbeat pulls a snapshot and sends a Telegram summary."""

    @pytest.mark.asyncio
    async def test_snapshot_obtained_and_notified(self):
        """notify_heartbeat_snapshot is called once with the canned snapshot."""
        snapshot = _make_snapshot()
        factory = _make_healthy_session_factory()
        notifier = _make_mock_notifier()

        checker = HeartbeatChecker(
            notification_chat_id="999",
            telegram_bot_token="tok",
            session_factory=factory,
            notifier=notifier,
        )

        # Patch MetricsService at its canonical module path (deferred import)
        with patch(
            "app.services.metrics_service.MetricsService"
        ) as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.get_bot_health = AsyncMock(return_value=snapshot)
            mock_svc_cls.return_value = mock_svc

            result = await checker.run()

        assert result["db_healthy"] is True
        notifier.notify_heartbeat_snapshot.assert_awaited_once_with(snapshot)

    @pytest.mark.asyncio
    async def test_notify_heartbeat_failure_not_called_on_success(self):
        """notify_heartbeat_failure must NOT be called when DB is healthy."""
        snapshot = _make_snapshot()
        factory = _make_healthy_session_factory()
        notifier = _make_mock_notifier()

        checker = HeartbeatChecker(
            notification_chat_id="999",
            telegram_bot_token="tok",
            session_factory=factory,
            notifier=notifier,
        )

        with patch("app.services.metrics_service.MetricsService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.get_bot_health = AsyncMock(return_value=snapshot)
            mock_svc_cls.return_value = mock_svc

            await checker.run()

        notifier.notify_heartbeat_failure.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_snapshot_exception_does_not_crash_heartbeat(self):
        """If MetricsService raises, the heartbeat run() must still return successfully."""
        factory = _make_healthy_session_factory()
        notifier = _make_mock_notifier()

        checker = HeartbeatChecker(
            notification_chat_id="999",
            telegram_bot_token="tok",
            session_factory=factory,
            notifier=notifier,
        )

        with patch("app.services.metrics_service.MetricsService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.get_bot_health = AsyncMock(side_effect=Exception("DB blew up"))
            mock_svc_cls.return_value = mock_svc

            result = await checker.run()

        assert result["db_healthy"] is True  # heartbeat still passes


# ---------------------------------------------------------------------------
# Test F.2b — structured log emitted on success
# ---------------------------------------------------------------------------

class TestHeartbeatSuccessEmitsStructuredLog:
    """heartbeat.success log record contains all required structured keys."""

    @pytest.mark.asyncio
    async def test_structured_log_keys_present(self, caplog):
        """logger.info('heartbeat.success', extra=...) emits required keys."""
        snapshot = _make_snapshot()
        factory = _make_healthy_session_factory()
        notifier = _make_mock_notifier()

        checker = HeartbeatChecker(
            notification_chat_id="999",
            telegram_bot_token="tok",
            session_factory=factory,
            notifier=notifier,
        )

        with patch("app.services.metrics_service.MetricsService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.get_bot_health = AsyncMock(return_value=snapshot)
            mock_svc_cls.return_value = mock_svc

            with caplog.at_level(logging.INFO, logger="app.bot.scheduler.tasks.heartbeat"):
                await checker.run()

        # Find the heartbeat.success record
        success_records = [r for r in caplog.records if "heartbeat.success" in r.getMessage()]
        assert success_records, "Expected a 'heartbeat.success' log record"
        rec = success_records[0]

        assert hasattr(rec, "stuck_conversations"), "missing stuck_conversations"
        assert hasattr(rec, "msgs_24h"), "missing msgs_24h"
        assert hasattr(rec, "latency_p95_ms"), "missing latency_p95_ms"
        assert hasattr(rec, "pct_fallback"), "missing pct_fallback"
        assert hasattr(rec, "errors_24h"), "missing errors_24h"
        assert hasattr(rec, "total_today_usd"), "missing total_today_usd"
        assert hasattr(rec, "total_month_usd"), "missing total_month_usd"

        assert rec.stuck_conversations == 2
        assert rec.msgs_24h == 100
        assert rec.latency_p95_ms == 350
        assert rec.pct_fallback == round(11.1, 2)
        assert rec.errors_24h == 1
        assert rec.total_today_usd == pytest.approx(1.50, abs=0.01)

    @pytest.mark.asyncio
    async def test_structured_log_json_format(self):
        """JsonFormatter includes heartbeat keys when LOG_FORMAT=json."""
        from app.bot.observability.json_formatter import JsonFormatter

        snapshot = _make_snapshot()
        factory = _make_healthy_session_factory()
        notifier = _make_mock_notifier()

        checker = HeartbeatChecker(
            notification_chat_id="999",
            telegram_bot_token="tok",
            session_factory=factory,
            notifier=notifier,
        )

        captured_json: list[dict] = []

        class _ListHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured_json.append(json.loads(JsonFormatter().format(record)))

        handler = _ListHandler()
        handler.setLevel(logging.DEBUG)
        hb_logger = logging.getLogger("app.bot.scheduler.tasks.heartbeat")
        hb_logger.setLevel(logging.DEBUG)
        hb_logger.addHandler(handler)

        try:
            with patch("app.services.metrics_service.MetricsService") as mock_svc_cls:
                mock_svc = MagicMock()
                mock_svc.get_bot_health = AsyncMock(return_value=snapshot)
                mock_svc_cls.return_value = mock_svc

                await checker.run()
        finally:
            hb_logger.removeHandler(handler)

        success_records = [r for r in captured_json if "heartbeat.success" in r.get("msg", "")]
        assert success_records, "Expected a JSON log line with msg='heartbeat.success'"
        rec = success_records[0]

        for key in ("stuck_conversations", "msgs_24h", "latency_p95_ms", "pct_fallback", "errors_24h",
                    "total_today_usd", "total_month_usd"):
            assert key in rec, f"JSON log missing key: {key}"


# ---------------------------------------------------------------------------
# Test F.3a — DB failure records to bot_errors
# ---------------------------------------------------------------------------

class TestHeartbeatFailureRecordsBotError:
    """On DB failure, heartbeat writes a row to bot_errors."""

    @pytest.mark.asyncio
    async def test_record_error_called_on_db_failure(self):
        """BotErrorService.record_error is called with workflow='heartbeat', node='db_check'."""
        factory = _make_unhealthy_session_factory()
        notifier = _make_mock_notifier()

        checker = HeartbeatChecker(
            notification_chat_id="999",
            telegram_bot_token="tok",
            session_factory=factory,
            notifier=notifier,
        )

        mock_record = AsyncMock()

        # Patch at the source module (deferred import in _record_db_failure)
        with patch(
            "app.bot.services.error_service.BotErrorService"
        ) as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.record_error = mock_record
            mock_svc_cls.return_value = mock_svc

            result = await checker.run()

        assert result["db_healthy"] is False
        mock_svc_cls.assert_called_once_with(workflow="heartbeat")
        mock_record.assert_awaited_once()
        call_kwargs = mock_record.call_args
        assert call_kwargs.kwargs.get("node") == "db_check"

    @pytest.mark.asyncio
    async def test_legacy_failure_notification_still_sent(self):
        """notify_heartbeat_failure is still called on DB failure (legacy behaviour preserved)."""
        factory = _make_unhealthy_session_factory()
        notifier = _make_mock_notifier()

        checker = HeartbeatChecker(
            notification_chat_id="999",
            telegram_bot_token="tok",
            session_factory=factory,
            notifier=notifier,
        )

        with patch("app.bot.services.error_service.BotErrorService"):
            await checker.run()

        notifier.notify_heartbeat_failure.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test F.3b — bot_errors recording failure is non-fatal
# ---------------------------------------------------------------------------

class TestHeartbeatFailureSurvivesBotErrorRecordingFailure:
    """If BotErrorService.record_error raises, the heartbeat must not propagate."""

    @pytest.mark.asyncio
    async def test_no_exception_propagates_when_record_error_raises(self):
        """BotErrorService.record_error raising must not crash the heartbeat."""
        factory = _make_unhealthy_session_factory()
        notifier = _make_mock_notifier()

        checker = HeartbeatChecker(
            notification_chat_id="999",
            telegram_bot_token="tok",
            session_factory=factory,
            notifier=notifier,
        )

        with patch(
            "app.bot.services.error_service.BotErrorService"
        ) as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.record_error = AsyncMock(side_effect=Exception("DB still down"))
            mock_svc_cls.return_value = mock_svc

            # Must not raise
            result = await checker.run()

        assert result["db_healthy"] is False

    @pytest.mark.asyncio
    async def test_exception_logged_when_record_error_raises(self, caplog):
        """An exception log record is emitted when record_error itself raises."""
        factory = _make_unhealthy_session_factory()
        notifier = _make_mock_notifier()

        checker = HeartbeatChecker(
            notification_chat_id="999",
            telegram_bot_token="tok",
            session_factory=factory,
            notifier=notifier,
        )

        with patch(
            "app.bot.services.error_service.BotErrorService"
        ) as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.record_error = AsyncMock(side_effect=Exception("DB still down"))
            mock_svc_cls.return_value = mock_svc

            with caplog.at_level(logging.ERROR, logger="app.bot.scheduler.tasks.heartbeat"):
                await checker.run()

        # Either the message or exc_info will flag the failure
        error_records = [
            r for r in caplog.records
            if "could not record error" in r.getMessage() or r.exc_info
        ]
        assert error_records, "Expected an exception log when record_error raises"


# ---------------------------------------------------------------------------
# Test F.1 — notify_heartbeat_snapshot silent when credentials empty
# ---------------------------------------------------------------------------

class TestNotifyHeartbeatSnapshotSilentWhenCredentialsEmpty:
    """notify_heartbeat_snapshot returns False and makes no HTTP call when creds empty."""

    @pytest.mark.asyncio
    async def test_returns_false_no_chat_id(self):
        """Returns False immediately when chat_id is empty."""
        notifier = AdminNotifier(chat_id="", bot_token="tok123")
        snapshot = _make_snapshot()

        with patch("app.bot.services.admin_notifier.httpx.AsyncClient") as mock_client_cls:
            result = await notifier.notify_heartbeat_snapshot(snapshot)

        assert result is False
        mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_false_no_bot_token(self):
        """Returns False immediately when bot_token is empty."""
        notifier = AdminNotifier(chat_id="999", bot_token="")
        snapshot = _make_snapshot()

        with patch("app.bot.services.admin_notifier.httpx.AsyncClient") as mock_client_cls:
            result = await notifier.notify_heartbeat_snapshot(snapshot)

        assert result is False
        mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_false_both_empty(self):
        """Returns False immediately when both chat_id and bot_token are empty."""
        notifier = AdminNotifier(chat_id="", bot_token="")
        snapshot = _make_snapshot()

        with patch("app.bot.services.admin_notifier.httpx.AsyncClient") as mock_client_cls:
            result = await notifier.notify_heartbeat_snapshot(snapshot)

        assert result is False
        mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_message_content_when_credentials_present(self):
        """Message includes all expected metrics fields."""
        notifier = AdminNotifier(chat_id="999", bot_token="tok123")
        notifier.notify = AsyncMock(return_value=True)  # type: ignore[method-assign]
        snapshot = _make_snapshot()

        result = await notifier.notify_heartbeat_snapshot(snapshot)

        assert result is True
        notifier.notify.assert_awaited_once()
        msg = notifier.notify.call_args[0][0]
        assert "Heartbeat" in msg
        assert "Mensajes 24h" in msg
        assert "100" in msg
        assert "Latencia p95" in msg
        assert "350ms" in msg
        assert "Fallback Gemini" in msg
        assert "Conv. trabadas: 2" in msg
        assert "Errores 24h: 1" in msg
        assert "Costo hoy" in msg
        assert "Costo mes" in msg
