"""Tests for IC welcome context pre-loading (Phase 3 of GSD Templates v20).

Covers:
  - test_ic_welcome_preload_context_con_match: after successful Twilio send with
    matched_property, update_search_context is called with last_detalle_id,
    etapa='viendo_detalle', and filtros populated from IC property.
  - test_ic_welcome_usa_template_v2_si_disponible: when wa_tpl_ic_welcome_v3 is
    set in bot_settings, the v2 SID is used and ContentVariables has 4 keys.
  - test_ic_welcome_fallback_template_v1: when wa_tpl_ic_welcome_v3 is None,
    falls back to v1 SID (ContentVariables keeps 2 vars).
  - test_ic_welcome_sin_match_sin_cambios: when matched_property is None,
    update_search_context is NOT called (non-regression).
  - test_ic_welcome_no_preload_si_twilio_falla: when Twilio returns HTTP 400,
    update_search_context is NOT called.

All dependencies are mocked; no real DB, network, or Twilio calls.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from app.bot.services.infocasas.infocasas_service import InfocasasService
from app.bot.services.infocasas.lead_parser import ParsedLead
from app.bot.core.types import ConversationState
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Shared helpers (mirrored from test_infocasas_service.py)
# ---------------------------------------------------------------------------


def _make_parsed_lead(
    *,
    consulta_id: str = "66065340",
    name: str = "Nicole Caceres",
    phone: str | None = "+595900000001",
    email: str | None = "nicole@example.com",
    message: str | None = "Me interesa la propiedad",
    property_code: str | None = "OF23CE",
    property_title: str | None = "Casa en Fernando de la Mora",
    listing_city: str | None = "Fernando de la Mora",
    has_whatsapp: bool = True,
    is_reassigned: bool = False,
) -> ParsedLead:
    return ParsedLead(
        consulta_id=consulta_id,
        name=name,
        phone=phone,
        email=email,
        message=message,
        consulta_date=datetime(2026, 3, 28, 14, 30, tzinfo=timezone.utc),
        property_code=property_code,
        property_title=property_title,
        listing_city=listing_city,
        has_whatsapp=has_whatsapp,
        is_reassigned=is_reassigned,
    )


def _make_session_factory(*, session: AsyncMock | None = None) -> MagicMock:
    mock_session = session or AsyncMock()
    mock_session.add = MagicMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=mock_ctx)
    return factory


def _make_service(*, session_factory=None) -> InfocasasService:
    svc = InfocasasService(
        session_manager=AsyncMock(),
        notification_fetcher=AsyncMock(),
        notifier=None,
        session_factory=session_factory or _make_session_factory(),
    )
    return svc


def _make_ic_property(
    *,
    ic_id: int = 101,
    property_id: int | None = 9901,
    infocasas_ref: str = "OF23CE",
    title: str = "Casa 3 dorm Fernando de la Mora",
    city: str = "Fernando de la Mora",
    neighborhood: str = "Centro",
    property_type: str = "Casa",
    operation: str = "venta",
    price_sale: float | None = 120000.0,
    price_rent: float | None = None,
) -> MagicMock:
    """Build a mock InfocasasProperty ORM object."""
    prop = MagicMock()
    prop.id = ic_id
    prop.property_id = property_id
    prop.infocasas_ref = infocasas_ref
    prop.title = title
    prop.city = city
    prop.neighborhood = neighborhood
    prop.property_type = property_type
    prop.operation = operation
    prop.price_sale = price_sale
    prop.price_rent = price_rent
    return prop


# ---------------------------------------------------------------------------
# Shared fake_get_value helpers
# ---------------------------------------------------------------------------


def _fake_get_value_v1_only(template_sid: str = "HXv1abc"):
    """Returns v1 template SID only; v2 returns None."""
    async def fake(session, key):
        if key == "wa_tpl_ic_welcome":
            return template_sid
        if key == "wa_tpl_ic_welcome_v3":
            return None
        if key in ("infocasas_wa_delay_min", "infocasas_wa_delay_max"):
            return "0"
        if key == "ic_autoreply_enabled":
            return "true"
        return None
    return fake


def _fake_get_value_v2(template_sid_v1: str = "HXv1abc", template_sid_v2: str = "HXv2xyz"):
    """Returns both v1 and v2 template SIDs."""
    async def fake(session, key):
        if key == "wa_tpl_ic_welcome":
            return template_sid_v1
        if key == "wa_tpl_ic_welcome_v3":
            return template_sid_v2
        if key in ("infocasas_wa_delay_min", "infocasas_wa_delay_max"):
            return "0"
        if key == "ic_autoreply_enabled":
            return "true"
        return None
    return fake


# ---------------------------------------------------------------------------
# Helper: build a mock httpx client that returns a given status_code
# ---------------------------------------------------------------------------


def _make_mock_http(status_code: int = 201):
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.text = ""
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_response)
    return mock_http


# ---------------------------------------------------------------------------
# TestIcWelcomeContextPreload
# ---------------------------------------------------------------------------


class TestIcWelcomeContextPreload:
    """Phase 3: search_context pre-loaded after successful IC welcome send."""

    @pytest.mark.asyncio
    async def test_ic_welcome_preload_context_con_match(self):
        """When matched_property is not None, update_search_context is called
        with last_detalle_id=IC property id, etapa='viendo_detalle', and
        filtros populated from IC property fields."""
        parsed = _make_parsed_lead()
        ic_prop = _make_ic_property(
            ic_id=101,
            property_id=9901,
            city="Fernando de la Mora",
            neighborhood="Centro",
            property_type="Casa",
            operation="venta",
            price_sale=120000.0,
        )
        matched_property = {
            "city": ic_prop.city,
            "title": ic_prop.title,
            "matched_by": "infocasas_ref",
        }

        mock_http = _make_mock_http(201)
        mock_conv = MagicMock()
        mock_conv.id = 555
        mock_update_ctx = AsyncMock()

        svc = _make_service()

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=_fake_get_value_v1_only(),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=ic_prop),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_cls, patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
            new=AsyncMock(),
        ), patch.object(
            svc, "_save_welcome_message", new=AsyncMock()
        ), patch(
            "app.bot.services.infocasas.infocasas_service.ConversationManager"
        ) as mock_conv_mgr_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_conv_mgr_instance = AsyncMock()
            mock_conv_mgr_instance.get_or_create_conversation = AsyncMock(return_value=mock_conv)
            mock_conv_mgr_instance.update_search_context = mock_update_ctx
            mock_conv_mgr_cls.return_value = mock_conv_mgr_instance

            await svc._send_whatsapp_welcome(parsed, matched_property, contact_id=99)

        # update_search_context must have been called
        mock_update_ctx.assert_awaited_once()
        call_args = mock_update_ctx.call_args

        # First positional arg is session, second is conversation_id, third is state
        conv_id_arg = call_args.args[1]
        state_arg = call_args.args[2]

        assert conv_id_arg == 555, "conversation_id debe ser el de la conv creada"
        assert isinstance(state_arg, ConversationState)
        assert state_arg.last_detalle_id == 9901
        assert state_arg.etapa == "viendo_detalle"

        filtros = state_arg.filtros
        assert filtros.get("tipo") == "Casa"
        assert filtros.get("ciudad") == "Fernando de la Mora"
        assert filtros.get("operacion") == "venta"
        assert "precio_max" in filtros
        assert filtros.get("moneda") == "usd"

    @pytest.mark.asyncio
    async def test_ic_welcome_usa_template_v2_si_disponible(self):
        """When wa_tpl_ic_welcome_v3 is set, the v2 SID is used and
        ContentVariables has 4 keys."""
        parsed = _make_parsed_lead(name="Nicole")
        ic_prop = _make_ic_property(
            ic_id=101,
            title="Casa 3 dorm Fernando de la Mora",
            city="Fernando de la Mora",
            property_type="Casa",
            operation="venta",
            price_sale=120000.0,
        )
        matched_property = {
            "city": ic_prop.city,
            "title": ic_prop.title,
            "matched_by": "infocasas_ref",
        }

        template_sid_v2 = "HXv2xyz"
        mock_http = _make_mock_http(201)
        mock_conv = MagicMock()
        mock_conv.id = 555

        svc = _make_service()

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=_fake_get_value_v2(template_sid_v2=template_sid_v2),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=ic_prop),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_cls, patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
            new=AsyncMock(),
        ), patch.object(
            svc, "_save_welcome_message", new=AsyncMock()
        ), patch(
            "app.bot.services.infocasas.infocasas_service.ConversationManager"
        ) as mock_conv_mgr_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_conv_mgr_instance = AsyncMock()
            mock_conv_mgr_instance.get_or_create_conversation = AsyncMock(return_value=mock_conv)
            mock_conv_mgr_instance.update_search_context = AsyncMock()
            mock_conv_mgr_cls.return_value = mock_conv_mgr_instance

            await svc._send_whatsapp_welcome(parsed, matched_property, contact_id=99)

        # Verify ContentSid is v2
        call_kwargs = mock_http.post.call_args.kwargs
        posted_data = call_kwargs.get("data") or {}
        assert posted_data["ContentSid"] == template_sid_v2

        # Verify 4 ContentVariables
        content_vars = json.loads(posted_data["ContentVariables"])
        assert len(content_vars) == 4
        assert "1" in content_vars
        assert "2" in content_vars
        assert "3" in content_vars
        assert "4" in content_vars
        # var 1 is the lead name
        assert content_vars["1"] == "Nicole"

    @pytest.mark.asyncio
    async def test_ic_welcome_fallback_template_v1(self):
        """When wa_tpl_ic_welcome_v3 is None, falls back to v1 SID.
        ContentVariables has 2 vars (v1 format)."""
        parsed = _make_parsed_lead(name="Nicole", listing_city="Asuncion")
        matched_property = {
            "city": "Asuncion",
            "title": "Depto Asuncion",
            "matched_by": "infocasas_ref",
        }

        template_sid_v1 = "HXv1abc"
        mock_http = _make_mock_http(201)
        mock_conv = MagicMock()
        mock_conv.id = 555
        ic_prop = _make_ic_property(city="Asuncion")

        svc = _make_service()

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=_fake_get_value_v1_only(template_sid=template_sid_v1),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=ic_prop),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_cls, patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
            new=AsyncMock(),
        ), patch.object(
            svc, "_save_welcome_message", new=AsyncMock()
        ), patch(
            "app.bot.services.infocasas.infocasas_service.ConversationManager"
        ) as mock_conv_mgr_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_conv_mgr_instance = AsyncMock()
            mock_conv_mgr_instance.get_or_create_conversation = AsyncMock(return_value=mock_conv)
            mock_conv_mgr_instance.update_search_context = AsyncMock()
            mock_conv_mgr_cls.return_value = mock_conv_mgr_instance

            await svc._send_whatsapp_welcome(parsed, matched_property, contact_id=99)

        call_kwargs = mock_http.post.call_args.kwargs
        posted_data = call_kwargs.get("data") or {}
        # Must use v1 SID
        assert posted_data["ContentSid"] == template_sid_v1
        # v1 has 2 content variables
        content_vars = json.loads(posted_data["ContentVariables"])
        assert len(content_vars) == 2
        assert content_vars["1"] == "Nicole"

    @pytest.mark.asyncio
    async def test_ic_welcome_sin_match_sin_cambios(self):
        """Non-regression: when matched_property is None, update_search_context
        is NOT called. Behavior unchanged from current code."""
        parsed = _make_parsed_lead()
        mock_http = _make_mock_http(201)
        mock_conv = MagicMock()
        mock_conv.id = 555

        svc = _make_service()

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=_fake_get_value_v1_only(),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_cls, patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
            new=AsyncMock(),
        ), patch.object(
            svc, "_save_welcome_message", new=AsyncMock()
        ), patch(
            "app.bot.services.infocasas.infocasas_service.ConversationManager"
        ) as mock_conv_mgr_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_conv_mgr_instance = AsyncMock()
            mock_conv_mgr_instance.get_or_create_conversation = AsyncMock(return_value=mock_conv)
            mock_conv_mgr_instance.update_search_context = AsyncMock()
            mock_conv_mgr_cls.return_value = mock_conv_mgr_instance

            # matched_property is None
            await svc._send_whatsapp_welcome(parsed, None, contact_id=99)

        # update_search_context must NOT have been called
        mock_conv_mgr_instance.update_search_context.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ic_welcome_no_preload_si_twilio_falla(self):
        """When Twilio returns HTTP 400, update_search_context is NOT called."""
        parsed = _make_parsed_lead()
        ic_prop = _make_ic_property()
        matched_property = {
            "city": ic_prop.city,
            "title": ic_prop.title,
            "matched_by": "infocasas_ref",
        }

        # HTTP 400 error from Twilio
        mock_http = _make_mock_http(400)
        mock_conv = MagicMock()
        mock_conv.id = 555

        svc = _make_service()

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=_fake_get_value_v1_only(),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=ic_prop),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_cls, patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
            new=AsyncMock(),
        ), patch.object(
            svc, "_save_welcome_message", new=AsyncMock()
        ), patch(
            "app.bot.services.infocasas.infocasas_service.ConversationManager"
        ) as mock_conv_mgr_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_conv_mgr_instance = AsyncMock()
            mock_conv_mgr_instance.get_or_create_conversation = AsyncMock(return_value=mock_conv)
            mock_conv_mgr_instance.update_search_context = AsyncMock()
            mock_conv_mgr_cls.return_value = mock_conv_mgr_instance

            await svc._send_whatsapp_welcome(parsed, matched_property, contact_id=99)

        # update_search_context must NOT be called when Twilio fails
        mock_conv_mgr_instance.update_search_context.assert_not_awaited()
