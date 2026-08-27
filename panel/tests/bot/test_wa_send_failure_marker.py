"""Tests for WA permanent-failure marker — TDD RED phase.

Covers:
- TwilioPostResult carries to_number and message_type fields
- twilio_post_with_retry invokes on_permanent_failure callback on exhaustion
- twilio_post_with_retry invokes on_permanent_failure callback on permanent 4xx
- on_permanent_failure NOT called on success
- on_permanent_failure NOT called on silent 4xx (63016/63003) — no alert AND no callback
- Callback receives correct TwilioPostResult (success=False, attempts, to_number, message_type)
- DB marker helper: wa_send_failed lead_event written
- DB marker helper: existing message row updated to status=failed
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch

import httpx
import pytest

from app.bot.channels.twilio_retry import (
    TwilioPostResult,
    twilio_post_with_retry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status: int, body: dict | None = None) -> httpx.Response:
    content = json.dumps(body or {}).encode()
    return httpx.Response(status, content=content)


def _make_client(*responses: httpx.Response | Exception) -> httpx.AsyncClient:
    client = MagicMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(side_effect=list(responses))
    return client


def _mock_notifier() -> MagicMock:
    notifier = MagicMock()
    notifier.notify_twilio_error = AsyncMock(return_value=True)
    return notifier


# ---------------------------------------------------------------------------
# Part 1 — TwilioPostResult carries new fields
# ---------------------------------------------------------------------------

class TestTwilioPostResultNewFields:
    """TwilioPostResult must expose to_number and message_type."""

    def test_to_number_field_default_empty_string(self):
        r = TwilioPostResult(
            success=False,
            status_code=400,
            response_json=None,
            twilio_error_code="21211",
            attempts=1,
        )
        assert r.to_number == ""

    def test_message_type_field_default_empty_string(self):
        r = TwilioPostResult(
            success=True,
            status_code=201,
            response_json={"sid": "SM1"},
            twilio_error_code=None,
            attempts=1,
        )
        assert r.message_type == ""

    def test_fields_set_explicitly(self):
        r = TwilioPostResult(
            success=False,
            status_code=500,
            response_json=None,
            twilio_error_code=None,
            attempts=4,
            to_number="whatsapp:+595981000001",
            message_type="template",
        )
        assert r.to_number == "whatsapp:+595981000001"
        assert r.message_type == "template"

    @pytest.mark.asyncio
    async def test_result_carries_to_number_and_message_type_on_success(self):
        """On success the result propagates to_number and message_type."""
        client = _make_client(_make_response(201, {"sid": "SM1"}))
        result = await twilio_post_with_retry(
            client=client,
            url="https://api.twilio.com/Messages.json",
            data={"Body": "hi"},
            auth=("AC", "tok"),
            to_number="whatsapp:+595981000002",
            message_type="text",
            sleep=AsyncMock(),
        )
        assert result.success is True
        assert result.to_number == "whatsapp:+595981000002"
        assert result.message_type == "text"

    @pytest.mark.asyncio
    async def test_result_carries_to_number_and_message_type_on_failure(self):
        """On permanent failure the result propagates to_number and message_type."""
        client = _make_client(
            _make_response(500),
            _make_response(500),
            _make_response(500),
            _make_response(500),
        )
        result = await twilio_post_with_retry(
            client=client,
            url="https://api.twilio.com/Messages.json",
            data={"Body": "hi"},
            auth=("AC", "tok"),
            to_number="whatsapp:+595981000003",
            message_type="template",
            sleep=AsyncMock(),
        )
        assert result.success is False
        assert result.to_number == "whatsapp:+595981000003"
        assert result.message_type == "template"


# ---------------------------------------------------------------------------
# Part 2 — on_permanent_failure callback invocation
# ---------------------------------------------------------------------------

class TestOnPermanentFailureCallback:
    """twilio_post_with_retry invokes on_permanent_failure on final failure."""

    @pytest.mark.asyncio
    async def test_permanent_failure_callback_invoked_on_exhausted_5xx(self):
        """Callback called once after all retries exhausted (5xx × 4)."""
        client = _make_client(
            _make_response(500),
            _make_response(500),
            _make_response(500),
            _make_response(500),
        )
        cb = AsyncMock()

        result = await twilio_post_with_retry(
            client=client,
            url="https://api.twilio.com/Messages.json",
            data={"Body": "hi"},
            auth=("AC", "tok"),
            to_number="whatsapp:+595981000001",
            message_type="template",
            on_permanent_failure=cb,
            sleep=AsyncMock(),
        )

        assert result.success is False
        assert result.attempts == 4
        cb.assert_called_once()
        # Callback receives the TwilioPostResult
        called_result: TwilioPostResult = cb.call_args[0][0]
        assert called_result.success is False
        assert called_result.attempts == 4
        assert called_result.to_number == "whatsapp:+595981000001"
        assert called_result.message_type == "template"

    @pytest.mark.asyncio
    async def test_permanent_failure_callback_invoked_on_exhausted_network_error(self):
        """Callback called once after network errors exhaust retries."""
        client = _make_client(
            httpx.NetworkError("conn reset"),
            httpx.NetworkError("conn reset"),
            httpx.NetworkError("conn reset"),
            httpx.NetworkError("conn reset"),
        )
        cb = AsyncMock()

        result = await twilio_post_with_retry(
            client=client,
            url="https://api.twilio.com/Messages.json",
            data={"Body": "hi"},
            auth=("AC", "tok"),
            on_permanent_failure=cb,
            sleep=AsyncMock(),
        )

        assert result.success is False
        cb.assert_called_once()

    @pytest.mark.asyncio
    async def test_permanent_failure_callback_invoked_on_non_silent_4xx(self):
        """Callback called once on permanent 4xx (non-silent code)."""
        client = _make_client(
            _make_response(400, {"code": 21211, "message": "Invalid phone"})
        )
        cb = AsyncMock()

        result = await twilio_post_with_retry(
            client=client,
            url="https://api.twilio.com/Messages.json",
            data={"Body": "hi"},
            auth=("AC", "tok"),
            to_number="whatsapp:+invalid",
            on_permanent_failure=cb,
            sleep=AsyncMock(),
        )

        assert result.success is False
        assert result.attempts == 1
        cb.assert_called_once()

    @pytest.mark.asyncio
    async def test_callback_not_invoked_on_success(self):
        """Callback NOT called when Twilio returns 200/201."""
        client = _make_client(_make_response(201, {"sid": "SM1"}))
        cb = AsyncMock()

        result = await twilio_post_with_retry(
            client=client,
            url="https://api.twilio.com/Messages.json",
            data={"Body": "hi"},
            auth=("AC", "tok"),
            on_permanent_failure=cb,
            sleep=AsyncMock(),
        )

        assert result.success is True
        cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_callback_not_invoked_on_silent_63016(self):
        """Callback NOT called for Twilio code 63016 (non-WA user) — silent failure."""
        client = _make_client(
            _make_response(400, {"code": 63016, "message": "Not a WA user"})
        )
        cb = AsyncMock()
        notifier = _mock_notifier()

        result = await twilio_post_with_retry(
            client=client,
            url="https://api.twilio.com/Messages.json",
            data={"Body": "hi"},
            auth=("AC", "tok"),
            admin_notifier=notifier,
            on_permanent_failure=cb,
            sleep=AsyncMock(),
        )

        assert result.success is False
        assert result.twilio_error_code == "63016"
        notifier.notify_twilio_error.assert_not_called()
        cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_callback_not_invoked_on_silent_63003(self):
        """Callback NOT called for Twilio code 63003 — silent failure."""
        client = _make_client(
            _make_response(400, {"code": 63003, "message": "Capability disabled"})
        )
        cb = AsyncMock()

        result = await twilio_post_with_retry(
            client=client,
            url="https://api.twilio.com/Messages.json",
            data={"Body": "hi"},
            auth=("AC", "tok"),
            on_permanent_failure=cb,
            sleep=AsyncMock(),
        )

        assert result.success is False
        cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_callback_does_not_raise(self):
        """on_permanent_failure=None (default) must not raise on failure."""
        client = _make_client(
            _make_response(400, {"code": 21211, "message": "Invalid"})
        )

        result = await twilio_post_with_retry(
            client=client,
            url="https://api.twilio.com/Messages.json",
            data={"Body": "hi"},
            auth=("AC", "tok"),
            on_permanent_failure=None,
            sleep=AsyncMock(),
        )
        # Must not raise
        assert result.success is False

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_propagate(self):
        """A raising callback must not bubble up — failure is logged only."""
        client = _make_client(
            _make_response(400, {"code": 21211, "message": "Invalid"})
        )

        async def bad_cb(result: TwilioPostResult) -> None:
            raise RuntimeError("DB unavailable")

        # Must not raise despite callback error
        result = await twilio_post_with_retry(
            client=client,
            url="https://api.twilio.com/Messages.json",
            data={"Body": "hi"},
            auth=("AC", "tok"),
            on_permanent_failure=bad_cb,
            sleep=AsyncMock(),
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_callback_invoked_after_admin_alert(self):
        """Callback fires AFTER admin alert (ordering matters for tracing)."""
        events: list[str] = []
        notifier = MagicMock()

        async def mock_notify_error(code, msg, **kw):
            events.append("alert")
            return True

        notifier.notify_twilio_error = mock_notify_error

        async def cb(result: TwilioPostResult) -> None:
            events.append("callback")

        client = _make_client(
            _make_response(400, {"code": 21211, "message": "Invalid"})
        )
        await twilio_post_with_retry(
            client=client,
            url="https://api.twilio.com/Messages.json",
            data={"Body": "hi"},
            auth=("AC", "tok"),
            admin_notifier=notifier,
            on_permanent_failure=cb,
            sleep=AsyncMock(),
        )

        assert events == ["alert", "callback"]


# ---------------------------------------------------------------------------
# Part 3 — DB marker logic (unit-level, no real DB)
# ---------------------------------------------------------------------------

class TestWaSendFailedMarkerHelper:
    """Unit tests for the wa_send_failed marker write helper."""

    @pytest.mark.asyncio
    async def test_wa_send_failed_event_written(self):
        """write_wa_send_failed_marker writes a lead_event row."""
        from app.bot.channels.wa_failure_marker import write_wa_send_failed_marker

        contact_id = 999
        result = TwilioPostResult(
            success=False,
            status_code=500,
            response_json=None,
            twilio_error_code="63016",
            attempts=4,
            to_number="whatsapp:+595981000001",
            message_type="template",
        )

        # Mock a session factory that captures SQL calls
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()

        mock_factory = MagicMock(return_value=mock_session)

        await write_wa_send_failed_marker(
            result=result,
            contact_id=contact_id,
            message_id=None,
            session_factory=mock_factory,
        )

        mock_session.execute.assert_called()
        mock_session.commit.assert_called_once()

        # Verify the INSERT call has the right parameters
        first_call = mock_session.execute.call_args_list[0]
        params = first_call[0][1]  # second positional arg = params dict
        assert params["id"] == contact_id
        meta = json.loads(params["meta"])
        assert meta["to_number"] == "whatsapp:+595981000001"
        assert meta["attempts"] == 4
        assert meta["message_type"] == "template"
        assert meta["twilio_error_code"] == "63016"

    @pytest.mark.asyncio
    async def test_wa_send_failed_marks_message_row_when_id_provided(self):
        """write_wa_send_failed_marker also UPDATEs the message row when message_id given."""
        from app.bot.channels.wa_failure_marker import write_wa_send_failed_marker

        contact_id = 999
        message_id = 42
        result = TwilioPostResult(
            success=False,
            status_code=400,
            response_json=None,
            twilio_error_code="21211",
            attempts=1,
            to_number="whatsapp:+595981000002",
            message_type="text",
        )

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()

        mock_factory = MagicMock(return_value=mock_session)

        await write_wa_send_failed_marker(
            result=result,
            contact_id=contact_id,
            message_id=message_id,
            session_factory=mock_factory,
        )

        # Should have 2 execute calls: INSERT lead_event + UPDATE messages
        assert mock_session.execute.call_count == 2
        mock_session.commit.assert_called_once()

        # Second call is the UPDATE
        update_call = mock_session.execute.call_args_list[1]
        update_params = update_call[0][1]
        assert update_params["mid"] == message_id
        assert update_params["ec"] == "21211"

    @pytest.mark.asyncio
    async def test_wa_send_failed_no_message_id_only_one_execute(self):
        """When message_id is None only INSERT is executed (no UPDATE)."""
        from app.bot.channels.wa_failure_marker import write_wa_send_failed_marker

        result = TwilioPostResult(
            success=False,
            status_code=500,
            response_json=None,
            twilio_error_code=None,
            attempts=4,
            to_number="whatsapp:+595981000003",
            message_type="template",
        )

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()

        mock_factory = MagicMock(return_value=mock_session)

        await write_wa_send_failed_marker(
            result=result,
            contact_id=1,
            message_id=None,
            session_factory=mock_factory,
        )

        assert mock_session.execute.call_count == 1  # only INSERT

    @pytest.mark.asyncio
    async def test_marker_exception_is_swallowed(self):
        """DB errors in the marker are swallowed (best-effort)."""
        from app.bot.channels.wa_failure_marker import write_wa_send_failed_marker

        result = TwilioPostResult(
            success=False,
            status_code=500,
            response_json=None,
            twilio_error_code=None,
            attempts=4,
        )

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(side_effect=Exception("DB down"))

        mock_factory = MagicMock(return_value=mock_session)

        # Must not raise
        await write_wa_send_failed_marker(
            result=result,
            contact_id=1,
            message_id=None,
            session_factory=mock_factory,
        )


# ---------------------------------------------------------------------------
# Part 4 — IC service wires the callback
# ---------------------------------------------------------------------------

class TestIcServiceWiresFailureCallback:
    """InfocasasService._twilio_post passes on_permanent_failure to retry helper."""

    @pytest.mark.asyncio
    async def test_ic_twilio_post_passes_on_permanent_failure(self):
        """_twilio_post_with_marker calls twilio_post_with_retry with on_permanent_failure."""
        from app.bot.services.infocasas.infocasas_service import InfocasasService
        from app.bot.services.infocasas.session_manager import SessionManager
        from app.bot.services.infocasas.notification_fetcher import NotificationFetcher

        svc = InfocasasService(
            session_manager=MagicMock(spec=SessionManager),
            notification_fetcher=MagicMock(spec=NotificationFetcher),
        )

        captured: dict = {}

        async def mock_retry(client, url, data, auth, **kwargs):
            captured.update(kwargs)
            return TwilioPostResult(
                success=False,
                status_code=500,
                response_json=None,
                twilio_error_code=None,
                attempts=4,
            )

        with patch("app.bot.services.infocasas.infocasas_service.twilio_post_with_retry", mock_retry), \
             patch("app.bot.services.admin_notifier.get_admin_notifier", MagicMock(return_value=None)), \
             patch("app.bot.config.bot_settings") as mock_settings:
            mock_settings.TWILIO_ACCOUNT_SID = "AC_test"
            mock_settings.TWILIO_AUTH_TOKEN = "tok_test"
            mock_settings.TWILIO_WHATSAPP_FROM = "whatsapp:+595900000000"

            await svc._twilio_post_with_marker(
                "https://api.twilio.com/test",
                {"To": "whatsapp:+595981000001", "ContentSid": "HX123"},
                contact_id=42,
                message_id=None,
            )

        assert "on_permanent_failure" in captured
        assert callable(captured["on_permanent_failure"])
