"""Tests for MessageHandler — the bot pipeline entry point.

Plan 65-01: HAND-01..08.
All dependencies are mocked — no real API calls or DB access.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.core.types import (
    BotRequest,
    BotResponse,
    ChannelPayload,
    PayloadMessage,
)
from app.bot.handlers.message_handler import MessageHandler
from app.bot.middleware.rate_limiter import RateLimiter
from app.bot.middleware.idempotency import IdempotencyGuard


# Default settings map used by autouse fixture (bot enabled, WA auto)
_DEFAULT_SETTINGS = {
    "bot_enabled": "true",
    "whatsapp_mode": "auto",
}


@pytest.fixture(autouse=True)
def _patch_bot_settings():
    """Patch BotSettingRepository.get_value for all handler tests.

    Returns "true" for bot_enabled and "auto" for whatsapp_mode so
    existing tests pass through the new kill-switch checks.
    Individual tests can override with their own @patch.
    """
    with patch(
        "app.bot.handlers.message_handler.BotSettingRepository"
    ) as mock_repo_class:
        mock_repo_class.get_value = AsyncMock(
            side_effect=lambda session, key: _DEFAULT_SETTINGS.get(key)
        )
        yield mock_repo_class


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(
    platform: str = "telegram",
    text: str = "Hola",
    external_id: str = "msg_001",
) -> BotRequest:
    return BotRequest(
        platform=platform,
        chat_id="12345",
        user_id="user_001",
        user_name="Test User",
        text=text,
        external_id=external_id,
    )


def _make_wa_request(
    text: str = "Hola",
    external_id: str = "SM123",
) -> BotRequest:
    return BotRequest(
        platform="whatsapp",
        chat_id="+595981123456",
        user_id="+595981123456",
        user_name="Test WA",
        text=text,
        external_id=external_id,
    )


def _make_bot_response(
    text: str = "Hola! Soy el bot.",
    intent: str = "saludo",
) -> BotResponse:
    return BotResponse(text=text, intent=intent)


def _make_payload(text: str = "Hola") -> ChannelPayload:
    return ChannelPayload(
        messages=[PayloadMessage(text=text)],
        channel="telegram",
    )


def _make_handler(
    orchestrator_response=None,
    sender_result: bool = True,
) -> tuple[MessageHandler, dict]:
    """Build a MessageHandler with all dependencies mocked.

    Returns (handler, mocks_dict) for assertion access.
    """
    orchestrator = AsyncMock()
    if orchestrator_response is not None:
        orchestrator.handle_message.return_value = orchestrator_response
    else:
        orchestrator.handle_message.return_value = _make_bot_response()

    response_builder = MagicMock()
    response_builder.build_payload.return_value = _make_payload()

    sender = AsyncMock()
    sender.send.return_value = sender_result

    rate_limiter = RateLimiter(max_messages=5, window_seconds=60)
    idempotency_guard = IdempotencyGuard()

    handler = MessageHandler(
        orchestrator=orchestrator,
        response_builder=response_builder,
        sender=sender,
        rate_limiter=rate_limiter,
        idempotency_guard=idempotency_guard,
    )

    mocks = {
        "orchestrator": orchestrator,
        "response_builder": response_builder,
        "sender": sender,
        "rate_limiter": rate_limiter,
        "idempotency_guard": idempotency_guard,
    }
    return handler, mocks


# ===========================================================================
# TestMessageHandler
# ===========================================================================

class TestMessageHandler:
    """Pipeline integration tests with mocked dependencies."""

    @pytest.mark.asyncio
    async def test_full_pipeline_success(self):
        """Happy path: message → orchestrator → sender."""
        handler, mocks = _make_handler()
        session = AsyncMock()

        result = await handler.handle(_make_request(), session)
        assert result is True

        # Orchestrator was called
        mocks["orchestrator"].handle_message.assert_awaited_once()

        # Response builder was called
        mocks["response_builder"].build_payload.assert_called_once()

        # Sender was called
        mocks["sender"].send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_duplicate_message_skipped(self):
        """Duplicate message (same external_id) is skipped."""
        handler, mocks = _make_handler()
        session = AsyncMock()
        request = _make_request(external_id="msg_dup")

        # First call processes normally
        result1 = await handler.handle(request, session)
        assert result1 is True
        assert mocks["orchestrator"].handle_message.await_count == 1

        # Second call with same external_id is skipped
        result2 = await handler.handle(request, session)
        assert result2 is True
        assert mocks["orchestrator"].handle_message.await_count == 1  # Not called again

    @pytest.mark.asyncio
    async def test_rate_limited_sends_warning(self):
        """Rate-limited user gets a warning message instead of AI response."""
        handler, mocks = _make_handler()
        session = AsyncMock()

        # Exhaust the rate limit (5 messages)
        for i in range(5):
            req = _make_request(external_id=f"msg_{i}")
            await handler.handle(req, session)

        # 6th message should be rate-limited
        req = _make_request(external_id="msg_rate_limit")
        result = await handler.handle(req, session)
        assert result is True

        # Orchestrator was NOT called for the 6th message
        assert mocks["orchestrator"].handle_message.await_count == 5

        # But sender WAS called (to send rate limit warning)
        # 5 normal sends + 1 rate limit warning = 6
        assert mocks["sender"].send.await_count == 6

    @pytest.mark.asyncio
    async def test_bot_silent_no_send(self):
        """When orchestrator returns None, no message is sent."""
        handler, mocks = _make_handler(orchestrator_response=None)
        # Override — we need None, not a BotResponse
        mocks["orchestrator"].handle_message.return_value = None
        session = AsyncMock()

        result = await handler.handle(_make_request(), session)
        assert result is True

        # Orchestrator was called
        mocks["orchestrator"].handle_message.assert_awaited_once()

        # Sender was NOT called (bot stays silent)
        mocks["sender"].send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sender_failure_returns_false(self):
        """When sender fails, handle returns False."""
        handler, mocks = _make_handler(sender_result=False)
        session = AsyncMock()

        result = await handler.handle(_make_request(), session)
        assert result is False

    @pytest.mark.asyncio
    async def test_orchestrator_exception_sends_safe_error(self):
        """When orchestrator raises, a safe error message is sent."""
        handler, mocks = _make_handler()
        mocks["orchestrator"].handle_message.side_effect = RuntimeError("DB crash")
        session = AsyncMock()

        result = await handler.handle(_make_request(), session)
        # Error was handled — safe message sent
        assert result is False

        # Sender was called with error message
        mocks["sender"].send.assert_awaited()

    @pytest.mark.asyncio
    async def test_response_builder_called_with_correct_args(self):
        """Response builder receives the correct parameters from BotResponse."""
        bot_resp = BotResponse(
            text="Encontre 2 propiedades",
            intent="busqueda",
            properties=[{"id": 1}, {"id": 2}],
            pending_ids=[3, 4, 5],
        )
        handler, mocks = _make_handler(orchestrator_response=bot_resp)
        session = AsyncMock()

        await handler.handle(_make_request(), session)

        mocks["response_builder"].build_payload.assert_called_once_with(
            text="Encontre 2 propiedades",
            intent="busqueda",
            properties=[{"id": 1}, {"id": 2}],
            channel="telegram",
            has_pending=True,
            metadata={},
        )

    @pytest.mark.asyncio
    async def test_whatsapp_platform_passed_to_payload(self):
        """WhatsApp platform is passed correctly to response builder."""
        handler, mocks = _make_handler()
        session = AsyncMock()

        req = _make_request(platform="whatsapp", external_id="wa_001")
        await handler.handle(req, session)

        call_kwargs = mocks["response_builder"].build_payload.call_args
        assert call_kwargs.kwargs.get("channel") == "whatsapp" or \
               call_kwargs[1].get("channel") == "whatsapp" or \
               (len(call_kwargs[0]) > 3 and call_kwargs[0][3] == "whatsapp")

    @pytest.mark.asyncio
    async def test_none_external_id_not_deduplicated(self):
        """Messages with None external_id are never deduplicated."""
        handler, mocks = _make_handler()
        session = AsyncMock()

        req1 = _make_request(external_id=None)
        req2 = _make_request(external_id=None)

        await handler.handle(req1, session)
        await handler.handle(req2, session)

        # Both should reach the orchestrator
        assert mocks["orchestrator"].handle_message.await_count == 2

    @pytest.mark.asyncio
    async def test_has_pending_false_when_no_pending(self):
        """has_pending=False when bot_response has empty pending_ids."""
        bot_resp = BotResponse(
            text="Solo texto",
            intent="saludo",
            pending_ids=[],
        )
        handler, mocks = _make_handler(orchestrator_response=bot_resp)
        session = AsyncMock()

        await handler.handle(_make_request(), session)

        call_kwargs = mocks["response_builder"].build_payload.call_args
        assert call_kwargs.kwargs.get("has_pending") is False or \
               (len(call_kwargs[0]) > 4 and call_kwargs[0][4] is False)

    @pytest.mark.asyncio
    async def test_sender_receives_chat_id(self):
        """Sender is called with the correct chat_id."""
        handler, mocks = _make_handler()
        session = AsyncMock()

        req = _make_request()
        await handler.handle(req, session)

        call_args = mocks["sender"].send.call_args
        assert call_args[0][1] == "12345"  # chat_id

    @pytest.mark.asyncio
    async def test_idempotency_only_blocks_same_id(self):
        """Different external_ids are processed independently."""
        handler, mocks = _make_handler()
        session = AsyncMock()

        await handler.handle(_make_request(external_id="msg_a"), session)
        await handler.handle(_make_request(external_id="msg_b"), session)

        assert mocks["orchestrator"].handle_message.await_count == 2


# ===========================================================================
# TestMessageHandlerKillSwitches
# ===========================================================================

class TestMessageHandlerKillSwitches:
    """Tests for bot_enabled and whatsapp_mode kill-switch checks."""

    @pytest.mark.asyncio
    @patch("app.bot.handlers.message_handler.BotSettingRepository")
    async def test_bot_disabled_sends_off_message(self, mock_repo_class):
        """bot_enabled=false sends bot_off_message, orchestrator NOT called."""
        settings_map = {
            "bot_enabled": "false",
            "bot_off_message": "No disponible ahora.",
        }
        mock_repo_class.get_value = AsyncMock(
            side_effect=lambda session, key: settings_map.get(key)
        )

        handler, mocks = _make_handler()
        session = AsyncMock()

        result = await handler.handle(_make_wa_request(), session)
        assert result is True

        # Sender called with off message
        mocks["sender"].send.assert_awaited_once()
        sent_payload = mocks["sender"].send.call_args[0][0]
        assert sent_payload.messages[0].text == "No disponible ahora."

        # Orchestrator NOT called
        mocks["orchestrator"].handle_message.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.bot.handlers.message_handler.BotSettingRepository")
    async def test_bot_disabled_applies_to_telegram(self, mock_repo_class):
        """bot_enabled=false also applies to Telegram requests."""
        settings_map = {
            "bot_enabled": "false",
            "bot_off_message": "Fuera de servicio.",
        }
        mock_repo_class.get_value = AsyncMock(
            side_effect=lambda session, key: settings_map.get(key)
        )

        handler, mocks = _make_handler()
        session = AsyncMock()

        result = await handler.handle(_make_request(), session)
        assert result is True

        # Sender called with off message
        mocks["sender"].send.assert_awaited_once()
        sent_payload = mocks["sender"].send.call_args[0][0]
        assert sent_payload.messages[0].text == "Fuera de servicio."

        # Orchestrator NOT called
        mocks["orchestrator"].handle_message.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.bot.handlers.message_handler.BotSettingRepository")
    async def test_bot_enabled_proceeds_normally(self, mock_repo_class):
        """bot_enabled=true allows pipeline to proceed to orchestrator."""
        settings_map = {
            "bot_enabled": "true",
            "whatsapp_mode": "auto",
        }
        mock_repo_class.get_value = AsyncMock(
            side_effect=lambda session, key: settings_map.get(key)
        )

        handler, mocks = _make_handler()
        session = AsyncMock()

        result = await handler.handle(_make_request(), session)
        assert result is True

        # Orchestrator WAS called
        mocks["orchestrator"].handle_message.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.bot.handlers.message_handler.BotSettingRepository")
    async def test_whatsapp_manual_mode_skips_silently(self, mock_repo_class):
        """whatsapp_mode=manual + whatsapp request: no send, no orchestrator."""
        settings_map = {
            "bot_enabled": "true",
            "whatsapp_mode": "manual",
        }
        mock_repo_class.get_value = AsyncMock(
            side_effect=lambda session, key: settings_map.get(key)
        )

        handler, mocks = _make_handler()
        session = AsyncMock()

        result = await handler.handle(_make_wa_request(), session)
        assert result is True

        # Sender NOT called (silent skip)
        mocks["sender"].send.assert_not_awaited()

        # Orchestrator NOT called
        mocks["orchestrator"].handle_message.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.bot.handlers.message_handler.BotSettingRepository")
    async def test_whatsapp_auto_mode_proceeds(self, mock_repo_class):
        """whatsapp_mode=auto + whatsapp request: orchestrator IS called."""
        settings_map = {
            "bot_enabled": "true",
            "whatsapp_mode": "auto",
        }
        mock_repo_class.get_value = AsyncMock(
            side_effect=lambda session, key: settings_map.get(key)
        )

        handler, mocks = _make_handler()
        session = AsyncMock()

        result = await handler.handle(_make_wa_request(), session)
        assert result is True

        # Orchestrator WAS called
        mocks["orchestrator"].handle_message.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.bot.handlers.message_handler.BotSettingRepository")
    async def test_telegram_ignores_whatsapp_mode(self, mock_repo_class):
        """whatsapp_mode=manual + telegram request: orchestrator IS called."""
        settings_map = {
            "bot_enabled": "true",
            "whatsapp_mode": "manual",
        }
        mock_repo_class.get_value = AsyncMock(
            side_effect=lambda session, key: settings_map.get(key)
        )

        handler, mocks = _make_handler()
        session = AsyncMock()

        result = await handler.handle(_make_request(), session)
        assert result is True

        # Orchestrator WAS called — Telegram ignores whatsapp_mode
        mocks["orchestrator"].handle_message.assert_awaited_once()
