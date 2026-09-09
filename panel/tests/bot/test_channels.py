"""Tests for channel senders — WhatsApp.

Plan 63-01: CHAN-01, CHAN-02, CHAN-03.
All HTTP calls are mocked via httpx.MockTransport — no real API calls.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.bot.channels.base import BaseSender
from app.bot.channels.whatsapp import WhatsAppSender
from app.bot.core.types import ChannelPayload, PayloadMessage


# ---------------------------------------------------------------------------
# Helpers: mock httpx transports
# ---------------------------------------------------------------------------

def _twilio_ok_transport() -> httpx.MockTransport:
    """Returns a mock transport that simulates Twilio API success."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={"sid": "SM123", "status": "queued"},
        )
    return httpx.MockTransport(handler)


def _twilio_error_transport(status: int = 400) -> httpx.MockTransport:
    """Returns a mock transport that simulates Twilio API error."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={"code": 21211, "message": "Invalid phone"},
        )
    return httpx.MockTransport(handler)


def _timeout_transport() -> httpx.MockTransport:
    """Returns a transport that raises a timeout."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("Connection timed out")
    return httpx.MockTransport(handler)


# ===========================================================================
# TestBaseSender
# ===========================================================================

class TestBaseSender:
    """CHAN-01: Base sender interface."""

    def test_cannot_instantiate_directly(self):
        """BaseSender is abstract and cannot be instantiated."""
        with pytest.raises(TypeError):
            BaseSender()

    def test_has_send_method(self):
        """BaseSender defines the send() interface."""
        assert hasattr(BaseSender, "send")
        assert callable(getattr(BaseSender, "send", None))

    def test_subclass_must_implement_send(self):
        """A subclass that doesn't implement send() can't be instantiated."""
        class IncompleteSender(BaseSender):
            pass

        with pytest.raises(TypeError):
            IncompleteSender()

    def test_subclass_with_send_works(self):
        """A subclass that implements send() can be instantiated."""
        class MockSender(BaseSender):
            async def send(self, payload, chat_id):
                return True

        sender = MockSender()
        assert sender is not None

# ===========================================================================
# TestWhatsAppSender
# ===========================================================================

class TestWhatsAppSender:
    """CHAN-02: WhatsApp sender tests."""

    @pytest.mark.asyncio
    async def test_send_text_message(self):
        """Send a plain text message successfully."""
        client = httpx.AsyncClient(transport=_twilio_ok_transport())
        sender = WhatsAppSender("AC_test", "auth_test", "whatsapp:+595900000000", client=client)

        payload = ChannelPayload(
            messages=[PayloadMessage(text="Hola!")],
            channel="whatsapp",
        )
        result = await sender.send(payload, "+595981999999")
        assert result is True

    @pytest.mark.asyncio
    async def test_auto_prepends_whatsapp_prefix(self):
        """chat_id without whatsapp: prefix gets it added."""
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content.decode()
            return httpx.Response(201, json={"sid": "SM1", "status": "queued"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        sender = WhatsAppSender("AC_test", "auth_test", "whatsapp:+595900000000", client=client)

        payload = ChannelPayload(
            messages=[PayloadMessage(text="Hola")],
            channel="whatsapp",
        )
        await sender.send(payload, "+595981999999")
        assert "whatsapp%3A%2B595981999999" in captured["body"]

    @pytest.mark.asyncio
    async def test_send_with_template(self):
        """ContentSid template takes priority over body."""
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content.decode()
            return httpx.Response(201, json={"sid": "SM1", "status": "queued"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        sender = WhatsAppSender("AC_test", "auth_test", "whatsapp:+595900000000", client=client)

        payload = ChannelPayload(
            messages=[PayloadMessage(text="", template_id="HX_template_123")],
            channel="whatsapp",
        )
        result = await sender.send(payload, "+595981999999")
        assert result is True
        assert "ContentSid" in captured["body"]
        assert "HX_template_123" in captured["body"]

    @pytest.mark.asyncio
    async def test_send_with_photo(self):
        """Photo is sent via MediaUrl."""
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content.decode()
            return httpx.Response(201, json={"sid": "SM1", "status": "queued"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        sender = WhatsAppSender("AC_test", "auth_test", "whatsapp:+595900000000", client=client)

        payload = ChannelPayload(
            messages=[PayloadMessage(
                text="Casa linda",
                photo_url="https://onnix.com.py/images/onnix/123/0.webp",
            )],
            channel="whatsapp",
        )
        result = await sender.send(payload, "+595981999999")
        assert result is True
        assert "MediaUrl" in captured["body"]

    @pytest.mark.asyncio
    async def test_empty_payload_returns_true(self):
        """Empty payload returns True immediately."""
        sender = WhatsAppSender("AC_test", "auth_test", "whatsapp:+595900000000")
        payload = ChannelPayload(messages=[], channel="whatsapp")
        result = await sender.send(payload, "+595981999999")
        assert result is True

    @pytest.mark.asyncio
    async def test_http_error_returns_false(self):
        """Twilio error returns False."""
        client = httpx.AsyncClient(transport=_twilio_error_transport(400))
        sender = WhatsAppSender("AC_test", "auth_test", "whatsapp:+595900000000", client=client)

        payload = ChannelPayload(
            messages=[PayloadMessage(text="Hola")],
            channel="whatsapp",
        )
        result = await sender.send(payload, "+595981999999")
        assert result is False

    @pytest.mark.asyncio
    async def test_timeout_returns_false(self):
        """Connection timeout returns False (sleep patched to avoid real delays)."""
        client = httpx.AsyncClient(transport=_timeout_transport())
        sender = WhatsAppSender("AC_test", "auth_test", "whatsapp:+595900000000", client=client)

        payload = ChannelPayload(
            messages=[PayloadMessage(text="Hola")],
            channel="whatsapp",
        )
        with patch("app.bot.channels.twilio_retry.asyncio.sleep", new=AsyncMock()):
            result = await sender.send(payload, "+595981999999")
        assert result is False

    @pytest.mark.asyncio
    async def test_basic_auth_sent(self):
        """Twilio request includes Basic Auth header."""
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["auth"] = request.headers.get("authorization", "")
            return httpx.Response(201, json={"sid": "SM1", "status": "queued"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        sender = WhatsAppSender("AC_test", "auth_test", "whatsapp:+595900000000", client=client)

        payload = ChannelPayload(
            messages=[PayloadMessage(text="Hi")],
            channel="whatsapp",
        )
        await sender.send(payload, "+595981999999")
        assert captured["auth"].startswith("Basic ")

    @pytest.mark.asyncio
    async def test_multiple_messages(self):
        """Multiple messages are all sent."""
        call_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            return httpx.Response(201, json={"sid": f"SM{call_count['n']}", "status": "queued"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        sender = WhatsAppSender("AC_test", "auth_test", "whatsapp:+595900000000", client=client)

        payload = ChannelPayload(
            messages=[
                PayloadMessage(text="Msg 1"),
                PayloadMessage(text="Msg 2"),
            ],
            channel="whatsapp",
        )
        result = await sender.send(payload, "+595981999999")
        assert result is True
        assert call_count["n"] == 2

    @pytest.mark.asyncio
    async def test_skip_empty_message(self):
        """Empty message (no text, no template, no photo) is skipped."""
        call_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            return httpx.Response(201, json={"sid": "SM1", "status": "queued"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        sender = WhatsAppSender("AC_test", "auth_test", "whatsapp:+595900000000", client=client)

        payload = ChannelPayload(
            messages=[PayloadMessage(text="")],
            channel="whatsapp",
        )
        result = await sender.send(payload, "+595981999999")
        assert result is True
        assert call_count["n"] == 0  # Nothing sent

    @pytest.mark.asyncio
    async def test_whatsapp_prefix_preserved(self):
        """chat_id already with whatsapp: prefix is not doubled."""
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content.decode()
            return httpx.Response(201, json={"sid": "SM1", "status": "queued"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        sender = WhatsAppSender("AC_test", "auth_test", "whatsapp:+595900000000", client=client)

        payload = ChannelPayload(
            messages=[PayloadMessage(text="Hi")],
            channel="whatsapp",
        )
        await sender.send(payload, "whatsapp:+595981999999")
        # Should NOT have double whatsapp: prefix
        assert "whatsapp%3Awhatsapp" not in captured["body"]

    @pytest.mark.asyncio
    async def test_post_uses_twilio_retry_on_5xx(self):
        """WhatsAppSender._post delegates to twilio_post_with_retry; retries 5xx."""
        from unittest.mock import AsyncMock, patch

        from app.bot.channels.twilio_retry import TwilioPostResult

        success_result = TwilioPostResult(
            success=True,
            status_code=201,
            response_json={"sid": "SM_retry_ok"},
            twilio_error_code=None,
            attempts=2,
        )
        with patch(
            "app.bot.channels.whatsapp.twilio_post_with_retry",
            new=AsyncMock(return_value=success_result),
        ) as mock_retry:
            sender = WhatsAppSender("AC_test", "auth_test", "whatsapp:+595900000000")
            mock_client = AsyncMock(spec=httpx.AsyncClient)
            result = await sender._post(mock_client, {"Body": "test", "To": "whatsapp:+5959111"})

        assert result is True
        mock_retry.assert_called_once()
