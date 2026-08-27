"""
Tests for app/services/template_service.py

Uses unittest.mock to patch the module-level _http_client so no real
Twilio API requests are made.  The DB session is the live test NullPool
session from conftest so we exercise the real repository layer.

Covers:
  - Happy path: template sent, message saved, conversation created
  - Contact not found raises ValueError
  - Contact without phone raises ValueError
  - Discarded contact raises ValueError
  - Template not configured (None) raises ValueError
  - Template PLACEHOLDER rejected
  - Template invalid SID (not starting with HX) rejected
  - Twilio HTTP error propagates as httpx.HTTPStatusError
  - Auto-status new -> agent_replied
  - No Body field in Twilio POST data
  - created_at captured before Twilio call
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
import httpx

from app.services.template_service import TemplateService


# ---------------------------------------------------------------------------
# Helpers
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
    """Return a patch context manager for the shared _http_client."""
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    return patch("app.services.template_service._http_client", mock_client), mock_client


def _patch_bot_setting(return_value):
    """Patch BotSettingRepository.get_value to return a fixed value."""
    return patch(
        "app.services.template_service.BotSettingRepository.get_value",
        new_callable=AsyncMock,
        return_value=return_value,
    )


def _patch_conv_manager(conv_id: int):
    """Patch ConversationManager to return a mock with the given conv_id."""
    conv_info = MagicMock()
    conv_info.id = conv_id
    mock_mgr = MagicMock()
    mock_mgr.get_or_create_conversation = AsyncMock(return_value=conv_info)
    mock_mgr.get_search_context = AsyncMock(return_value=MagicMock())
    mock_mgr.update_search_context = AsyncMock()
    return patch(
        "app.services.template_service.ConversationManager",
        return_value=mock_mgr,
    ), mock_mgr


# ---------------------------------------------------------------------------
# Helper to create test contact + conversation in DB
# ---------------------------------------------------------------------------

async def _create_contact(db, phone="+595981900001", status="new", name="Template Test", property_id=None):
    from app.models.contact import Contact
    c = Contact(
        name=name,
        phone=phone,
        phone_normalized=phone,
        source="manual",
        status=status,
        property_id=property_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(c)
    await db.flush()
    return c


async def _create_property(db, title="Casa en venta", city="Asuncion", price_usd=None):
    from app.models.property import Property
    from decimal import Decimal
    p = Property(
        source="manual",
        external_id=f"test-{id(title)}",
        title=title,
        city=city,
        price_usd=Decimal(str(price_usd)) if price_usd else None,
        is_active=True,
    )
    db.add(p)
    await db.flush()
    return p


async def _create_conversation(db, contact_id: int) -> int:
    """Create a real conversation row in the DB and return its ID."""
    from app.models.conversation import Conversation
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
    return conv.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTemplateService:

    async def test_send_template_success(self, db):
        """Happy path: template sent, message saved, conversation created."""
        contact = await _create_contact(db, phone="+595981900001")
        conv_id = await _create_conversation(db, contact.id)

        mock_resp = _make_response(201, {"sid": "SM_TPL_001", "status": "queued"})
        http_patcher, mock_client = _patch_http_client(mock_resp)
        setting_patcher = _patch_bot_setting("HX1234567890abcdef")
        conv_patcher, _ = _patch_conv_manager(conv_id)

        with http_patcher, setting_patcher, conv_patcher:
            result = await TemplateService.send_template(
                db=db,
                contact_id=contact.id,
                template_key="wa_tpl_send_generic",
            )

        assert result["conversation_id"] == conv_id
        mock_client.post.assert_called_once()

    async def test_contact_not_found_raises(self, db):
        """Non-existent contact_id raises ValueError."""
        with pytest.raises(ValueError, match="Contacto no encontrado"):
            await TemplateService.send_template(
                db=db,
                contact_id=999999,
                template_key="wa_tpl_send_generic",
            )

    async def test_contact_no_phone_raises(self, db):
        """Contact without phone raises ValueError."""
        from app.models.contact import Contact
        c = Contact(
            name="No Phone",
            phone=None,
            source="manual",
            status="new",
            created_at=datetime.now(timezone.utc),
        )
        db.add(c)
        await db.flush()

        with pytest.raises(ValueError, match="sin telefono"):
            await TemplateService.send_template(
                db=db,
                contact_id=c.id,
                template_key="wa_tpl_send_generic",
            )

    async def test_contact_discarded_raises(self, db):
        """Discarded contact raises ValueError."""
        contact = await _create_contact(db, phone="+595981900002", status="discarded")

        with pytest.raises(ValueError, match="descartado"):
            await TemplateService.send_template(
                db=db,
                contact_id=contact.id,
                template_key="wa_tpl_send_generic",
            )

    async def test_template_not_configured_raises(self, db):
        """Template key returning None from bot_settings raises ValueError."""
        contact = await _create_contact(db, phone="+595981900003")

        setting_patcher = _patch_bot_setting(None)
        with setting_patcher:
            with pytest.raises(ValueError, match="no configurada"):
                await TemplateService.send_template(
                    db=db,
                    contact_id=contact.id,
                    template_key="wa_tpl_send_generic",
                )

    async def test_template_placeholder_rejected(self, db):
        """Template with value 'PLACEHOLDER' is rejected."""
        contact = await _create_contact(db, phone="+595981900004")

        setting_patcher = _patch_bot_setting("PLACEHOLDER")
        with setting_patcher:
            with pytest.raises(ValueError, match="no configurada"):
                await TemplateService.send_template(
                    db=db,
                    contact_id=contact.id,
                    template_key="wa_tpl_send_generic",
                )

    async def test_template_invalid_sid_rejected(self, db):
        """Template SID not starting with HX is rejected."""
        contact = await _create_contact(db, phone="+595981900005")

        setting_patcher = _patch_bot_setting("SM_NOT_A_TEMPLATE")
        with setting_patcher:
            with pytest.raises(ValueError, match="no configurada"):
                await TemplateService.send_template(
                    db=db,
                    contact_id=contact.id,
                    template_key="wa_tpl_send_generic",
                )

    async def test_twilio_error_propagates(self, db):
        """HTTP error from Twilio propagates as httpx.HTTPStatusError."""
        contact = await _create_contact(db, phone="+595981900006")
        conv_id = await _create_conversation(db, contact.id)

        mock_resp = _make_response(401, {"message": "Unauthorized"})
        http_patcher, _ = _patch_http_client(mock_resp)
        setting_patcher = _patch_bot_setting("HXvalid123")
        conv_patcher, _ = _patch_conv_manager(conv_id)

        with http_patcher, setting_patcher, conv_patcher:
            with pytest.raises(httpx.HTTPStatusError):
                await TemplateService.send_template(
                    db=db,
                    contact_id=contact.id,
                    template_key="wa_tpl_send_generic",
                )

    async def test_auto_status_new_to_agent_replied(self, db):
        """Contact in 'new' status auto-updates to 'agent_replied' after send."""
        contact = await _create_contact(db, phone="+595981900007", status="new")
        conv_id = await _create_conversation(db, contact.id)

        mock_resp = _make_response(201, {"sid": "SM_AUTO_TPL", "status": "queued"})
        http_patcher, _ = _patch_http_client(mock_resp)
        setting_patcher = _patch_bot_setting("HXauto123")
        conv_patcher, _ = _patch_conv_manager(conv_id)

        with http_patcher, setting_patcher, conv_patcher:
            await TemplateService.send_template(
                db=db,
                contact_id=contact.id,
                template_key="wa_tpl_send_generic",
            )

        from app.repositories.contact_repo import contact_repo
        refreshed = await contact_repo.get_by_id(db, contact.id)
        assert refreshed.status == "agent_replied"

    async def test_no_body_field_in_twilio_call(self, db):
        """Twilio POST must NOT contain a Body field -- only ContentSid."""
        contact = await _create_contact(db, phone="+595981900008")
        conv_id = await _create_conversation(db, contact.id)

        mock_resp = _make_response(201, {"sid": "SM_NOBODY", "status": "queued"})
        http_patcher, mock_client = _patch_http_client(mock_resp)
        setting_patcher = _patch_bot_setting("HXnobody123")
        conv_patcher, _ = _patch_conv_manager(conv_id)

        with http_patcher, setting_patcher, conv_patcher:
            await TemplateService.send_template(
                db=db,
                contact_id=contact.id,
                template_key="wa_tpl_send_generic",
            )

        # Inspect the data dict passed to httpx.post
        call_kwargs = mock_client.post.call_args[1]
        post_data = call_kwargs.get("data", {})
        assert "Body" not in post_data, "Twilio POST must not include Body field"
        assert "ContentSid" in post_data
        assert post_data["ContentSid"] == "HXnobody123"

    async def test_created_at_before_twilio_call(self, db):
        """sent_at timestamp must be captured BEFORE the Twilio call."""
        contact = await _create_contact(db, phone="+595981900009")
        conv_id = await _create_conversation(db, contact.id)

        before_call = datetime.now(timezone.utc)

        mock_resp = _make_response(201, {"sid": "SM_TIME", "status": "queued"})
        http_patcher, _ = _patch_http_client(mock_resp)
        setting_patcher = _patch_bot_setting("HXtime123")
        conv_patcher, _ = _patch_conv_manager(conv_id)

        with http_patcher, setting_patcher, conv_patcher:
            await TemplateService.send_template(
                db=db,
                contact_id=contact.id,
                template_key="wa_tpl_send_generic",
            )

        after_call = datetime.now(timezone.utc)

        # Verify the message was persisted with created_at between before and after
        from sqlalchemy import select
        from app.models.message import Message
        result = await db.execute(
            select(Message)
            .where(Message.external_id == "SM_TIME")
        )
        msg = result.scalar_one()
        assert msg.created_at is not None
        # The created_at should be >= before_call (captured before Twilio)
        # and <= after_call
        assert msg.created_at >= before_call
        assert msg.created_at <= after_call
        assert msg.intent == "manual_template"

    # -----------------------------------------------------------------------
    # Property ContentVariables tests (FIX: template must send real property)
    # -----------------------------------------------------------------------

    async def test_send_template_with_explicit_property_id_includes_property_vars(self, db):
        """When property_id is passed, ContentVariables must include title/city/price."""
        prop = await _create_property(db, title="Casa en Luque", city="Luque", price_usd=85000)
        contact = await _create_contact(db, phone="+595981910001")
        conv_id = await _create_conversation(db, contact.id)

        mock_resp = _make_response(201, {"sid": "SM_PROP_001", "status": "queued"})
        http_patcher, mock_client = _patch_http_client(mock_resp)
        setting_patcher = _patch_bot_setting("HXprop123")
        conv_patcher, _ = _patch_conv_manager(conv_id)

        with http_patcher, setting_patcher, conv_patcher:
            await TemplateService.send_template(
                db=db,
                contact_id=contact.id,
                template_key="wa_tpl_send_property",
                property_id=prop.id,
            )

        call_kwargs = mock_client.post.call_args[1]
        post_data = call_kwargs.get("data", {})
        import json
        content_vars = json.loads(post_data["ContentVariables"])
        assert content_vars.get("1") == "Template Test"
        assert content_vars.get("2") == "Casa en Luque"
        assert content_vars.get("3") == "Luque"
        assert "85" in content_vars.get("4", "")  # price includes 85000

    async def test_send_template_falls_back_to_contact_property_id(self, db):
        """When no property_id arg given, falls back to contact.property_id."""
        prop = await _create_property(db, title="Depto en Lambare", city="Lambare", price_usd=42000)
        contact = await _create_contact(db, phone="+595981910002", property_id=prop.id)
        conv_id = await _create_conversation(db, contact.id)

        mock_resp = _make_response(201, {"sid": "SM_FALLBACK", "status": "queued"})
        http_patcher, mock_client = _patch_http_client(mock_resp)
        setting_patcher = _patch_bot_setting("HXfallback123")
        conv_patcher, _ = _patch_conv_manager(conv_id)

        with http_patcher, setting_patcher, conv_patcher:
            await TemplateService.send_template(
                db=db,
                contact_id=contact.id,
                template_key="wa_tpl_send_property",
            )

        call_kwargs = mock_client.post.call_args[1]
        post_data = call_kwargs.get("data", {})
        import json
        content_vars = json.loads(post_data["ContentVariables"])
        assert content_vars.get("2") == "Depto en Lambare"
        assert content_vars.get("3") == "Lambare"

    async def test_send_template_invalid_property_id_raises(self, db):
        """Non-existent property_id raises ValueError."""
        contact = await _create_contact(db, phone="+595981910003")

        setting_patcher = _patch_bot_setting("HXinvprop123")
        with setting_patcher:
            with pytest.raises(ValueError, match="Propiedad no encontrada"):
                await TemplateService.send_template(
                    db=db,
                    contact_id=contact.id,
                    template_key="wa_tpl_send_property",
                    property_id=999999,
                )

    async def test_send_template_no_property_sends_only_name(self, db):
        """When no property data available, ContentVariables only has name (backward compat)."""
        contact = await _create_contact(db, phone="+595981910004")
        conv_id = await _create_conversation(db, contact.id)

        mock_resp = _make_response(201, {"sid": "SM_NOVAR", "status": "queued"})
        http_patcher, mock_client = _patch_http_client(mock_resp)
        setting_patcher = _patch_bot_setting("HXnovar123")
        conv_patcher, _ = _patch_conv_manager(conv_id)

        with http_patcher, setting_patcher, conv_patcher:
            await TemplateService.send_template(
                db=db,
                contact_id=contact.id,
                template_key="wa_tpl_send_generic",
            )

        call_kwargs = mock_client.post.call_args[1]
        post_data = call_kwargs.get("data", {})
        import json
        content_vars = json.loads(post_data["ContentVariables"])
        assert content_vars == {"1": "Template Test"}

    # -----------------------------------------------------------------------
    # search_context.filtros tests (FIX 1)
    # -----------------------------------------------------------------------

    async def test_send_template_saves_property_filtros_in_search_context(self, db):
        """When template sent with property_id, search_context.filtros is populated."""
        import uuid
        from app.bot.core.types import ConversationState
        from app.models.property import Property
        from decimal import Decimal

        contact = await _create_contact(db, phone="+595981900099")

        prop = Property(
            source="manual",
            external_id=f"test-filtros-001-{uuid.uuid4().hex[:8]}",
            title="Casa Luque",
            city="Luque",
            property_type="casa",
            operation="alquiler",
            neighborhood=None,
            price_usd=Decimal("460.79"),
            is_active=True,
        )
        db.add(prop)
        await db.flush()

        conv_id = await _create_conversation(db, contact.id)
        mock_resp = _make_response(201, {"sid": "SM_FILTROS_001", "status": "queued"})
        http_patcher, _ = _patch_http_client(mock_resp)
        setting_patcher = _patch_bot_setting("HX1234567890abcdef")

        state = ConversationState()
        conv_patcher, mock_mgr = _patch_conv_manager(conv_id)
        mock_mgr.get_search_context.return_value = state

        with http_patcher, setting_patcher, conv_patcher:
            await TemplateService.send_template(
                db=db,
                contact_id=contact.id,
                template_key="wa_tpl_send_property",
                property_id=prop.id,
            )

        mock_mgr.update_search_context.assert_called_once()
        assert state.filtros["tipo"] == "casa"
        assert state.filtros["ciudad"] == "Luque"
        assert state.filtros["operacion"] == "alquiler"
        assert state.filtros["barrio"] == ""
        assert state.filtros["precio_max"] == int(460.79 * 1.3)
        assert state.filtros["moneda"] == "usd"

    async def test_send_template_saves_filtros_with_null_price(self, db):
        """When property has no price, filtros.precio_max is None."""
        import uuid
        from app.bot.core.types import ConversationState
        from app.models.property import Property

        contact = await _create_contact(db, phone="+595981900098")
        prop = Property(
            source="manual",
            external_id=f"test-filtros-002-{uuid.uuid4().hex[:8]}",
            title="Terreno sin precio",
            city="Asuncion",
            property_type="terreno",
            operation="venta",
            price_usd=None,
            is_active=True,
        )
        db.add(prop)
        await db.flush()

        conv_id = await _create_conversation(db, contact.id)
        mock_resp = _make_response(201, {"sid": "SM_FILTROS_002", "status": "queued"})
        http_patcher, _ = _patch_http_client(mock_resp)
        setting_patcher = _patch_bot_setting("HX1234567890abcdef")

        state = ConversationState()
        conv_patcher, mock_mgr = _patch_conv_manager(conv_id)
        mock_mgr.get_search_context.return_value = state

        with http_patcher, setting_patcher, conv_patcher:
            await TemplateService.send_template(
                db=db,
                contact_id=contact.id,
                template_key="wa_tpl_send_property",
                property_id=prop.id,
            )

        assert state.filtros["precio_max"] is None


# ---------------------------------------------------------------------------
# Body generation tests
# ---------------------------------------------------------------------------

class TestTemplateBodyGeneration:
    """Verify that the message body saved to DB is a real human-readable string,
    not the placeholder '[Template: ...]' fallback."""

    async def test_followup_72h_body_saved_to_db(self, db):
        """wa_tpl_followup_72h saves a real body (not the placeholder) to the DB."""
        from sqlalchemy import select
        from app.models.message import Message

        contact = await _create_contact(db, phone="+595981920001", name="Maria")
        conv_id = await _create_conversation(db, contact.id)

        mock_resp = _make_response(201, {"sid": "SM_F72H_001", "status": "queued"})
        http_patcher, _ = _patch_http_client(mock_resp)
        setting_patcher = _patch_bot_setting("HXf72h123")
        conv_patcher, _ = _patch_conv_manager(conv_id)

        with http_patcher, setting_patcher, conv_patcher:
            await TemplateService.send_template(
                db=db,
                contact_id=contact.id,
                template_key="wa_tpl_followup_72h",
            )

        result = await db.execute(
            select(Message).where(Message.external_id == "SM_F72H_001")
        )
        msg = result.scalar_one()
        assert "Hola" in msg.body
        assert "propiedades nuevas" in msg.body
        assert "[Template:" not in msg.body

    async def test_agent_reply_body_saved_to_db(self, db):
        """wa_tpl_agent_reply saves a real body (not the placeholder) to the DB."""
        from sqlalchemy import select
        from app.models.message import Message

        contact = await _create_contact(db, phone="+595981920002", name="Carlos")
        conv_id = await _create_conversation(db, contact.id)

        mock_resp = _make_response(201, {"sid": "SM_AGTRPL_001", "status": "queued"})
        http_patcher, _ = _patch_http_client(mock_resp)
        setting_patcher = _patch_bot_setting("HXagtrpl123")
        conv_patcher, _ = _patch_conv_manager(conv_id)

        with http_patcher, setting_patcher, conv_patcher:
            await TemplateService.send_template(
                db=db,
                contact_id=contact.id,
                template_key="wa_tpl_agent_reply",
            )

        result = await db.execute(
            select(Message).where(Message.external_id == "SM_AGTRPL_001")
        )
        msg = result.scalar_one()
        assert "Hola" in msg.body
        assert "equipo de Onnix SA" in msg.body
        assert "[Template:" not in msg.body
