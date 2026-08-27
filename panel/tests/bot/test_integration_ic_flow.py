"""Integration tests for the InfoCasas lead-to-callback pipeline.

Tests two components working together:
  1. InfocasasService._send_whatsapp_welcome / _send_whatsapp_reenviado_welcome
     sets up the conversation search_context.
  2. Orchestrator.handle_message reads that context and routes the
     subsequent button callback correctly — without calling Claude.

All external dependencies (DB, Twilio, Telegram) are mocked.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.core.orchestrator import Orchestrator
from app.bot.core.types import (
    BotRequest,
    BotResponse,
    ContactInfo,
    ConversationInfo,
    ConversationState,
)
from app.bot.search.search_service import SearchResult
from app.bot.services.infocasas.infocasas_service import InfocasasService
from app.bot.services.infocasas.lead_parser import ParsedLead, parse_lead


# ---------------------------------------------------------------------------
# Orchestrator helpers — copied exactly from test_handler_ver_detalles.py
# ---------------------------------------------------------------------------

def _make_orchestrator():
    """Create an Orchestrator with all dependencies mocked."""
    claude = AsyncMock()
    gemini = AsyncMock()
    circuit_breaker = MagicMock()
    circuit_breaker.is_open = False
    search_service = AsyncMock()
    conversation_manager = AsyncMock()
    conversation_manager.check_human_cooldown = MagicMock(return_value=False)
    response_builder = MagicMock()
    tool_executor = AsyncMock()
    tool_executor.build_tool_result_message = MagicMock()

    orch = Orchestrator(
        claude=claude,
        gemini=gemini,
        circuit_breaker=circuit_breaker,
        search_service=search_service,
        conversation_manager=conversation_manager,
        response_builder=response_builder,
        tool_executor=tool_executor,
    )

    return orch, {
        "claude": claude,
        "gemini": gemini,
        "circuit_breaker": circuit_breaker,
        "search_service": search_service,
        "conversation_manager": conversation_manager,
        "response_builder": response_builder,
        "tool_executor": tool_executor,
    }


def _default_contact(status="contacted", is_baja=False):
    return ContactInfo(
        id=1, name="Test User", status=status, is_baja=is_baja,
        platform="whatsapp", source_id="+595981234567",
    )


def _default_conversation(is_bot_active=True):
    return ConversationInfo(
        id=10, contact_id=1, platform="whatsapp", chat_id="+595981234567",
        is_bot_active=is_bot_active,
    )


# ---------------------------------------------------------------------------
# InfocasasService helpers — from test_infocasas_service.py patterns
# ---------------------------------------------------------------------------

def _make_session_factory(*, session: AsyncMock | None = None) -> MagicMock:
    """Return an async context manager factory wrapping *session*."""
    mock_session = session or AsyncMock()
    mock_session.add = MagicMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=mock_ctx)
    return factory


def _make_service(
    *,
    session: AsyncMock | None = None,
) -> tuple[InfocasasService, MagicMock]:
    """Build an InfocasasService with mocked session factory.

    Returns (service, session_factory).
    """
    factory = _make_session_factory(session=session)

    mock_sm = AsyncMock()
    mock_fetcher = AsyncMock()
    mock_notifier = AsyncMock()
    mock_notifier.notify = AsyncMock(return_value=True)

    svc = InfocasasService(
        session_manager=mock_sm,
        notification_fetcher=mock_fetcher,
        notifier=mock_notifier,
        session_factory=factory,
    )
    return svc, factory


# ---------------------------------------------------------------------------
# Shared property fixture
# ---------------------------------------------------------------------------

def _make_active_property(prop_id: int = 42) -> dict:
    """Build a minimal active property dict."""
    return {
        "id": prop_id,
        "title": f"Casa test {prop_id}",
        "city": "Asuncion",
        "neighborhood": "Recoleta",
        "operation": "venta",
        "property_type": "casa",
        "price_usd": 180000,
        "price_currency": "usd",
        "bedrooms": 3,
        "bathrooms": 2,
        "total_area_m2": 220,
        "source": "onnix",
        "external_id": f"ext_{prop_id}",
        "local_image_count": 3,
        "is_active": True,
        "description": "Casa amplia con jardín.",
    }


# ---------------------------------------------------------------------------
# Test class 1: Direct lead with match → VER_DETALLES callback
# ---------------------------------------------------------------------------

class TestIntegrationDirectLeadWithMatch:
    """Direct IC lead with matched property → VER_DETALLES callback resolved."""

    @pytest.mark.asyncio
    async def test_send_welcome_calls_twilio_and_preloads_context(self):
        """_send_whatsapp_welcome calls Twilio and then _preload_search_context.

        Chain: IC welcome send (HTTP 201) → context pre-loaded with
        etapa='viendo_detalle' and last_detalle_id.
        """
        svc, factory = _make_service()

        parsed = ParsedLead(
            consulta_id="66065340",
            name="Maria Gomez",
            phone="+595981234567",
            email=None,
            message="Me interesa la propiedad",
            consulta_date=datetime(2026, 4, 12, tzinfo=timezone.utc),
            property_code="OF23CE",
            property_title="Casa en Fernando de la Mora",
            listing_city="Fernando de la Mora",
            has_whatsapp=True,
            is_reassigned=False,
            listing_type=None,
            listing_operation=None,
            listing_bedrooms=None,
            listing_area_m2=None,
            listing_price=None,
            listing_currency=None,
            listing_zone_from_message=None,
        )

        # ic_prop_full mock returned by PropertyRepository.get_ic_by_ref
        # Must use plain MagicMock (no spec) with string attributes so that
        # _build_v2_content_vars can slice .title and json.dumps succeeds.
        ic_prop_full = MagicMock()
        ic_prop_full.id = 9999  # infocasas_properties.id (different from properties.id)
        ic_prop_full.property_id = 42  # FK to properties.id — used by _preload_search_context
        ic_prop_full.title = "Casa en Fernando de la Mora"
        ic_prop_full.property_type = "Casa"
        ic_prop_full.city = "Fernando de la Mora"
        ic_prop_full.neighborhood = "Recoleta"
        ic_prop_full.operation = "venta"
        ic_prop_full.price_sale = 180000
        ic_prop_full.price_rent = None

        matched_property = {"city": "Fernando de la Mora", "title": "Casa test", "matched_by": "infocasas_ref"}

        mock_conv_obj = MagicMock()
        mock_conv_obj.id = 10

        mock_http_response = MagicMock()
        mock_http_response.status_code = 201

        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(return_value=mock_http_response)
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=False)

        update_search_context_calls = []

        with (
            patch("app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
                  new=AsyncMock(side_effect=lambda _s, key: {
                      "ic_autoreply_enabled": "true",
                      "wa_tpl_ic_welcome": "HXabc123",
                      "wa_tpl_ic_welcome_v3": "HXdef456",
                      "infocasas_wa_delay_min": "0",
                      "infocasas_wa_delay_max": "0",
                  }.get(key))),
            patch("app.bot.services.infocasas.infocasas_service.PropertyRepository.get_ic_by_ref",
                  new=AsyncMock(return_value=ic_prop_full)),
            patch("app.bot.services.infocasas.infocasas_service.asyncio.sleep",
                  new=AsyncMock()),
            patch("app.bot.services.infocasas.infocasas_service.httpx.AsyncClient",
                  return_value=mock_http_client),
            patch("app.bot.services.infocasas.infocasas_service.ConversationManager") as MockCM,
            patch("app.bot.services.infocasas.infocasas_service.message_repo") as mock_msg_repo,
        ):
            mock_cm_instance = AsyncMock()
            mock_cm_instance.get_or_create_conversation = AsyncMock(return_value=mock_conv_obj)

            # Capture state passed to update_search_context
            async def capture_update(session, conv_id, state):
                update_search_context_calls.append(state)

            mock_cm_instance.update_search_context = AsyncMock(side_effect=capture_update)
            MockCM.return_value = mock_cm_instance

            mock_msg_repo.create = AsyncMock()

            await svc._send_whatsapp_welcome(parsed, matched_property, contact_id=1)

        # Twilio was called once
        mock_http_client.post.assert_awaited_once()
        # Context was pre-loaded
        assert len(update_search_context_calls) == 1
        state = update_search_context_calls[0]
        assert state.etapa == "viendo_detalle"
        assert state.last_detalle_id == 42

    @pytest.mark.asyncio
    async def test_ver_detalles_callback_resolves_to_detalle_intent(self):
        """VER_DETALLES callback with pre-loaded context returns intent='detalle'.

        Simulates the orchestrator flow AFTER the IC welcome has set
        search_context with etapa='viendo_detalle' and last_detalle_id=42.
        """
        orch, mocks = _make_orchestrator()
        prop = _make_active_property(42)

        # Pre-loaded context: set by _preload_search_context after IC welcome
        ctx = ConversationState(
            etapa="viendo_detalle",
            filtros={"tipo": "casa", "ciudad": "Fernando de la Mora", "operacion": "venta"},
            last_detalle_id=42,
        )
        mocks["conversation_manager"].resolve_contact.return_value = _default_contact()
        mocks["conversation_manager"].get_or_create_conversation.return_value = _default_conversation()
        mocks["conversation_manager"].check_human_cooldown.return_value = False
        mocks["conversation_manager"].get_history.return_value = []
        mocks["conversation_manager"].get_search_context.return_value = ctx
        mocks["search_service"].get_by_ids.return_value = SearchResult(
            properties=[prop], total_found=1,
        )

        request = BotRequest(
            platform="whatsapp",
            chat_id="+595981234567",
            user_id="+595981234567",
            user_name="Maria Gomez",
            text="VER_DETALLES",
            external_id="msg_vd_001",
            callback_data="VER_DETALLES",
        )

        result = await orch.handle_message(request, AsyncMock())

        assert result is not None
        assert result.intent == "detalle"
        assert len(result.properties) == 1
        assert result.properties[0]["id"] == 42
        # Claude was NOT called
        mocks["claude"].send_message.assert_not_called()


# ---------------------------------------------------------------------------
# Test class 2: Direct lead WITHOUT match → reenviado fallback → SI_MOSTRAME
# ---------------------------------------------------------------------------

class TestIntegrationDirectLeadNoMatch:
    """Direct IC lead, no property match → reenviado fallback → SI_MOSTRAME_REENVIADO."""

    @pytest.mark.asyncio
    async def test_reenviado_welcome_no_context_when_no_ic_ref(self):
        """When infocasas_ref is None, reenviado welcome sends Twilio but skips context preload.

        Chain:
        - contact.infocasas_ref = None → ic_prop_full = None
        - _send_whatsapp_reenviado_welcome HTTP 201
        - _preload_search_context returns early (ic_prop_full is None) → no context written
        """
        svc, factory = _make_service()

        parsed = ParsedLead(
            consulta_id="66065341",
            name="Maria Gomez",
            phone="+595981234567",
            email=None,
            message="Consulta sobre departamento en alquiler",
            consulta_date=datetime(2026, 4, 12, tzinfo=timezone.utc),
            property_code=None,
            property_title=None,
            listing_city="Luque",
            has_whatsapp=True,
            is_reassigned=False,
            listing_type="departamento",
            listing_operation="alquiler",
            listing_bedrooms=2,
            listing_area_m2=80.0,
            listing_price=5000000.0,
            listing_currency="gs",
            listing_zone_from_message="Luque",
        )

        # contact mock with id so _preload_reenviado_context gets contact_id
        contact_mock = MagicMock()
        contact_mock.id = 1
        contact_mock.infocasas_ref = None  # no IC ref → fallback to parsed.listing_*

        mock_conv_obj = MagicMock()
        mock_conv_obj.id = 10

        mock_http_response = MagicMock()
        mock_http_response.status_code = 201

        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(return_value=mock_http_response)
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=False)

        update_search_context_calls = []

        with (
            patch("app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
                  new=AsyncMock(side_effect=lambda _s, key: {
                      "wa_tpl_ic_reenviado_welcome_v3": "HXreenviado001",
                      "infocasas_wa_delay_min": "0",
                      "infocasas_wa_delay_max": "0",
                  }.get(key))),
            patch("app.bot.services.infocasas.infocasas_service.asyncio.sleep",
                  new=AsyncMock()),
            patch("app.bot.services.infocasas.infocasas_service.httpx.AsyncClient",
                  return_value=mock_http_client),
            patch("app.bot.services.infocasas.infocasas_service.ConversationManager") as MockCM,
            patch("app.bot.services.infocasas.infocasas_service.message_repo") as mock_msg_repo,
        ):
            mock_cm_instance = AsyncMock()
            mock_cm_instance.get_or_create_conversation = AsyncMock(return_value=mock_conv_obj)

            async def capture_update(session, conv_id, state):
                update_search_context_calls.append(state)

            mock_cm_instance.update_search_context = AsyncMock(side_effect=capture_update)
            MockCM.return_value = mock_cm_instance
            mock_msg_repo.create = AsyncMock()

            await svc._send_whatsapp_reenviado_welcome(contact_mock, parsed, None)

        # Twilio was called
        mock_http_client.post.assert_awaited_once()
        # No context written — ic_prop_full is None, _preload_search_context returns early
        assert len(update_search_context_calls) == 0

    @pytest.mark.asyncio
    async def test_si_mostrame_reenviado_returns_busqueda_intent(self):
        """SI_MOSTRAME_REENVIADO with pre-loaded reenviado context returns intent='busqueda'.

        Simulates the orchestrator flow AFTER _preload_reenviado_context wrote
        etapa='esperando_confirmacion_busqueda' with alquiler/Luque filtros.
        """
        orch, mocks = _make_orchestrator()

        # Context as written by _preload_reenviado_context
        ctx = ConversationState(
            etapa="esperando_confirmacion_busqueda",
            filtros={
                "tipo": "departamento",
                "ciudad": "Luque",
                "operacion": "alquiler",
                "dormitorios": 2,
                "precio_max": 5750000,
                "moneda": "gs",
            },
        )
        mocks["conversation_manager"].resolve_contact.return_value = _default_contact()
        mocks["conversation_manager"].get_or_create_conversation.return_value = _default_conversation()
        mocks["conversation_manager"].check_human_cooldown.return_value = False
        mocks["conversation_manager"].get_history.return_value = []
        mocks["conversation_manager"].get_search_context.return_value = ctx

        # Return 2 properties from the search
        props = [_make_active_property(100 + i) for i in range(2)]
        mocks["search_service"].search_properties.return_value = SearchResult(
            properties=props, total_found=2,
        )

        request = BotRequest(
            platform="whatsapp",
            chat_id="+595981234567",
            user_id="+595981234567",
            user_name="Maria Gomez",
            text=None,
            callback_data="SI_MOSTRAME_REENVIADO",
        )

        result = await orch.handle_message(request, AsyncMock())

        assert result is not None
        assert result.intent == "busqueda"
        # Claude was NOT called
        mocks["claude"].send_message.assert_not_called()
        mocks["gemini"].send_message.assert_not_called()


# ---------------------------------------------------------------------------
# Test class 3: Real reenviado message parse + AHORA_NO_REENVIADO callback
# ---------------------------------------------------------------------------

class TestIntegrationRealReenviadoMessage:
    """Real reenviado message is parsed correctly and AHORA_NO_REENVIADO handled."""

    # The canonical reenviado message format from InfoCasas
    REENVIADO_MESSAGE = (
        "Hola, recibiste una consulta reenviada.\n"
        "La propiedad consultada tenía las siguientes características: "
        "Apartamento en Alquiler de 3 dorms. en Recoleta, 125 m² "
        "por Gs. 6.500.000"
    )

    def _make_lead_data(self) -> dict:
        return {
            "id": "66065340",
            "message": self.REENVIADO_MESSAGE,
            "created_at": "2026-04-12 10:00:00",
            "from": {
                "name": "Maria Gomez",
                "email": None,
                "phone": "+595981234567",
                "whatsapp_phone": None,
                "has_whatsapp": True,
            },
            "listing": {
                "id": "193572330",
                "title": "Apartamento en Alquiler en Recoleta",
                "code": None,
                "neighborhood": {"name": "Recoleta"},
            },
        }

    def test_parse_lead_detects_is_reassigned(self):
        """parse_lead correctly detects is_reassigned=True from reenviado message."""
        parsed = parse_lead(self._make_lead_data())

        assert parsed is not None
        assert parsed.is_reassigned is True

    def test_parse_lead_extracts_listing_type(self):
        """listing_type is extracted as 'apartamento' (lowercased)."""
        parsed = parse_lead(self._make_lead_data())

        assert parsed is not None
        assert parsed.listing_type == "apartamento"

    def test_parse_lead_extracts_listing_operation(self):
        """listing_operation is extracted as 'alquiler' (lowercased)."""
        parsed = parse_lead(self._make_lead_data())

        assert parsed is not None
        assert parsed.listing_operation == "alquiler"

    def test_parse_lead_extracts_bedrooms(self):
        """listing_bedrooms is extracted as 3."""
        parsed = parse_lead(self._make_lead_data())

        assert parsed is not None
        assert parsed.listing_bedrooms == 3

    def test_parse_lead_extracts_area(self):
        """listing_area_m2 is extracted as 125.0."""
        parsed = parse_lead(self._make_lead_data())

        assert parsed is not None
        assert parsed.listing_area_m2 == 125.0

    def test_parse_lead_extracts_price(self):
        """listing_price is extracted as 6500000.0 (Guaraníes)."""
        parsed = parse_lead(self._make_lead_data())

        assert parsed is not None
        assert parsed.listing_price == 6500000.0

    def test_parse_lead_extracts_currency_gs(self):
        """listing_currency is 'gs' for Guaraní amounts."""
        parsed = parse_lead(self._make_lead_data())

        assert parsed is not None
        assert parsed.listing_currency == "gs"

    def test_parse_lead_extracts_zone_from_message(self):
        """listing_zone_from_message is truthy (zone was parsed from the message)."""
        parsed = parse_lead(self._make_lead_data())

        assert parsed is not None
        assert parsed.listing_zone_from_message  # truthy — zone was extracted

    @pytest.mark.asyncio
    async def test_ahora_no_reenviado_returns_non_detalle_intent(self):
        """AHORA_NO_REENVIADO callback returns intent != 'detalle' with non-empty text.

        The contact taps 'Ahora no' on the reenviado welcome template.
        The orchestrator must:
        - NOT call Claude
        - Return a BotResponse with text (polite decline message)
        - Use an intent other than 'detalle'
        """
        orch, mocks = _make_orchestrator()

        ctx = ConversationState(
            etapa="esperando_confirmacion_busqueda",
            filtros={
                "tipo": "apartamento",
                "ciudad": "Recoleta",
                "operacion": "alquiler",
                "dormitorios": 3,
                "precio_max": 7475000,
                "moneda": "gs",
            },
        )
        mocks["conversation_manager"].resolve_contact.return_value = _default_contact(status="new")
        mocks["conversation_manager"].get_or_create_conversation.return_value = _default_conversation()
        mocks["conversation_manager"].check_human_cooldown.return_value = False
        mocks["conversation_manager"].get_history.return_value = []
        mocks["conversation_manager"].get_search_context.return_value = ctx

        mock_db_session = AsyncMock()

        with (
            patch(
                "app.repositories.contact_repo.ContactRepository.update_status",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.repositories.lead_event_repo.LeadEventRepository.create",
                new=AsyncMock(return_value=None),
            ),
        ):
            request = BotRequest(
                platform="whatsapp",
                chat_id="+595981234567",
                user_id="+595981234567",
                user_name="Maria Gomez",
                text=None,
                callback_data="AHORA_NO_REENVIADO",
            )
            result = await orch.handle_message(request, mock_db_session)

        assert result is not None
        assert result.intent != "detalle"
        assert result.text  # non-empty polite response
        # Claude and Gemini were NOT called
        mocks["claude"].send_message.assert_not_called()
        mocks["gemini"].send_message.assert_not_called()
