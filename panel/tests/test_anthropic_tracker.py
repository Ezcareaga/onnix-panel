"""Tests for app/bot/observability/anthropic_tracker.py

TDD: tests written before implementation is verified end-to-end.
Covers:
  - Success path: row is written with correct tokens/cost.
  - Exception path: error is recorded, exception is re-raised.
  - contextvars: request_id and conversation_id are picked up.
  - Fire-and-forget: DB write failure logs warning, does NOT propagate.
  - set_response() not called (bare exception): records error=str(exc), tokens=0.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.observability.anthropic_tracker import track_anthropic_call, _persist


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_anthropic_response(
    model: str = "claude-haiku-4-5-20251001",
    tokens_in: int = 100,
    tokens_out: int = 50,
    cache_creation: int = 0,
    cache_read: int = 0,
):
    """Build a minimal mock Anthropic SDK Message."""
    resp = MagicMock()
    resp.model = model
    usage = MagicMock()
    usage.input_tokens = tokens_in
    usage.output_tokens = tokens_out
    usage.cache_creation_input_tokens = cache_creation
    usage.cache_read_input_tokens = cache_read
    resp.usage = usage
    return resp


def _make_broken_session_factory():
    """Return a session factory whose commit() always raises."""
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock(side_effect=Exception("DB connection lost"))
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock()
    factory.return_value = session
    return factory


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

class TestTrackerSuccessPath:

    async def test_writes_row_on_success(self):
        """Context manager persists a row when set_response() is called."""
        persisted: list = []

        async def fake_persist(tracker, duration_ms):
            persisted.append({"tracker": tracker, "duration_ms": duration_ms})

        with patch("app.bot.observability.anthropic_tracker._persist", fake_persist):
            async with track_anthropic_call("bot.orchestrator") as tracker:
                resp = _make_anthropic_response()
                tracker.set_response(resp)

        assert len(persisted) == 1
        assert persisted[0]["tracker"]._response is resp
        assert persisted[0]["duration_ms"] >= 0

    async def test_response_attributes_are_stored(self):
        """_response on the tracker matches the object passed to set_response()."""
        resp = _make_anthropic_response(tokens_in=200, tokens_out=80)

        async def fake_persist(tracker, duration_ms):
            pass

        with patch("app.bot.observability.anthropic_tracker._persist", fake_persist):
            async with track_anthropic_call("property_classifier") as tracker:
                tracker.set_response(resp)

        assert tracker._response is resp
        assert tracker.source == "property_classifier"

    async def test_cache_tokens_persisted_to_db(self):
        """cache_creation_in and cache_read_in are written to the DB row correctly."""
        saved: list = []
        session = AsyncMock()
        session.add = MagicMock(side_effect=lambda row: saved.append(row))
        session.commit = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=session)

        with patch("app.database.async_session_factory", mock_factory):
            resp = _make_anthropic_response(
                model="claude-haiku-4-5-20251001",
                tokens_in=100,
                tokens_out=50,
                cache_creation=500,
                cache_read=2000,
            )
            async with track_anthropic_call("bot.orchestrator") as tracker:
                tracker.set_response(resp)

        assert len(saved) == 1
        row = saved[0]
        assert row.cache_creation_in == 500
        assert row.cache_read_in == 2000
        assert row.tokens_in == 100
        assert row.tokens_out == 50
        assert row.cost_usd > Decimal("0")
        assert row.model == "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# Exception path
# ---------------------------------------------------------------------------

class TestTrackerExceptionPath:

    async def test_exception_is_reraised(self):
        """Exceptions inside the async with block are always re-raised."""
        persisted: list = []

        async def fake_persist(tracker, duration_ms):
            persisted.append(tracker._error)

        with patch("app.bot.observability.anthropic_tracker._persist", fake_persist):
            with pytest.raises(ValueError, match="api down"):
                async with track_anthropic_call("bot.orchestrator") as tracker:
                    raise ValueError("api down")

    async def test_error_string_is_recorded(self):
        """When an exception is raised, tracker._error contains its str()[:200]."""
        captured_error: list = []

        async def fake_persist(tracker, duration_ms):
            captured_error.append(tracker._error)

        with patch("app.bot.observability.anthropic_tracker._persist", fake_persist):
            with pytest.raises(RuntimeError):
                async with track_anthropic_call("bot.lead_profiler") as tracker:
                    raise RuntimeError("network timeout")

        assert len(captured_error) == 1
        assert "network timeout" in captured_error[0]

    async def test_persist_called_even_on_exception(self):
        """_persist is always called, even when the body raises."""
        persist_called = []

        async def fake_persist(tracker, duration_ms):
            persist_called.append(True)

        with patch("app.bot.observability.anthropic_tracker._persist", fake_persist):
            with pytest.raises(Exception):
                async with track_anthropic_call("bot.orchestrator"):
                    raise Exception("boom")

        assert persist_called == [True]

    async def test_no_response_set_means_zero_tokens(self):
        """If set_response() is never called, row is inserted with tokens_in/out=0."""
        inserted_rows: list = []

        async def fake_persist(tracker, duration_ms):
            inserted_rows.append(tracker)

        with patch("app.bot.observability.anthropic_tracker._persist", fake_persist):
            with pytest.raises(ValueError):
                async with track_anthropic_call("bot.orchestrator"):
                    raise ValueError("early failure")

        assert len(inserted_rows) == 1
        assert inserted_rows[0]._response is None


# ---------------------------------------------------------------------------
# contextvars integration
# ---------------------------------------------------------------------------

class TestTrackerContextVars:

    async def test_picks_up_request_id_from_contextvars(self):
        """request_id from contextvars is stored in the DB row."""
        from app.bot.observability.context import set_request_context, clear_request_context

        set_request_context(request_id="req-abc-123")
        try:
            saved: list = []
            session = AsyncMock()
            session.add = MagicMock(side_effect=lambda row: saved.append(row))
            session.commit = AsyncMock()
            session.__aenter__ = AsyncMock(return_value=session)
            session.__aexit__ = AsyncMock(return_value=False)

            mock_factory = MagicMock(return_value=session)

            with patch("app.database.async_session_factory", mock_factory):
                resp = _make_anthropic_response()
                async with track_anthropic_call("bot.orchestrator") as tracker:
                    tracker.set_response(resp)
        finally:
            clear_request_context()

        assert len(saved) == 1
        assert saved[0].request_id == "req-abc-123"

    async def test_picks_up_conversation_id_from_contextvars(self):
        """conversation_id from contextvars is stored in the DB row."""
        from app.bot.observability.context import set_request_context, clear_request_context

        set_request_context(conversation_id=42)
        try:
            saved: list = []
            session = AsyncMock()
            session.add = MagicMock(side_effect=lambda row: saved.append(row))
            session.commit = AsyncMock()
            session.__aenter__ = AsyncMock(return_value=session)
            session.__aexit__ = AsyncMock(return_value=False)

            mock_factory = MagicMock(return_value=session)

            with patch("app.database.async_session_factory", mock_factory):
                resp = _make_anthropic_response()
                async with track_anthropic_call("bot.orchestrator") as tracker:
                    tracker.set_response(resp)
        finally:
            clear_request_context()

        assert len(saved) == 1
        assert saved[0].conversation_id == 42


# ---------------------------------------------------------------------------
# Fire-and-forget: DB write failure
# ---------------------------------------------------------------------------

class TestTrackerFireAndForget:

    async def test_db_write_failure_does_not_propagate(self, caplog):
        """A broken session factory logs a WARNING but never raises."""
        broken_factory = _make_broken_session_factory()

        with patch("app.database.async_session_factory", broken_factory):
            with caplog.at_level(logging.WARNING, logger="app.bot.observability.anthropic_tracker"):
                # Should NOT raise even though DB commit raises
                async with track_anthropic_call("bot.orchestrator") as tracker:
                    tracker.set_response(_make_anthropic_response())

        # Warning must mention the source
        assert any("bot.orchestrator" in r.message for r in caplog.records)

    async def test_db_write_failure_does_not_swallow_response_exception(self):
        """When both the body and the DB write fail, the body exception propagates."""
        broken_factory = _make_broken_session_factory()

        with patch("app.database.async_session_factory", broken_factory):
            with pytest.raises(ValueError, match="api_error"):
                async with track_anthropic_call("bot.orchestrator"):
                    raise ValueError("api_error")
