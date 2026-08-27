"""`GEMINI_API_KEY` vacia es un estado esperado, no un error fatal.

Esta vacia a proposito en produccion desde hace meses. El commit 3a75092 la
volvio fatal —`RuntimeError` al armar el grafo— y como el grafo se arma
perezosamente en la primera request, cada WhatsApp entrante moria ahi.

Gemini entra en dos lugares y los dos ya saben vivir sin el:
  - `SearchService`: sin cliente, `_vector_search` queda en None y la busqueda
    es SQL puro. La pierna vectorial es una de las dos del hibrido.
  - Fallback del circuit breaker: sin cliente, `ai_dispatch` cae al texto fijo,
    que es la ultima red que ya existia.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.bot.webhooks.dependencies import (
    build_bot_dependencies,
    reset_bot_dependencies,
)


@pytest.fixture(autouse=True)
def _singleton_limpio():
    reset_bot_dependencies()
    yield
    reset_bot_dependencies()


@pytest.fixture
def bot_settings_falsos():
    """Valores de juguete: ningun cliente hace I/O al construirse."""
    with patch("app.bot.webhooks.dependencies.bot_settings") as falso:
        falso.ANTHROPIC_API_KEY = "sk-test-anthropic"
        falso.CLAUDE_MODEL = "claude-haiku-4-5-20251001"
        falso.BOT_TIMEOUT_SECONDS = 10
        falso.BOT_MAX_RETRIES = 2
        falso.GEMINI_API_KEY = "test-gemini-key"
        falso.GEMINI_EMBEDDING_MODEL = "gemini-embedding-001"
        falso.BOT_CIRCUIT_BREAKER_THRESHOLD = 3
        falso.BOT_CIRCUIT_BREAKER_RESET_SECONDS = 300
        falso.RATE_LIMIT_MAX_MESSAGES = 5
        falso.RATE_LIMIT_WINDOW_SECONDS = 60
        falso.TELEGRAM_BOT_TOKEN = "123456:ABC-DEF"
        falso.TWILIO_ACCOUNT_SID = "AC_test"
        falso.TWILIO_AUTH_TOKEN = "auth_test"
        falso.TWILIO_WHATSAPP_FROM = "whatsapp:+595900000000"
        falso.TWILIO_STATUS_CALLBACK_URL = ""
        falso.GEO_DATA_PATH = "/tmp/test_geo"
        yield falso


def test_key_vacia_no_levanta_y_deja_el_grafo_armado(bot_settings_falsos):
    bot_settings_falsos.GEMINI_API_KEY = ""

    deps = build_bot_dependencies()

    assert deps.wa_handler is not None
    assert deps.tg_handler is not None


def test_key_vacia_deja_la_busqueda_sin_pierna_vectorial(bot_settings_falsos):
    bot_settings_falsos.GEMINI_API_KEY = ""

    deps = build_bot_dependencies()
    search_service = deps.wa_handler._orchestrator._search_service

    assert search_service._vector_search is None, (
        "sin key la busqueda tiene que quedar SQL puro, no reventar"
    )


def test_key_vacia_deja_el_fallback_sin_cliente(bot_settings_falsos):
    bot_settings_falsos.GEMINI_API_KEY = ""

    deps = build_bot_dependencies()

    assert deps.wa_handler._orchestrator._gemini is None


def test_con_key_el_grafo_arma_la_pierna_vectorial(bot_settings_falsos):
    """La contracara: con key, nada se degrada."""
    deps = build_bot_dependencies()

    assert deps.wa_handler._orchestrator._gemini is not None
    assert deps.wa_handler._orchestrator._search_service._vector_search is not None
