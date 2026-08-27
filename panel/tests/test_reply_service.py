"""
Tests for app/services/reply_service.py

Uses unittest.mock to patch the module-level _http_client so no real
Twilio/Telegram API requests are made.  The DB session is the live test
NullPool session from conftest so we exercise the real repository layer.

Covers:
  - WhatsApp (Twilio) send success path
  - Telegram send success path
  - Missing conversation raises ValueError
  - Contact without phone raises ValueError
  - HTTP error from Twilio propagates as httpx.HTTPStatusError
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from app.services.reply_service import ReplyService


# ---------------------------------------------------------------------------
# Helpers — build lightweight mock objects
# ---------------------------------------------------------------------------

def _make_response(status_code: int, json_data: dict) -> MagicMock:
    """Return a mock httpx.Response-like object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _patch_http_client(mock_resp):
    """Return a patch context manager for the module-level _http_client."""
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    return patch("app.services.reply_service._http_client", mock_client), mock_client


# ---------------------------------------------------------------------------
# Unit tests for _send_twilio_whatsapp
# ---------------------------------------------------------------------------

class TestSendTwilioWhatsApp:
    async def test_success_returns_dict_with_sid(self):
        mock_resp = _make_response(201, {"sid": "SM123", "status": "queued"})
        patcher, mock_client = _patch_http_client(mock_resp)

        with patcher:
            result = await ReplyService._send_twilio_whatsapp("+595981000111", "Hola")

        assert result["sid"] == "SM123"
        assert result["status"] == "queued"

    async def test_phone_gets_whatsapp_prefix(self):
        """Bare phone without whatsapp: prefix must be prefixed before posting."""
        mock_resp = _make_response(201, {"sid": "SM_PREFIX", "status": "queued"})
        patcher, mock_client = _patch_http_client(mock_resp)

        with patcher:
            await ReplyService._send_twilio_whatsapp("+595981000222", "Test prefix")

        post_data = mock_client.post.call_args[1].get("data", {})
        assert post_data.get("To", "").startswith("whatsapp:")

    async def test_already_prefixed_phone_not_double_prefixed(self):
        mock_resp = _make_response(201, {"sid": "SM_NDUP", "status": "queued"})
        patcher, mock_client = _patch_http_client(mock_resp)

        with patcher:
            await ReplyService._send_twilio_whatsapp("whatsapp:+595981000333", "No dup")

        post_data = mock_client.post.call_args[1].get("data", {})
        to_val = post_data.get("To", "")
        assert to_val.count("whatsapp:") == 1

    async def test_http_error_propagates(self):
        mock_resp = _make_response(401, {"message": "Unauthorized"})
        patcher, _ = _patch_http_client(mock_resp)

        with patcher:
            with pytest.raises(httpx.HTTPStatusError):
                await ReplyService._send_twilio_whatsapp("+595981000444", "Error test")


# ---------------------------------------------------------------------------
# Unit tests for _send_telegram
# ---------------------------------------------------------------------------

class TestSendTelegram:
    async def test_success_returns_message_id(self):
        mock_resp = _make_response(200, {"ok": True, "result": {"message_id": 42}})
        patcher, _ = _patch_http_client(mock_resp)

        with patcher:
            result = await ReplyService._send_telegram("12345678", "Hola Telegram")

        assert result["message_id"] == 42

    async def test_telegram_ok_false_raises_value_error(self):
        mock_resp = _make_response(200, {"ok": False, "description": "Chat not found"})
        patcher, _ = _patch_http_client(mock_resp)

        with patcher:
            with pytest.raises(ValueError, match="Telegram API error"):
                await ReplyService._send_telegram("99999", "Bad chat")

    async def test_http_error_propagates(self):
        mock_resp = _make_response(403, {})
        patcher, _ = _patch_http_client(mock_resp)

        with patcher:
            with pytest.raises(httpx.HTTPStatusError):
                await ReplyService._send_telegram("12345", "Forbidden")


# ---------------------------------------------------------------------------
# Integration tests for send_reply (uses real DB, mocked HTTP)
# ---------------------------------------------------------------------------

class TestSendReply:
    async def test_missing_conversation_raises_value_error(self, db):
        with pytest.raises(ValueError, match="Conversacion no encontrada"):
            await ReplyService.send_reply(
                db=db,
                conversation_id=999999,
                message_text="Hello",
                user_id=1,
            )

    async def test_whatsapp_send_success_returns_message(self, db):
        """Create a real contact + conversation in dev DB, then mock Twilio."""
        from app.models.contact import Contact
        from app.models.conversation import Conversation
        from datetime import datetime, timezone, timedelta

        # Insert contact
        c = Contact(
            name="Reply WA Test",
            phone="+595981888001",
            phone_normalized="+595981888001",
            source="manual",
            status="new",
            last_user_message_at=datetime.now(timezone.utc) - timedelta(hours=1),
            created_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.flush()

        # Insert conversation
        conv = Conversation(
            contact_id=c.id,
            status="active",
            channel="whatsapp",
            platform="whatsapp",
            message_count=0,
            created_at=datetime.now(timezone.utc),
        )
        db.add(conv)
        await db.flush()

        mock_resp = _make_response(201, {"sid": "SM_TEST_WA", "status": "queued"})
        patcher, _ = _patch_http_client(mock_resp)

        with patcher:
            result = await ReplyService.send_reply(
                db=db,
                conversation_id=conv.id,
                message_text="Test WA reply",
                user_id=1,
            )

        assert result["message"] is not None
        assert result["message"].body == "Test WA reply"
        assert result["message"].direction == "outbound"

    async def test_telegram_send_success_returns_message(self, db):
        """Create a telegram conversation and mock Telegram API."""
        from app.models.contact import Contact
        from app.models.conversation import Conversation
        from datetime import datetime, timezone, timedelta

        c = Contact(
            name="Reply TG Test",
            phone="+595981888002",
            phone_normalized="+595981888002",
            source="manual",
            status="new",
            last_user_message_at=datetime.now(timezone.utc) - timedelta(hours=1),
            created_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.flush()

        conv = Conversation(
            contact_id=c.id,
            status="active",
            channel="telegram",
            platform="telegram",
            platform_chat_id="987654321",
            message_count=0,
            created_at=datetime.now(timezone.utc),
        )
        db.add(conv)
        await db.flush()

        mock_resp = _make_response(200, {"ok": True, "result": {"message_id": 99}})
        patcher, _ = _patch_http_client(mock_resp)

        with patcher:
            result = await ReplyService.send_reply(
                db=db,
                conversation_id=conv.id,
                message_text="Test TG reply",
                user_id=1,
            )

        assert result["message"].body == "Test TG reply"

    async def test_discarded_contact_raises_value_error(self, db):
        """Discarded contacts must not receive messages."""
        from app.models.contact import Contact
        from app.models.conversation import Conversation
        from datetime import datetime, timezone

        c = Contact(
            name="Discarded Contact",
            phone="+595981888003",
            phone_normalized="+595981888003",
            source="manual",
            status="discarded",
            created_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.flush()

        conv = Conversation(
            contact_id=c.id,
            status="active",
            channel="whatsapp",
            platform="whatsapp",
            message_count=0,
            created_at=datetime.now(timezone.utc),
        )
        db.add(conv)
        await db.flush()

        with pytest.raises(ValueError, match="descartado"):
            await ReplyService.send_reply(
                db=db,
                conversation_id=conv.id,
                message_text="Should fail",
                user_id=1,
            )

    async def test_baja_at_contact_raises_value_error(self, db):
        """Contact with baja_at set (opt-out) must raise ValueError even when status != 'discarded'.

        Uses last_user_message_at within the 24h window so only the baja_at
        guard is exercised — not the window check.
        """
        from app.models.contact import Contact
        from app.models.conversation import Conversation
        from datetime import datetime, timezone, timedelta

        c = Contact(
            name="Opt-Out Contact",
            phone="+595981888030",
            phone_normalized="+595981888030",
            source="manual",
            status="new",  # NOT discarded — the edge case
            baja_at=datetime.now(timezone.utc),
            last_user_message_at=datetime.now(timezone.utc) - timedelta(hours=1),
            created_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.flush()

        conv = Conversation(
            contact_id=c.id,
            status="active",
            channel="whatsapp",
            platform="whatsapp",
            message_count=0,
            created_at=datetime.now(timezone.utc),
        )
        db.add(conv)
        await db.flush()

        with pytest.raises(ValueError, match="(?i)(opt-out|baja)"):
            await ReplyService.send_reply(
                db=db,
                conversation_id=conv.id,
                message_text="Should fail due to opt-out",
                user_id=1,
            )

    async def test_baja_at_contact_does_not_send_or_auto_advance(self, db):
        """When baja_at is set, no message must be saved and status must not change.

        Uses last_user_message_at within the 24h window so the baja_at guard
        fires before any send or auto-advance logic is reached.
        """
        from app.models.contact import Contact
        from app.models.conversation import Conversation
        from app.repositories.message_repo import message_repo as msg_repo
        from datetime import datetime, timezone, timedelta

        c = Contact(
            name="Opt-Out No Send",
            phone="+595981888031",
            phone_normalized="+595981888031",
            source="manual",
            status="new",  # NOT discarded — the edge case
            baja_at=datetime.now(timezone.utc),
            last_user_message_at=datetime.now(timezone.utc) - timedelta(hours=1),
            created_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.flush()

        conv = Conversation(
            contact_id=c.id,
            status="active",
            channel="whatsapp",
            platform="whatsapp",
            message_count=0,
            created_at=datetime.now(timezone.utc),
        )
        db.add(conv)
        await db.flush()

        with pytest.raises(ValueError, match="(?i)(opt-out|baja)"):
            await ReplyService.send_reply(
                db=db,
                conversation_id=conv.id,
                message_text="Should not be saved",
                user_id=1,
            )

        # No outbound message must have been persisted
        messages = await msg_repo.get_by_conversation(db, conv.id)
        assert messages == [] or all(m.direction != "outbound" for m in messages)

        # Status must remain "new" — no auto-advance occurred
        await db.refresh(c)
        assert c.status == "new"

    async def test_contact_without_phone_raises_value_error(self, db):
        """Contact with no phone must raise ValueError before sending."""
        from app.models.contact import Contact
        from app.models.conversation import Conversation
        from datetime import datetime, timezone

        c = Contact(
            name="No Phone Contact",
            phone=None,
            source="manual",
            status="new",
            created_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.flush()

        conv = Conversation(
            contact_id=c.id,
            status="active",
            channel="whatsapp",
            platform="whatsapp",
            message_count=0,
            created_at=datetime.now(timezone.utc),
        )
        db.add(conv)
        await db.flush()

        with pytest.raises(ValueError, match="tel"):
            await ReplyService.send_reply(
                db=db,
                conversation_id=conv.id,
                message_text="No phone",
                user_id=1,
            )

    async def test_window_blocked_when_no_last_message_at(self, db):
        """WhatsApp send must be BLOCKED when contact has no last_user_message_at."""
        from app.models.contact import Contact
        from app.models.conversation import Conversation
        from datetime import datetime, timezone

        c = Contact(
            name="Warning Test",
            phone="+595981888004",
            phone_normalized="+595981888004",
            source="manual",
            status="new",
            last_user_message_at=None,
            created_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.flush()

        conv = Conversation(
            contact_id=c.id,
            status="active",
            channel="whatsapp",
            platform="whatsapp",
            message_count=0,
            created_at=datetime.now(timezone.utc),
        )
        db.add(conv)
        await db.flush()

        with pytest.raises(ValueError, match="ventana"):
            await ReplyService.send_reply(
                db=db,
                conversation_id=conv.id,
                message_text="Warning check",
                user_id=1,
            )

    async def test_auto_status_new_to_agent_replied(self, db):
        """Contacts in 'new' status must be auto-updated to 'agent_replied' on reply."""
        from app.models.contact import Contact
        from app.models.conversation import Conversation
        from app.repositories.contact_repo import contact_repo
        from datetime import datetime, timezone, timedelta

        c = Contact(
            name="Auto Status Test",
            phone="+595981888005",
            phone_normalized="+595981888005",
            source="manual",
            status="new",
            last_user_message_at=datetime.now(timezone.utc) - timedelta(hours=2),
            created_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.flush()
        contact_id = c.id

        conv = Conversation(
            contact_id=contact_id,
            status="active",
            channel="whatsapp",
            platform="whatsapp",
            message_count=0,
            created_at=datetime.now(timezone.utc),
        )
        db.add(conv)
        await db.flush()

        mock_resp = _make_response(201, {"sid": "SM_AUTO", "status": "queued"})
        patcher, _ = _patch_http_client(mock_resp)

        with patcher:
            await ReplyService.send_reply(
                db=db,
                conversation_id=conv.id,
                message_text="Auto status",
                user_id=1,
            )

        refreshed = await contact_repo.get_by_id(db, contact_id)
        assert refreshed.status == "agent_replied"

    async def test_whatsapp_blocked_when_window_expired(self, db):
        """WhatsApp send must be BLOCKED when last message from user was over 24h ago."""
        from app.models.contact import Contact
        from app.models.conversation import Conversation
        from datetime import datetime, timezone, timedelta

        c = Contact(
            name="Expired Window",
            phone="+595981888010",
            phone_normalized="+595981888010",
            source="manual",
            status="new",
            last_user_message_at=datetime.now(timezone.utc) - timedelta(hours=25),
            created_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.flush()

        conv = Conversation(
            contact_id=c.id,
            status="active",
            channel="whatsapp",
            platform="whatsapp",
            message_count=0,
            created_at=datetime.now(timezone.utc),
        )
        db.add(conv)
        await db.flush()

        with pytest.raises(ValueError, match="ventana"):
            await ReplyService.send_reply(
                db=db,
                conversation_id=conv.id,
                message_text="Should be blocked",
                user_id=1,
            )

    async def test_whatsapp_not_blocked_when_window_open(self, db):
        """WhatsApp send must succeed when last message from user was under 24h ago."""
        from app.models.contact import Contact
        from app.models.conversation import Conversation
        from datetime import datetime, timezone, timedelta

        c = Contact(
            name="Open Window",
            phone="+595981888011",
            phone_normalized="+595981888011",
            source="manual",
            status="new",
            last_user_message_at=datetime.now(timezone.utc) - timedelta(hours=12),
            created_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.flush()

        conv = Conversation(
            contact_id=c.id,
            status="active",
            channel="whatsapp",
            platform="whatsapp",
            message_count=0,
            created_at=datetime.now(timezone.utc),
        )
        db.add(conv)
        await db.flush()

        mock_resp = _make_response(201, {"sid": "SM_OPEN_WIN", "status": "queued"})
        patcher, _ = _patch_http_client(mock_resp)
        with patcher:
            result = await ReplyService.send_reply(
                db=db,
                conversation_id=conv.id,
                message_text="Should send",
                user_id=1,
            )

        assert result["message"] is not None

    async def test_telegram_not_blocked_when_no_last_message(self, db):
        """Telegram must NOT be blocked by the 24h WhatsApp window rule."""
        from app.models.contact import Contact
        from app.models.conversation import Conversation
        from datetime import datetime, timezone

        c = Contact(
            name="TG No Window",
            phone="+595981888012",
            phone_normalized="+595981888012",
            source="manual",
            status="new",
            last_user_message_at=None,  # No WA session at all
            created_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.flush()

        conv = Conversation(
            contact_id=c.id,
            status="active",
            channel="telegram",
            platform="telegram",
            platform_chat_id="111222333",
            message_count=0,
            created_at=datetime.now(timezone.utc),
        )
        db.add(conv)
        await db.flush()

        mock_resp = _make_response(200, {"ok": True, "result": {"message_id": 77}})
        patcher, _ = _patch_http_client(mock_resp)
        with patcher:
            result = await ReplyService.send_reply(
                db=db,
                conversation_id=conv.id,
                message_text="Telegram test",
                user_id=1,
            )

        assert result["message"] is not None

    async def test_whatsapp_blocked_when_field_stale_but_no_recent_message(self, db):
        """Campo last_user_message_at stale y NO hay mensaje reciente en tabla → bloquea."""
        from app.models.contact import Contact
        from app.models.conversation import Conversation
        from datetime import datetime, timezone, timedelta

        c = Contact(
            name="Stale Field Test",
            phone="+595981888020",
            phone_normalized="+595981888020",
            source="manual",
            status="new",
            last_user_message_at=datetime.now(timezone.utc) - timedelta(days=8),
            created_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.flush()

        conv = Conversation(
            contact_id=c.id,
            status="active",
            channel="whatsapp",
            platform="whatsapp",
            message_count=0,
            created_at=datetime.now(timezone.utc),
        )
        db.add(conv)
        await db.flush()

        # No messages inserted → get_last_inbound_at returns None → falls back to stale field
        with pytest.raises(ValueError, match="ventana"):
            await ReplyService.send_reply(
                db=db,
                conversation_id=conv.id,
                message_text="Blocked",
                user_id=1,
            )


# ===========================================================================
# M2.F8 — is_bot_active consistency
# ===========================================================================


class TestSendReplyDisablesBot:
    """M2.F8: un reply manual desactiva el bot para esa conversación.

    Sin esto, `contact.status` puede quedar en "agent_replied" pero el gate
    del orchestrator (que lee `is_bot_active`) permite al bot responder
    encima del asesor, creando mensajes contradictorios.
    """

    async def test_send_reply_sets_is_bot_active_false(self, db):
        """Después de enviar reply, conv.is_bot_active queda en False."""
        from app.models.contact import Contact
        from app.models.conversation import Conversation
        from datetime import datetime, timezone, timedelta

        c = Contact(
            name="Reply Disable Bot Test",
            phone="+595981888040",
            phone_normalized="+595981888040",
            source="manual",
            status="new",
            last_user_message_at=datetime.now(timezone.utc) - timedelta(hours=1),
            created_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.flush()

        conv = Conversation(
            contact_id=c.id,
            status="active",
            channel="whatsapp",
            platform="whatsapp",
            message_count=0,
            is_bot_active=True,  # default — bot activo antes del reply
            created_at=datetime.now(timezone.utc),
        )
        db.add(conv)
        await db.flush()

        mock_resp = _make_response(201, {"sid": "SM_DISABLE", "status": "queued"})
        patcher, _ = _patch_http_client(mock_resp)

        with patcher:
            await ReplyService.send_reply(
                db=db,
                conversation_id=conv.id,
                message_text="Agent manual reply",
                user_id=1,
            )

        await db.refresh(conv)
        assert conv.is_bot_active is False, (
            "Expected is_bot_active=False after manual agent reply"
        )

    async def test_send_reply_disables_bot_idempotent(self, db):
        """Llamar send_reply sobre conv con is_bot_active ya False no falla."""
        from app.models.contact import Contact
        from app.models.conversation import Conversation
        from datetime import datetime, timezone, timedelta

        c = Contact(
            name="Reply Idempotent Test",
            phone="+595981888041",
            phone_normalized="+595981888041",
            source="manual",
            status="agent_replied",
            last_user_message_at=datetime.now(timezone.utc) - timedelta(hours=1),
            created_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.flush()

        conv = Conversation(
            contact_id=c.id,
            status="active",
            channel="whatsapp",
            platform="whatsapp",
            message_count=0,
            is_bot_active=False,  # ya estaba desactivado
            created_at=datetime.now(timezone.utc),
        )
        db.add(conv)
        await db.flush()

        mock_resp = _make_response(201, {"sid": "SM_IDEMPOTENT", "status": "queued"})
        patcher, _ = _patch_http_client(mock_resp)

        with patcher:
            # No debe fallar — segundo reply sobre conv ya disabled
            await ReplyService.send_reply(
                db=db,
                conversation_id=conv.id,
                message_text="Second agent reply",
                user_id=1,
            )

        await db.refresh(conv)
        assert conv.is_bot_active is False
