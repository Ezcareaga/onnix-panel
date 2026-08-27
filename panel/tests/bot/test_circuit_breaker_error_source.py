"""Tests para la diferenciación API vs tool errors en el circuit breaker.

Invariante: solo errores del SDK de Anthropic (APIConnectionError,
APIStatusError, RateLimitError, AuthenticationError, BadRequestError)
deben registrar fallo en el circuit breaker y caer a Gemini. Errores
de DB / validación / bugs de código deben propagar al error handler
externo sin tocar al breaker.

Ver docs/AUDIT_M4_FASE0_20260419.md §4 y PLAN_M4_REFACTOR.md Task 2.2.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anthropic import APIConnectionError
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.bot.core.types import (
    BotRequest, ConversationState,
)

# Reuse fixtures already defined in test_orchestrator.py.
# These helpers live there for historical reasons — migrarán a conftest
# en Task 3.14 del PLAN_M4_REFACTOR.md.
from tests.bot.test_orchestrator import (  # noqa: E402
    _default_contact,
    _default_conversation,
    _default_request,
    _make_orchestrator,
    _setup_normal_flow,
    _text_ai_response,
)


# ---------------------------------------------------------------------------
# Tool / DB errors — should NOT trip breaker, should propagate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_operational_error_in_tool_does_not_trip_breaker():
    """DB connection drop durante tool execution propaga, no registra fallo."""
    orch, mocks = _make_orchestrator()
    _setup_normal_flow(mocks)
    mocks["circuit_breaker"].failure_count = 0

    with patch(
        "app.bot.ai.ai_dispatch.run_tool_use_loop",
        side_effect=OperationalError("stmt", {}, Exception("server gone")),
    ):
        session = AsyncMock()
        with pytest.raises(OperationalError):
            await orch.handle_message(_default_request(), session)

    mocks["circuit_breaker"].record_failure.assert_not_called()


@pytest.mark.asyncio
async def test_programming_error_in_tool_does_not_trip_breaker():
    """SQL syntax bug en nuestro código no debe disparar fallback a Gemini."""
    orch, mocks = _make_orchestrator()
    _setup_normal_flow(mocks)

    with patch(
        "app.bot.ai.ai_dispatch.run_tool_use_loop",
        side_effect=ProgrammingError("stmt", {}, Exception("syntax")),
    ):
        session = AsyncMock()
        with pytest.raises(ProgrammingError):
            await orch.handle_message(_default_request(), session)

    mocks["circuit_breaker"].record_failure.assert_not_called()
    mocks["gemini"].send_message.assert_not_called()


@pytest.mark.asyncio
async def test_value_error_in_tool_does_not_trip_breaker():
    """ValueError (e.g. _resolve_referencia fail) propaga sin fallback."""
    orch, mocks = _make_orchestrator()
    _setup_normal_flow(mocks)

    with patch(
        "app.bot.ai.ai_dispatch.run_tool_use_loop",
        side_effect=ValueError("bad ordinal"),
    ):
        session = AsyncMock()
        with pytest.raises(ValueError):
            await orch.handle_message(_default_request(), session)

    mocks["circuit_breaker"].record_failure.assert_not_called()


# ---------------------------------------------------------------------------
# Anthropic API errors — SHOULD trip breaker + fallback a Gemini
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_connection_error_trips_breaker_and_falls_back_to_gemini():
    """APIConnectionError triggers breaker fail + Gemini fallback."""
    orch, mocks = _make_orchestrator()
    _setup_normal_flow(mocks)
    mocks["gemini"].send_message.return_value = _text_ai_response(
        "Respuesta Gemini fallback"
    )

    with patch(
        "app.bot.ai.ai_dispatch.run_tool_use_loop",
        side_effect=APIConnectionError(request=MagicMock()),
    ):
        result = await orch.handle_message(_default_request(), AsyncMock())

    mocks["circuit_breaker"].record_failure.assert_called_once()
    mocks["gemini"].send_message.assert_awaited_once()
    assert result is not None
