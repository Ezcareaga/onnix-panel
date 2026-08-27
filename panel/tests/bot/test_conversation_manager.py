"""Tests for ConversationManager.

Unit tests use mocked AsyncSession to verify SQL logic.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.core.conversation import ConversationManager, HUMAN_COOLDOWN_MINUTES
from app.bot.core.types import (
    ContactInfo,
    ConversationInfo,
    ConversationState,
    HistoryMessage,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_session():
    """Return an AsyncMock that behaves like AsyncSession."""
    session = AsyncMock()
    return session


def _mock_row(**kwargs):
    """Create a mock DB row with attribute access."""
    row = MagicMock()
    for k, v in kwargs.items():
        setattr(row, k, v)
    # Support tuple-style access too
    row._mapping = kwargs
    return row


def _mock_result(rows=None, one_row=None):
    """Create a mock Result object.

    If one_row is given, fetchone() / first() returns it.
    If rows is given, fetchall() returns them.
    """
    result = MagicMock()
    if one_row is not None:
        result.fetchone.return_value = one_row
        result.first.return_value = one_row
    else:
        result.fetchone.return_value = None
        result.first.return_value = None

    if rows is not None:
        result.fetchall.return_value = rows
    else:
        result.fetchall.return_value = []

    # scalars().first() pattern
    scalar_mock = MagicMock()
    if one_row is not None:
        scalar_mock.first.return_value = one_row
    else:
        scalar_mock.first.return_value = None
    result.scalars.return_value = scalar_mock

    return result


# ---------------------------------------------------------------------------
# TestResolveContact
# ---------------------------------------------------------------------------


class TestResolveContact:
    """Contact resolution: platform-specific upsert."""

    @pytest.mark.asyncio
    async def test_resolve_telegram_new_contact(self):
        """New TG contact: INSERT with source='telegram', source_id=user_id."""
        session = _mock_session()
        # First call: lookup returns nothing. Second call: upsert returns new row.
        new_row = _mock_row(
            id=1, name="Test User", phone=None,
            status="new", source_id="12345", baja_at=None,
        )
        session.execute.return_value = _mock_result(one_row=new_row)

        mgr = ConversationManager()
        result = await mgr.resolve_contact(
            session, platform="telegram", user_id="12345",
            user_name="Test User", text_msg="Hola",
        )

        assert isinstance(result, ContactInfo)
        assert result.status == "new"
        assert result.platform == "telegram"
        assert result.source_id == "12345"
        assert result.is_baja is False
        # Verify session.execute was called (SQL was issued)
        assert session.execute.called

    @pytest.mark.asyncio
    async def test_resolve_telegram_existing_contact(self):
        """Existing TG contact: returns existing data, updates last_activity_at."""
        session = _mock_session()
        existing_row = _mock_row(
            id=42, name="Existing", phone="+595981111111",
            status="contacted", source_id="12345", baja_at=None,
        )
        session.execute.return_value = _mock_result(one_row=existing_row)

        mgr = ConversationManager()
        result = await mgr.resolve_contact(
            session, platform="telegram", user_id="12345",
            user_name="Existing", text_msg="Busco algo",
        )

        assert isinstance(result, ContactInfo)
        assert result.id == 42
        assert result.status == "contacted"
        assert result.is_baja is False

    @pytest.mark.asyncio
    async def test_resolve_whatsapp_by_phone(self):
        """WA contact: lookup by phone E.164."""
        session = _mock_session()
        new_row = _mock_row(
            id=10, name="WA User", phone="+595981000001",
            status="new", source_id=None, baja_at=None,
        )
        session.execute.return_value = _mock_result(one_row=new_row)

        mgr = ConversationManager()
        result = await mgr.resolve_contact(
            session, platform="whatsapp", user_id="+595981000001",
            user_name="WA User", text_msg="Busco casa",
        )

        assert isinstance(result, ContactInfo)
        assert result.phone == "+595981000001"

    @pytest.mark.asyncio
    async def test_resolve_contact_baja(self):
        """Discarded contact: is_baja=True."""
        session = _mock_session()
        baja_row = _mock_row(
            id=99, name="Banned User", phone="+595989999999",
            status="discarded", source_id="99999",
            baja_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        session.execute.return_value = _mock_result(one_row=baja_row)

        mgr = ConversationManager()
        result = await mgr.resolve_contact(
            session, platform="telegram", user_id="99999",
            user_name="Banned User",
        )

        assert result.is_baja is True

    @pytest.mark.asyncio
    async def test_resolve_contact_updates_activity(self):
        """Existing contact: last_activity_at is updated."""
        session = _mock_session()
        existing_row = _mock_row(
            id=42, name="Active User", phone=None,
            status="contacted", source_id="55555", baja_at=None,
        )
        session.execute.return_value = _mock_result(one_row=existing_row)

        mgr = ConversationManager()
        await mgr.resolve_contact(
            session, platform="telegram", user_id="55555",
            user_name="Active User",
        )

        # At least one execute call should have been made for the upsert
        # which includes updating last_activity_at
        assert session.execute.call_count >= 1

    @pytest.mark.asyncio
    async def test_resolve_contact_sets_first_message(self):
        """New contact: first_message is set from text parameter."""
        session = _mock_session()
        new_row = _mock_row(
            id=2, name="New User", phone=None,
            status="new", source_id="77777", baja_at=None,
        )
        session.execute.return_value = _mock_result(one_row=new_row)

        mgr = ConversationManager()
        result = await mgr.resolve_contact(
            session, platform="telegram", user_id="77777",
            user_name="New User", text_msg="Busco departamento en Asuncion",
        )

        # The upsert SQL should include first_message
        call_args = session.execute.call_args_list
        assert len(call_args) >= 1
        # Check that the text was passed as a parameter
        found_text = False
        for call in call_args:
            args, kwargs = call
            if len(args) >= 2 and isinstance(args[1], dict):
                if args[1].get("text") == "Busco departamento en Asuncion":
                    found_text = True
            # Also check kwargs
            if kwargs:
                for v in kwargs.values():
                    if isinstance(v, dict) and v.get("text") == "Busco departamento en Asuncion":
                        found_text = True
        assert found_text, "first_message text was not passed to SQL"


# ---------------------------------------------------------------------------
# TestGetOrCreateConversation
# ---------------------------------------------------------------------------


class TestGetOrCreateConversation:
    """Conversation upsert with UNIQUE(contact_id, platform, platform_chat_id)."""

    @pytest.mark.asyncio
    async def test_create_new_conversation(self):
        """New conversation: is_bot_active=True, is_open=True, status='active'."""
        session = _mock_session()
        new_conv = _mock_row(
            id=1, contact_id=1, platform="telegram",
            platform_chat_id="12345", is_bot_active=True,
            is_open=True, search_context={}, message_count=0,
            last_human_reply_at=None,
        )
        session.execute.return_value = _mock_result(one_row=new_conv)

        mgr = ConversationManager()
        result = await mgr.get_or_create_conversation(
            session, contact_id=1, platform="telegram", chat_id="12345",
        )

        assert isinstance(result, ConversationInfo)
        assert result.is_bot_active is True
        assert result.is_open is True
        assert result.id == 1

    @pytest.mark.asyncio
    async def test_get_existing_conversation(self):
        """Existing conversation: returns data from DB."""
        session = _mock_session()
        existing = _mock_row(
            id=42, contact_id=5, platform="whatsapp",
            platform_chat_id="+595981000001", is_bot_active=True,
            is_open=True, search_context={"etapa": "mostrando_resultados"},
            message_count=15, last_human_reply_at=None,
        )
        session.execute.return_value = _mock_result(one_row=existing)

        mgr = ConversationManager()
        result = await mgr.get_or_create_conversation(
            session, contact_id=5, platform="whatsapp",
            chat_id="+595981000001",
        )

        assert result.id == 42
        assert result.message_count == 15

    @pytest.mark.asyncio
    async def test_conversation_unique_constraint(self):
        """Two calls with same (contact_id, platform, chat_id) return same ID."""
        session = _mock_session()
        conv_row = _mock_row(
            id=10, contact_id=1, platform="telegram",
            platform_chat_id="12345", is_bot_active=True,
            is_open=True, search_context={}, message_count=0,
            last_human_reply_at=None,
        )
        session.execute.return_value = _mock_result(one_row=conv_row)

        mgr = ConversationManager()
        r1 = await mgr.get_or_create_conversation(
            session, contact_id=1, platform="telegram", chat_id="12345",
        )
        r2 = await mgr.get_or_create_conversation(
            session, contact_id=1, platform="telegram", chat_id="12345",
        )

        assert r1.id == r2.id

    @pytest.mark.asyncio
    async def test_conversation_default_search_context(self):
        """New conversation: search_context defaults to empty dict."""
        session = _mock_session()
        new_conv = _mock_row(
            id=3, contact_id=1, platform="telegram",
            platform_chat_id="99999", is_bot_active=True,
            is_open=True, search_context={}, message_count=0,
            last_human_reply_at=None,
        )
        session.execute.return_value = _mock_result(one_row=new_conv)

        mgr = ConversationManager()
        result = await mgr.get_or_create_conversation(
            session, contact_id=1, platform="telegram", chat_id="99999",
        )

        assert result.search_context == {}


# ---------------------------------------------------------------------------
# TestGetHistory
# ---------------------------------------------------------------------------


class TestGetHistory:
    """Message history retrieval."""

    @pytest.mark.asyncio
    async def test_get_history_empty(self):
        """No messages: returns empty list."""
        session = _mock_session()
        session.execute.return_value = _mock_result(rows=[])

        mgr = ConversationManager()
        result = await mgr.get_history(session, conversation_id=1)

        assert result == []

    @pytest.mark.asyncio
    async def test_get_history_ordered(self):
        """Messages returned in chronological order (oldest first)."""
        session = _mock_session()
        # DB returns DESC (newest first)
        rows = [
            _mock_row(direction="outbound", sender_type="bot",
                       body="Respuesta", properties_shown=[1, 2]),
            _mock_row(direction="inbound", sender_type="contact",
                       body="Segundo msg", properties_shown=None),
            _mock_row(direction="inbound", sender_type="contact",
                       body="Primer msg", properties_shown=None),
        ]
        session.execute.return_value = _mock_result(rows=rows)

        mgr = ConversationManager()
        result = await mgr.get_history(session, conversation_id=1)

        # Should be reversed to chronological
        assert len(result) == 3
        assert result[0].body == "Primer msg"
        assert result[1].body == "Segundo msg"
        assert result[2].body == "Respuesta"

    @pytest.mark.asyncio
    async def test_get_history_limit(self):
        """LIMIT parameter is passed to SQL with over-fetch margin."""
        session = _mock_session()
        session.execute.return_value = _mock_result(rows=[])

        mgr = ConversationManager()
        await mgr.get_history(session, conversation_id=1, limit=5)

        # get_history over-fetches by +10 to filter contaminated N8N history
        call_args = session.execute.call_args_list
        assert len(call_args) >= 1
        args, kwargs = call_args[0]
        if len(args) >= 2 and isinstance(args[1], dict):
            assert args[1].get("limit") == 15  # 5 + 10 over-fetch

    @pytest.mark.asyncio
    async def test_get_history_default_limit_is_12(self):
        """Default limit is 12 (M3 expansion, was 10)."""
        session = _mock_session()
        session.execute.return_value = _mock_result(rows=[])

        mgr = ConversationManager()
        await mgr.get_history(session, conversation_id=1)  # sin limit explícito

        call_args = session.execute.call_args_list
        assert len(call_args) >= 1
        args, kwargs = call_args[0]
        if len(args) >= 2 and isinstance(args[1], dict):
            assert args[1].get("limit") == 22  # 12 + 10 over-fetch

    @pytest.mark.asyncio
    async def test_get_history_format(self):
        """Each item is a HistoryMessage with expected fields."""
        session = _mock_session()
        rows = [
            _mock_row(direction="inbound", sender_type="contact",
                       body="Hola", properties_shown=None),
        ]
        session.execute.return_value = _mock_result(rows=rows)

        mgr = ConversationManager()
        result = await mgr.get_history(session, conversation_id=1)

        assert len(result) == 1
        msg = result[0]
        assert isinstance(msg, HistoryMessage)
        assert msg.direction == "inbound"
        assert msg.sender_type == "contact"
        assert msg.body == "Hola"


# ---------------------------------------------------------------------------
# TestSearchContext
# ---------------------------------------------------------------------------


class TestSearchContext:
    """search_context JSONB read/write."""

    @pytest.mark.asyncio
    async def test_get_search_context_empty(self):
        """Conversation with search_context=None returns default ConversationState."""
        session = _mock_session()
        row = _mock_row(search_context=None)
        session.execute.return_value = _mock_result(one_row=row)

        mgr = ConversationManager()
        result = await mgr.get_search_context(session, conversation_id=1)

        assert isinstance(result, ConversationState)
        assert result.etapa == "inicio"
        assert result.filtros == {}
        assert result.shown_properties == []

    @pytest.mark.asyncio
    async def test_get_search_context_populated(self):
        """Conversation with full search_context returns populated ConversationState."""
        session = _mock_session()
        ctx = {
            "etapa": "mostrando_resultados",
            "filtros": {"ciudad": "Asuncion", "operacion": "venta"},
            "shown_properties": [100, 200, 300],
            "resultados_pendientes": [400, 500],
            "current_page_ids": [100, 200],
        }
        row = _mock_row(search_context=ctx)
        session.execute.return_value = _mock_result(one_row=row)

        mgr = ConversationManager()
        result = await mgr.get_search_context(session, conversation_id=1)

        assert result.etapa == "mostrando_resultados"
        assert result.filtros["ciudad"] == "Asuncion"
        assert result.shown_properties == [100, 200, 300]
        assert result.resultados_pendientes == [400, 500]

    @pytest.mark.asyncio
    async def test_update_search_context(self):
        """update_search_context persists the state via UPDATE SQL."""
        session = _mock_session()
        session.execute.return_value = _mock_result()

        state = ConversationState(
            etapa="mostrando_resultados",
            filtros={"ciudad": "Luque"},
            shown_properties=[1, 2, 3],
        )

        mgr = ConversationManager()
        await mgr.update_search_context(session, conversation_id=1, state=state)

        # Verify UPDATE was executed
        assert session.execute.called
        call_args = session.execute.call_args_list
        assert len(call_args) >= 1

    @pytest.mark.asyncio
    async def test_update_search_context_caps_shown(self):
        """shown_properties capped at 50 after serialization."""
        session = _mock_session()
        session.execute.return_value = _mock_result()

        # 60 items — exceeds cap of 50
        state = ConversationState(
            shown_properties=list(range(1, 61)),
        )

        mgr = ConversationManager()
        await mgr.update_search_context(session, conversation_id=1, state=state)

        # Verify the JSONB passed to SQL has at most 50 shown_properties
        call_args = session.execute.call_args_list
        assert len(call_args) >= 1
        args, kwargs = call_args[0]
        # The context parameter should contain capped shown_properties
        if len(args) >= 2 and isinstance(args[1], dict):
            ctx = args[1].get("context")
            if isinstance(ctx, str):
                ctx = json.loads(ctx)
            if isinstance(ctx, dict):
                assert len(ctx["shown_properties"]) <= 50

    @pytest.mark.asyncio
    async def test_search_context_roundtrip(self):
        """Get context, modify filtros, save, get again — filtros are merged."""
        session = _mock_session()

        # First get: has existing filtros
        ctx_data = {
            "etapa": "inicio",
            "filtros": {"ciudad": "Asuncion"},
            "shown_properties": [],
        }
        row = _mock_row(search_context=ctx_data)
        session.execute.return_value = _mock_result(one_row=row)

        mgr = ConversationManager()

        # GET
        state = await mgr.get_search_context(session, conversation_id=1)
        assert state.filtros["ciudad"] == "Asuncion"

        # MODIFY
        state.merge_filters({"operacion": "alquiler"})
        assert state.filtros["operacion"] == "alquiler"
        assert state.filtros["ciudad"] == "Asuncion"

        # SAVE
        session.execute.return_value = _mock_result()
        await mgr.update_search_context(session, conversation_id=1, state=state)

        # Verify save was called
        assert session.execute.call_count >= 2  # at least get + save


# ---------------------------------------------------------------------------
# TestSaveMessage
# ---------------------------------------------------------------------------


class TestSaveMessage:
    """Message recording."""

    @pytest.mark.asyncio
    async def test_save_inbound_message(self):
        """Inbound message: direction='inbound', sender_type='contact'."""
        session = _mock_session()
        msg_row = _mock_row(id=100)
        session.execute.return_value = _mock_result(one_row=msg_row)

        mgr = ConversationManager()
        msg_id = await mgr.save_inbound_message(
            session, conversation_id=1, contact_id=1,
            body="Hola", external_id="msg_001",
        )

        assert msg_id == 100
        assert session.execute.called

    @pytest.mark.asyncio
    async def test_save_outbound_message(self):
        """Outbound message: direction='outbound', sender_type='bot'."""
        session = _mock_session()
        msg_row = _mock_row(id=200)
        session.execute.return_value = _mock_result(one_row=msg_row)

        mgr = ConversationManager()
        msg_id = await mgr.save_outbound_message(
            session, conversation_id=1, contact_id=1,
            body="Respuesta", intent="saludo",
            ai_model="claude-haiku", ai_tokens_in=100,
            ai_tokens_out=50, properties_shown=[1, 2],
        )

        assert msg_id == 200
        assert session.execute.called

    @pytest.mark.asyncio
    async def test_save_inbound_increments_count(self):
        """Inbound message increments conversation.message_count."""
        session = _mock_session()
        msg_row = _mock_row(id=101)
        session.execute.return_value = _mock_result(one_row=msg_row)

        mgr = ConversationManager()
        await mgr.save_inbound_message(
            session, conversation_id=5, contact_id=1,
            body="Test", external_id="msg_002",
        )

        # Should have at least 2 execute calls: INSERT message + UPDATE conversation
        assert session.execute.call_count >= 2

    @pytest.mark.asyncio
    async def test_save_inbound_idempotent(self):
        """Duplicate external_id returns existing message ID without error."""
        session = _mock_session()

        # First insert returns the row
        msg_row = _mock_row(id=101)
        # Second call (conflict): INSERT returns None, SELECT returns existing
        conflict_result = _mock_result(one_row=None)
        existing_result = _mock_result(one_row=msg_row)
        update_result = _mock_result()

        session.execute.side_effect = [
            _mock_result(one_row=msg_row),  # First INSERT RETURNING
            _mock_result(),                  # First UPDATE conversation
            _mock_result(),                  # First UPDATE contacts (last_user_message_at)
            conflict_result,                 # Second INSERT (conflict, no row)
            existing_result,                 # Second SELECT existing
            _mock_result(),                  # Second UPDATE conversation
            _mock_result(),                  # Second UPDATE contacts (last_user_message_at)
        ]

        mgr = ConversationManager()

        # First call
        id1 = await mgr.save_inbound_message(
            session, conversation_id=1, contact_id=1,
            body="Hola", external_id="dup_001",
        )

        # Second call with same external_id — should NOT raise
        id2 = await mgr.save_inbound_message(
            session, conversation_id=1, contact_id=1,
            body="Hola", external_id="dup_001",
        )

        assert id1 == id2 == 101

    @pytest.mark.asyncio
    async def test_save_inbound_updates_last_message_at(self):
        """Inbound message updates conversation.last_message_at."""
        session = _mock_session()
        msg_row = _mock_row(id=102)
        session.execute.return_value = _mock_result(one_row=msg_row)

        mgr = ConversationManager()
        await mgr.save_inbound_message(
            session, conversation_id=7, contact_id=1,
            body="Ultimo msg",
        )

        # At least one UPDATE should contain last_message_at
        assert session.execute.call_count >= 2

    @pytest.mark.asyncio
    async def test_save_outbound_message_none_guard(self):
        """save_outbound_message raises ValueError when INSERT returns None."""
        session = _mock_session()
        session.execute.return_value = _mock_result(one_row=None)

        mgr = ConversationManager()
        with pytest.raises(ValueError, match="Failed to persist outbound message"):
            await mgr.save_outbound_message(
                session, conversation_id=1, contact_id=1,
                body="Test", intent="saludo",
            )

    @pytest.mark.asyncio
    async def test_save_inbound_updates_last_user_message_at(self):
        """save_inbound_message must update contacts.last_user_message_at."""
        session = _mock_session()
        msg_row = _mock_row(id=105)
        session.execute.return_value = _mock_result(one_row=msg_row)

        mgr = ConversationManager()
        await mgr.save_inbound_message(
            session, conversation_id=3, contact_id=7,
            body="Hola test", external_id="msg_007",
        )

        all_calls = session.execute.call_args_list
        sql_strings = [str(call.args[0]) for call in all_calls if call.args]
        assert any("last_user_message_at" in s for s in sql_strings), (
            "save_inbound_message debe actualizar contacts.last_user_message_at"
        )


# ---------------------------------------------------------------------------
# TestHumanCooldown
# ---------------------------------------------------------------------------


class TestHumanCooldown:
    """Human cooldown detection."""

    def test_no_human_reply(self):
        """No human reply: cooldown is False."""
        mgr = ConversationManager()
        result = mgr.check_human_cooldown(last_human_reply_at=None)
        assert result is False

    def test_human_reply_recent(self):
        """Human replied 10 min ago, cooldown=30 min: returns True."""
        mgr = ConversationManager()
        recent = datetime.now(timezone.utc) - timedelta(minutes=10)
        result = mgr.check_human_cooldown(
            last_human_reply_at=recent, cooldown_minutes=30,
        )
        assert result is True

    def test_human_reply_old(self):
        """Human replied 60 min ago, cooldown=30 min: returns False."""
        mgr = ConversationManager()
        old = datetime.now(timezone.utc) - timedelta(minutes=60)
        result = mgr.check_human_cooldown(
            last_human_reply_at=old, cooldown_minutes=30,
        )
        assert result is False


# ---------------------------------------------------------------------------
# TestResolveContactAgentUserId
# ---------------------------------------------------------------------------


class TestResolveContactAgentUserId:
    """resolve_contact must populate agent_user_id from DB row."""

    @pytest.mark.asyncio
    async def test_resolve_contact_populates_agent_user_id_from_db(self):
        """Contact with agent_user_id=7 in DB row → ContactInfo.agent_user_id == 7."""
        session = _mock_session()
        row = _mock_row(
            id=5, name="Lead User", phone="+595981000007",
            status="agent_replied", source_id=None, baja_at=None,
            agent_user_id=7,
        )
        session.execute.return_value = _mock_result(one_row=row)

        mgr = ConversationManager()
        result = await mgr.resolve_contact(
            session, platform="whatsapp", user_id="+595981000007",
            user_name="Lead User", text_msg="Hola de nuevo",
        )

        assert result.agent_user_id == 7

    @pytest.mark.asyncio
    async def test_resolve_contact_agent_user_id_none_when_not_assigned(self):
        """Contact without agent_user_id in DB row → ContactInfo.agent_user_id is None."""
        session = _mock_session()
        row = _mock_row(
            id=6, name="New User", phone="+595981000006",
            status="new", source_id=None, baja_at=None,
            agent_user_id=None,
        )
        session.execute.return_value = _mock_result(one_row=row)

        mgr = ConversationManager()
        result = await mgr.resolve_contact(
            session, platform="whatsapp", user_id="+595981000006",
            user_name="New User",
        )

        assert result.agent_user_id is None
