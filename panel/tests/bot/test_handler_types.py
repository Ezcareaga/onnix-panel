"""Tests for HandlerResult contract (M4 Task 3.1)."""
from __future__ import annotations

from app.bot.core.types import BotResponse, ConversationState
from app.bot.handlers._types import HandlerResult


def test_handler_result_defaults():
    """HandlerResult sin events_to_record usa lista vacía."""
    ctx = ConversationState()
    r = HandlerResult(response=None, search_context=ctx)
    assert r.response is None
    assert r.search_context is ctx
    assert r.events_to_record == []


def test_handler_result_with_response():
    """HandlerResult acepta un BotResponse real."""
    ctx = ConversationState()
    resp = BotResponse(text="hola", intent="saludo")
    r = HandlerResult(response=resp, search_context=ctx)
    assert r.response is resp
    assert r.response.intent == "saludo"


def test_handler_result_with_events():
    """HandlerResult acepta lista de eventos para persistir."""
    events = [
        {"event_type": "search", "metadata": {"filters": {"ciudad": "asuncion"}}},
        {"event_type": "detail_view", "metadata": {"property_id": 42}},
    ]
    r = HandlerResult(response=None, search_context=ConversationState(), events_to_record=events)
    assert len(r.events_to_record) == 2
    assert r.events_to_record[0]["event_type"] == "search"


def test_handler_result_search_context_passes_through():
    """search_context no se muta — el handler devuelve el que quiere persistir."""
    original = ConversationState(filtros={"ciudad": "luque"})
    new = ConversationState(filtros={"ciudad": "luque", "tipo": "casa"})
    r = HandlerResult(response=None, search_context=new)
    assert r.search_context is new
    assert r.search_context is not original
    assert "tipo" in r.search_context.filtros


def test_handler_result_events_default_factory_is_independent():
    """Cada instancia obtiene su propia lista de eventos (no shared mutable state)."""
    r1 = HandlerResult(response=None, search_context=ConversationState())
    r2 = HandlerResult(response=None, search_context=ConversationState())
    r1.events_to_record.append({"event_type": "test"})
    assert r2.events_to_record == []
