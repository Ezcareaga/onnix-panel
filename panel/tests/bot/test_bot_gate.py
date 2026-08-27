"""Tests unitarios para ``app.bot.state.bot_gate`` (M4 Task 4.1 + 4.2).

Cubren tanto el check in-memory (``check_bot_active``) como la versión
con ``SELECT FOR UPDATE`` (``check_bot_active_locked``) introducida en
Task 4.2 para cerrar las races #1 y #2 del gate is_bot_active con el
panel.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from datetime import datetime, timezone

from app.bot.core.types import ContactInfo, ConversationInfo
from app.bot.state.bot_gate import (
    check_bot_active,
    check_bot_active_locked,
    reactivate_from_agent_replied,
)


def _make_conversation(is_bot_active: bool = True) -> ConversationInfo:
    return ConversationInfo(
        id=10,
        contact_id=1,
        platform="whatsapp",
        chat_id="+595981000001",
        is_bot_active=is_bot_active,
    )


# ---------------------------------------------------------------------------
# check_bot_active — in-memory read (no DB, no lock)
# ---------------------------------------------------------------------------


class TestCheckBotActiveInMemory:
    """``check_bot_active(conversation)`` reads the in-memory attribute only."""

    def test_returns_true_when_conversation_is_active(self):
        assert check_bot_active(_make_conversation(is_bot_active=True)) is True

    def test_returns_false_when_conversation_is_inactive(self):
        assert check_bot_active(_make_conversation(is_bot_active=False)) is False


# ---------------------------------------------------------------------------
# check_bot_active_locked — DB read with SELECT FOR UPDATE
# ---------------------------------------------------------------------------


class TestCheckBotActiveLocked:
    """``check_bot_active_locked`` issues SELECT ... FOR UPDATE against the DB."""

    @pytest.mark.asyncio
    async def test_returns_true_when_db_row_is_active(self):
        session = AsyncMock()
        session.execute.return_value = MagicMock(scalar=MagicMock(return_value=True))

        assert await check_bot_active_locked(session, conversation_id=10) is True
        session.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_false_when_db_row_is_inactive(self):
        session = AsyncMock()
        session.execute.return_value = MagicMock(scalar=MagicMock(return_value=False))

        assert await check_bot_active_locked(session, conversation_id=10) is False

    @pytest.mark.asyncio
    async def test_sql_includes_for_update_clause(self):
        """Compiled statement must contain ``FOR UPDATE`` to take the row lock.

        This is the defining contract of this function — without FOR UPDATE
        there is no lock and races #1 and #2 remain open.
        """
        session = AsyncMock()
        session.execute.return_value = MagicMock(scalar=MagicMock(return_value=True))

        await check_bot_active_locked(session, conversation_id=42)

        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "FOR UPDATE" in compiled.upper(), (
            f"Expected FOR UPDATE in compiled SQL, got: {compiled}"
        )

    @pytest.mark.asyncio
    async def test_filters_by_conversation_id(self):
        """Statement must filter by the given conversation_id."""
        session = AsyncMock()
        session.execute.return_value = MagicMock(scalar=MagicMock(return_value=True))

        await check_bot_active_locked(session, conversation_id=1234)

        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "1234" in compiled, (
            f"Expected conversation_id=1234 in compiled SQL, got: {compiled}"
        )


# ---------------------------------------------------------------------------
# reactivate_from_agent_replied — race #3 guard against opt-out
# ---------------------------------------------------------------------------


def _make_contact(status: str = "agent_replied") -> ContactInfo:
    return ContactInfo(
        id=42,
        name="Race #3 Test",
        status=status,
        platform="whatsapp",
        phone="+595981599901",
        source_id="+595981599901",
    )


class TestReactivateRaceThreeGuard:
    """``reactivate_from_agent_replied`` must NOT reactivate an opted-out contact.

    Race #3: opt-out handler commits ``baja_at = NOW()`` while a client's
    webhook is mid-flight and about to reactivate. The SELECT FOR UPDATE
    on ``contacts.baja_at`` forces reactivation to see the committed
    opt-out and exit early.
    """

    @pytest.mark.asyncio
    async def test_skips_when_baja_at_is_set(self):
        """When baja_at IS NOT NULL, reactivation must bail before any write."""
        contact = _make_contact()
        conversation = ConversationInfo(
            id=10, contact_id=42, platform="whatsapp", chat_id="+595981599901",
        )
        session = AsyncMock()
        # Simulate contact ALREADY opted out (baja_at = NOW from concurrent commit).
        session.execute.return_value = MagicMock(
            scalar=MagicMock(return_value=datetime.now(timezone.utc))
        )

        with (
            patch("app.bot.state.bot_gate.lead_event_repo") as mock_repo,
            patch("app.bot.state.bot_gate.get_admin_notifier") as mock_notifier,
        ):
            mock_repo.create = AsyncMock()
            mock_notifier.return_value = AsyncMock()
            await reactivate_from_agent_replied(session, contact, conversation)

        # Only the SELECT FOR UPDATE ran — no UPDATE contacts, no UPDATE conversations,
        # no lead_event, no in-memory mutation.
        assert session.execute.call_count == 1
        mock_repo.create.assert_not_awaited()
        assert contact.status == "agent_replied"  # NOT flipped to bot_replied

    @pytest.mark.asyncio
    async def test_baja_at_select_uses_for_update(self):
        """The baja_at lookup must compile with FOR UPDATE — otherwise there is no lock."""
        contact = _make_contact()
        conversation = ConversationInfo(
            id=10, contact_id=42, platform="whatsapp", chat_id="+595981599901",
        )
        session = AsyncMock()
        session.execute.return_value = MagicMock(scalar=MagicMock(return_value=None))

        with (
            patch("app.bot.state.bot_gate.lead_event_repo") as mock_repo,
            patch("app.bot.state.bot_gate.get_admin_notifier") as mock_notifier,
        ):
            mock_repo.create = AsyncMock()
            mock_notifier.return_value = AsyncMock()
            await reactivate_from_agent_replied(session, contact, conversation)

        # The FIRST execute is the race #3 guard SELECT
        first_stmt = session.execute.call_args_list[0].args[0]
        compiled = str(first_stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "FOR UPDATE" in compiled.upper(), (
            f"Expected FOR UPDATE on baja_at SELECT, got: {compiled}"
        )
        assert "baja_at" in compiled.lower(), (
            f"Expected baja_at column in guard SELECT, got: {compiled}"
        )


# ---------------------------------------------------------------------------
# reactivate_from_agent_replied — clears human cooldown (fix: reactivation
# was announcing bot active but leaving last_human_reply_at set, causing the
# orchestrator cooldown check to silence the bot immediately after).
# ---------------------------------------------------------------------------


def _make_session_with_baja_at(value=None):
    """Return AsyncMock session whose execute() returns scalar(value).

    Used for the race-#3 guard (baja_at IS NULL → proceed with reactivation).
    When value=None the guard lets reactivation proceed.
    """
    session = AsyncMock()
    session.execute.return_value = MagicMock(scalar=MagicMock(return_value=value))
    return session


class TestReactivateClearsCooldown:
    """``reactivate_from_agent_replied`` must clear the human cooldown.

    Bug: The function set ``is_bot_active=True`` in DB and in-memory but did
    NOT clear ``conversations.last_human_reply_at`` (DB) nor
    ``conversation.last_human_reply_at`` (in-memory object).  The orchestrator
    checks the cooldown **after** reactivation on the same in-memory object,
    so a non-NULL ``last_human_reply_at`` < 30 min old would silence the bot
    even though the reactivation announcement said "Bot reactivado
    automáticamente".

    Fix (approved): SET last_human_reply_at = NULL in the UPDATE conversations
    statement AND set conversation.last_human_reply_at = None in-memory.
    """

    @pytest.mark.asyncio
    async def test_db_update_clears_last_human_reply_at(self):
        """The UPDATE conversations SQL must include last_human_reply_at = NULL.

        This is the DB-side fix — without it the cooldown survives across
        reconnections / new conversation objects loaded from DB.
        """
        contact = _make_contact()
        conversation = ConversationInfo(
            id=10, contact_id=42, platform="whatsapp", chat_id="+595981599901",
            last_human_reply_at=datetime.now(timezone.utc),
        )
        session = _make_session_with_baja_at(None)  # proceed with reactivation

        with (
            patch("app.bot.state.bot_gate.lead_event_repo") as mock_repo,
            patch("app.bot.state.bot_gate.get_admin_notifier") as mock_notifier,
        ):
            mock_repo.create = AsyncMock()
            mock_notifier.return_value = AsyncMock()
            await reactivate_from_agent_replied(session, contact, conversation)

        # Find the UPDATE conversations statement (not the baja_at SELECT)
        conv_update_sql = None
        for call in session.execute.call_args_list:
            stmt = call.args[0]
            compiled = str(stmt) if not hasattr(stmt, "compile") else str(
                stmt.compile(compile_kwargs={"literal_binds": True})
            )
            if "conversations" in compiled.lower() and "is_bot_active" in compiled.lower():
                conv_update_sql = compiled
                break

        assert conv_update_sql is not None, (
            "Expected an UPDATE conversations statement, none found"
        )
        assert "last_human_reply_at" in conv_update_sql.lower(), (
            f"UPDATE conversations must set last_human_reply_at = NULL, got: {conv_update_sql}"
        )

    @pytest.mark.asyncio
    async def test_in_memory_conversation_last_human_reply_at_cleared(self):
        """After reactivation the in-memory conversation object must have
        last_human_reply_at = None.

        Critical: ``orchestrator.py`` calls
        ``conversation_manager.check_human_cooldown(conversation.last_human_reply_at)``
        on the SAME object returned by ``get_or_create_conversation`` — which is
        the same object passed into ``reactivate_from_agent_replied``.  If this
        field is not cleared in-memory the cooldown fires and the bot stays
        silent in the current request.
        """
        contact = _make_contact()
        conversation = ConversationInfo(
            id=10, contact_id=42, platform="whatsapp", chat_id="+595981599901",
            last_human_reply_at=datetime.now(timezone.utc),
        )
        session = _make_session_with_baja_at(None)

        with (
            patch("app.bot.state.bot_gate.lead_event_repo") as mock_repo,
            patch("app.bot.state.bot_gate.get_admin_notifier") as mock_notifier,
        ):
            mock_repo.create = AsyncMock()
            mock_notifier.return_value = AsyncMock()
            await reactivate_from_agent_replied(session, contact, conversation)

        assert conversation.last_human_reply_at is None, (
            f"Expected conversation.last_human_reply_at=None after reactivation, "
            f"got: {conversation.last_human_reply_at}"
        )

    @pytest.mark.asyncio
    async def test_skipped_reactivation_does_not_touch_last_human_reply_at(self):
        """When baja_at is set (opt-out guard fires), last_human_reply_at must
        not be mutated — the conversation object should remain unchanged."""
        contact = _make_contact()
        original_ts = datetime.now(timezone.utc)
        conversation = ConversationInfo(
            id=10, contact_id=42, platform="whatsapp", chat_id="+595981599901",
            last_human_reply_at=original_ts,
        )
        # baja_at IS NOT NULL → guard exits early
        session = _make_session_with_baja_at(datetime.now(timezone.utc))

        with (
            patch("app.bot.state.bot_gate.lead_event_repo") as mock_repo,
            patch("app.bot.state.bot_gate.get_admin_notifier") as mock_notifier,
        ):
            mock_repo.create = AsyncMock()
            mock_notifier.return_value = AsyncMock()
            await reactivate_from_agent_replied(session, contact, conversation)

        # Guard fired — in-memory object must be untouched
        assert conversation.last_human_reply_at is original_ts, (
            "last_human_reply_at must not be modified when reactivation is skipped"
        )
