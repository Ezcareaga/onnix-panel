"""
Tests for app/services/conversation_service.py

All repository calls are fully mocked at the module-import level so no real
database connection is required.  The 24-hour window calculation is tested by
constructing contacts whose last_user_message_at is pinned to a known time
relative to datetime.now(timezone.utc).

Coverage target: lines 10-16 (get_conversations) and 18-41 (get_thread).
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.conversation_service import ConversationService


# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

_CONV_REPO = "app.services.conversation_service.conversation_repo"
_MSG_REPO = "app.services.conversation_service.message_repo"
_CONTACT_REPO = "app.services.conversation_service.contact_repo"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conv(contact_id=1, conv_id=10):
    conv = MagicMock()
    conv.id = conv_id
    conv.contact_id = contact_id
    return conv


def _make_contact(name="Ana Torres", phone="+595981000001", last_msg_at=None):
    contact = MagicMock()
    contact.name = name
    contact.phone = phone
    contact.last_user_message_at = last_msg_at
    return contact


def _make_conv_with_contact(
    conv_id=10,
    contact_id=1,
    name="Ana Torres",
    phone="+595981000001",
    last_message_preview="",
    last_message_direction="",
):
    """Build a dict matching conversation_repo.get_with_contacts() output."""
    conv = _make_conv(contact_id=contact_id, conv_id=conv_id)
    return {
        "conversation": conv,
        "contact_name": name or "Desconocido",
        "contact_phone": phone or "",
        "last_message_preview": last_message_preview,
        "last_message_direction": last_message_direction,
    }


# ---------------------------------------------------------------------------
# get_conversations
# ---------------------------------------------------------------------------

class TestGetConversations:
    async def test_returns_list_with_contact_info(self):
        row = _make_conv_with_contact(name="Luis Gomez", phone="+595981111001")

        with patch(f"{_CONV_REPO}.get_with_contacts", new=AsyncMock(return_value=[row])):
            result = await ConversationService.get_conversations(AsyncMock())

        assert len(result) == 1
        assert result[0]["contact_name"] == "Luis Gomez"
        assert result[0]["contact_phone"] == "+595981111001"
        assert result[0]["conversation"] is row["conversation"]

    async def test_contact_id_none_yields_unknown(self):
        row = _make_conv_with_contact(contact_id=None, name="Desconocido", phone="")

        with patch(f"{_CONV_REPO}.get_with_contacts", new=AsyncMock(return_value=[row])):
            result = await ConversationService.get_conversations(AsyncMock())

        assert result[0]["contact_name"] == "Desconocido"
        assert result[0]["contact_phone"] == ""

    async def test_empty_conversations_returns_empty_list(self):
        with patch(f"{_CONV_REPO}.get_with_contacts", new=AsyncMock(return_value=[])):
            result = await ConversationService.get_conversations(AsyncMock())

        assert result == []

    async def test_multiple_conversations_enriched_independently(self):
        row_a = _make_conv_with_contact(conv_id=10, contact_id=1, name="Maria", phone="+595981000010")
        row_b = _make_conv_with_contact(conv_id=11, contact_id=2, name="Pedro", phone="+595981000011")

        with patch(f"{_CONV_REPO}.get_with_contacts", new=AsyncMock(return_value=[row_a, row_b])):
            result = await ConversationService.get_conversations(AsyncMock())

        assert len(result) == 2
        names = {r["contact_name"] for r in result}
        assert names == {"Maria", "Pedro"}

    async def test_limit_forwarded_to_repo(self):
        with patch(f"{_CONV_REPO}.get_with_contacts", new=AsyncMock(return_value=[])) as mock_get:
            db = AsyncMock()
            await ConversationService.get_conversations(db, limit=25)

        mock_get.assert_awaited_once_with(db, 25, offset=0, agent_filter=None, channel=None, stuck=False)

    async def test_needs_reply_true_when_inbound(self):
        row = _make_conv_with_contact(
            name="Luis Gomez", phone="+595981111001",
            last_message_preview="Hola", last_message_direction="inbound",
        )

        with patch(f"{_CONV_REPO}.get_with_contacts", new=AsyncMock(return_value=[row])):
            result = await ConversationService.get_conversations(AsyncMock())

        assert result[0]["needs_reply"] is True

    async def test_needs_reply_false_when_outbound(self):
        row = _make_conv_with_contact(
            name="Luis Gomez", phone="+595981111001",
            last_message_preview="Gracias", last_message_direction="outbound",
        )

        with patch(f"{_CONV_REPO}.get_with_contacts", new=AsyncMock(return_value=[row])):
            result = await ConversationService.get_conversations(AsyncMock())

        assert result[0]["needs_reply"] is False

    async def test_needs_reply_false_when_no_direction(self):
        row = _make_conv_with_contact(
            name="Luis Gomez", phone="+595981111001",
            last_message_preview="", last_message_direction="",
        )

        with patch(f"{_CONV_REPO}.get_with_contacts", new=AsyncMock(return_value=[row])):
            result = await ConversationService.get_conversations(AsyncMock())

        assert result[0]["needs_reply"] is False

    async def test_preview_and_direction_forwarded(self):
        row = _make_conv_with_contact(
            name="Ana Torres", phone="+595981000001",
            last_message_preview="Busco depto", last_message_direction="inbound",
        )

        with patch(f"{_CONV_REPO}.get_with_contacts", new=AsyncMock(return_value=[row])):
            result = await ConversationService.get_conversations(AsyncMock())

        assert result[0]["last_message_preview"] == "Busco depto"
        assert result[0]["last_message_direction"] == "inbound"


# ---------------------------------------------------------------------------
# get_thread
# ---------------------------------------------------------------------------

class TestGetThread:
    async def test_not_found_returns_none(self):
        with patch(f"{_CONV_REPO}.get_by_id", new=AsyncMock(return_value=None)):
            result = await ConversationService.get_thread(AsyncMock(), conversation_id=999)

        assert result is None

    async def test_returns_all_required_keys(self):
        conv = _make_conv(contact_id=1, conv_id=10)
        contact = _make_contact()
        messages = [MagicMock(), MagicMock()]

        with (
            patch(f"{_CONV_REPO}.get_by_id", new=AsyncMock(return_value=conv)),
            patch(f"{_MSG_REPO}.get_by_conversation", new=AsyncMock(return_value=messages)),
            patch(f"{_MSG_REPO}.get_last_inbound_at", new=AsyncMock(return_value=None)),
            patch(f"{_CONTACT_REPO}.get_by_id", new=AsyncMock(return_value=contact)),
        ):
            result = await ConversationService.get_thread(AsyncMock(), conversation_id=10)

        assert set(result.keys()) == {
            "conversation", "messages", "contact_name", "contact_phone",
            "contact", "window_expired", "properties_map",
        }

    async def test_with_contact_populates_name_and_phone(self):
        conv = _make_conv(contact_id=5, conv_id=10)
        contact = _make_contact(name="Sofia Ruiz", phone="+595981555001")

        with (
            patch(f"{_CONV_REPO}.get_by_id", new=AsyncMock(return_value=conv)),
            patch(f"{_MSG_REPO}.get_by_conversation", new=AsyncMock(return_value=[])),
            patch(f"{_MSG_REPO}.get_last_inbound_at", new=AsyncMock(return_value=None)),
            patch(f"{_CONTACT_REPO}.get_by_id", new=AsyncMock(return_value=contact)),
        ):
            result = await ConversationService.get_thread(AsyncMock(), conversation_id=10)

        assert result["contact_name"] == "Sofia Ruiz"
        assert result["contact_phone"] == "+595981555001"
        assert result["contact"] is contact

    async def test_without_contact_id_yields_unknown(self):
        conv = _make_conv(contact_id=None, conv_id=10)

        with (
            patch(f"{_CONV_REPO}.get_by_id", new=AsyncMock(return_value=conv)),
            patch(f"{_MSG_REPO}.get_by_conversation", new=AsyncMock(return_value=[])),
            patch(f"{_CONTACT_REPO}.get_by_id", new=AsyncMock()) as mock_get,
        ):
            result = await ConversationService.get_thread(AsyncMock(), conversation_id=10)

        mock_get.assert_not_awaited()
        assert result["contact_name"] == "Desconocido"
        assert result["contact_phone"] == ""
        assert result["contact"] is None
        assert result["window_expired"] is False

    async def test_window_expired_true_when_over_24h(self):
        conv = _make_conv(contact_id=1, conv_id=10)
        # 25 hours ago -- window is expired
        last_msg = datetime.now(timezone.utc) - timedelta(hours=25)
        contact = _make_contact(last_msg_at=last_msg)

        with (
            patch(f"{_CONV_REPO}.get_by_id", new=AsyncMock(return_value=conv)),
            patch(f"{_MSG_REPO}.get_by_conversation", new=AsyncMock(return_value=[])),
            patch(f"{_MSG_REPO}.get_last_inbound_at", new=AsyncMock(return_value=None)),
            patch(f"{_CONTACT_REPO}.get_by_id", new=AsyncMock(return_value=contact)),
        ):
            result = await ConversationService.get_thread(AsyncMock(), conversation_id=10)

        assert result["window_expired"] is True

    async def test_window_expired_false_when_under_24h(self):
        conv = _make_conv(contact_id=1, conv_id=10)
        # 2 hours ago -- window is still open
        last_msg = datetime.now(timezone.utc) - timedelta(hours=2)
        contact = _make_contact(last_msg_at=last_msg)

        with (
            patch(f"{_CONV_REPO}.get_by_id", new=AsyncMock(return_value=conv)),
            patch(f"{_MSG_REPO}.get_by_conversation", new=AsyncMock(return_value=[])),
            patch(f"{_MSG_REPO}.get_last_inbound_at", new=AsyncMock(return_value=None)),
            patch(f"{_CONTACT_REPO}.get_by_id", new=AsyncMock(return_value=contact)),
        ):
            result = await ConversationService.get_thread(AsyncMock(), conversation_id=10)

        assert result["window_expired"] is False

    async def test_window_expired_false_when_just_under_24h(self):
        conv = _make_conv(contact_id=1, conv_id=10)
        # 23h 59m 59s ago -- strictly under 24 hours, window must still be open.
        last_msg = datetime.now(timezone.utc) - timedelta(hours=23, minutes=59, seconds=59)
        contact = _make_contact(last_msg_at=last_msg)

        with (
            patch(f"{_CONV_REPO}.get_by_id", new=AsyncMock(return_value=conv)),
            patch(f"{_MSG_REPO}.get_by_conversation", new=AsyncMock(return_value=[])),
            patch(f"{_MSG_REPO}.get_last_inbound_at", new=AsyncMock(return_value=None)),
            patch(f"{_CONTACT_REPO}.get_by_id", new=AsyncMock(return_value=contact)),
        ):
            result = await ConversationService.get_thread(AsyncMock(), conversation_id=10)

        assert result["window_expired"] is False

    async def test_window_expired_true_when_wa_contact_has_no_last_message(self):
        """WhatsApp contact with last_user_message_at=None and no inbound messages → expired."""
        conv = _make_conv(contact_id=1, conv_id=10)
        conv.channel = 'whatsapp'
        conv.platform = 'whatsapp'
        contact = _make_contact(last_msg_at=None)

        with (
            patch(f"{_CONV_REPO}.get_by_id", new=AsyncMock(return_value=conv)),
            patch(f"{_MSG_REPO}.get_by_conversation", new=AsyncMock(return_value=[])),
            patch(f"{_MSG_REPO}.get_last_inbound_at", new=AsyncMock(return_value=None)),
            patch(f"{_CONTACT_REPO}.get_by_id", new=AsyncMock(return_value=contact)),
        ):
            result = await ConversationService.get_thread(AsyncMock(), conversation_id=10)

        assert result["window_expired"] is True

    async def test_window_not_expired_for_telegram_with_no_last_message(self):
        """Telegram contact with last_user_message_at=None and no inbound messages → not expired."""
        conv = _make_conv(contact_id=1, conv_id=10)
        conv.channel = 'telegram'
        conv.platform = 'telegram'
        contact = _make_contact(last_msg_at=None)

        with (
            patch(f"{_CONV_REPO}.get_by_id", new=AsyncMock(return_value=conv)),
            patch(f"{_MSG_REPO}.get_by_conversation", new=AsyncMock(return_value=[])),
            patch(f"{_MSG_REPO}.get_last_inbound_at", new=AsyncMock(return_value=None)),
            patch(f"{_CONTACT_REPO}.get_by_id", new=AsyncMock(return_value=contact)),
        ):
            result = await ConversationService.get_thread(AsyncMock(), conversation_id=10)

        assert result["window_expired"] is False

    async def test_window_open_when_message_repo_fallback_shows_recent(self):
        """last_user_message_at=None but message_repo shows recent inbound → window open."""
        conv = _make_conv(contact_id=1, conv_id=10)
        conv.channel = 'whatsapp'
        conv.platform = 'whatsapp'
        contact = _make_contact(last_msg_at=None)
        recent_ts = datetime.now(timezone.utc) - timedelta(hours=12)

        with (
            patch(f"{_CONV_REPO}.get_by_id", new=AsyncMock(return_value=conv)),
            patch(f"{_MSG_REPO}.get_by_conversation", new=AsyncMock(return_value=[])),
            patch(f"{_MSG_REPO}.get_last_inbound_at", new=AsyncMock(return_value=recent_ts)),
            patch(f"{_CONTACT_REPO}.get_by_id", new=AsyncMock(return_value=contact)),
        ):
            result = await ConversationService.get_thread(AsyncMock(), conversation_id=10)

        assert result["window_expired"] is False

    async def test_window_expired_when_message_repo_fallback_shows_old(self):
        """last_user_message_at=None and message_repo shows 25h old message → expired."""
        conv = _make_conv(contact_id=1, conv_id=10)
        conv.channel = 'whatsapp'
        conv.platform = 'whatsapp'
        contact = _make_contact(last_msg_at=None)
        old_ts = datetime.now(timezone.utc) - timedelta(hours=25)

        with (
            patch(f"{_CONV_REPO}.get_by_id", new=AsyncMock(return_value=conv)),
            patch(f"{_MSG_REPO}.get_by_conversation", new=AsyncMock(return_value=[])),
            patch(f"{_MSG_REPO}.get_last_inbound_at", new=AsyncMock(return_value=old_ts)),
            patch(f"{_CONTACT_REPO}.get_by_id", new=AsyncMock(return_value=contact)),
        ):
            result = await ConversationService.get_thread(AsyncMock(), conversation_id=10)

        assert result["window_expired"] is True

    async def test_window_open_when_field_stale_but_recent_message_in_table(self):
        """Cuando last_user_message_at es stale (>24h) pero hay un mensaje reciente en messages → ventana abierta."""
        conv = _make_conv(contact_id=1, conv_id=10)
        conv.channel = 'whatsapp'
        conv.platform = 'whatsapp'
        stale_ts = datetime.now(timezone.utc) - timedelta(days=8)  # campo stale tipo N8N
        contact = _make_contact(last_msg_at=stale_ts)
        recent_ts = datetime.now(timezone.utc) - timedelta(hours=1)  # escribió hace 1h

        with (
            patch(f"{_CONV_REPO}.get_by_id", new=AsyncMock(return_value=conv)),
            patch(f"{_MSG_REPO}.get_by_conversation", new=AsyncMock(return_value=[])),
            patch(f"{_MSG_REPO}.get_last_inbound_at", new=AsyncMock(return_value=recent_ts)),
            patch(f"{_CONTACT_REPO}.get_by_id", new=AsyncMock(return_value=contact)),
        ):
            result = await ConversationService.get_thread(AsyncMock(), conversation_id=10)

        assert result["window_expired"] is False

    async def test_window_not_expired_when_attribute_missing(self):
        """Contact object without last_user_message_at attribute at all."""
        conv = _make_conv(contact_id=1, conv_id=10)
        contact = MagicMock(spec=["name", "phone"])  # spec excludes last_user_message_at
        contact.name = "Sin Atributo"
        contact.phone = "+595981000099"

        with (
            patch(f"{_CONV_REPO}.get_by_id", new=AsyncMock(return_value=conv)),
            patch(f"{_MSG_REPO}.get_by_conversation", new=AsyncMock(return_value=[])),
            patch(f"{_CONTACT_REPO}.get_by_id", new=AsyncMock(return_value=contact)),
        ):
            result = await ConversationService.get_thread(AsyncMock(), conversation_id=10)

        assert result["window_expired"] is False

    async def test_messages_list_forwarded_from_repo(self):
        conv = _make_conv(contact_id=1, conv_id=10)
        contact = _make_contact()
        msg_a = MagicMock()
        msg_b = MagicMock()

        with (
            patch(f"{_CONV_REPO}.get_by_id", new=AsyncMock(return_value=conv)),
            patch(f"{_MSG_REPO}.get_by_conversation", new=AsyncMock(return_value=[msg_a, msg_b])),
            patch(f"{_MSG_REPO}.get_last_inbound_at", new=AsyncMock(return_value=None)),
            patch(f"{_CONTACT_REPO}.get_by_id", new=AsyncMock(return_value=contact)),
        ):
            result = await ConversationService.get_thread(AsyncMock(), conversation_id=10)

        assert result["messages"] == [msg_a, msg_b]

    # -----------------------------------------------------------------------
    # contact_phone None-safety (RED phase — documents the bug contract)
    # -----------------------------------------------------------------------

    async def test_get_thread_contact_phone_none_returns_empty_not_none_string(self):
        """When contact exists but contact.phone is None, contact_phone must be ""
        (empty string), never the literal string "None".

        Jinja2 renders Python None as the text "None" which leaks into the UI.
        The service layer is responsible for normalising None → "" before the
        dict reaches any template.
        """
        conv = _make_conv(contact_id=5, conv_id=10)
        # Simulate a DB contact whose phone column is NULL
        contact = _make_contact(name="Sin Telefono", phone=None)

        with (
            patch(f"{_CONV_REPO}.get_by_id", new=AsyncMock(return_value=conv)),
            patch(f"{_MSG_REPO}.get_by_conversation", new=AsyncMock(return_value=[])),
            patch(f"{_MSG_REPO}.get_last_inbound_at", new=AsyncMock(return_value=None)),
            patch(f"{_CONTACT_REPO}.get_by_id", new=AsyncMock(return_value=contact)),
        ):
            result = await ConversationService.get_thread(AsyncMock(), conversation_id=10)

        phone_value = result["contact_phone"]
        # Must NOT be the Python None object rendered as string
        assert phone_value != "None", (
            "contact_phone must not be the string 'None' — Jinja2 renders Python None "
            "as 'None' which leaks into the UI"
        )
        # Must be a safe empty string so the template renders nothing
        assert phone_value == "", (
            f"contact_phone should be '' when contact.phone is None, got {phone_value!r}"
        )

    async def test_get_thread_contact_phone_valid_preserved(self):
        """When contact.phone is a valid E.164 number it must pass through unchanged."""
        conv = _make_conv(contact_id=7, conv_id=20)
        contact = _make_contact(name="Carlos Lopez", phone="+595971788846")

        with (
            patch(f"{_CONV_REPO}.get_by_id", new=AsyncMock(return_value=conv)),
            patch(f"{_MSG_REPO}.get_by_conversation", new=AsyncMock(return_value=[])),
            patch(f"{_MSG_REPO}.get_last_inbound_at", new=AsyncMock(return_value=None)),
            patch(f"{_CONTACT_REPO}.get_by_id", new=AsyncMock(return_value=contact)),
        ):
            result = await ConversationService.get_thread(AsyncMock(), conversation_id=20)

        assert result["contact_phone"] == "+595971788846", (
            "A valid phone number must be preserved exactly as stored"
        )


# ---------------------------------------------------------------------------
# toggle_bot_active — cooldown clear on reactivation
# ---------------------------------------------------------------------------

class TestToggleBotActiveCooldown:
    """toggle_bot_active() must clear last_human_reply_at when reactivating the bot.

    Constraint: when is_bot_active flips False→True (reactivation), the human
    cooldown must be reset so the bot responds immediately.  When flipping
    True→False (deactivation), the timestamp must NOT be touched.
    """

    async def test_reactivate_clears_last_human_reply_at(self):
        """False→True: last_human_reply_at is set to None so cooldown is lifted."""
        from sqlalchemy.ext.asyncio import AsyncSession
        from unittest.mock import AsyncMock, MagicMock, patch
        from sqlalchemy import select
        from app.services.conversation_service import ConversationService

        conv = MagicMock()
        conv.id = 42
        conv.contact_id = 7
        conv.is_bot_active = False  # currently inactive
        conv.last_human_reply_at = datetime.now(timezone.utc)  # cooldown active

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = conv

        db = AsyncMock(spec=AsyncSession)
        db.execute = AsyncMock(return_value=mock_result)

        result = await ConversationService.toggle_bot_active(db, conv_id=42)

        # is_bot_active toggled to True
        assert result is not None
        new_val, contact_id = result
        assert new_val is True
        assert contact_id == 7
        # last_human_reply_at must be cleared so cooldown is lifted
        assert conv.last_human_reply_at is None, (
            "Reactivating the bot must set last_human_reply_at=None "
            "so the 30-min human cooldown is lifted immediately"
        )

    async def test_deactivate_preserves_last_human_reply_at(self):
        """True→False: last_human_reply_at is NOT touched."""
        from sqlalchemy.ext.asyncio import AsyncSession
        from unittest.mock import AsyncMock, MagicMock
        from app.services.conversation_service import ConversationService

        ts = datetime.now(timezone.utc)
        conv = MagicMock()
        conv.id = 43
        conv.contact_id = 8
        conv.is_bot_active = True  # currently active
        conv.last_human_reply_at = ts  # has a timestamp

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = conv

        db = AsyncMock(spec=AsyncSession)
        db.execute = AsyncMock(return_value=mock_result)

        result = await ConversationService.toggle_bot_active(db, conv_id=43)

        new_val, contact_id = result
        assert new_val is False
        assert contact_id == 8
        # timestamp must be untouched on deactivation
        assert conv.last_human_reply_at is ts, (
            "Deactivating the bot must NOT clear last_human_reply_at"
        )

    async def test_reactivate_already_none_stays_none(self):
        """False→True when last_human_reply_at is already None: stays None (no error)."""
        from sqlalchemy.ext.asyncio import AsyncSession
        from unittest.mock import AsyncMock, MagicMock
        from app.services.conversation_service import ConversationService

        conv = MagicMock()
        conv.id = 44
        conv.contact_id = 9
        conv.is_bot_active = False
        conv.last_human_reply_at = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = conv

        db = AsyncMock(spec=AsyncSession)
        db.execute = AsyncMock(return_value=mock_result)

        result = await ConversationService.toggle_bot_active(db, conv_id=44)

        new_val, _ = result
        assert new_val is True
        assert conv.last_human_reply_at is None  # was None, still None — no crash


# ---------------------------------------------------------------------------
# search_conversations
# ---------------------------------------------------------------------------

class TestSearchConversations:
    async def test_delegates_to_repo(self):
        row = _make_conv_with_contact(name="Ana Torres", phone="+595981000001")

        with patch(f"{_CONV_REPO}.search_with_contacts", new=AsyncMock(return_value=[row])) as mock_search:
            db = AsyncMock()
            await ConversationService.search_conversations(db, "Ana")

        mock_search.assert_awaited_once_with(db, "Ana", 50, offset=0, agent_filter=None, channel=None, stuck=False)

    async def test_empty_results(self):
        with patch(f"{_CONV_REPO}.search_with_contacts", new=AsyncMock(return_value=[])):
            result = await ConversationService.search_conversations(AsyncMock(), "nonexistent")

        assert result == []

    async def test_default_limit_50(self):
        with patch(f"{_CONV_REPO}.search_with_contacts", new=AsyncMock(return_value=[])) as mock_search:
            db = AsyncMock()
            await ConversationService.search_conversations(db, "test")

        _, args, kwargs = mock_search.mock_calls[0]
        assert args == (db, "test", 50)
        assert kwargs.get("offset", 0) == 0

    async def test_multiple_results(self):
        row_a = _make_conv_with_contact(conv_id=10, name="Maria Lopez", phone="+595981000010")
        row_b = _make_conv_with_contact(conv_id=11, name="Maria Garcia", phone="+595981000011")

        with patch(f"{_CONV_REPO}.search_with_contacts", new=AsyncMock(return_value=[row_a, row_b])):
            result = await ConversationService.search_conversations(AsyncMock(), "Maria")

        assert len(result) == 2
        names = {r["contact_name"] for r in result}
        assert names == {"Maria Lopez", "Maria Garcia"}

    async def test_search_needs_reply_true_when_inbound(self):
        row = _make_conv_with_contact(
            name="Ana Torres", phone="+595981000001",
            last_message_preview="Quiero info", last_message_direction="inbound",
        )

        with patch(f"{_CONV_REPO}.search_with_contacts", new=AsyncMock(return_value=[row])):
            result = await ConversationService.search_conversations(AsyncMock(), "Ana")

        assert result[0]["needs_reply"] is True

    async def test_search_needs_reply_false_when_outbound(self):
        row = _make_conv_with_contact(
            name="Ana Torres", phone="+595981000001",
            last_message_preview="Le envio info", last_message_direction="outbound",
        )

        with patch(f"{_CONV_REPO}.search_with_contacts", new=AsyncMock(return_value=[row])):
            result = await ConversationService.search_conversations(AsyncMock(), "Ana")

        assert result[0]["needs_reply"] is False


# ---------------------------------------------------------------------------
# Route q-parameter branching (conversation_list endpoint)
# ---------------------------------------------------------------------------

_ROUTE_SVC = "app.routes.conversations.conversation_service"


class TestConversationListRouteQParam:
    """Test the q-parameter branching in the /conversations/list route.

    When q is None, empty, or whitespace-only, get_conversations must be called.
    When q has a real search term, search_conversations must be called instead.
    """

    async def test_q_none_calls_get_not_search(self):
        from app.routes.conversations import conversation_list

        mock_get = AsyncMock(return_value=[])
        mock_search = AsyncMock(return_value=[])

        with (
            patch(f"{_ROUTE_SVC}.get_conversations", mock_get),
            patch(f"{_ROUTE_SVC}.search_conversations", mock_search),
            patch("app.routes.conversations.templates") as mock_templates,
        ):
            mock_templates.TemplateResponse.return_value = MagicMock()
            await conversation_list(
                request=MagicMock(), selected_id=None, q=None,
                user=MagicMock(), db=AsyncMock(),
            )

        mock_get.assert_awaited_once()
        mock_search.assert_not_awaited()

    async def test_q_empty_string_calls_get_not_search(self):
        from app.routes.conversations import conversation_list

        mock_get = AsyncMock(return_value=[])
        mock_search = AsyncMock(return_value=[])

        with (
            patch(f"{_ROUTE_SVC}.get_conversations", mock_get),
            patch(f"{_ROUTE_SVC}.search_conversations", mock_search),
            patch("app.routes.conversations.templates") as mock_templates,
        ):
            mock_templates.TemplateResponse.return_value = MagicMock()
            await conversation_list(
                request=MagicMock(), selected_id=None, q="",
                user=MagicMock(), db=AsyncMock(),
            )

        mock_get.assert_awaited_once()
        mock_search.assert_not_awaited()

    async def test_q_whitespace_calls_get_not_search(self):
        from app.routes.conversations import conversation_list

        mock_get = AsyncMock(return_value=[])
        mock_search = AsyncMock(return_value=[])

        with (
            patch(f"{_ROUTE_SVC}.get_conversations", mock_get),
            patch(f"{_ROUTE_SVC}.search_conversations", mock_search),
            patch("app.routes.conversations.templates") as mock_templates,
        ):
            mock_templates.TemplateResponse.return_value = MagicMock()
            await conversation_list(
                request=MagicMock(), selected_id=None, q="   ",
                user=MagicMock(), db=AsyncMock(),
            )

        mock_get.assert_awaited_once()
        mock_search.assert_not_awaited()

    async def test_q_maria_calls_search_not_get(self):
        from app.routes.conversations import conversation_list

        mock_get = AsyncMock(return_value=[])
        mock_search = AsyncMock(return_value=[])

        with (
            patch(f"{_ROUTE_SVC}.get_conversations", mock_get),
            patch(f"{_ROUTE_SVC}.search_conversations", mock_search),
            patch("app.routes.conversations.templates") as mock_templates,
        ):
            mock_templates.TemplateResponse.return_value = MagicMock()
            await conversation_list(
                request=MagicMock(), selected_id=None, q="maria",
                user=MagicMock(), db=AsyncMock(),
            )

        mock_search.assert_awaited_once()
        mock_get.assert_not_awaited()


# ---------------------------------------------------------------------------
# Route selected_id pass-through (server-side selection highlight)
# ---------------------------------------------------------------------------


class TestConversationListSelectedId:
    """Verify that the /conversations/list route passes selected_id to the
    template context so Jinja2 can render selection classes statically.

    This is the server-side fix for the Idiomorph morph bug: when SSE fires
    and #conv-list re-renders, Alpine-applied :class bindings are lost because
    Idiomorph replaces the DOM.  Baking the selection into the static HTML via
    Jinja2 ensures the highlight survives every morph cycle.
    """

    async def test_selected_id_integer_passed_to_template(self):
        """When selected_id='42' is sent, template receives selected_id=42 (int)."""
        from app.routes.conversations import conversation_list

        mock_get = AsyncMock(return_value=[])

        with (
            patch(f"{_ROUTE_SVC}.get_conversations", mock_get),
            patch("app.routes.conversations.templates") as mock_templates,
        ):
            mock_templates.TemplateResponse.return_value = MagicMock()
            await conversation_list(
                request=MagicMock(), selected_id="42", q=None,
                user=MagicMock(), db=AsyncMock(),
            )

        _, call_kwargs = mock_templates.TemplateResponse.call_args
        context = call_kwargs.get("context") or mock_templates.TemplateResponse.call_args[0][1]
        assert context["selected_id"] == 42
        assert isinstance(context["selected_id"], int)

    async def test_selected_id_none_when_not_provided(self):
        """When selected_id is not sent, template receives selected_id=None."""
        from app.routes.conversations import conversation_list

        mock_get = AsyncMock(return_value=[])

        with (
            patch(f"{_ROUTE_SVC}.get_conversations", mock_get),
            patch("app.routes.conversations.templates") as mock_templates,
        ):
            mock_templates.TemplateResponse.return_value = MagicMock()
            await conversation_list(
                request=MagicMock(), selected_id=None, q=None,
                user=MagicMock(), db=AsyncMock(),
            )

        _, call_kwargs = mock_templates.TemplateResponse.call_args
        context = call_kwargs.get("context") or mock_templates.TemplateResponse.call_args[0][1]
        assert context["selected_id"] is None
