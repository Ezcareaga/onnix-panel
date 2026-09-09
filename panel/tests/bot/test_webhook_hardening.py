"""Tests for webhook signature-verification hardening (E3).

Covers:
- is_production helper: False under pytest even with ENVIRONMENT=production
- Startup validator: raises RuntimeError when production + missing secret
- Startup validator: only logs warning when not production + missing secret
- WhatsApp webhook: 403 when production + missing Twilio auth token
- WhatsApp status callback: 403 when production + missing Twilio auth token
- WhatsApp webhook: dev-mode skip (200) when not production + missing token
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.bot.webhooks.whatsapp import (
    _EMPTY_TWIML,
    parse_twilio_webhook,
)


# ---------------------------------------------------------------------------
# Este archivo prueba el guard de firma, no el bot.
#
# Los dos casos «dev-mode skip» llegan al 200 y ahi el route agenda el
# procesamiento, que con TestClient corre sincronicamente dentro del request y
# arma el grafo entero del bot. Con GEMINI_API_KEY vacia moria en
# `genai.Client(api_key="")` y el 200 salia 500: el test fallaba por una
# credencial ausente y no por el guard que dice medir.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _sin_pipeline():
    with patch(
        "app.bot.webhooks.whatsapp._process_whatsapp", new_callable=AsyncMock
    ):
        yield


# ---------------------------------------------------------------------------
# Helpers shared with existing tests
# ---------------------------------------------------------------------------

def _base_wa_form() -> dict[str, str]:
    return {
        "MessageSid": "SM_hardening_001",
        "From": "whatsapp:+595981555000",
        "ProfileName": "Test User",
        "Body": "Hola",
        "SmsStatus": "received",
    }



# ===========================================================================
# Tests: is_production helper
# ===========================================================================

class TestIsProduction:
    """is_production must always be False under pytest."""

    def test_is_production_false_under_pytest_even_with_production_env(self):
        """Even if ENVIRONMENT=production, is_production is False when
        PYTEST_CURRENT_TEST is set (i.e. inside pytest).
        """
        from app.config import settings
        # PYTEST_CURRENT_TEST is set by pytest during all test runs
        assert os.environ.get("PYTEST_CURRENT_TEST") is not None
        # is_production must be False — we are running tests
        assert settings.is_production is False

    def test_is_production_false_when_environment_not_production(self):
        """is_production is False when ENVIRONMENT != 'production'."""
        from app.config import Settings
        s = Settings()
        # Default ENVIRONMENT is 'development' unless overridden in .env
        # We force it to development to ensure the check works
        with patch.object(s, "ENVIRONMENT", "development"):
            # Simulate not being in pytest by temporarily clearing the env var
            original = os.environ.pop("PYTEST_CURRENT_TEST", None)
            try:
                assert s.is_production is False
            finally:
                if original is not None:
                    os.environ["PYTEST_CURRENT_TEST"] = original

    def test_is_production_false_when_pytest_env_set_regardless_of_environment(self):
        """is_production is False when PYTEST_CURRENT_TEST is set,
        even if ENVIRONMENT attribute is 'production'.
        """
        from app.config import Settings
        s = Settings()
        with patch.object(s, "ENVIRONMENT", "production"):
            # PYTEST_CURRENT_TEST is already set (we are in pytest)
            assert os.environ.get("PYTEST_CURRENT_TEST") is not None
            assert s.is_production is False


# ===========================================================================
# Tests: startup validator
# ===========================================================================

class TestStartupValidator:
    """validate_required_secrets levanta RuntimeError en produccion si falta un secreto."""

    def test_raises_when_production_and_twilio_token_missing(self):
        """RuntimeError raised when forced production + no Twilio token."""
        from app.config import validate_required_secrets
        with pytest.raises(RuntimeError, match="TWILIO_AUTH_TOKEN"):
            validate_required_secrets(
                force_production=True,
                twilio_auth_token="",
            )

    def test_raises_names_both_when_both_missing(self):
        """RuntimeError raised when forced production + both secrets missing.
        The error message names the first missing secret encountered.
        """
        from app.config import validate_required_secrets
        with pytest.raises(RuntimeError):
            validate_required_secrets(
                force_production=True,
                twilio_auth_token="",
            )

    def test_no_raise_when_production_and_both_secrets_present(self):
        """No error when forced production + both secrets configured."""
        from app.config import validate_required_secrets
        # Should not raise
        validate_required_secrets(
            force_production=True,
            twilio_auth_token="real-token",
        )

    def test_no_raise_when_not_production_and_secrets_missing(self):
        """No error when not production + secrets missing (dev mode)."""
        from app.config import validate_required_secrets
        # Should not raise even with empty secrets
        validate_required_secrets(
            force_production=False,
            twilio_auth_token="",
        )


# ===========================================================================
# Tests: WhatsApp webhook — production fail-closed per request
# ===========================================================================

class TestWhatsAppWebhookProductionFailClosed:
    """Per-request 403 when production + no Twilio auth token."""

    @pytest.fixture
    def production_client_no_token(self):
        """TestClient where is_production=True and auth token is empty.

        Patches _settings (the module-level alias in whatsapp.py) directly,
        since the import `from app.config import settings as _settings` binds
        the name at import time.
        """
        mock_settings = MagicMock()
        mock_settings.is_production = True
        with patch("app.bot.webhooks.whatsapp._get_twilio_auth_token", return_value=""), \
             patch("app.bot.webhooks.whatsapp._settings", mock_settings):
            from app.main import app
            yield TestClient(app)

    @pytest.fixture
    def dev_client_no_token(self):
        """TestClient where is_production=False and auth token is empty."""
        mock_settings = MagicMock()
        mock_settings.is_production = False
        with patch("app.bot.webhooks.whatsapp._get_twilio_auth_token", return_value=""), \
             patch("app.bot.webhooks.whatsapp._settings", mock_settings):
            from app.main import app
            yield TestClient(app)

    def test_production_missing_token_returns_403_on_webhook(
        self, production_client_no_token
    ):
        """In production, missing Twilio token returns 403 on /webhook/whatsapp."""
        resp = production_client_no_token.post(
            "/webhook/whatsapp", data=_base_wa_form()
        )
        assert resp.status_code == 403

    def test_production_missing_token_returns_403_on_status_callback(
        self, production_client_no_token
    ):
        """In production, missing Twilio token returns 403 on /webhook/whatsapp/status."""
        form = {
            "MessageSid": "SM_status_001",
            "MessageStatus": "delivered",
            "To": "whatsapp:+595981555000",
        }
        resp = production_client_no_token.post(
            "/webhook/whatsapp/status", data=form
        )
        assert resp.status_code == 403

    def test_dev_missing_token_skips_and_returns_200(self, dev_client_no_token):
        """Outside production, missing token still returns 200 (dev-mode skip)."""
        resp = dev_client_no_token.post(
            "/webhook/whatsapp", data=_base_wa_form()
        )
        assert resp.status_code == 200
        assert resp.text == _EMPTY_TWIML
