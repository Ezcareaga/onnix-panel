"""Tests — save_outbound_message persists tool_iterations.

TDD Fase C.1: verify the new tool_iterations param flows to the SQL INSERT.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app.bot.core.conversation import ConversationManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(row_id: int = 42):
    """Return an AsyncMock mimicking AsyncSession with a RETURNING row."""
    session = AsyncMock()
    row = MagicMock()
    row.id = row_id
    result = MagicMock()
    result.first.return_value = row
    session.execute = AsyncMock(return_value=result)
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_outbound_persists_tool_iterations():
    """tool_iterations=3 must appear in the SQL params passed to session.execute."""
    session = _make_session()
    mgr = ConversationManager()

    await mgr.save_outbound_message(
        session,
        conversation_id=1,
        contact_id=2,
        body="Hola",
        intent="saludo",
        tool_iterations=3,
    )

    # Extract the params dict from the call
    assert session.execute.called
    _, call_kwargs = session.execute.call_args
    # SQLAlchemy execute: execute(sql, params) — positional
    args = session.execute.call_args.args
    # args[0] = TextClause, args[1] = params dict
    params = args[1]
    assert params["tool_iterations"] == 3


@pytest.mark.asyncio
async def test_save_outbound_defaults_to_none():
    """When tool_iterations is not provided it must default to None in params."""
    session = _make_session()
    mgr = ConversationManager()

    await mgr.save_outbound_message(
        session,
        conversation_id=1,
        contact_id=2,
        body="Hola",
        intent="saludo",
    )

    args = session.execute.call_args.args
    params = args[1]
    assert params["tool_iterations"] is None


@pytest.mark.asyncio
async def test_save_outbound_zero_iterations():
    """tool_iterations=0 (Gemini fallback or no tools used) is stored as 0, not None."""
    session = _make_session()
    mgr = ConversationManager()

    await mgr.save_outbound_message(
        session,
        conversation_id=1,
        contact_id=2,
        body="Respuesta de Gemini",
        intent="conversacion",
        tool_iterations=0,
    )

    args = session.execute.call_args.args
    params = args[1]
    assert params["tool_iterations"] == 0
