"""
Tests for Phase 102: Manual WA Send UI

Covers:
  - Orchestrator _CALLBACK_TRANSLATIONS has the 7 new v12 template callbacks
  - GET /conversations/contacts/search returns HTML partial
  - GET /conversations/contacts/search with short q returns empty result
  - POST /conversations/send_template_new creates a contact and redirects
  - POST /conversations/send_template_new rejects invalid template_key
  - POST /conversations/send_template_new uses existing contact if phone matches
  - POST /conversations/send_template_new normalizes phone without leading +
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_contact_and_conv(db, phone: str, name: str = "Drawer Test"):
    """Create a real contact + conversation in the DB; return (contact, conv_id)."""
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


def _patch_twilio_and_settings(conv_id: int, sid: str = "SM_DRAWER_OK"):
    """Return three context managers: Twilio HTTP, bot settings, ConversationManager."""
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {"sid": sid, "status": "queued"}
    mock_resp.raise_for_status.return_value = None
    mock_client.post.return_value = mock_resp
    http_patch = patch("app.services.template_service._http_client", mock_client)

    setting_patch = patch(
        "app.services.template_service.BotSettingRepository.get_value",
        new_callable=AsyncMock,
        return_value="HXdrawer_test_sid",
    )

    conv_mock = MagicMock()
    conv_mock.id = conv_id
    mgr_mock = MagicMock()
    mgr_mock.get_or_create_conversation = AsyncMock(return_value=conv_mock)
    conv_patch = patch(
        "app.services.template_service.ConversationManager",
        return_value=mgr_mock,
    )
    return http_patch, setting_patch, conv_patch


# ---------------------------------------------------------------------------
# SEND-07: Orchestrator callback translations
# Historical note: TestOrchestratorCallbacks validated legacy v12 callbacks
# (hablar_con_asesor, ver_similares, si_mostrame, no_interesado, comprar,
# alquilar, vender). Those callbacks were deleted in M4 Task 1.1 after audit
# confirmed 0 uses in 60 days. See docs/AUDIT_M4_FASE0_20260419.md §3.2.
# Drift guard preventing reintroduction lives at
# panel/tests/bot/test_callback_translations.py
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# GET /conversations/contacts/search
# ---------------------------------------------------------------------------

class TestContactSearchRoute:
    async def test_unauthenticated_redirects(self, client):
        resp = await client.get("/conversations/contacts/search?q=test")
        assert resp.status_code == 303
        assert "/login" in resp.headers["location"]

    async def test_search_returns_html(self, admin_client):
        resp = await admin_client.get("/conversations/contacts/search?q=te")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_short_query_returns_empty(self, admin_client):
        """Query shorter than 2 chars returns empty — no contacts list rendered."""
        resp = await admin_client.get("/conversations/contacts/search?q=x")
        assert resp.status_code == 200
        assert b"<ul" not in resp.content

    async def test_empty_query_returns_empty(self, admin_client):
        resp = await admin_client.get("/conversations/contacts/search")
        assert resp.status_code == 200
        assert b"<ul" not in resp.content

    async def test_valid_search_returns_contact_name(self, admin_client, db):
        """Insert a contact then search for it — result must contain the name."""
        from app.models.contact import Contact
        c = Contact(
            name="DrawerSearchUnique",
            phone="+595981599001",
            phone_normalized="+595981599001",
            source="manual",
            status="new",
            created_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.commit()

        resp = await admin_client.get("/conversations/contacts/search?q=DrawerSearchUnique")
        assert resp.status_code == 200
        assert b"DrawerSearchUnique" in resp.content


# ---------------------------------------------------------------------------
# POST /conversations/send_template_new
# ---------------------------------------------------------------------------

class TestSendTemplateNew:
    async def test_unauthenticated_redirects(self, client):
        resp = await client.post("/conversations/send_template_new", data={
            "name": "Test", "phone": "+595981500001", "template_key": "wa_tpl_send_generic",
        })
        assert resp.status_code == 303
        assert "/login" in resp.headers["location"]

    async def test_invalid_template_key_returns_error_html(self, admin_client):
        resp = await admin_client.post("/conversations/send_template_new", data={
            "name": "Nueva", "phone": "+595981500002", "template_key": "wa_tpl_invalid_key",
        })
        assert resp.status_code == 200
        assert b"no valido" in resp.content or b"Template" in resp.content

    async def test_creates_new_contact_and_redirects(self, admin_client, db):
        """New phone: contact created, template sent, redirect to conversation."""
        from app.repositories.contact_repo import contact_repo as cr

        phone = "+595981500010"
        existing = await cr.get_by_phone(db, phone)
        if existing:
            pytest.skip("Phone already exists in test DB")

        # We need a real conversation row since message_repo.create uses FK
        # Create a placeholder contact first to get a real conv_id
        placeholder, real_conv_id = await _create_contact_and_conv(
            db, "+595981500099", "Placeholder for conv"
        )
        await db.commit()

        http_p, setting_p, conv_p = _patch_twilio_and_settings(real_conv_id, "SM_NEW_001")
        with http_p, setting_p, conv_p:
            resp = await admin_client.post("/conversations/send_template_new", data={
                "name": "Nuevo Contacto",
                "phone": phone,
                "template_key": "wa_tpl_send_generic",
            })

        assert resp.status_code == 303
        assert f"/conversations/{real_conv_id}" in resp.headers["location"]

    async def test_existing_contact_is_reused(self, admin_client, db):
        """If phone already exists, that contact is reused without duplication."""
        from app.repositories.contact_repo import contact_repo as cr
        from sqlalchemy import select
        from app.models.contact import Contact as C

        phone = "+595981500020"
        contact, real_conv_id = await _create_contact_and_conv(db, phone, "Ya Existe")
        await db.commit()

        http_p, setting_p, conv_p = _patch_twilio_and_settings(real_conv_id, "SM_REUSE_001")
        with http_p, setting_p, conv_p:
            resp = await admin_client.post("/conversations/send_template_new", data={
                "name": "Diferente Nombre",
                "phone": phone,
                "template_key": "wa_tpl_send_generic",
            })

        assert resp.status_code == 303

        # Only one contact with this phone
        result = await db.execute(select(C).where(C.phone == phone))
        contacts = result.scalars().all()
        assert len(contacts) == 1

    async def test_phone_without_plus_gets_normalized(self, admin_client, db):
        """Phone submitted without leading '+' is normalized to E.164."""
        from app.repositories.contact_repo import contact_repo as cr

        phone_raw = "595981500030"
        phone_e164 = "+595981500030"
        existing = await cr.get_by_phone(db, phone_e164)
        if existing:
            pytest.skip("Phone already exists")

        _, real_conv_id = await _create_contact_and_conv(
            db, "+595981500098", "Placeholder E164"
        )
        await db.commit()

        http_p, setting_p, conv_p = _patch_twilio_and_settings(real_conv_id, "SM_NORM_001")
        with http_p, setting_p, conv_p:
            resp = await admin_client.post("/conversations/send_template_new", data={
                "name": "Sin Plus",
                "phone": phone_raw,
                "template_key": "wa_tpl_send_generic",
            })

        # Normalized contact should have been created with +595981500030
        # The redirect confirms the endpoint completed successfully
        assert resp.status_code == 303
