"""
Integration tests for POST /conversations/send_template_new

Covers:
  1. Happy path — new contact created, Twilio mocked, 303 redirect, message
     saved with intent='manual_template'
  2. Happy path — existing contact reused (no duplicate), same success flow
  3. Invalid template key — route returns error HTML (not 422, see note below)
  4. PLACEHOLDER ContentSid — service raises ValueError, route returns 4xx
     error HTML, no Twilio call made
  5. Missing phone — form without phone field returns 422 Unprocessable Entity

Design notes
------------
* The route (send_template_new) does NOT use Pydantic's SendTemplateRequest
  schema for validation — it validates template_key inline via
  `ALLOWED_TEMPLATE_KEYS`. A missing Form(...) field triggers FastAPI's own
  422 Unprocessable Entity. An invalid template_key returns a rendered
  error_message.html partial (200 with error body).
* All Twilio HTTP calls are patched at app.services.template_service._http_client
  — identical to the pattern used in test_template_service.py.
* The real NullPool test DB (onnix_dev) is used for contact/message
  persistence assertions via the `db` fixture from conftest.py.
* Each test uses a unique phone in the +5959815xxxxx range so the session-
  scoped cleanup_test_data fixture handles teardown without cross-test leakage.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select


# ---------------------------------------------------------------------------
# Phone range reserved for this test file: +5959815[1-9]xxxxx
# Distinct from test_template_service.py (+5959819xxxxx) and
# test_send_template_drawer.py (+5959815[0]xxxxx) to avoid collisions.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_contact_and_conv(db, phone: str, name: str = "SendTplNew Test"):
    """Insert a real contact + conversation into the test DB; return (contact, conv_id)."""
    from app.models.contact import Contact
    from app.models.conversation import Conversation

    c = Contact(
        name=name,
        phone=phone,
        phone_normalized=phone,
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
    return c, conv.id


def _make_twilio_success_response(sid: str = "SM_SNTN_OK") -> MagicMock:
    """Build a mock httpx response that simulates a successful Twilio 201."""
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"sid": sid, "status": "queued"}
    resp.raise_for_status.return_value = None
    return resp


def _patch_stack(conv_id: int, sid: str = "SM_SNTN_OK", content_sid: str = "HXsntn_valid_123"):
    """Return three patchers: Twilio HTTP client, BotSettingRepository, ConversationManager.

    All three must be active simultaneously during route calls so that
    template_service.send_template() completes without real network or DB
    calls to bot_settings.
    """
    mock_client = AsyncMock()
    mock_client.post.return_value = _make_twilio_success_response(sid)
    http_patcher = patch("app.services.template_service._http_client", mock_client)

    setting_patcher = patch(
        "app.services.template_service.BotSettingRepository.get_value",
        new_callable=AsyncMock,
        return_value=content_sid,
    )

    conv_obj = MagicMock()
    conv_obj.id = conv_id
    mgr = MagicMock()
    mgr.get_or_create_conversation = AsyncMock(return_value=conv_obj)
    conv_patcher = patch(
        "app.services.template_service.ConversationManager",
        return_value=mgr,
    )

    return http_patcher, setting_patcher, conv_patcher, mock_client


# ---------------------------------------------------------------------------
# 1. Happy path — new contact
# ---------------------------------------------------------------------------

class TestSendTemplateNewContact:

    async def test_new_contact_created_in_db(self, admin_client, db):
        """A new phone creates exactly one contact row with source='manual'."""
        from app.repositories.contact_repo import contact_repo as cr
        from app.models.contact import Contact

        phone = "+595981510001"
        assert await cr.get_by_phone(db, phone) is None, "Pre-condition: phone must not exist"

        # Create a placeholder contact/conv so ConversationManager mock returns a real conv FK
        _, real_conv_id = await _create_contact_and_conv(db, "+595981519901", "Placeholder 01")
        await db.commit()

        http_p, setting_p, conv_p, _ = _patch_stack(real_conv_id, sid="SM_NEW_CONTACT_01")
        with http_p, setting_p, conv_p:
            resp = await admin_client.post("/conversations/send_template_new", data={
                "name": "Contacto Nuevo",
                "phone": phone,
                "template_key": "wa_tpl_send_generic",
            })

        assert resp.status_code == 303, f"Expected redirect, got {resp.status_code}"

        # Commit the db session used in the test so the insert from the route is visible
        await db.commit()

        result = await db.execute(select(Contact).where(Contact.phone == phone))
        contacts = result.scalars().all()
        assert len(contacts) == 1, "Exactly one contact must be created"
        assert contacts[0].source == "manual"
        assert contacts[0].name == "Contacto Nuevo"

    async def test_new_contact_redirect_points_to_conversation(self, admin_client, db):
        """303 redirect Location header must be /conversations/{conv_id}."""
        from app.repositories.contact_repo import contact_repo as cr

        phone = "+595981510002"
        assert await cr.get_by_phone(db, phone) is None

        _, real_conv_id = await _create_contact_and_conv(db, "+595981519902", "Placeholder 02")
        await db.commit()

        http_p, setting_p, conv_p, _ = _patch_stack(real_conv_id, sid="SM_NEW_REDIR_01")
        with http_p, setting_p, conv_p:
            resp = await admin_client.post("/conversations/send_template_new", data={
                "name": "Redir Test",
                "phone": phone,
                "template_key": "wa_tpl_send_generic",
            })

        assert resp.status_code == 303
        location = resp.headers["location"]
        assert location == f"/conversations/{real_conv_id}", (
            f"Expected /conversations/{real_conv_id}, got {location}"
        )

    async def test_new_contact_message_saved_with_manual_template_intent(self, admin_client, db):
        """The outbound message row must carry intent='manual_template'."""
        from app.repositories.contact_repo import contact_repo as cr
        from app.models.message import Message

        phone = "+595981510003"
        assert await cr.get_by_phone(db, phone) is None

        _, real_conv_id = await _create_contact_and_conv(db, "+595981519903", "Placeholder 03")
        await db.commit()

        unique_sid = "SM_INTENT_CHECK_01"
        http_p, setting_p, conv_p, _ = _patch_stack(
            real_conv_id, sid=unique_sid, content_sid="HXintent_check"
        )
        with http_p, setting_p, conv_p:
            resp = await admin_client.post("/conversations/send_template_new", data={
                "name": "Intent Check",
                "phone": phone,
                "template_key": "wa_tpl_send_generic",
            })

        assert resp.status_code == 303

        await db.commit()

        result = await db.execute(
            select(Message).where(Message.external_id == unique_sid)
        )
        msg = result.scalar_one_or_none()
        assert msg is not None, "Message row not found after send"
        assert msg.intent == "manual_template"
        assert msg.direction == "outbound"
        assert msg.sender_type == "agent"
        assert "Onnix SA" in msg.body

    async def test_twilio_called_exactly_once_for_new_contact(self, admin_client, db):
        """Twilio HTTP POST is invoked exactly once per request."""
        from app.repositories.contact_repo import contact_repo as cr

        phone = "+595981510004"
        assert await cr.get_by_phone(db, phone) is None

        _, real_conv_id = await _create_contact_and_conv(db, "+595981519904", "Placeholder 04")
        await db.commit()

        http_p, setting_p, conv_p, mock_client = _patch_stack(real_conv_id, sid="SM_ONCE_01")
        with http_p, setting_p, conv_p:
            await admin_client.post("/conversations/send_template_new", data={
                "name": "Once Call",
                "phone": phone,
                "template_key": "wa_tpl_send_generic",
            })

        mock_client.post.assert_called_once()

    async def test_conversation_created_for_new_contact(self, admin_client, db):
        """A conversation is created (via get_or_create) and its id is in the redirect."""
        from app.repositories.contact_repo import contact_repo as cr

        phone = "+595981510005"
        assert await cr.get_by_phone(db, phone) is None

        _, real_conv_id = await _create_contact_and_conv(db, "+595981519905", "Placeholder 05")
        await db.commit()

        http_p, setting_p, conv_p, _ = _patch_stack(real_conv_id, sid="SM_CONV_NEW_01")
        with http_p, setting_p, conv_p:
            resp = await admin_client.post("/conversations/send_template_new", data={
                "name": "Conv Create",
                "phone": phone,
                "template_key": "wa_tpl_send_generic",
            })

        assert resp.status_code == 303
        assert str(real_conv_id) in resp.headers["location"]


# ---------------------------------------------------------------------------
# 2. Happy path — existing contact
# ---------------------------------------------------------------------------

class TestSendTemplateExistingContact:

    async def test_existing_contact_not_duplicated(self, admin_client, db):
        """Submitting a phone that already exists reuses the contact row."""
        from app.models.contact import Contact

        phone = "+595981511001"
        existing_contact, real_conv_id = await _create_contact_and_conv(
            db, phone, "Ya Existia"
        )
        await db.commit()

        http_p, setting_p, conv_p, _ = _patch_stack(real_conv_id, sid="SM_REUSE_SNTN_01")
        with http_p, setting_p, conv_p:
            resp = await admin_client.post("/conversations/send_template_new", data={
                "name": "Nombre Diferente",
                "phone": phone,
                "template_key": "wa_tpl_send_generic",
            })

        assert resp.status_code == 303

        await db.commit()

        result = await db.execute(select(Contact).where(Contact.phone == phone))
        all_contacts = result.scalars().all()
        assert len(all_contacts) == 1, "Must not duplicate the contact"
        # The name must not have changed — the existing contact was reused
        assert all_contacts[0].name == "Ya Existia"

    async def test_existing_contact_still_redirects_to_conversation(self, admin_client, db):
        """Even with an existing contact the redirect goes to a valid conversation URL."""
        phone = "+595981511002"
        _, real_conv_id = await _create_contact_and_conv(db, phone, "Existente Redir")
        await db.commit()

        http_p, setting_p, conv_p, _ = _patch_stack(real_conv_id, sid="SM_EXIST_REDIR_01")
        with http_p, setting_p, conv_p:
            resp = await admin_client.post("/conversations/send_template_new", data={
                "name": "No importa",
                "phone": phone,
                "template_key": "wa_tpl_send_generic",
            })

        assert resp.status_code == 303
        location = resp.headers["location"]
        assert location.startswith("/conversations/"), f"Bad location: {location}"

    async def test_existing_contact_message_saved(self, admin_client, db):
        """Sending to an existing contact still persists the outbound message."""
        from app.models.message import Message

        phone = "+595981511003"
        _, real_conv_id = await _create_contact_and_conv(db, phone, "Existente Msg")
        await db.commit()

        unique_sid = "SM_EXIST_MSG_01"
        http_p, setting_p, conv_p, _ = _patch_stack(
            real_conv_id, sid=unique_sid, content_sid="HXexist_msg"
        )
        with http_p, setting_p, conv_p:
            resp = await admin_client.post("/conversations/send_template_new", data={
                "name": "No importa",
                "phone": phone,
                "template_key": "wa_tpl_send_property",
            })

        assert resp.status_code == 303

        await db.commit()

        result = await db.execute(
            select(Message).where(Message.external_id == unique_sid)
        )
        msg = result.scalar_one_or_none()
        assert msg is not None
        assert msg.intent == "manual_template"


# ---------------------------------------------------------------------------
# 3. Invalid template key
# ---------------------------------------------------------------------------

class TestSendTemplateInvalidKey:

    async def test_invalid_key_returns_error_html(self, admin_client):
        """An unrecognized template_key returns HTTP 200 with an error partial."""
        resp = await admin_client.post("/conversations/send_template_new", data={
            "name": "Test",
            "phone": "+595981512001",
            "template_key": "wa_tpl_nonexistent_key",
        })
        # The route renders error_message.html (not a redirect, not a 422)
        assert resp.status_code == 200
        assert b"text/html" in resp.headers["content-type"].encode()
        body = resp.content
        assert b"no valido" in body or b"Template" in body, (
            f"Expected error message in body, got: {body[:300]}"
        )

    async def test_invalid_key_no_twilio_call(self, admin_client):
        """With an invalid key, Twilio must NOT be called."""
        mock_client = AsyncMock()
        with patch("app.services.template_service._http_client", mock_client):
            await admin_client.post("/conversations/send_template_new", data={
                "name": "Test",
                "phone": "+595981512002",
                "template_key": "wa_tpl_bad_key",
            })

        mock_client.post.assert_not_called()

    async def test_empty_template_key_returns_422(self, admin_client):
        """An empty string template_key triggers FastAPI 422 Unprocessable Entity.

        FastAPI's Form(...) with a str type annotation uses Pydantic v2 validation
        which rejects empty strings for required fields — so "" never reaches
        the ALLOWED_TEMPLATE_KEYS guard in the route body; it fails earlier at
        the form-parsing layer with a 422.
        """
        resp = await admin_client.post("/conversations/send_template_new", data={
            "name": "Test",
            "phone": "+595981512003",
            "template_key": "",
        })
        assert resp.status_code == 422

    async def test_all_allowed_keys_pass_validation(self, admin_client, db):
        """Each key in ALLOWED_TEMPLATE_KEYS passes the guard and reaches template_service."""
        from app.schemas.template import ALLOWED_TEMPLATE_KEYS
        from app.repositories.contact_repo import contact_repo as cr

        for idx, key in enumerate(sorted(ALLOWED_TEMPLATE_KEYS)):
            phone = f"+595981512{50 + idx:03d}"
            if await cr.get_by_phone(db, phone) is None:
                _, real_conv_id = await _create_contact_and_conv(
                    db, f"+595981519{80 + idx:03d}", f"AllowedKey Placeholder {idx}"
                )
                await db.commit()

                http_p, setting_p, conv_p, _ = _patch_stack(real_conv_id, sid=f"SM_ALLOWED_{idx}")
                with http_p, setting_p, conv_p:
                    resp = await admin_client.post("/conversations/send_template_new", data={
                        "name": f"Key Test {key}",
                        "phone": phone,
                        "template_key": key,
                    })

                # Should redirect (not the invalid-key error HTML)
                assert resp.status_code == 303, (
                    f"Expected 303 for valid key '{key}', got {resp.status_code}"
                )


# ---------------------------------------------------------------------------
# 4. PLACEHOLDER ContentSid
# ---------------------------------------------------------------------------

class TestSendTemplatePlaceholder:

    async def test_placeholder_returns_error_html(self, admin_client, db):
        """If bot_settings has 'PLACEHOLDER' value, route returns an error partial."""
        phone = "+595981513001"
        _, real_conv_id = await _create_contact_and_conv(db, phone, "Placeholder SID Test")
        await db.commit()

        setting_patcher = patch(
            "app.services.template_service.BotSettingRepository.get_value",
            new_callable=AsyncMock,
            return_value="PLACEHOLDER",
        )
        with setting_patcher:
            resp = await admin_client.post("/conversations/send_template_new", data={
                "name": "Placeholder Test",
                "phone": phone,
                "template_key": "wa_tpl_send_generic",
            })

        # The ValueError from template_service is caught by the route and
        # rendered as error_message.html (HTTP 200 with error body)
        assert resp.status_code == 200, (
            f"Expected 200 error partial, got {resp.status_code}"
        )
        body = resp.content
        assert b"no configurada" in body or b"Template" in body or b"Error" in body, (
            f"Expected PLACEHOLDER error in body, got: {body[:300]}"
        )

    async def test_placeholder_no_twilio_call(self, admin_client, db):
        """When ContentSid is PLACEHOLDER, Twilio HTTP POST must not be invoked."""
        phone = "+595981513002"
        _, real_conv_id = await _create_contact_and_conv(db, phone, "Placeholder No Twilio")
        await db.commit()

        mock_client = AsyncMock()
        http_patcher = patch("app.services.template_service._http_client", mock_client)
        setting_patcher = patch(
            "app.services.template_service.BotSettingRepository.get_value",
            new_callable=AsyncMock,
            return_value="PLACEHOLDER",
        )

        with http_patcher, setting_patcher:
            await admin_client.post("/conversations/send_template_new", data={
                "name": "No Twilio",
                "phone": phone,
                "template_key": "wa_tpl_send_generic",
            })

        mock_client.post.assert_not_called()

    async def test_none_content_sid_returns_error_html(self, admin_client, db):
        """ContentSid=None (unconfigured key) is treated identically to PLACEHOLDER."""
        phone = "+595981513003"
        _, real_conv_id = await _create_contact_and_conv(db, phone, "None SID Test")
        await db.commit()

        setting_patcher = patch(
            "app.services.template_service.BotSettingRepository.get_value",
            new_callable=AsyncMock,
            return_value=None,
        )
        with setting_patcher:
            resp = await admin_client.post("/conversations/send_template_new", data={
                "name": "None SID",
                "phone": phone,
                "template_key": "wa_tpl_send_generic",
            })

        assert resp.status_code == 200
        body = resp.content
        assert b"no configurada" in body or b"Error" in body or b"Template" in body

    async def test_non_hx_sid_returns_error_html(self, admin_client, db):
        """A ContentSid not starting with 'HX' is also rejected by template_service."""
        phone = "+595981513004"
        _, real_conv_id = await _create_contact_and_conv(db, phone, "Bad SID Test")
        await db.commit()

        setting_patcher = patch(
            "app.services.template_service.BotSettingRepository.get_value",
            new_callable=AsyncMock,
            return_value="SM_NOT_A_TEMPLATE_SID",
        )
        with setting_patcher:
            resp = await admin_client.post("/conversations/send_template_new", data={
                "name": "Bad SID",
                "phone": phone,
                "template_key": "wa_tpl_send_generic",
            })

        assert resp.status_code == 200
        body = resp.content
        assert b"no configurada" in body or b"Error" in body or b"Template" in body


# ---------------------------------------------------------------------------
# 5. Missing phone
# ---------------------------------------------------------------------------

class TestSendTemplateMissingPhone:

    async def test_missing_phone_returns_422(self, admin_client):
        """Omitting the phone Form(...) field triggers FastAPI 422 validation."""
        resp = await admin_client.post("/conversations/send_template_new", data={
            "name": "Sin Telefono",
            # phone is intentionally omitted
            "template_key": "wa_tpl_send_generic",
        })
        assert resp.status_code == 422

    async def test_missing_name_returns_422(self, admin_client):
        """Omitting the name Form(...) field also triggers FastAPI 422 validation."""
        resp = await admin_client.post("/conversations/send_template_new", data={
            # name is intentionally omitted
            "phone": "+595981514001",
            "template_key": "wa_tpl_send_generic",
        })
        assert resp.status_code == 422

    async def test_missing_template_key_returns_422(self, admin_client):
        """Omitting template_key Form(...) field triggers FastAPI 422 validation."""
        resp = await admin_client.post("/conversations/send_template_new", data={
            "name": "Sin Template Key",
            "phone": "+595981514002",
            # template_key is intentionally omitted
        })
        assert resp.status_code == 422

    async def test_unauthenticated_request_redirects_to_login(self, client):
        """Without a session cookie the endpoint redirects to /login."""
        resp = await client.post("/conversations/send_template_new", data={
            "name": "Anon",
            "phone": "+595981514003",
            "template_key": "wa_tpl_send_generic",
        })
        assert resp.status_code == 303
        assert "/login" in resp.headers["location"]


# ---------------------------------------------------------------------------
# 6. Phone normalization
# ---------------------------------------------------------------------------

class TestSendTemplatePhoneNormalization:

    async def test_phone_without_plus_is_normalized(self, admin_client, db):
        """Phone submitted without leading '+' is stored with '+' prefix."""
        from app.models.contact import Contact

        raw_phone = "595981515001"
        e164_phone = "+595981515001"

        from app.repositories.contact_repo import contact_repo as cr
        if await cr.get_by_phone(db, e164_phone):
            pytest.skip("Phone already exists in test DB")

        _, real_conv_id = await _create_contact_and_conv(db, "+595981519801", "Norm Placeholder")
        await db.commit()

        http_p, setting_p, conv_p, _ = _patch_stack(real_conv_id, sid="SM_NORM_SNTN_01")
        with http_p, setting_p, conv_p:
            resp = await admin_client.post("/conversations/send_template_new", data={
                "name": "Sin Plus",
                "phone": raw_phone,
                "template_key": "wa_tpl_send_generic",
            })

        assert resp.status_code == 303

        await db.commit()

        result = await db.execute(select(Contact).where(Contact.phone == e164_phone))
        contact = result.scalar_one_or_none()
        assert contact is not None, "Contact with normalized E.164 phone not found"

    async def test_phone_with_extra_plus_is_deduplicated(self, admin_client, db):
        """Phone with leading '+' is stored as-is without double-plus."""
        from app.models.contact import Contact

        phone = "+595981515002"

        from app.repositories.contact_repo import contact_repo as cr
        if await cr.get_by_phone(db, phone):
            pytest.skip("Phone already exists in test DB")

        _, real_conv_id = await _create_contact_and_conv(db, "+595981519802", "Plus Placeholder")
        await db.commit()

        http_p, setting_p, conv_p, _ = _patch_stack(real_conv_id, sid="SM_PLUS_SNTN_01")
        with http_p, setting_p, conv_p:
            resp = await admin_client.post("/conversations/send_template_new", data={
                "name": "Con Plus",
                "phone": phone,
                "template_key": "wa_tpl_send_generic",
            })

        assert resp.status_code == 303

        await db.commit()

        result = await db.execute(select(Contact).where(Contact.phone == phone))
        contacts = result.scalars().all()
        assert len(contacts) == 1
        assert contacts[0].phone == phone
        assert not contacts[0].phone.startswith("++")


# ---------------------------------------------------------------------------
# 7. Consistency fix — source=manual + lead_event audit (Task 3)
# ---------------------------------------------------------------------------

class TestSendTemplateConsistencyFix:
    """Verify fix/consistency-cleanup changes:
    - New contacts get source='manual' (not 'panel')
    - A lead_event of type 'new_contact' is created for new contacts only
    """

    async def test_new_contact_source_is_manual(self, admin_client, db):
        """Contact created via send_template_new must have source='manual'."""
        from app.models.contact import Contact
        from app.repositories.contact_repo import contact_repo as cr

        phone = "+595981516001"
        assert await cr.get_by_phone(db, phone) is None, "Pre-condition: phone must not exist"

        _, real_conv_id = await _create_contact_and_conv(db, "+595981519951", "Consistency Placeholder 01")
        await db.commit()

        http_p, setting_p, conv_p, _ = _patch_stack(real_conv_id, sid="SM_CONSIST_01")
        with http_p, setting_p, conv_p:
            resp = await admin_client.post("/conversations/send_template_new", data={
                "name": "Manual Source Test",
                "phone": phone,
                "template_key": "wa_tpl_send_generic",
            })

        assert resp.status_code == 303
        await db.commit()

        result = await db.execute(select(Contact).where(Contact.phone == phone))
        contact = result.scalar_one_or_none()
        assert contact is not None, "Contact must have been created"
        assert contact.source == "manual", (
            f"Expected source='manual', got source='{contact.source}'"
        )

    async def test_new_contact_lead_event_created(self, admin_client, db):
        """A lead_event of type 'new_contact' is created when a new contact is made."""
        from app.models.contact import Contact
        from app.models.lead_event import LeadEvent
        from app.repositories.contact_repo import contact_repo as cr

        phone = "+595981516002"
        assert await cr.get_by_phone(db, phone) is None, "Pre-condition: phone must not exist"

        _, real_conv_id = await _create_contact_and_conv(db, "+595981519952", "Consistency Placeholder 02")
        await db.commit()

        http_p, setting_p, conv_p, _ = _patch_stack(real_conv_id, sid="SM_CONSIST_02")
        with http_p, setting_p, conv_p:
            resp = await admin_client.post("/conversations/send_template_new", data={
                "name": "Lead Event Test",
                "phone": phone,
                "template_key": "wa_tpl_send_generic",
            })

        assert resp.status_code == 303
        await db.commit()

        contact_result = await db.execute(select(Contact).where(Contact.phone == phone))
        contact = contact_result.scalar_one_or_none()
        assert contact is not None, "Contact must exist before checking lead_event"

        event_result = await db.execute(
            select(LeadEvent).where(
                LeadEvent.contact_id == contact.id,
                LeadEvent.event_type == "new_contact",
            )
        )
        events = event_result.scalars().all()
        assert len(events) == 1, f"Expected 1 new_contact lead_event, found {len(events)}"
        event = events[0]
        assert event.new_status == "new"
        assert event.old_status is None
        assert event.triggered_by.startswith("user:"), (
            f"triggered_by must start with 'user:', got '{event.triggered_by}'"
        )

    async def test_existing_contact_no_lead_event_created(self, admin_client, db):
        """No lead_event is created when sending to an already-existing contact."""
        from app.models.lead_event import LeadEvent

        phone = "+595981516003"
        existing_contact, real_conv_id = await _create_contact_and_conv(
            db, phone, "Already Exists Lead Event"
        )
        await db.commit()

        http_p, setting_p, conv_p, _ = _patch_stack(real_conv_id, sid="SM_CONSIST_03")
        with http_p, setting_p, conv_p:
            resp = await admin_client.post("/conversations/send_template_new", data={
                "name": "Ignored Name",
                "phone": phone,
                "template_key": "wa_tpl_send_generic",
            })

        assert resp.status_code == 303
        await db.commit()

        event_result = await db.execute(
            select(LeadEvent).where(
                LeadEvent.contact_id == existing_contact.id,
                LeadEvent.event_type == "new_contact",
            )
        )
        events = event_result.scalars().all()
        assert len(events) == 0, (
            f"Expected 0 new_contact lead_events for existing contact, found {len(events)}"
        )
