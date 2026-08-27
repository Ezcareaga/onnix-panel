"""M6.3 Plan 123-10 (BOT-16 §9/§11): vista_publica leads are cold-swept.

cold_lead_check transitions stale bot-originated leads (status 'new' or
'bot_replied', no activity past the threshold, not opted-out) to 'no_response'.
The source filter is ``_BOT_SOURCES``. vista_publica is a bot-originated source
(public-site CTA → bot conversation), so a stale vista_publica lead MUST be
swept — which requires 'vista_publica' in ``_BOT_SOURCES`` AND in the SQL
WHERE clause's ``source IN (...)`` predicate.

The task runs entirely on a mocked session (the source filter lives in the SQL
WHERE clause, not in Python), mirroring tests/bot/test_cold_lead_check.py.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.bot.scheduler.tasks.cold_lead_check import (
    ColdLeadChecker,
    _BOT_SOURCES,
)


def _make_capturing_factory(stale_rows):
    """Build a mock session factory that captures the SELECT statement and
    returns *stale_rows* so the transition path runs."""
    rows = stale_rows

    mock_result = MagicMock()
    mock_result.fetchall.return_value = rows

    mock_update_result = MagicMock()
    mock_update_result.rowcount = len(rows)

    captured: list = []
    call_count = {"n": 0}

    async def fake_execute(stmt):
        call_count["n"] += 1
        if call_count["n"] == 1:
            captured.append(stmt)
            return mock_result
        return mock_update_result

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=fake_execute)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    mock_factory = MagicMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory.return_value = mock_ctx

    return mock_factory, mock_session, captured


def test_vista_publica_in_bot_sources():
    """'vista_publica' must be an eligible cold-lead source."""
    assert "vista_publica" in _BOT_SOURCES, (
        "vista_publica leads (public-site CTA) are bot-originated and must be "
        "swept by cold_lead_check — add it to _BOT_SOURCES."
    )


@pytest.mark.asyncio
async def test_find_stale_select_includes_vista_publica():
    """The _find_stale WHERE clause must list 'vista_publica' in source IN (...)."""
    factory, _, captured = _make_capturing_factory(stale_rows=[])
    checker = ColdLeadChecker(
        notification_chat_id="",
        telegram_bot_token="",
        session_factory=factory,
    )
    await checker.run()

    assert len(captured) == 1, "expected exactly one SELECT execution"
    compiled_sql = str(
        captured[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "'vista_publica'" in compiled_sql, (
        "cold_lead_check SELECT must filter source IN (..., 'vista_publica').\n"
        f"Compiled SQL:\n{compiled_sql}"
    )


@pytest.mark.asyncio
async def test_stale_vista_publica_lead_swept():
    """A stale vista_publica lead in a stale status is transitioned to
    'no_response' (the query returns it because the source matches)."""
    # The query (mocked) returns the seeded stale vista_publica lead; the
    # checker transitions it. This asserts the transition path runs end-to-end
    # for a vista_publica row.
    factory, session, _ = _make_capturing_factory(stale_rows=[(777, "bot_replied")])
    checker = ColdLeadChecker(
        notification_chat_id="",
        telegram_bot_token="",
        session_factory=factory,
    )
    result = await checker.run()

    assert result == {"checked": 1, "updated": 1}
    session.commit.assert_awaited_once()
    # LeadEvent recorded for the swept lead.
    event = session.add.call_args[0][0]
    assert event.contact_id == 777
    assert event.old_status == "bot_replied"
    assert event.new_status == "no_response"
    assert event.triggered_by == "cold_lead_check"
