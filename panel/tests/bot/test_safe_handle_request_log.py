"""Tests — safe_handle emits request.complete log.

TDD Fase C.5: verify the structured log is emitted on ok/skipped/error paths,
with correct status, skip_reason, error fields, and non-zero processing_ms.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.bot.middleware.error_handler import safe_handle
from app.bot.observability.outcome import RequestOutcome


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _ok_coro():
    return RequestOutcome(
        status="ok",
        intent="saludo",
        llm_provider="claude",
        tool_iterations=0,
    )


async def _skipped_duplicate_coro():
    return RequestOutcome(status="skipped", skip_reason="duplicate")


async def _error_coro():
    raise ValueError("Something blew up with a very long message " + "x" * 600)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_handle_emits_request_complete_log_on_success(caplog):
    """Success path emits one 'request.complete' record with status='ok'."""
    with caplog.at_level(logging.INFO, logger="bot.request"):
        outcome = await safe_handle(_ok_coro())

    assert outcome is not None
    assert outcome.status == "ok"

    records = [r for r in caplog.records if r.name == "bot.request"]
    assert len(records) == 1
    rec = records[0]
    assert rec.getMessage() == "request.complete"
    assert rec.__dict__["status"] == "ok"
    assert rec.__dict__["intent"] == "saludo"
    assert rec.__dict__["llm_provider"] == "claude"


@pytest.mark.asyncio
async def test_safe_handle_emits_log_on_skip_duplicate(caplog):
    """Skipped-duplicate path emits record with status='skipped', skip_reason='duplicate'."""
    with caplog.at_level(logging.INFO, logger="bot.request"):
        outcome = await safe_handle(_skipped_duplicate_coro())

    assert outcome is not None
    assert outcome.status == "skipped"

    records = [r for r in caplog.records if r.name == "bot.request"]
    assert len(records) == 1
    rec = records[0]
    assert rec.__dict__["status"] == "skipped"
    assert rec.__dict__["skip_reason"] == "duplicate"


@pytest.mark.asyncio
async def test_safe_handle_emits_log_on_exception(caplog):
    """Exception path emits record with status='error', error_type and truncated error_message."""
    with caplog.at_level(logging.INFO, logger="bot.request"):
        outcome = await safe_handle(_error_coro())

    assert outcome is not None
    assert outcome.status == "error"

    records = [r for r in caplog.records if r.name == "bot.request"]
    assert len(records) == 1
    rec = records[0]
    assert rec.__dict__["status"] == "error"
    assert rec.__dict__["error_type"] == "ValueError"
    # error_message must be <= 500 chars
    assert len(rec.__dict__["error_message"]) <= 500


@pytest.mark.asyncio
async def test_safe_handle_processing_ms_is_reasonable(caplog):
    """processing_ms is > 0 (measured, not zero)."""
    async def _slow_coro():
        await asyncio.sleep(0.01)  # 10ms
        return RequestOutcome(status="ok")

    with caplog.at_level(logging.INFO, logger="bot.request"):
        await safe_handle(_slow_coro())

    records = [r for r in caplog.records if r.name == "bot.request"]
    assert len(records) == 1
    assert records[0].__dict__["processing_ms"] >= 1  # sleep(0.01) guarantees >=1 ms


@pytest.mark.asyncio
async def test_safe_handle_exception_returns_error_outcome():
    """safe_handle does NOT re-raise; it returns a RequestOutcome with status='error'."""
    outcome = await safe_handle(_error_coro())

    assert outcome is not None
    assert outcome.status == "error"
    assert outcome.error_type == "ValueError"


@pytest.mark.asyncio
async def test_safe_handle_error_message_truncated():
    """error_message is always <= 500 chars even for very long exception messages."""
    long_msg = "A" * 1000

    async def _long_error():
        raise RuntimeError(long_msg)

    outcome = await safe_handle(_long_error())

    assert outcome is not None
    assert len(outcome.error_message) <= 500


@pytest.mark.asyncio
async def test_request_complete_log_has_contact_id(caplog):
    """request.complete log carries contact_id when set in RequestOutcome."""

    async def _coro_with_contact():
        return RequestOutcome(status="ok", intent="saludo", contact_id=42)

    with caplog.at_level(logging.INFO, logger="bot.request"):
        await safe_handle(_coro_with_contact())

    records = [r for r in caplog.records if r.name == "bot.request"]
    assert len(records) == 1
    assert records[0].__dict__["contact_id"] == 42


@pytest.mark.asyncio
async def test_request_complete_log_has_ai_latency_ms_when_claude_responds(caplog):
    """request.complete log carries ai_latency_ms=123 when set in RequestOutcome."""

    async def _coro_with_latency():
        return RequestOutcome(status="ok", intent="busqueda", ai_latency_ms=123)

    with caplog.at_level(logging.INFO, logger="bot.request"):
        await safe_handle(_coro_with_latency())

    records = [r for r in caplog.records if r.name == "bot.request"]
    assert len(records) == 1
    assert records[0].__dict__["ai_latency_ms"] == 123
