"""Tests for webhook wiring and dependency injection.

Plan 66-03 Task 7: 10 tests covering BotDependencies, singleton pattern,
handler wiring, session lifecycle, and app registration.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from app.bot.core.types import BotRequest
from app.bot.handlers.message_handler import MessageHandler
from app.bot.webhooks.dependencies import (
    BotDependencies,
    build_bot_dependencies,
    get_bot_dependencies,
    reset_bot_dependencies,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Ensure a clean singleton state for each test."""
    reset_bot_dependencies()
    yield
    reset_bot_dependencies()


@pytest.fixture(autouse=True)
def _sin_guardado():
    """Este archivo es del cableado, no del guardado.

    Desde el 2026-08-24 `_process_*` guarda el entrante antes de pedir el
    grafo, y `persist_inbound` hace SQL de verdad: contra la `AsyncMock` que
    usan estos tests como sesion, `result.first()` devuelve una corrutina y
    revienta antes de llegar al handler. Lo que este archivo verifica —que el
    webhook llame al handler y arme una sola sesion— no cambia por eso.
    El invariante del guardado vive en tests/bot/test_entrante_nunca_se_pierde.py.
    """
    with patch(
        "app.bot.core.conversation.persist_inbound", new_callable=AsyncMock
    ) as doble:
        yield doble


@pytest.fixture
def _patch_bot_settings():
    """Patch bot_settings with dummy values so clients don't need real keys."""
    with patch("app.bot.webhooks.dependencies.bot_settings") as mock_settings:
        mock_settings.ANTHROPIC_API_KEY = "sk-test-anthropic"
        mock_settings.CLAUDE_MODEL = "claude-haiku-4-5-20251001"
        mock_settings.BOT_TIMEOUT_SECONDS = 10
        mock_settings.BOT_MAX_RETRIES = 2
        mock_settings.GEMINI_API_KEY = "test-gemini-key"
        mock_settings.GEMINI_EMBEDDING_MODEL = "gemini-embedding-001"
        mock_settings.BOT_CIRCUIT_BREAKER_THRESHOLD = 3
        mock_settings.BOT_CIRCUIT_BREAKER_RESET_SECONDS = 300
        mock_settings.RATE_LIMIT_MAX_MESSAGES = 5
        mock_settings.RATE_LIMIT_WINDOW_SECONDS = 60
        mock_settings.TELEGRAM_BOT_TOKEN = "123456:ABC-DEF"
        mock_settings.TWILIO_ACCOUNT_SID = "AC_test"
        mock_settings.TWILIO_AUTH_TOKEN = "auth_test"
        mock_settings.TWILIO_WHATSAPP_FROM = "whatsapp:+595900000000"
        mock_settings.GEO_DATA_PATH = "/tmp/test_geo"
        yield mock_settings


def _make_bot_request(platform: str = "telegram") -> BotRequest:
    """Create a minimal BotRequest for testing."""
    return BotRequest(
        platform=platform,
        chat_id="123456",
        user_id="user_001",
        user_name="Test User",
        text="Hola",
        external_id="ext_001",
    )


def _mock_deps_and_session():
    """Create mock deps and session for process tests."""
    mock_deps = MagicMock()
    mock_deps.tg_handler.handle = AsyncMock(return_value=True)
    mock_deps.wa_handler.handle = AsyncMock(return_value=True)

    mock_session = AsyncMock()
    mock_session_factory = MagicMock(return_value=mock_session)

    return mock_deps, mock_session, mock_session_factory


# ---------------------------------------------------------------------------
# Test 1: build_bot_dependencies constructs all components
# ---------------------------------------------------------------------------


def test_build_bot_dependencies_constructs_all(_patch_bot_settings):
    """build_bot_dependencies returns a BotDependencies with tg/wa handlers."""
    deps = build_bot_dependencies()
    assert isinstance(deps, BotDependencies)
    assert isinstance(deps.tg_handler, MessageHandler)
    assert isinstance(deps.wa_handler, MessageHandler)


# ---------------------------------------------------------------------------
# Test 2: get_bot_dependencies is a singleton
# ---------------------------------------------------------------------------


def test_get_bot_dependencies_singleton(_patch_bot_settings):
    """get_bot_dependencies returns the same instance on repeated calls."""
    deps1 = get_bot_dependencies()
    deps2 = get_bot_dependencies()
    assert deps1 is deps2


# ---------------------------------------------------------------------------
# Test 3: Telegram webhook calls handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_telegram_webhook_calls_handler():
    """_process_telegram should call deps.tg_handler.handle."""
    mock_deps, mock_session, mock_factory = _mock_deps_and_session()

    with patch(
        "app.bot.webhooks.dependencies.get_bot_dependencies",
        return_value=mock_deps,
    ), patch(
        "app.database.async_session_factory",
        mock_factory,
    ):
        from app.bot.webhooks.telegram import _process_telegram

        req = _make_bot_request("telegram")
        await _process_telegram(req)

    mock_deps.tg_handler.handle.assert_awaited_once_with(req, mock_session)
    # Dos commits: el del guardado del entrante y el del pipeline. El primero
    # es propio a proposito — el rollback del except no tiene que poder
    # deshacer el mensaje guardado (ver `persist_inbound`).
    assert mock_session.commit.await_count == 2
    mock_session.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test 4: WhatsApp webhook calls handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_whatsapp_webhook_calls_handler():
    """_process_whatsapp should call deps.wa_handler.handle."""
    mock_deps, mock_session, mock_factory = _mock_deps_and_session()

    with patch(
        "app.bot.webhooks.dependencies.get_bot_dependencies",
        return_value=mock_deps,
    ), patch(
        "app.database.async_session_factory",
        mock_factory,
    ):
        from app.bot.webhooks.whatsapp import _process_whatsapp

        req = _make_bot_request("whatsapp")
        await _process_whatsapp(req)

    mock_deps.wa_handler.handle.assert_awaited_once_with(req, mock_session)
    # Idem telegram: guardado + pipeline.
    assert mock_session.commit.await_count == 2
    mock_session.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test 5: Telegram process creates session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_telegram_process_creates_session():
    """_process_telegram should create a session from async_session_factory."""
    mock_deps, mock_session, mock_factory = _mock_deps_and_session()

    with patch(
        "app.bot.webhooks.dependencies.get_bot_dependencies",
        return_value=mock_deps,
    ), patch(
        "app.database.async_session_factory",
        mock_factory,
    ):
        from app.bot.webhooks.telegram import _process_telegram

        req = _make_bot_request("telegram")
        await _process_telegram(req)

    mock_factory.assert_called_once()


# ---------------------------------------------------------------------------
# Test 6: WhatsApp process creates session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_whatsapp_process_creates_session():
    """_process_whatsapp should create a session from async_session_factory."""
    mock_deps, mock_session, mock_factory = _mock_deps_and_session()

    with patch(
        "app.bot.webhooks.dependencies.get_bot_dependencies",
        return_value=mock_deps,
    ), patch(
        "app.database.async_session_factory",
        mock_factory,
    ):
        from app.bot.webhooks.whatsapp import _process_whatsapp

        req = _make_bot_request("whatsapp")
        await _process_whatsapp(req)

    mock_factory.assert_called_once()


# ---------------------------------------------------------------------------
# Test 7: Dependencies tg_handler has TelegramSender
# ---------------------------------------------------------------------------


def test_dependencies_handler_has_correct_sender(_patch_bot_settings):
    """tg_handler uses TelegramSender, wa_handler uses WhatsAppSender."""
    from app.bot.channels.telegram import TelegramSender
    from app.bot.channels.whatsapp import WhatsAppSender

    deps = build_bot_dependencies()

    # Access the private _sender attribute on MessageHandler
    assert isinstance(deps.tg_handler._sender, TelegramSender)
    assert isinstance(deps.wa_handler._sender, WhatsAppSender)


# ---------------------------------------------------------------------------
# Test 8: _process_telegram handles exception (rollback + close)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_telegram_handles_exception():
    """On handler exception, session should rollback and close."""
    mock_deps = MagicMock()
    mock_deps.tg_handler.handle = AsyncMock(side_effect=RuntimeError("boom"))

    mock_session = AsyncMock()
    # Provide a separate session for the error-recording block so its
    # calls (rollback/close) don't collide with the main session mock.
    err_session = AsyncMock()
    mock_factory = MagicMock(side_effect=[mock_session, err_session])

    with patch(
        "app.bot.webhooks.dependencies.get_bot_dependencies",
        return_value=mock_deps,
    ), patch(
        "app.database.async_session_factory",
        mock_factory,
    ):
        from app.bot.webhooks.telegram import _process_telegram

        req = _make_bot_request("telegram")
        # Should NOT raise — exception is caught internally
        await _process_telegram(req)

    mock_session.rollback.assert_awaited_once()
    mock_session.close.assert_awaited_once()
    # El unico commit es el del entrante, que va ANTES del pipeline: el
    # rollback del except no puede borrar el mensaje del cliente. El commit
    # del pipeline no llego a correr.
    assert mock_session.commit.await_count == 1


# ---------------------------------------------------------------------------
# Test 9: _process_whatsapp handles exception (rollback + close)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_whatsapp_handles_exception():
    """On handler exception, session should rollback and close."""
    mock_deps = MagicMock()
    mock_deps.wa_handler.handle = AsyncMock(side_effect=RuntimeError("boom"))

    mock_session = AsyncMock()
    # Provide a separate session for the error-recording block so its
    # calls (rollback/close) don't collide with the main session mock.
    err_session = AsyncMock()
    mock_factory = MagicMock(side_effect=[mock_session, err_session])

    with patch(
        "app.bot.webhooks.dependencies.get_bot_dependencies",
        return_value=mock_deps,
    ), patch(
        "app.database.async_session_factory",
        mock_factory,
    ):
        from app.bot.webhooks.whatsapp import _process_whatsapp

        req = _make_bot_request("whatsapp")
        # Should NOT raise — exception is caught internally
        await _process_whatsapp(req)

    mock_session.rollback.assert_awaited_once()
    mock_session.close.assert_awaited_once()
    # Idem telegram: el commit del entrante si, el del pipeline no.
    assert mock_session.commit.await_count == 1


# ---------------------------------------------------------------------------
# Test 10: webhook_router is registered in the FastAPI app
# ---------------------------------------------------------------------------


def test_webhook_router_registered_in_app():
    """The FastAPI app should have /webhook/telegram and /webhook/whatsapp routes."""
    from app.main import app

    route_paths = [route.path for route in app.routes]
    assert "/webhook/telegram" in route_paths, (
        f"/webhook/telegram not in app routes: {route_paths}"
    )
    assert "/webhook/whatsapp" in route_paths, (
        f"/webhook/whatsapp not in app routes: {route_paths}"
    )


# ---------------------------------------------------------------------------
# Test 11: Regression — SearchService receives bot_settings_repo (Fase H flag)
# ---------------------------------------------------------------------------


def test_build_bot_dependencies_wires_bot_settings_repo_into_search_service(
    _patch_bot_settings,
):
    """Regression: SearchService must receive bot_settings_repo for m5 flags to work in prod.

    Commit beb96c5 (Fase H) added m5_construction_state_filter_enabled flag read via
    SearchService._bot_settings_repo. If the repo is None the flag is inert and the
    column-based filter is never activated at runtime despite the DB backfill.
    """
    deps = build_bot_dependencies()

    # Navigate: BotDependencies → tg_handler → _orchestrator → _tool_executor → _search_service
    search_service = deps.tg_handler._orchestrator._tool_executor._search_service
    assert search_service._bot_settings_repo is not None, (
        "SearchService._bot_settings_repo is None — "
        "wire bot_settings_repo in build_bot_dependencies() so Fase H flag works at runtime"
    )
