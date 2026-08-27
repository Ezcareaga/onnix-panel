"""Tests for twilio_retry.twilio_post_with_retry helper.

TDD: RED phase — written before the implementation exists.

Covers:
- Success on first attempt
- Retry on 500, 429, TimeoutException, NetworkError
- Admin alert on retries exhausted
- Admin alert on permanent 4xx (non-special codes)
- No alert for Twilio subcodes 63016 and 63003
- Sleep called with correct backoff delays
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import httpx
import pytest

from app.bot.channels.twilio_retry import (
    RETRY_DELAYS,
    TwilioPostResult,
    twilio_post_with_retry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_notifier() -> MagicMock:
    notifier = MagicMock()
    notifier.notify_twilio_error = AsyncMock(return_value=True)
    return notifier


def _make_response(status: int, body: dict | None = None) -> httpx.Response:
    """Build a minimal httpx.Response for mocking."""
    import json as _json

    content = _json.dumps(body or {}).encode()
    return httpx.Response(status, content=content)


def _make_client_with_responses(*responses: httpx.Response | Exception) -> httpx.AsyncClient:
    """Return an AsyncClient whose .post is patched to return responses in order."""
    client = MagicMock(spec=httpx.AsyncClient)
    side_effects = list(responses)
    client.post = AsyncMock(side_effect=side_effects)
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTwilioPostWithRetry:
    """Unit tests for twilio_post_with_retry."""

    @pytest.mark.asyncio
    async def test_success_first_attempt_no_retry(self):
        """200 response → success=True, attempts=1, no sleeps, no alert."""
        resp = _make_response(200, {"sid": "SM1", "status": "queued"})
        client = _make_client_with_responses(resp)
        sleep_mock = AsyncMock()
        notifier = _mock_notifier()

        result = await twilio_post_with_retry(
            client=client,
            url="https://api.twilio.com/Messages.json",
            data={"Body": "hi"},
            auth=("AC_sid", "token"),
            admin_notifier=notifier,
            sleep=sleep_mock,
        )

        assert result.success is True
        assert result.attempts == 1
        assert result.status_code == 200
        sleep_mock.assert_not_called()
        notifier.notify_twilio_error.assert_not_called()

    @pytest.mark.asyncio
    async def test_retries_on_500_then_succeeds(self):
        """500, 500, 200 → success=True, attempts=3, 2 sleeps (1.0, 3.0)."""
        client = _make_client_with_responses(
            _make_response(500, {"message": "Internal error"}),
            _make_response(500, {"message": "Internal error"}),
            _make_response(201, {"sid": "SM2", "status": "queued"}),
        )
        sleep_mock = AsyncMock()
        notifier = _mock_notifier()

        result = await twilio_post_with_retry(
            client=client,
            url="https://api.twilio.com/Messages.json",
            data={"Body": "hi"},
            auth=("AC_sid", "token"),
            admin_notifier=notifier,
            sleep=sleep_mock,
        )

        assert result.success is True
        assert result.attempts == 3
        assert sleep_mock.call_count == 2
        sleep_mock.assert_any_call(RETRY_DELAYS[0])  # 1.0
        sleep_mock.assert_any_call(RETRY_DELAYS[1])  # 3.0
        notifier.notify_twilio_error.assert_not_called()

    @pytest.mark.asyncio
    async def test_retries_on_429_then_succeeds(self):
        """429 (rate limited), 200 → success=True, attempts=2."""
        client = _make_client_with_responses(
            _make_response(429, {"message": "Rate limited"}),
            _make_response(201, {"sid": "SM3", "status": "queued"}),
        )
        sleep_mock = AsyncMock()
        notifier = _mock_notifier()

        result = await twilio_post_with_retry(
            client=client,
            url="https://api.twilio.com/Messages.json",
            data={"Body": "hi"},
            auth=("AC_sid", "token"),
            admin_notifier=notifier,
            sleep=sleep_mock,
        )

        assert result.success is True
        assert result.attempts == 2
        assert sleep_mock.call_count == 1
        sleep_mock.assert_called_once_with(RETRY_DELAYS[0])  # 1.0

    @pytest.mark.asyncio
    async def test_retries_on_timeout_then_succeeds(self):
        """TimeoutException, 200 → success=True, attempts=2."""
        client = _make_client_with_responses(
            httpx.TimeoutException("timed out"),
            _make_response(201, {"sid": "SM4", "status": "queued"}),
        )
        sleep_mock = AsyncMock()
        notifier = _mock_notifier()

        result = await twilio_post_with_retry(
            client=client,
            url="https://api.twilio.com/Messages.json",
            data={"Body": "hi"},
            auth=("AC_sid", "token"),
            admin_notifier=notifier,
            sleep=sleep_mock,
        )

        assert result.success is True
        assert result.attempts == 2
        assert sleep_mock.call_count == 1

    @pytest.mark.asyncio
    async def test_exhausts_retries_alerts_admin(self):
        """500 × 4 → success=False, attempts=4, admin alerted exactly once."""
        client = _make_client_with_responses(
            _make_response(500, {"message": "err"}),
            _make_response(500, {"message": "err"}),
            _make_response(500, {"message": "err"}),
            _make_response(500, {"message": "err"}),
        )
        sleep_mock = AsyncMock()
        notifier = _mock_notifier()

        result = await twilio_post_with_retry(
            client=client,
            url="https://api.twilio.com/Messages.json",
            data={"Body": "hi"},
            auth=("AC_sid", "token"),
            admin_notifier=notifier,
            to_number="whatsapp:+595981000001",
            sleep=sleep_mock,
        )

        assert result.success is False
        assert result.attempts == 4
        assert sleep_mock.call_count == 3  # delays before retries 1, 2, 3
        notifier.notify_twilio_error.assert_called_once()

    @pytest.mark.asyncio
    async def test_permanent_4xx_alerts_admin_no_retry(self):
        """400 with generic body → success=False, attempts=1, admin alerted."""
        client = _make_client_with_responses(
            _make_response(400, {"code": 21211, "message": "Invalid phone number"})
        )
        sleep_mock = AsyncMock()
        notifier = _mock_notifier()

        result = await twilio_post_with_retry(
            client=client,
            url="https://api.twilio.com/Messages.json",
            data={"Body": "hi"},
            auth=("AC_sid", "token"),
            admin_notifier=notifier,
            to_number="whatsapp:+5959INVALID",
            sleep=sleep_mock,
        )

        assert result.success is False
        assert result.attempts == 1
        sleep_mock.assert_not_called()
        notifier.notify_twilio_error.assert_called_once()

    @pytest.mark.asyncio
    async def test_twilio_code_63016_no_retry_no_alert(self):
        """400 with code=63016 → success=False, attempts=1, admin NOT alerted."""
        client = _make_client_with_responses(
            _make_response(400, {"code": 63016, "message": "Recipient is not a WhatsApp user"})
        )
        sleep_mock = AsyncMock()
        notifier = _mock_notifier()

        result = await twilio_post_with_retry(
            client=client,
            url="https://api.twilio.com/Messages.json",
            data={"Body": "hi"},
            auth=("AC_sid", "token"),
            admin_notifier=notifier,
            sleep=sleep_mock,
        )

        assert result.success is False
        assert result.attempts == 1
        assert result.twilio_error_code == "63016"
        sleep_mock.assert_not_called()
        notifier.notify_twilio_error.assert_not_called()

    @pytest.mark.asyncio
    async def test_twilio_code_63003_no_retry_no_alert(self):
        """400 with code=63003 → success=False, attempts=1, admin NOT alerted."""
        client = _make_client_with_responses(
            _make_response(400, {"code": 63003, "message": "Capability not enabled"})
        )
        sleep_mock = AsyncMock()
        notifier = _mock_notifier()

        result = await twilio_post_with_retry(
            client=client,
            url="https://api.twilio.com/Messages.json",
            data={"Body": "hi"},
            auth=("AC_sid", "token"),
            admin_notifier=notifier,
            sleep=sleep_mock,
        )

        assert result.success is False
        assert result.attempts == 1
        assert result.twilio_error_code == "63003"
        sleep_mock.assert_not_called()
        notifier.notify_twilio_error.assert_not_called()

    @pytest.mark.asyncio
    async def test_injected_sleep_called_with_correct_delays(self):
        """On full exhaustion, sleep is called with delays 1.0, 3.0, 9.0 in order."""
        client = _make_client_with_responses(
            _make_response(500, {}),
            _make_response(500, {}),
            _make_response(500, {}),
            _make_response(500, {}),
        )
        sleep_mock = AsyncMock()
        notifier = _mock_notifier()

        await twilio_post_with_retry(
            client=client,
            url="https://api.twilio.com/Messages.json",
            data={"Body": "hi"},
            auth=("AC_sid", "token"),
            admin_notifier=notifier,
            sleep=sleep_mock,
        )

        assert sleep_mock.call_count == 3
        assert sleep_mock.call_args_list == [
            call(RETRY_DELAYS[0]),  # 1.0
            call(RETRY_DELAYS[1]),  # 3.0
            call(RETRY_DELAYS[2]),  # 9.0
        ]

    @pytest.mark.asyncio
    async def test_network_error_retried(self):
        """httpx.NetworkError is treated as transient — retried like timeout."""
        client = _make_client_with_responses(
            httpx.NetworkError("connection refused"),
            _make_response(201, {"sid": "SM5", "status": "queued"}),
        )
        sleep_mock = AsyncMock()
        notifier = _mock_notifier()

        result = await twilio_post_with_retry(
            client=client,
            url="https://api.twilio.com/Messages.json",
            data={"Body": "hi"},
            auth=("AC_sid", "token"),
            admin_notifier=notifier,
            sleep=sleep_mock,
        )

        assert result.success is True
        assert result.attempts == 2
        assert sleep_mock.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_http_transport_error(self):
        """httpx.ReadError (TransportError subclass) is treated as transient — retried."""
        client = _make_client_with_responses(
            httpx.ReadError("connection reset by peer"),
            _make_response(200, {"sid": "SM6", "status": "queued"}),
        )
        sleep_mock = AsyncMock()
        notifier = _mock_notifier()

        result = await twilio_post_with_retry(
            client=client,
            url="https://api.twilio.com/Messages.json",
            data={"Body": "hi"},
            auth=("AC_sid", "token"),
            admin_notifier=notifier,
            sleep=sleep_mock,
        )

        assert result.success is True
        assert result.attempts == 2
        assert sleep_mock.call_count == 1
        sleep_mock.assert_called_once_with(RETRY_DELAYS[0])  # 1.0
        notifier.notify_twilio_error.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_notifier_does_not_raise(self):
        """admin_notifier=None on permanent 4xx must not raise."""
        client = _make_client_with_responses(
            _make_response(400, {"code": 21211, "message": "Invalid phone"})
        )
        sleep_mock = AsyncMock()

        result = await twilio_post_with_retry(
            client=client,
            url="https://api.twilio.com/Messages.json",
            data={"Body": "hi"},
            auth=("AC_sid", "token"),
            admin_notifier=None,
            sleep=sleep_mock,
        )

        assert result.success is False
        assert result.attempts == 1

    @pytest.mark.asyncio
    async def test_permanent_failure_invokes_on_permanent_failure_callback(self):
        """Callback called once with TwilioPostResult(success=False, attempts=4, to_number, message_type)."""
        client = _make_client_with_responses(
            _make_response(500, {"message": "err"}),
            _make_response(500, {"message": "err"}),
            _make_response(500, {"message": "err"}),
            _make_response(500, {"message": "err"}),
        )
        sleep_mock = AsyncMock()
        notifier = _mock_notifier()
        cb = AsyncMock()

        result = await twilio_post_with_retry(
            client=client,
            url="https://api.twilio.com/Messages.json",
            data={"Body": "hi"},
            auth=("AC_sid", "token"),
            admin_notifier=notifier,
            to_number="whatsapp:+595981000001",
            message_type="template",
            on_permanent_failure=cb,
            sleep=sleep_mock,
        )

        assert result.success is False
        assert result.attempts == 4
        cb.assert_called_once()
        called_result: TwilioPostResult = cb.call_args[0][0]
        assert called_result.success is False
        assert called_result.attempts == 4
        assert called_result.to_number == "whatsapp:+595981000001"
        assert called_result.message_type == "template"

    @pytest.mark.asyncio
    async def test_twilio_post_result_carries_to_number_and_message_type(self):
        """TwilioPostResult.to_number and .message_type are propagated from call args."""
        client = _make_client_with_responses(
            _make_response(201, {"sid": "SM_ok"})
        )
        sleep_mock = AsyncMock()

        result = await twilio_post_with_retry(
            client=client,
            url="https://api.twilio.com/Messages.json",
            data={"Body": "hola"},
            auth=("AC_sid", "token"),
            to_number="whatsapp:+595981000099",
            message_type="text",
            sleep=sleep_mock,
        )

        assert result.success is True
        assert result.to_number == "whatsapp:+595981000099"
        assert result.message_type == "text"
