"""Tests for AdminNotifier service.

Plan 71-03: Task 8 — unit tests for Telegram admin notification.
All tests use mocked httpx; no real network calls.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.services.admin_notifier import AdminNotifier, get_admin_notifier


# ---------------------------------------------------------------------------
# Tests: notify sends telegram message
# ---------------------------------------------------------------------------

class TestNotifySend:
    """notify() sends a Telegram message via httpx."""

    @pytest.mark.asyncio
    async def test_notify_sends_message(self):
        """notify() POSTs to Telegram sendMessage and returns True."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("app.bot.services.admin_notifier.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            notifier = AdminNotifier(chat_id="999", bot_token="tok123")
            result = await notifier.notify("test message")

            assert result is True
            mock_client.post.assert_awaited_once()
            call_args = mock_client.post.call_args
            assert "999" in str(call_args)
            assert "test message" in str(call_args)

    @pytest.mark.asyncio
    async def test_notify_returns_false_on_http_error(self):
        """notify() returns False when Telegram returns non-200."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch("app.bot.services.admin_notifier.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            notifier = AdminNotifier(chat_id="999", bot_token="tok123")
            result = await notifier.notify("test message")

            assert result is False

    @pytest.mark.asyncio
    async def test_notify_returns_false_on_exception(self):
        """notify() returns False on network exception (never raises)."""
        with patch("app.bot.services.admin_notifier.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=Exception("network down"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            notifier = AdminNotifier(chat_id="999", bot_token="tok123")
            result = await notifier.notify("test message")

            assert result is False

    @pytest.mark.asyncio
    async def test_notify_returns_false_when_no_config(self):
        """notify() returns False when chat_id or bot_token is empty."""
        notifier = AdminNotifier(chat_id="", bot_token="")
        result = await notifier.notify("test message")
        assert result is False

        notifier2 = AdminNotifier(chat_id="999", bot_token="")
        result2 = await notifier2.notify("test message")
        assert result2 is False

        notifier3 = AdminNotifier(chat_id="", bot_token="tok123")
        result3 = await notifier3.notify("test message")
        assert result3 is False


# ---------------------------------------------------------------------------
# Tests: convenience methods format correctly
# ---------------------------------------------------------------------------

class TestConvenienceMethods:
    """Convenience methods format the message and call notify()."""

    @pytest.mark.asyncio
    async def test_notify_error_formats_correctly(self):
        """notify_error includes workflow, node, chat_id, and error."""
        notifier = AdminNotifier(chat_id="999", bot_token="tok123")
        notifier.notify = AsyncMock(return_value=True)

        result = await notifier.notify_error(
            "telegram",
            "timeout connecting to Claude",
            node="webhook_process",
            chat_id="12345",
        )

        assert result is True
        notifier.notify.assert_awaited_once()
        msg = notifier.notify.call_args[0][0]
        assert "Bot Error" in msg
        assert "telegram" in msg
        assert "webhook_process" in msg
        assert "12345" in msg
        assert "timeout connecting to Claude" in msg

    @pytest.mark.asyncio
    async def test_notify_bot_disabled_formats_correctly(self):
        """notify_bot_disabled includes reason and reactivation note."""
        notifier = AdminNotifier(chat_id="999", bot_token="tok123")
        notifier.notify = AsyncMock(return_value=True)

        result = await notifier.notify_bot_disabled("3 errors in 15min")

        assert result is True
        notifier.notify.assert_awaited_once()
        msg = notifier.notify.call_args[0][0]
        assert "Auto-Desactivado" in msg
        assert "3 errors in 15min" in msg
        assert "Reactivar" in msg

    @pytest.mark.asyncio
    async def test_notify_cold_leads_formats_correctly(self):
        """notify_cold_leads includes count and IDs."""
        notifier = AdminNotifier(chat_id="999", bot_token="tok123")
        notifier.notify = AsyncMock(return_value=True)

        result = await notifier.notify_cold_leads(3, [10, 20, 30])

        assert result is True
        msg = notifier.notify.call_args[0][0]
        assert "Cold Lead Check" in msg
        assert "3" in msg
        assert "10" in msg
        assert "20" in msg
        assert "30" in msg

    @pytest.mark.asyncio
    async def test_notify_cold_leads_truncates_ids(self):
        """notify_cold_leads truncates to 20 IDs and adds '... y N mas'."""
        notifier = AdminNotifier(chat_id="999", bot_token="tok123")
        notifier.notify = AsyncMock(return_value=True)

        ids = list(range(1, 26))  # 25 IDs
        await notifier.notify_cold_leads(25, ids)

        msg = notifier.notify.call_args[0][0]
        assert "... y 5 mas" in msg

    @pytest.mark.asyncio
    async def test_notify_heartbeat_failure_formats_correctly(self):
        """notify_heartbeat_failure includes timestamp and DB alert."""
        notifier = AdminNotifier(chat_id="999", bot_token="tok123")
        notifier.notify = AsyncMock(return_value=True)

        result = await notifier.notify_heartbeat_failure("2026-03-27T12:00:00+00:00")

        assert result is True
        msg = notifier.notify.call_args[0][0]
        assert "DB Health Check Failed" in msg
        assert "2026-03-27T12:00:00+00:00" in msg

    @pytest.mark.asyncio
    async def test_notify_new_lead_formats_correctly(self):
        """notify_new_lead includes name, phone, property, source, motivo."""
        notifier = AdminNotifier(chat_id="999", bot_token="tok123")
        notifier.notify = AsyncMock(return_value=True)

        result = await notifier.notify_new_lead(
            "Maria Lopez", "+595981234567",
            property_id=42, source="whatsapp", motivo="Quiere visitar",
        )

        assert result is True
        msg = notifier.notify.call_args[0][0]
        assert "Nuevo Lead" in msg
        assert "Maria Lopez" in msg
        assert "+595981234567" in msg
        assert "#42" in msg
        assert "whatsapp" in msg
        assert "Quiere visitar" in msg

    @pytest.mark.asyncio
    async def test_notify_new_lead_minimal(self):
        """notify_new_lead works with minimal info (no property/source/motivo)."""
        notifier = AdminNotifier(chat_id="999", bot_token="tok123")
        notifier.notify = AsyncMock(return_value=True)

        result = await notifier.notify_new_lead("", "")

        assert result is True
        msg = notifier.notify.call_args[0][0]
        assert "Sin nombre" in msg
        assert "Sin tel" in msg

    @pytest.mark.asyncio
    async def test_notify_circuit_breaker_open_formats_correctly(self):
        """notify_circuit_breaker_open includes failure count and Gemini mention."""
        notifier = AdminNotifier(chat_id="999", bot_token="tok123")
        notifier.notify = AsyncMock(return_value=True)

        result = await notifier.notify_circuit_breaker_open(5)

        assert result is True
        msg = notifier.notify.call_args[0][0]
        assert "Circuit Breaker ABIERTO" in msg
        assert "5" in msg
        assert "Gemini" in msg

    @pytest.mark.asyncio
    async def test_notify_twilio_error_formats_correctly(self):
        """notify_twilio_error includes error code, message, and destination."""
        notifier = AdminNotifier(chat_id="999", bot_token="tok123")
        notifier.notify = AsyncMock(return_value=True)

        result = await notifier.notify_twilio_error(
            "63016", "Channel could not authenticate",
            to_number="whatsapp:+595981234567",
        )

        assert result is True
        msg = notifier.notify.call_args[0][0]
        assert "Twilio Error" in msg
        assert "63016" in msg
        assert "Channel could not authenticate" in msg
        assert "whatsapp:+595981234567" in msg


# ---------------------------------------------------------------------------
# Tests: factory function
# ---------------------------------------------------------------------------

class TestGetAdminNotifier:
    """get_admin_notifier reads from bot_settings."""

    def test_factory_returns_notifier(self):
        """get_admin_notifier() creates an AdminNotifier with bot_settings values."""
        with patch("app.bot.config.bot_settings") as mock_settings:
            mock_settings.TELEGRAM_EZ_CHAT_ID = "42"
            mock_settings.TELEGRAM_BOT_TOKEN = "tok_abc"

            notifier = get_admin_notifier()

            assert isinstance(notifier, AdminNotifier)
            assert notifier.chat_id == "42"
            assert notifier.bot_token == "tok_abc"
