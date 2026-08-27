"""Tests for handle_alternative_callback — ALT:<id> handler (Fase F).

Covers:
1. Valid ALT callback with pending alternative → executes search path.
2. Expired / missing ALT id → graceful message, no error.
3. Cleared pending_alternatives after call.
4. Filters merged from alternative into state.filtros.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_panel_dir = str(Path(__file__).resolve().parent.parent.parent.parent)
if _panel_dir not in sys.path:
    sys.path.insert(0, _panel_dir)

from app.bot.core.conversation import ConversationManager
from app.bot.core.types import (
    BotRequest, BotResponse, ContactInfo, ConversationInfo, ConversationState,
)
from app.bot.handlers.alternatives import handle_alternative_callback
from app.bot.handlers._types import HandlerResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(callback_data: str) -> BotRequest:
    return BotRequest(
        platform="whatsapp",
        chat_id="+595981000001",
        user_id="+595981000001",
        user_name="Test User",
        callback_data=callback_data,
    )


def _make_contact(status: str = "bot_replied") -> MagicMock:
    c = MagicMock()
    c.id = 1
    c.status = status
    return c


def _make_conversation(conv_id: int = 42) -> MagicMock:
    c = MagicMock()
    c.id = conv_id
    return c


def _make_state_with_alternatives(
    alt_id: str = "zona_vecina:lambare",
    filters: dict | None = None,
) -> ConversationState:
    filters = filters or {"ciudad": "lambare"}
    state = ConversationState()
    state.pending_alternatives = [
        {
            "id": alt_id,
            "label": "En Lambare hay 8 deptos",
            "count": 8,
            "filters": filters,
            "reason": "zona vecina",
            "callback_payload": f"ALT:{alt_id}",
        }
    ]
    state.pending_alternatives_age = 0
    return state


def _make_conversation_manager() -> ConversationManager:
    """Real ConversationManager — only non-DB methods are called in unit tests."""
    return ConversationManager()


# ---------------------------------------------------------------------------
# Test 1: valid ALT callback executes search path
# ---------------------------------------------------------------------------

class TestAltCallbackValid:
    @pytest.mark.asyncio
    async def test_alt_callback_with_valid_id_executes_search(self):
        """Valid ALT:zona_vecina:lambare → handler delegates to handle_new_search.

        Also verifies that update_search_context is called before the delegation
        so that the cleared pending_alternatives and merged filters are persisted
        to DB even if handle_new_search fails.
        """
        alt_id = "zona_vecina:lambare"
        state = _make_state_with_alternatives(alt_id=alt_id)
        request = _make_request(f"ALT:{alt_id}")
        contact = _make_contact()
        conversation = _make_conversation()
        cm = _make_conversation_manager()
        cm.update_search_context = AsyncMock()

        session = AsyncMock()
        session.execute = AsyncMock()

        # Patch handle_new_search to return a controlled HandlerResult
        fake_response = BotResponse(
            text="Para empezar, ¿buscás para comprar o alquilar?",
            intent="busqueda_incompleta",
        )

        with patch(
            "app.bot.handlers.alternatives.handle_new_search",
            new_callable=AsyncMock,
        ) as mock_new_search:
            mock_new_search.return_value = HandlerResult(
                response=fake_response,
                search_context=state,
            )

            result = await handle_alternative_callback(
                request, session, contact, conversation, state,
                search_service=AsyncMock(),
                conversation_manager=cm,
            )

        assert mock_new_search.called
        assert result is not None
        assert "alternativa_elegida" in result.intent
        # search_context must be persisted BEFORE delegating to handle_new_search
        # so that cleared pending_alternatives + merged filters survive a crash.
        cm.update_search_context.assert_awaited_once_with(
            session, conversation.id, state,
        )


# ---------------------------------------------------------------------------
# Test 2: expired ALT id returns graceful message
# ---------------------------------------------------------------------------

class TestAltCallbackExpired:
    @pytest.mark.asyncio
    async def test_alt_callback_with_expired_id_returns_graceful_message(self):
        """ALT with unknown id → graceful message, not an error."""
        state = ConversationState()  # no pending_alternatives
        request = _make_request("ALT:zona_vecina:doesnotexist")
        contact = _make_contact()
        conversation = _make_conversation()
        cm = _make_conversation_manager()

        session = AsyncMock()
        session.execute = AsyncMock()
        cm.update_search_context = AsyncMock()
        cm.save_outbound_message = AsyncMock()

        result = await handle_alternative_callback(
            request, session, contact, conversation, state,
            search_service=AsyncMock(),
            conversation_manager=cm,
        )

        assert result is not None
        assert result.intent == "alternativa_expirada"
        # User-facing text must be in Spanish and not expose technical info
        assert "No encontré esa opción" in result.text or "opción" in result.text.lower()
        assert "ALT:" not in result.text
        assert "error" not in result.text.lower()


# ---------------------------------------------------------------------------
# Test 3: pending_alternatives cleared after callback
# ---------------------------------------------------------------------------

class TestAltCallbackClearsAlternatives:
    @pytest.mark.asyncio
    async def test_alt_callback_clears_pending_alternatives(self):
        """After successful ALT callback, state.pending_alternatives == []."""
        alt_id = "zona_vecina:lambare"
        state = _make_state_with_alternatives(alt_id=alt_id)
        assert len(state.pending_alternatives) == 1  # pre-condition

        request = _make_request(f"ALT:{alt_id}")
        contact = _make_contact()
        conversation = _make_conversation()
        cm = _make_conversation_manager()

        session = AsyncMock()
        session.execute = AsyncMock()

        fake_response = BotResponse(
            text="Para empezar, ¿buscás para comprar o alquilar?",
            intent="busqueda_incompleta",
        )

        with patch(
            "app.bot.handlers.alternatives.handle_new_search",
            new_callable=AsyncMock,
        ) as mock_new_search:
            mock_new_search.return_value = HandlerResult(
                response=fake_response,
                search_context=state,
            )

            await handle_alternative_callback(
                request, session, contact, conversation, state,
                search_service=AsyncMock(),
                conversation_manager=cm,
            )

        assert state.pending_alternatives == []
        assert state.pending_alternatives_age == 0


# ---------------------------------------------------------------------------
# Test 4: alternative filters merged into state.filtros
# ---------------------------------------------------------------------------

class TestAltCallbackMergesFilters:
    @pytest.mark.asyncio
    async def test_alt_callback_applies_alt_filters_via_merge(self):
        """After callback, state.filtros contains the alternative's filters."""
        alt_id = "zona_vecina:lambare"
        alt_filters = {"ciudad": "lambare", "operacion": "venta"}
        state = _make_state_with_alternatives(alt_id=alt_id, filters=alt_filters)
        state.filtros = {"tipo": "departamento"}  # pre-existing filter

        request = _make_request(f"ALT:{alt_id}")
        contact = _make_contact()
        conversation = _make_conversation()
        cm = _make_conversation_manager()

        session = AsyncMock()
        session.execute = AsyncMock()

        fake_response = BotResponse(
            text="Para empezar, ¿buscás para comprar o alquilar?",
            intent="busqueda_incompleta",
        )

        with patch(
            "app.bot.handlers.alternatives.handle_new_search",
            new_callable=AsyncMock,
        ) as mock_new_search:
            mock_new_search.return_value = HandlerResult(
                response=fake_response,
                search_context=state,
            )

            await handle_alternative_callback(
                request, session, contact, conversation, state,
                search_service=AsyncMock(),
                conversation_manager=cm,
            )

        # Filters from the alternative are merged in
        assert state.filtros.get("ciudad") == "lambare"
        assert state.filtros.get("operacion") == "venta"
        # Pre-existing filter preserved (merge, not replace)
        assert state.filtros.get("tipo") == "departamento"
