"""Tests for ALT:<id> dispatch in try_shortcut_dispatch (Fase F).

Covers:
5. Dispatcher matches ALT callback BEFORE _RESET_SEARCH_CALLBACKS.
6. Non-ALT callback doesn't match ALT path.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_panel_dir = str(Path(__file__).resolve().parent.parent)
if _panel_dir not in sys.path:
    sys.path.insert(0, _panel_dir)

from app.bot.core.types import (
    BotRequest, BotResponse, ConversationState,
)
from app.bot.handlers.dispatcher import try_shortcut_dispatch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(
    callback_data: str | None = None,
    text: str | None = None,
) -> BotRequest:
    return BotRequest(
        platform="whatsapp",
        chat_id="+595981000001",
        user_id="+595981000001",
        user_name="Test User",
        callback_data=callback_data,
        text=text,
    )


def _make_contact() -> MagicMock:
    c = MagicMock()
    c.id = 1
    c.status = "bot_replied"
    return c


def _make_conversation() -> MagicMock:
    c = MagicMock()
    c.id = 42
    return c


# ---------------------------------------------------------------------------
# Test 5: dispatcher matches ALT callback before reset-search
# ---------------------------------------------------------------------------

class TestDispatcherMatchesAlt:
    @pytest.mark.asyncio
    async def test_dispatcher_matches_alt_callback_before_reset(self):
        """ALT:foo callback → handle_alternative_callback called, not reset-search."""
        state = ConversationState()
        state.pending_alternatives = [
            {
                "id": "foo",
                "label": "En Lambare",
                "count": 5,
                "filters": {"ciudad": "lambare"},
                "reason": "zona vecina",
                "callback_payload": "ALT:foo",
            }
        ]

        request = _make_request(callback_data="ALT:foo")
        contact = _make_contact()
        conversation = _make_conversation()

        session = AsyncMock()
        session.execute = AsyncMock()

        fake_alt_response = BotResponse(
            text="Para empezar, ¿buscás para comprar o alquilar?",
            intent="alternativa_elegida:foo",
        )
        fake_reset_response = BotResponse(
            text="Para empezar, ¿buscás para comprar o alquilar?",
            intent="busqueda_incompleta",
        )

        with patch(
            "app.bot.handlers.dispatcher.handle_alternative_callback",
            new_callable=AsyncMock,
            return_value=fake_alt_response,
        ) as mock_alt, patch(
            "app.bot.handlers.dispatcher.handle_new_search",
            new_callable=AsyncMock,
        ) as mock_reset:
            # build a fake HandlerResult for reset path (shouldn't be called)
            from app.bot.handlers._types import HandlerResult
            mock_reset.return_value = HandlerResult(
                response=fake_reset_response, search_context=state
            )

            cm = MagicMock()
            cm.update_search_context = AsyncMock()

            result = await try_shortcut_dispatch(
                request, session, contact, conversation, state,
                search_service=AsyncMock(),
                conversation_manager=cm,
            )

        assert mock_alt.called, "handle_alternative_callback should have been called"
        assert mock_reset.call_count == 0, "handle_new_search (reset path) should NOT be called for ALT callback"
        assert result is not None
        assert result.intent == "alternativa_elegida:foo"


# ---------------------------------------------------------------------------
# Test 6: non-ALT callback doesn't match ALT path
# ---------------------------------------------------------------------------

class TestDispatcherDoesNotMatchNonAlt:
    @pytest.mark.asyncio
    async def test_dispatcher_does_not_match_non_alt_callback(self):
        """seguir_buscando callback → reset-search path, not ALT handler."""
        state = ConversationState()
        state.pending_alternatives = []

        request = _make_request(callback_data="seguir_buscando")
        contact = _make_contact()
        conversation = _make_conversation()

        session = AsyncMock()
        session.execute = AsyncMock()

        fake_reset_response = BotResponse(
            text="Para empezar, ¿buscás para comprar o alquilar?",
            intent="busqueda_incompleta",
        )

        with patch(
            "app.bot.handlers.dispatcher.handle_alternative_callback",
            new_callable=AsyncMock,
        ) as mock_alt, patch(
            "app.bot.handlers.dispatcher.handle_new_search",
            new_callable=AsyncMock,
        ) as mock_reset:
            from app.bot.handlers._types import HandlerResult
            mock_reset.return_value = HandlerResult(
                response=fake_reset_response, search_context=state
            )

            cm = MagicMock()
            cm.update_search_context = AsyncMock()

            result = await try_shortcut_dispatch(
                request, session, contact, conversation, state,
                search_service=AsyncMock(),
                conversation_manager=cm,
            )

        assert not mock_alt.called, "ALT handler should NOT be called for non-ALT callback"
        assert mock_reset.called, "handle_new_search (reset path) should have been called"
        assert result is not None
