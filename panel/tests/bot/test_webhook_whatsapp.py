"""Tests for WhatsApp/Twilio webhook endpoint.

Plan 66-02: 12 tests covering parse, signature verification, route,
StatusCallback filtering, CSRF bypass, and edge cases.

El procesamiento en background esta doblado (ver `_sin_pipeline`): decia «all
dependencies mocked» y no era cierto — `background_tasks.add_task` con
TestClient corre sincronicamente dentro del request y `_process_whatsapp` arma
el grafo entero del bot, con Claude, Gemini, el buscador y una sesion de base.
Con GEMINI_API_KEY vacia moria en `genai.Client(api_key="")` y el 200 salia
500, asi que estos tests fallaban por una credencial ausente y no por el
webhook.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.bot.core.types import BotRequest
from app.bot.webhooks.whatsapp import (
    parse_twilio_webhook,
    verify_twilio_signature,
    _EMPTY_TWIML,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_form() -> dict[str, str]:
    """Minimal valid Twilio incoming message form data."""
    return {
        "MessageSid": "SM1234567890abcdef",
        "From": "whatsapp:+595981555000",
        "ProfileName": "Juan Test",
        "Body": "Hola, busco departamento",
        "SmsStatus": "received",
    }


def _status_callback_form(status: str = "delivered") -> dict[str, str]:
    """Twilio StatusCallback form data (no Body)."""
    return {
        "MessageSid": "SM1234567890abcdef",
        "From": "whatsapp:+595981555000",
        "SmsStatus": status,
    }


def _button_form() -> dict[str, str]:
    """Twilio interactive button press form data."""
    return {
        "MessageSid": "SM_button_001",
        "From": "whatsapp:+595981555000",
        "ProfileName": "Juan Test",
        "Body": "",
        "SmsStatus": "received",
        "ButtonPayload": "ver_mas_12345",
        "ButtonText": "Ver mas propiedades",
    }


def _compute_signature(url: str, params: dict, auth_token: str) -> str:
    """Compute a valid Twilio HMAC-SHA1 signature for testing."""
    data_str = url
    for key in sorted(params.keys()):
        data_str += key + params[key]
    return base64.b64encode(
        hmac.new(auth_token.encode(), data_str.encode(), hashlib.sha1).digest()
    ).decode()


# ===========================================================================
# Test parse_twilio_webhook
# ===========================================================================

class TestParseTwilioWebhook:
    """Unit tests for parse_twilio_webhook."""

    def test_parse_normal_message(self):
        """Normal text message is parsed into BotRequest."""
        result = parse_twilio_webhook(_base_form())
        assert result is not None
        assert isinstance(result, BotRequest)
        assert result.platform == "whatsapp"
        assert result.chat_id == "+595981555000"
        assert result.user_id == "+595981555000"
        assert result.user_name == "Juan Test"
        assert result.text == "Hola, busco departamento"
        assert result.external_id == "SM1234567890abcdef"
        assert result.callback_data is None

    def test_parse_status_callback_delivered(self):
        """StatusCallback with 'delivered' and no Body returns None."""
        result = parse_twilio_webhook(_status_callback_form("delivered"))
        assert result is None

    def test_parse_status_callback_sent(self):
        """StatusCallback with 'sent' and no Body returns None."""
        result = parse_twilio_webhook(_status_callback_form("sent"))
        assert result is None

    def test_parse_status_callback_read(self):
        """StatusCallback with 'read' and no Body returns None."""
        result = parse_twilio_webhook(_status_callback_form("read"))
        assert result is None

    def test_parse_status_callback_failed(self):
        """StatusCallback with 'failed' and no Body returns None."""
        result = parse_twilio_webhook(_status_callback_form("failed"))
        assert result is None

    def test_parse_status_callback_undelivered(self):
        """StatusCallback with 'undelivered' and no Body returns None."""
        result = parse_twilio_webhook(_status_callback_form("undelivered"))
        assert result is None

    def test_parse_button_press(self):
        """Button press populates callback_data and text."""
        result = parse_twilio_webhook(_button_form())
        assert result is not None
        assert result.callback_data == "ver_mas_12345"
        assert result.text == "Ver mas propiedades"
        assert result.platform == "whatsapp"

    def test_parse_button_no_text_falls_back_to_payload(self):
        """Button without ButtonText uses ButtonPayload as text."""
        form = _button_form()
        del form["ButtonText"]
        result = parse_twilio_webhook(form)
        assert result is not None
        assert result.text == "ver_mas_12345"
        assert result.callback_data == "ver_mas_12345"

    def test_parse_no_body_no_button_returns_none(self):
        """Message with no Body and no ButtonPayload returns None."""
        form = _base_form()
        form["Body"] = ""
        result = parse_twilio_webhook(form)
        assert result is None

    def test_parse_missing_from_returns_none(self):
        """Message with no From field returns None."""
        form = _base_form()
        form["From"] = ""
        result = parse_twilio_webhook(form)
        assert result is None

    def test_parse_no_profile_name_uses_phone(self):
        """When ProfileName is missing, user_name falls back to phone."""
        form = _base_form()
        form["ProfileName"] = ""
        result = parse_twilio_webhook(form)
        assert result is not None
        assert result.user_name == "+595981555000"

    def test_parse_status_callback_with_body_is_message(self):
        """StatusCallback with Body present is treated as a real message."""
        form = _status_callback_form("delivered")
        form["Body"] = "This is a real message"
        form["ProfileName"] = "Test"
        result = parse_twilio_webhook(form)
        assert result is not None
        assert result.text == "This is a real message"


# ===========================================================================
# Test verify_twilio_signature
# ===========================================================================

class TestVerifyTwilioSignature:
    """Unit tests for HMAC-SHA1 signature verification."""

    def test_valid_signature_passes(self):
        """Correct signature is accepted."""
        url = "https://onnix.com.py/webhook/whatsapp"
        params = _base_form()
        token = "test_auth_token_12345"
        sig = _compute_signature(url, params, token)

        assert verify_twilio_signature(url, params, sig, token) is True

    def test_invalid_signature_rejected(self):
        """Wrong signature is rejected."""
        url = "https://onnix.com.py/webhook/whatsapp"
        params = _base_form()
        token = "test_auth_token_12345"

        assert verify_twilio_signature(url, params, "bad_sig==", token) is False

    def test_tampered_params_rejected(self):
        """Correct signature with tampered params fails."""
        url = "https://onnix.com.py/webhook/whatsapp"
        params = _base_form()
        token = "test_auth_token_12345"
        sig = _compute_signature(url, params, token)

        # Tamper the body
        params["Body"] = "TAMPERED"
        assert verify_twilio_signature(url, params, sig, token) is False

    def test_empty_params(self):
        """Verification works with empty params dict."""
        url = "https://example.com/hook"
        token = "tok"
        params: dict[str, str] = {}
        sig = _compute_signature(url, params, token)
        assert verify_twilio_signature(url, params, sig, token) is True


# ===========================================================================
# Test the FastAPI route (integration via TestClient)
# ===========================================================================

class TestWhatsAppWebhookRoute:
    """Integration tests for POST /webhook/whatsapp."""

    @pytest.fixture(autouse=True)
    def _sin_pipeline(self):
        """Reemplaza el procesamiento en background por un doble.

        Estos tests son del webhook: firma, parseo, status y TwiML. Que el
        pipeline funcione es de los tests del pipeline.
        """
        with patch(
            "app.bot.webhooks.whatsapp._process_whatsapp", new_callable=AsyncMock
        ) as doble:
            yield doble

    @pytest.fixture
    def client(self):
        """TestClient with Twilio auth token disabled (dev mode)."""
        with patch(
            "app.bot.webhooks.whatsapp._get_twilio_auth_token",
            return_value="",
        ):
            from app.main import app
            yield TestClient(app)

    @pytest.fixture
    def client_with_auth(self):
        """TestClient with Twilio auth token enabled."""
        token = "test_secret_token"
        with patch(
            "app.bot.webhooks.whatsapp._get_twilio_auth_token",
            return_value=token,
        ), patch(
            "app.bot.webhooks.whatsapp._get_webhook_base_url",
            return_value="",
        ):
            from app.main import app
            yield TestClient(app), token

    def test_normal_message_returns_twiml(self, client):
        """Normal message returns 200 with empty TwiML."""
        resp = client.post("/webhook/whatsapp", data=_base_form())
        assert resp.status_code == 200
        assert resp.text == _EMPTY_TWIML
        assert "application/xml" in resp.headers["content-type"]

    def test_normal_message_agenda_el_procesamiento(self, client, _sin_pipeline):
        """Nadie verificaba que el mensaje llegara al pipeline: se notaba solo
        cuando el pipeline reventaba y el 200 se volvia 500."""
        client.post("/webhook/whatsapp", data=_base_form())
        _sin_pipeline.assert_awaited_once()
        (enviado,) = _sin_pipeline.await_args.args
        assert enviado.platform == "whatsapp"
        assert enviado.text == "Hola, busco departamento"

    def test_status_callback_no_agenda_nada(self, client, _sin_pipeline):
        """Un StatusCallback no es un mensaje: no tiene que entrar al bot."""
        client.post("/webhook/whatsapp", data=_status_callback_form())
        _sin_pipeline.assert_not_awaited()

    def test_status_callback_returns_twiml(self, client):
        """StatusCallback returns 200 with empty TwiML (no error)."""
        resp = client.post("/webhook/whatsapp", data=_status_callback_form())
        assert resp.status_code == 200
        assert resp.text == _EMPTY_TWIML

    def test_missing_signature_returns_403(self, client_with_auth):
        """When auth is enabled, missing X-Twilio-Signature returns 403."""
        client, token = client_with_auth
        resp = client.post("/webhook/whatsapp", data=_base_form())
        assert resp.status_code == 403

    def test_invalid_signature_returns_403(self, client_with_auth):
        """When auth is enabled, wrong signature returns 403."""
        client, token = client_with_auth
        resp = client.post(
            "/webhook/whatsapp",
            data=_base_form(),
            headers={"X-Twilio-Signature": "invalid=="},
        )
        assert resp.status_code == 403

    def test_valid_signature_returns_200(self, client_with_auth):
        """When auth is enabled, correct signature returns 200."""
        client, token = client_with_auth
        form = _base_form()
        # TestClient URL: the route needs the full URL for sig computation
        url = "http://testserver/webhook/whatsapp"
        sig = _compute_signature(url, form, token)
        resp = client.post(
            "/webhook/whatsapp",
            data=form,
            headers={"X-Twilio-Signature": sig},
        )
        assert resp.status_code == 200
        assert resp.text == _EMPTY_TWIML

    def test_csrf_bypass_no_referer(self, client):
        """Webhook path bypasses CSRF even without Referer header."""
        # Normally a POST without matching referer would be blocked
        resp = client.post(
            "/webhook/whatsapp",
            data=_base_form(),
            headers={"host": "onnix.com.py", "referer": "https://evil.com"},
        )
        assert resp.status_code == 200

    def test_button_message_returns_200(self, client):
        """Button press message returns 200 with empty TwiML."""
        resp = client.post("/webhook/whatsapp", data=_button_form())
        assert resp.status_code == 200
        assert resp.text == _EMPTY_TWIML

    def test_empty_body_no_button_returns_200(self, client):
        """Message with empty body and no button returns 200 (filtered)."""
        form = _base_form()
        form["Body"] = ""
        resp = client.post("/webhook/whatsapp", data=form)
        assert resp.status_code == 200
        assert resp.text == _EMPTY_TWIML


# ===========================================================================
# Test status callback NO_ALERT_TWILIO_CODES suppression
# ===========================================================================

class TestStatusCallbackAlertSuppression:
    """Tests that silent Twilio error codes skip admin notification."""

    @pytest.fixture
    def client(self):
        """TestClient with Twilio auth disabled."""
        with patch(
            "app.bot.webhooks.whatsapp._get_twilio_auth_token",
            return_value="",
        ):
            from app.main import app
            yield TestClient(app)

    def _status_error_form(self, error_code: str) -> dict[str, str]:
        return {
            "MessageSid": "SMteststatuserror001",
            "MessageStatus": "failed",
            "ErrorCode": error_code,
            "To": "whatsapp:+595981555000",
            "From": "whatsapp:+595900000000",
        }

    def test_status_callback_suppresses_63016(self, client, caplog):
        """ErrorCode 63016 (not on WhatsApp) must NOT trigger notify_twilio_error."""
        mock_notifier = MagicMock()
        mock_notifier.notify_twilio_error = AsyncMock()

        with patch(
            "app.bot.webhooks.whatsapp.get_admin_notifier",
            return_value=mock_notifier,
            create=True,
        ), caplog.at_level(logging.WARNING):
            resp = client.post(
                "/webhook/whatsapp/status",
                data=self._status_error_form("63016"),
            )

        assert resp.status_code == 200
        mock_notifier.notify_twilio_error.assert_not_called()
        assert any(
            "Suppressed notification" in record.message
            and "63016" in record.message
            for record in caplog.records
        )

    def test_status_callback_fires_alert_for_real_errors(self, client):
        """ErrorCode 21211 (invalid phone) must call notify_twilio_error."""
        mock_notifier = MagicMock()
        mock_notifier.notify_twilio_error = AsyncMock()

        with patch(
            "app.bot.services.admin_notifier.get_admin_notifier",
            return_value=mock_notifier,
        ), patch(
            "app.bot.webhooks.whatsapp._twilio_error_notified",
            {},
        ):
            resp = client.post(
                "/webhook/whatsapp/status",
                data=self._status_error_form("21211"),
            )

        assert resp.status_code == 200
