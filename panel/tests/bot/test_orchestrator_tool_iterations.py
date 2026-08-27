"""Tests — orchestrator propagates tool_iterations and fallback_used.

TDD Fase C.2:
- _call_claude_with_tools returns (response, ..., iterations) tuple
- handle_message calls save_outbound_message with tool_iterations
- bot_response.metadata carries tool_iterations, fallback_used, llm_provider
- Gemini fallback sets fallback_used=True, tool_iterations=0, llm_provider="gemini"
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from anthropic import APIConnectionError

import pytest

from app.bot.ai.types import AIResponse, ToolCall
from app.bot.core.orchestrator import Orchestrator
from app.bot.core.types import (
    BotRequest,
    ContactInfo,
    ConversationInfo,
    ConversationState,
)


# ---------------------------------------------------------------------------
# Helpers (mirrors test_orchestrator.py conventions)
# ---------------------------------------------------------------------------


def _make_orchestrator():
    claude = AsyncMock()
    gemini = AsyncMock()
    circuit_breaker = MagicMock()
    circuit_breaker.is_open = False
    search_service = AsyncMock()
    conversation_manager = AsyncMock()
    conversation_manager.check_human_cooldown = MagicMock(return_value=False)
    response_builder = MagicMock()
    tool_executor = AsyncMock()
    tool_executor.build_tool_result_message = MagicMock(return_value={
        "type": "tool_result", "tool_use_id": "t1", "content": "{}",
    })

    orch = Orchestrator(
        claude=claude,
        gemini=gemini,
        circuit_breaker=circuit_breaker,
        search_service=search_service,
        conversation_manager=conversation_manager,
        response_builder=response_builder,
        tool_executor=tool_executor,
    )
    return orch, {
        "claude": claude,
        "gemini": gemini,
        "circuit_breaker": circuit_breaker,
        "conversation_manager": conversation_manager,
        "tool_executor": tool_executor,
    }


def _contact():
    return ContactInfo(id=1, name="Test", status="new", platform="telegram")


def _conversation():
    return ConversationInfo(id=10, contact_id=1, platform="telegram", chat_id="123")


def _request():
    return BotRequest(
        platform="telegram", chat_id="123", user_id="123",
        user_name="Test", text="Busco casa", external_id="msg_001",
    )


def _text_response(text="Hola"):
    return AIResponse(
        text=text, tool_calls=[], model="claude-haiku",
        input_tokens=50, output_tokens=20,
        stop_reason="end_turn", raw_content=[],
    )


def _tool_response(tool_name="search_properties"):
    return AIResponse(
        text=None,
        tool_calls=[ToolCall(id="t1", name=tool_name, input={"ciudad": "asuncion"})],
        model="claude-haiku",
        input_tokens=100, output_tokens=30,
        stop_reason="tool_use",
        raw_content=[{"type": "tool_use", "id": "t1", "name": tool_name, "input": {}}],
    )


def _setup(mocks):
    mocks["conversation_manager"].resolve_contact.return_value = _contact()
    mocks["conversation_manager"].get_or_create_conversation.return_value = _conversation()
    mocks["conversation_manager"].check_human_cooldown.return_value = False
    mocks["conversation_manager"].get_history.return_value = []
    mocks["conversation_manager"].get_search_context.return_value = ConversationState()
    mocks["conversation_manager"].save_outbound_message = AsyncMock(return_value=99)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_tools_gives_zero_iterations():
    """Claude responds without tool_use → tool_iterations=0 in BotResponse.metadata."""
    orch, mocks = _make_orchestrator()
    _setup(mocks)

    mocks["claude"].send_message.return_value = _text_response()
    mocks["circuit_breaker"].record_success = MagicMock()

    session = AsyncMock()
    result = await orch.handle_message(_request(), session)

    assert result is not None
    assert result.metadata["tool_iterations"] == 0
    assert result.metadata["fallback_used"] is False
    assert result.metadata["llm_provider"] == "claude"


@pytest.mark.asyncio
async def test_claude_iterations_propagated():
    """3 tool-use loops → tool_iterations=3 in metadata and save_outbound_message."""
    orch, mocks = _make_orchestrator()
    _setup(mocks)

    # First 3 calls return tool_use, final call returns text
    tool_resp = _tool_response()
    text_resp = _text_response("Encontré propiedades")

    call_count = 0

    async def _side_effect(**_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            return tool_resp
        return text_resp

    mocks["claude"].send_message.side_effect = _side_effect
    mocks["circuit_breaker"].record_success = MagicMock()
    # Tool executor returns empty result for each iteration
    mocks["tool_executor"].execute.return_value = {}

    session = AsyncMock()
    result = await orch.handle_message(_request(), session)

    assert result is not None
    assert result.metadata["tool_iterations"] == 3
    assert result.metadata["fallback_used"] is False
    assert result.metadata["llm_provider"] == "claude"

    # save_outbound_message must be called with tool_iterations=3
    save_call = mocks["conversation_manager"].save_outbound_message.call_args
    assert save_call.kwargs.get("tool_iterations") == 3


@pytest.mark.asyncio
async def test_gemini_fallback_sets_fallback_used_true_and_iterations_zero():
    """Claude raises → Gemini fallback → fallback_used=True, tool_iterations=0, llm_provider='gemini'."""
    orch, mocks = _make_orchestrator()
    _setup(mocks)

    # Use APIConnectionError (real Anthropic SDK exception) para disparar
    # fallback a Gemini. Post M4 Task 2.2, RuntimeError propaga sin fallback.
    mocks["claude"].send_message.side_effect = APIConnectionError(request=MagicMock())
    mocks["circuit_breaker"].record_success = MagicMock()
    mocks["circuit_breaker"].record_failure = MagicMock()
    mocks["gemini"].send_message.return_value = _text_response("Lo siento, intente más tarde")

    session = AsyncMock()
    result = await orch.handle_message(_request(), session)

    assert result is not None
    assert result.metadata["fallback_used"] is True
    assert result.metadata["tool_iterations"] == 0
    assert result.metadata["llm_provider"] == "gemini"

    save_call = mocks["conversation_manager"].save_outbound_message.call_args
    assert save_call.kwargs.get("tool_iterations") == 0


@pytest.mark.asyncio
async def test_circuit_breaker_open_sets_fallback():
    """Circuit breaker open → Gemini used, fallback_used=True, tool_iterations=0."""
    orch, mocks = _make_orchestrator()
    _setup(mocks)

    mocks["circuit_breaker"].is_open = True
    mocks["circuit_breaker"].state = MagicMock()
    mocks["circuit_breaker"].state.value = "open"
    mocks["gemini"].send_message.return_value = _text_response("Gemini response")

    session = AsyncMock()
    result = await orch.handle_message(_request(), session)

    assert result is not None
    assert result.metadata["fallback_used"] is True
    assert result.metadata["tool_iterations"] == 0
    assert result.metadata["llm_provider"] == "gemini"
