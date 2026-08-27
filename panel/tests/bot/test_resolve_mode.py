"""Tests for Orchestrator._resolve_mode — the per-turn mode router.

Plan 123-02 (BOT-03/BOT-04 + D-2). Covers every branch of the priority chain:

  check 0 (D-2): platform != 'whatsapp'        -> 'busqueda' (always)
  check 1:       search_context['mode'] override
  check 2:       auto-detect (vista_publica handshake / source / infocasas_ref)
                 -> 'recepcionista'
  check 3:       bot_default_mode FRESH DB read (defensive default 'busqueda')

All dependencies mocked — no real DB or API calls.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.bot.core.orchestrator import Orchestrator
from app.bot.core.types import BotRequest, ContactInfo, ConversationState


def _make_orchestrator() -> Orchestrator:
    return Orchestrator(
        claude=AsyncMock(),
        gemini=AsyncMock(),
        circuit_breaker=AsyncMock(),
        search_service=AsyncMock(),
        conversation_manager=AsyncMock(),
        response_builder=AsyncMock(),
        tool_executor=AsyncMock(),
    )


def _make_request(platform: str = "whatsapp", text: str = "hola") -> BotRequest:
    return BotRequest(
        platform=platform,
        chat_id="123",
        user_id="595981000000",
        user_name="Tester",
        text=text,
    )


def _make_contact(
    source: str | None = None,
    infocasas_ref: str | None = None,
) -> ContactInfo:
    return ContactInfo(
        id=1,
        name="Tester",
        status="new",
        platform="whatsapp",
        source=source,
        infocasas_ref=infocasas_ref,
    )


@pytest.mark.asyncio
async def test_resolve_mode_channel_gate_tg():
    """D-2 check 0: telegram always resolves to busqueda, ignoring all signals."""
    orch = _make_orchestrator()
    request = _make_request(platform="telegram")
    contact = _make_contact(source="vista_publica", infocasas_ref="IC-999")
    ctx = ConversationState()
    ctx.set_mode_override("recepcionista")
    session = AsyncMock()

    with patch(
        "app.bot.core.orchestrator.bot_setting_repo.get_value",
        new=AsyncMock(return_value="recepcionista"),
    ):
        mode = await orch._resolve_mode(request, contact, ctx, session)

    assert mode == "busqueda"


@pytest.mark.asyncio
async def test_resolve_mode_override_wins():
    """check 1 > check 2: explicit override beats auto-detect signals."""
    orch = _make_orchestrator()
    request = _make_request(platform="whatsapp")
    # auto-detect would say recepcionista, but override says busqueda
    contact = _make_contact(source="vista_publica", infocasas_ref="IC-1")
    ctx = ConversationState()
    ctx.set_mode_override("busqueda")
    session = AsyncMock()

    with patch(
        "app.bot.core.orchestrator.bot_setting_repo.get_value",
        new=AsyncMock(return_value="recepcionista"),
    ):
        mode = await orch._resolve_mode(request, contact, ctx, session)

    assert mode == "busqueda"


@pytest.mark.asyncio
async def test_resolve_mode_autodetect_vista_publica():
    """check 2b: contact.source == 'vista_publica' -> recepcionista (default busqueda)."""
    orch = _make_orchestrator()
    request = _make_request(platform="whatsapp")
    contact = _make_contact(source="vista_publica")
    ctx = ConversationState()  # no override
    session = AsyncMock()

    with patch(
        "app.bot.core.orchestrator.bot_setting_repo.get_value",
        new=AsyncMock(return_value="busqueda"),
    ):
        mode = await orch._resolve_mode(request, contact, ctx, session)

    assert mode == "recepcionista"


@pytest.mark.asyncio
async def test_resolve_mode_autodetect_infocasas_ref():
    """check 2c: contact.infocasas_ref present -> recepcionista."""
    orch = _make_orchestrator()
    request = _make_request(platform="whatsapp")
    contact = _make_contact(infocasas_ref="IC-12345")
    ctx = ConversationState()
    session = AsyncMock()

    with patch(
        "app.bot.core.orchestrator.bot_setting_repo.get_value",
        new=AsyncMock(return_value="busqueda"),
    ):
        mode = await orch._resolve_mode(request, contact, ctx, session)

    assert mode == "recepcionista"


@pytest.mark.asyncio
async def test_resolve_mode_default_fresh_read():
    """check 3: no override/auto-detect -> fresh bot_default_mode read.

    Also asserts defensive fallback: unexpected/missing value -> busqueda.
    """
    orch = _make_orchestrator()
    request = _make_request(platform="whatsapp")
    contact = _make_contact()  # no source, no infocasas_ref
    ctx = ConversationState()
    session = AsyncMock()

    # DB says recepcionista -> returns recepcionista (fresh read)
    get_value = AsyncMock(return_value="recepcionista")
    with patch(
        "app.bot.core.orchestrator.bot_setting_repo.get_value", new=get_value,
    ):
        mode = await orch._resolve_mode(request, contact, ctx, session)
    assert mode == "recepcionista"
    get_value.assert_awaited_once_with(session, "bot_default_mode")

    # DB says busqueda -> busqueda
    with patch(
        "app.bot.core.orchestrator.bot_setting_repo.get_value",
        new=AsyncMock(return_value="busqueda"),
    ):
        assert await orch._resolve_mode(request, contact, ctx, session) == "busqueda"

    # Missing / unexpected value -> defensive busqueda
    with patch(
        "app.bot.core.orchestrator.bot_setting_repo.get_value",
        new=AsyncMock(return_value=None),
    ):
        assert await orch._resolve_mode(request, contact, ctx, session) == "busqueda"
    with patch(
        "app.bot.core.orchestrator.bot_setting_repo.get_value",
        new=AsyncMock(return_value="garbage"),
    ):
        assert await orch._resolve_mode(request, contact, ctx, session) == "busqueda"


def test_conversation_state_mode_override_helpers():
    """ConversationState get/set_mode_override read/write the 'mode' key, and it
    survives the JSONB round-trip (from_jsonb <-> to_jsonb)."""
    ctx = ConversationState()
    assert ctx.get_mode_override() is None

    ctx.set_mode_override("recepcionista")
    assert ctx.get_mode_override() == "recepcionista"

    # survives serialization round-trip
    data = ctx.to_jsonb()
    assert data["mode"] == "recepcionista"
    restored = ConversationState.from_jsonb(data)
    assert restored.get_mode_override() == "recepcionista"
