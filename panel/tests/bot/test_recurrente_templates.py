"""Tests for Fase 5: WA templates 3 (recurrente_directo) y 4 (recurrente_reenviado).

Covers:
  - _process_lead routing: is_new=False, is_new_property=True → recurrente branch
  - _send_whatsapp_recurrente_directo: sends template, respects toggle, skips without SID
  - _send_whatsapp_recurrente_reenviado: sends template, respects toggle
  - _build_recurrente_directo_content_vars: 4 variables, truncation, fallback
  - _build_recurrente_reenviado_content_vars: 5 variables, matched_property fallback

All dependencies are mocked; no real DB, network, or Twilio calls.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.services.infocasas.infocasas_service import InfocasasService
from app.bot.services.infocasas.lead_parser import ParsedLead
from app.bot.services.infocasas.notification_fetcher import NotificationFetcher
from app.bot.services.infocasas.session_manager import SessionManager


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_parsed_lead(
    *,
    consulta_id: str = "77001234",
    name: str = "Carlos Lopez",
    phone: str | None = "+595981234567",
    email: str | None = "carlos@example.com",
    message: str | None = "Me interesa otra propiedad",
    property_code: str | None = "OF99XX",
    property_title: str | None = "Departamento en Asuncion",
    listing_city: str | None = "Asuncion",
    has_whatsapp: bool = True,
    is_reassigned: bool = False,
    listing_type: str | None = "Departamento",
    listing_operation: str | None = "venta",
    listing_price: float | None = 85_000.0,
    listing_currency: str | None = "USD",
    listing_bedrooms: int | None = 2,
) -> ParsedLead:
    return ParsedLead(
        consulta_id=consulta_id,
        name=name,
        phone=phone,
        email=email,
        message=message,
        consulta_date=datetime(2026, 4, 16, 10, 0, tzinfo=timezone.utc),
        property_code=property_code,
        property_title=property_title,
        listing_city=listing_city,
        has_whatsapp=has_whatsapp,
        is_reassigned=is_reassigned,
        listing_type=listing_type,
        listing_operation=listing_operation,
        listing_bedrooms=listing_bedrooms,
        listing_area_m2=None,
        listing_price=listing_price,
        listing_currency=listing_currency,
    )


def _make_session_factory(*, session: AsyncMock | None = None) -> MagicMock:
    mock_session = session or AsyncMock()
    mock_session.add = MagicMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=mock_ctx)
    return factory


def _make_contact(*, contact_id: int = 42, phone: str = "+595981234567", name: str = "Carlos Lopez") -> MagicMock:
    contact = MagicMock()
    contact.id = contact_id
    contact.phone = phone
    contact.name = name
    return contact


def _make_service_for_process_lead(session_factory=None):
    """Build an InfocasasService with minimal mocks for _process_lead testing."""
    sm = MagicMock(spec=SessionManager)
    sm.get_valid_token = AsyncMock(return_value="tok")
    nf = MagicMock(spec=NotificationFetcher)
    nf.fetch_lead_details = AsyncMock(return_value={"id": "1"})
    return InfocasasService(
        session_manager=sm,
        notification_fetcher=nf,
        notifier=None,
        session_factory=session_factory,
    )


def _make_mock_session():
    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.commit = AsyncMock()
    return mock_session


# ---------------------------------------------------------------------------
# Test 1: is_new=False, is_new_property=True, is_reassigned=False → _send_whatsapp_recurrente_directo
# ---------------------------------------------------------------------------


class TestRecurrenteDirectoRouting:
    """_process_lead routes to _send_whatsapp_recurrente_directo correctly."""

    @pytest.mark.asyncio
    async def test_recurrente_directo_sends_template_3(self) -> None:
        """is_new=False, is_new_property=True, is_reassigned=False, autoreply=True → recurrente_directo called."""
        parsed = _make_parsed_lead(is_reassigned=False)
        contact = _make_contact()
        contact.status = "no_response"
        contact.baja_at = None

        svc = _make_service_for_process_lead(None)
        mock_session = _make_mock_session()
        svc._session_factory = MagicMock(return_value=mock_session)

        with patch.object(svc, "_notify_new_lead", new_callable=AsyncMock), \
             patch.object(svc, "_send_whatsapp_recurrente_directo", new_callable=AsyncMock) as mock_recurrente, \
             patch.object(svc, "_send_whatsapp_welcome", new_callable=AsyncMock) as mock_welcome, \
             patch.object(svc, "_send_whatsapp_reenviado_welcome", new_callable=AsyncMock) as mock_reenviado, \
             patch.object(svc, "_send_whatsapp_recurrente_reenviado", new_callable=AsyncMock) as mock_rec_reenviado, \
             patch.object(svc, "_upsert_contact", new_callable=AsyncMock) as mock_upsert, \
             patch.object(svc, "_match_property", new_callable=AsyncMock) as mock_match, \
             patch.object(svc, "_log_lead_event", new_callable=AsyncMock), \
             patch(
                 "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
                 new=AsyncMock(return_value="true"),  # autoreply ON
             ), \
             patch("app.bot.services.infocasas.infocasas_service.parse_lead", return_value=parsed):

            mock_upsert.return_value = (False, True, contact)  # is_new=False, is_new_property=True
            mock_match.return_value = {"city": "Asuncion", "matched_by": "infocasas_ref"}
            await svc._process_lead("tok", "77001234")

        mock_recurrente.assert_called_once()
        mock_welcome.assert_not_called()
        mock_reenviado.assert_not_called()
        mock_rec_reenviado.assert_not_called()

    @pytest.mark.asyncio
    async def test_recurrente_reenviado_sends_template_4(self) -> None:
        """is_new=False, is_new_property=True, is_reassigned=True, reenviados=True → recurrente_reenviado called."""
        parsed = _make_parsed_lead(is_reassigned=True)
        contact = _make_contact()
        contact.status = "no_response"
        contact.baja_at = None

        svc = _make_service_for_process_lead(None)
        mock_session = _make_mock_session()
        svc._session_factory = MagicMock(return_value=mock_session)

        with patch.object(svc, "_notify_new_lead", new_callable=AsyncMock), \
             patch.object(svc, "_send_whatsapp_recurrente_directo", new_callable=AsyncMock) as mock_recurrente, \
             patch.object(svc, "_send_whatsapp_welcome", new_callable=AsyncMock) as mock_welcome, \
             patch.object(svc, "_send_whatsapp_reenviado_welcome", new_callable=AsyncMock) as mock_reenviado, \
             patch.object(svc, "_send_whatsapp_recurrente_reenviado", new_callable=AsyncMock) as mock_rec_reenviado, \
             patch.object(svc, "_upsert_contact", new_callable=AsyncMock) as mock_upsert, \
             patch.object(svc, "_match_property", new_callable=AsyncMock) as mock_match, \
             patch.object(svc, "_log_lead_event", new_callable=AsyncMock), \
             patch(
                 "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
                 new=AsyncMock(return_value="true"),  # reenviados ON
             ), \
             patch("app.bot.services.infocasas.infocasas_service.parse_lead", return_value=parsed):

            mock_upsert.return_value = (False, True, contact)  # is_new=False, is_new_property=True
            mock_match.return_value = None
            await svc._process_lead("tok", "77001234")

        mock_rec_reenviado.assert_called_once()
        mock_recurrente.assert_not_called()
        mock_welcome.assert_not_called()
        mock_reenviado.assert_not_called()

    @pytest.mark.asyncio
    async def test_linked_existing_sends_nothing(self) -> None:
        """is_new=False, is_new_property=False, recent IC event within 24h → no templates called.

        Fase 5: the (False, False) case now routes through _has_recent_ic_event.
        When it returns True (within dedup window), all templates remain suppressed.
        """
        parsed = _make_parsed_lead(is_reassigned=False)
        contact = _make_contact()

        svc = _make_service_for_process_lead(None)
        mock_session = _make_mock_session()
        svc._session_factory = MagicMock(return_value=mock_session)

        with patch.object(svc, "_notify_new_lead", new_callable=AsyncMock), \
             patch.object(svc, "_send_whatsapp_recurrente_directo", new_callable=AsyncMock) as mock_recurrente, \
             patch.object(svc, "_send_whatsapp_welcome", new_callable=AsyncMock) as mock_welcome, \
             patch.object(svc, "_send_whatsapp_reenviado_welcome", new_callable=AsyncMock) as mock_reenviado, \
             patch.object(svc, "_send_whatsapp_recurrente_reenviado", new_callable=AsyncMock) as mock_rec_reenviado, \
             patch.object(svc, "_upsert_contact", new_callable=AsyncMock) as mock_upsert, \
             patch.object(svc, "_match_property", new_callable=AsyncMock) as mock_match, \
             patch.object(svc, "_log_lead_event", new_callable=AsyncMock), \
             patch.object(svc, "_has_recent_ic_event", new_callable=AsyncMock, return_value=True), \
             patch(
                 "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
                 new=AsyncMock(return_value="true"),
             ), \
             patch("app.bot.services.infocasas.infocasas_service.parse_lead", return_value=parsed):

            mock_upsert.return_value = (False, False, contact)  # is_new=False, is_new_property=False
            mock_match.return_value = None
            await svc._process_lead("tok", "77001234")

        mock_recurrente.assert_not_called()
        mock_rec_reenviado.assert_not_called()
        mock_welcome.assert_not_called()
        mock_reenviado.assert_not_called()

    @pytest.mark.asyncio
    async def test_recurrente_directo_respects_autoreply_toggle(self) -> None:
        """ic_autoreply_enabled=False → _send_whatsapp_recurrente_directo NOT called."""
        parsed = _make_parsed_lead(is_reassigned=False)
        contact = _make_contact()
        contact.status = "no_response"
        contact.baja_at = None

        svc = _make_service_for_process_lead(None)
        mock_session = _make_mock_session()
        svc._session_factory = MagicMock(return_value=mock_session)

        with patch.object(svc, "_notify_new_lead", new_callable=AsyncMock), \
             patch.object(svc, "_send_whatsapp_recurrente_directo", new_callable=AsyncMock) as mock_recurrente, \
             patch.object(svc, "_upsert_contact", new_callable=AsyncMock) as mock_upsert, \
             patch.object(svc, "_match_property", new_callable=AsyncMock) as mock_match, \
             patch.object(svc, "_log_lead_event", new_callable=AsyncMock), \
             patch(
                 "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
                 new=AsyncMock(return_value=None),  # toggle OFF (not "true")
             ), \
             patch("app.bot.services.infocasas.infocasas_service.parse_lead", return_value=parsed):

            mock_upsert.return_value = (False, True, contact)
            mock_match.return_value = {"city": "Asuncion", "matched_by": "infocasas_ref"}
            await svc._process_lead("tok", "77001234")

        mock_recurrente.assert_not_called()

    @pytest.mark.asyncio
    async def test_recurrente_reenviado_respects_reenviados_toggle(self) -> None:
        """ic_autoreply_reenviados_enabled=False → _send_whatsapp_recurrente_reenviado NOT called."""
        parsed = _make_parsed_lead(is_reassigned=True)
        contact = _make_contact()
        contact.status = "no_response"
        contact.baja_at = None

        svc = _make_service_for_process_lead(None)
        mock_session = _make_mock_session()
        svc._session_factory = MagicMock(return_value=mock_session)

        with patch.object(svc, "_notify_new_lead", new_callable=AsyncMock), \
             patch.object(svc, "_send_whatsapp_recurrente_reenviado", new_callable=AsyncMock) as mock_rec_reenviado, \
             patch.object(svc, "_upsert_contact", new_callable=AsyncMock) as mock_upsert, \
             patch.object(svc, "_match_property", new_callable=AsyncMock) as mock_match, \
             patch.object(svc, "_log_lead_event", new_callable=AsyncMock), \
             patch(
                 "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
                 new=AsyncMock(return_value=None),  # toggle OFF
             ), \
             patch("app.bot.services.infocasas.infocasas_service.parse_lead", return_value=parsed):

            mock_upsert.return_value = (False, True, contact)
            mock_match.return_value = None
            await svc._process_lead("tok", "77001234")

        mock_rec_reenviado.assert_not_called()

    @pytest.mark.asyncio
    async def test_new_contact_doesnt_trigger_recurrente(self) -> None:
        """is_new=True, is_new_property=True → only is_new branch, never elif recurrente."""
        parsed = _make_parsed_lead(is_reassigned=False)
        contact = _make_contact()

        svc = _make_service_for_process_lead(None)
        mock_session = _make_mock_session()
        svc._session_factory = MagicMock(return_value=mock_session)

        with patch.object(svc, "_notify_new_lead", new_callable=AsyncMock), \
             patch.object(svc, "_send_whatsapp_recurrente_directo", new_callable=AsyncMock) as mock_recurrente, \
             patch.object(svc, "_send_whatsapp_recurrente_reenviado", new_callable=AsyncMock) as mock_rec_reenviado, \
             patch.object(svc, "_send_whatsapp_welcome", new_callable=AsyncMock) as mock_welcome, \
             patch.object(svc, "_upsert_contact", new_callable=AsyncMock) as mock_upsert, \
             patch.object(svc, "_match_property", new_callable=AsyncMock) as mock_match, \
             patch.object(svc, "_log_lead_event", new_callable=AsyncMock), \
             patch(
                 "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
                 new=AsyncMock(return_value="true"),
             ), \
             patch("app.bot.services.infocasas.infocasas_service.parse_lead", return_value=parsed):

            mock_upsert.return_value = (True, True, contact)  # is_new=True
            mock_match.return_value = {"city": "Asuncion", "matched_by": "infocasas_ref"}
            await svc._process_lead("tok", "77001234")

        # Only the is_new branch fires — welcome (not recurrente)
        mock_recurrente.assert_not_called()
        mock_rec_reenviado.assert_not_called()

    @pytest.mark.asyncio
    async def test_recurrente_directo_skipped_for_interested_status(self) -> None:
        """is_new=False, is_new_property=True, contact.status=interested -> NO recurrente template."""
        parsed = _make_parsed_lead(is_reassigned=False)
        contact = _make_contact()
        contact.status = "interested"
        contact.baja_at = None

        svc = _make_service_for_process_lead(None)
        mock_session = _make_mock_session()
        svc._session_factory = MagicMock(return_value=mock_session)

        with patch.object(svc, "_notify_new_lead", new_callable=AsyncMock), \
             patch.object(svc, "_send_whatsapp_recurrente_directo", new_callable=AsyncMock) as mock_recurrente, \
             patch.object(svc, "_send_whatsapp_recurrente_reenviado", new_callable=AsyncMock) as mock_rec_reenviado, \
             patch.object(svc, "_upsert_contact", new_callable=AsyncMock) as mock_upsert, \
             patch.object(svc, "_match_property", new_callable=AsyncMock) as mock_match, \
             patch.object(svc, "_log_lead_event", new_callable=AsyncMock), \
             patch(
                 "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
                 new=AsyncMock(return_value="true"),
             ), \
             patch("app.bot.services.infocasas.infocasas_service.parse_lead", return_value=parsed):

            mock_upsert.return_value = (False, True, contact)
            mock_match.return_value = {"city": "Asuncion", "matched_by": "infocasas_ref"}
            await svc._process_lead("tok", "77001234")

        mock_recurrente.assert_not_called()
        mock_rec_reenviado.assert_not_called()

    @pytest.mark.asyncio
    async def test_recurrente_directo_skipped_for_optout(self) -> None:
        """is_new=False, is_new_property=True, contact.baja_at set -> NO recurrente template."""
        from datetime import timezone
        parsed = _make_parsed_lead(is_reassigned=False)
        contact = _make_contact()
        contact.status = "discarded"
        contact.baja_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

        svc = _make_service_for_process_lead(None)
        mock_session = _make_mock_session()
        svc._session_factory = MagicMock(return_value=mock_session)

        with patch.object(svc, "_notify_new_lead", new_callable=AsyncMock), \
             patch.object(svc, "_send_whatsapp_recurrente_directo", new_callable=AsyncMock) as mock_recurrente, \
             patch.object(svc, "_send_whatsapp_recurrente_reenviado", new_callable=AsyncMock) as mock_rec_reenviado, \
             patch.object(svc, "_upsert_contact", new_callable=AsyncMock) as mock_upsert, \
             patch.object(svc, "_match_property", new_callable=AsyncMock) as mock_match, \
             patch.object(svc, "_log_lead_event", new_callable=AsyncMock), \
             patch(
                 "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
                 new=AsyncMock(return_value="true"),
             ), \
             patch("app.bot.services.infocasas.infocasas_service.parse_lead", return_value=parsed):

            mock_upsert.return_value = (False, True, contact)
            mock_match.return_value = {"city": "Asuncion", "matched_by": "infocasas_ref"}
            await svc._process_lead("tok", "77001234")

        mock_recurrente.assert_not_called()
        mock_rec_reenviado.assert_not_called()

    @pytest.mark.asyncio
    async def test_recurrente_reenviado_skipped_for_closed_status(self) -> None:
        """is_new=False, is_new_property=True, is_reassigned=True, contact.status=closed -> NO template."""
        parsed = _make_parsed_lead(is_reassigned=True)
        contact = _make_contact()
        contact.status = "closed"
        contact.baja_at = None

        svc = _make_service_for_process_lead(None)
        mock_session = _make_mock_session()
        svc._session_factory = MagicMock(return_value=mock_session)

        with patch.object(svc, "_notify_new_lead", new_callable=AsyncMock), \
             patch.object(svc, "_send_whatsapp_recurrente_directo", new_callable=AsyncMock) as mock_recurrente, \
             patch.object(svc, "_send_whatsapp_recurrente_reenviado", new_callable=AsyncMock) as mock_rec_reenviado, \
             patch.object(svc, "_upsert_contact", new_callable=AsyncMock) as mock_upsert, \
             patch.object(svc, "_match_property", new_callable=AsyncMock) as mock_match, \
             patch.object(svc, "_log_lead_event", new_callable=AsyncMock), \
             patch(
                 "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
                 new=AsyncMock(return_value="true"),
             ), \
             patch("app.bot.services.infocasas.infocasas_service.parse_lead", return_value=parsed):

            mock_upsert.return_value = (False, True, contact)
            mock_match.return_value = None
            await svc._process_lead("tok", "77001234")

        mock_rec_reenviado.assert_not_called()
        mock_recurrente.assert_not_called()


# ---------------------------------------------------------------------------
# Test: _build_recurrente_directo_content_vars
# ---------------------------------------------------------------------------


class TestBuildRecurrenteDirectoContentVars:
    """_build_recurrente_directo_content_vars: 4 variables, truncation, fallback."""

    def test_template3_content_vars_titulo_truncado(self) -> None:
        """Title of 100 chars is truncated to 80."""
        parsed = _make_parsed_lead(name="Ana Martinez", listing_city="Luque")
        ic_prop = MagicMock()
        ic_prop.title = "A" * 100
        ic_prop.city = "Luque"
        ic_prop.property_type = "Casa"
        ic_prop.price_sale = 120_000
        ic_prop.price_rent = None

        result = InfocasasService._build_recurrente_directo_content_vars(parsed, ic_prop)

        assert len(result["2"]) == 80, f"Expected 80 chars, got {len(result['2'])}"
        assert result["2"] == "A" * 80

    def test_template3_content_vars_ic_prop_none_fallback(self) -> None:
        """ic_prop=None → uses parsed.property_title and parsed.listing_city."""
        parsed = _make_parsed_lead(
            name="Juan Perez",
            property_title="Terreno en Fernando de la Mora",
            listing_city="Fernando de la Mora",
        )

        result = InfocasasService._build_recurrente_directo_content_vars(parsed, None)

        assert result["1"] == "Juan Perez"
        assert result["2"] == "Terreno en Fernando de la Mora"
        assert result["3"] == "Fernando de la Mora"
        # var 4 will be empty or minimal since ic_prop=None
        assert "4" in result

    def test_template3_returns_4_keys(self) -> None:
        """Must return exactly 4 keys: '1' to '4'."""
        parsed = _make_parsed_lead()
        ic_prop = MagicMock()
        ic_prop.title = "Departamento en Asuncion"
        ic_prop.city = "Asuncion"
        ic_prop.property_type = "Departamento"
        ic_prop.price_sale = 85_000
        ic_prop.price_rent = None

        result = InfocasasService._build_recurrente_directo_content_vars(parsed, ic_prop)

        assert set(result.keys()) == {"1", "2", "3", "4"}

    def test_template3_name_in_var1(self) -> None:
        """Var 1 is the contact name from parsed."""
        parsed = _make_parsed_lead(name="Maria Garcia")
        ic_prop = MagicMock()
        ic_prop.title = "Depto"
        ic_prop.city = "Asuncion"
        ic_prop.property_type = "Departamento"
        ic_prop.price_sale = None
        ic_prop.price_rent = None

        result = InfocasasService._build_recurrente_directo_content_vars(parsed, ic_prop)

        assert result["1"] == "Maria Garcia"

    def test_template3_type_price_format(self) -> None:
        """Var 4 formats type+price as 'Casa · USD 120.000'."""
        parsed = _make_parsed_lead()
        ic_prop = MagicMock()
        ic_prop.title = "Casa en Luque"
        ic_prop.city = "Luque"
        ic_prop.property_type = "Casa"
        ic_prop.price_sale = 120_000
        ic_prop.price_rent = None

        result = InfocasasService._build_recurrente_directo_content_vars(parsed, ic_prop)

        assert result["4"] == "Casa · USD 120.000"

    def test_template3_fallback_property_title_truncated(self) -> None:
        """When ic_prop=None and parsed.property_title > 80 chars, truncate."""
        long_title = "Propiedad muy larga " * 5  # 100 chars+
        parsed = _make_parsed_lead(property_title=long_title)

        result = InfocasasService._build_recurrente_directo_content_vars(parsed, None)

        assert len(result["2"]) <= 80


# ---------------------------------------------------------------------------
# Test: _build_recurrente_reenviado_content_vars
# ---------------------------------------------------------------------------


class TestBuildRecurrenteReenviadoContentVars:
    """_build_recurrente_reenviado_content_vars: 5 variables, fallback."""

    def test_template4_content_vars_5_variables(self) -> None:
        """Must return exactly 5 keys: '1' to '5'."""
        contact = _make_contact(name="Pedro Alvarez")
        parsed = _make_parsed_lead(listing_city="San Lorenzo")
        matched = {
            "title": "Terreno en San Lorenzo",
            "city": "San Lorenzo",
            "price": 50_000,
            "currency": "USD",
        }

        result = InfocasasService._build_recurrente_reenviado_content_vars(contact, parsed, matched)

        assert set(result.keys()) == {"1", "2", "3", "4", "5"}

    def test_template4_var1_is_contact_name(self) -> None:
        """Var 1 is contact.name (not parsed.name)."""
        contact = _make_contact(name="Jose Rodriguez")
        parsed = _make_parsed_lead(name="Different Name")
        matched = {"title": "Depto", "city": "Asuncion", "price": 80_000, "currency": "USD"}

        result = InfocasasService._build_recurrente_reenviado_content_vars(contact, parsed, matched)

        assert result["1"] == "Jose Rodriguez"

    def test_template4_var2_is_listing_city(self) -> None:
        """Var 2 is parsed.listing_city (original zone searched)."""
        contact = _make_contact()
        parsed = _make_parsed_lead(listing_city="Ciudad del Este")
        matched = {"title": "Casa", "city": "Lambare", "price": 60_000, "currency": "USD"}

        result = InfocasasService._build_recurrente_reenviado_content_vars(contact, parsed, matched)

        assert result["2"] == "Ciudad del Este"

    def test_template4_matched_property_none_fallback(self) -> None:
        """When matched_property=None, vars 3-5 fall back to parsed data."""
        contact = _make_contact(name="Laura Torres")
        parsed = _make_parsed_lead(
            listing_city="Lambare",
            property_title="Casa en Lambare",
            listing_price=75_000.0,
            listing_currency="USD",
        )

        result = InfocasasService._build_recurrente_reenviado_content_vars(contact, parsed, None)

        assert set(result.keys()) == {"1", "2", "3", "4", "5"}
        # All vars must be strings
        for k, v in result.items():
            assert isinstance(v, str), f"Var {k} is not a string: {v!r}"

    def test_template4_var3_title_from_matched_property(self) -> None:
        """Var 3 is the title from matched_property when available."""
        contact = _make_contact()
        parsed = _make_parsed_lead()
        matched = {"title": "Hermosa Casa Moderna en Asuncion", "city": "Asuncion"}

        result = InfocasasService._build_recurrente_reenviado_content_vars(contact, parsed, matched)

        assert "Hermosa Casa Moderna" in result["3"]

    def test_template4_var3_title_truncated_to_80(self) -> None:
        """Var 3 title from matched_property is truncated to 80 chars."""
        contact = _make_contact()
        parsed = _make_parsed_lead()
        matched = {"title": "X" * 100, "city": "Asuncion"}

        result = InfocasasService._build_recurrente_reenviado_content_vars(contact, parsed, matched)

        assert len(result["3"]) <= 80

    def test_template4_var4_city_from_matched_property(self) -> None:
        """Var 4 is the city from matched_property when available."""
        contact = _make_contact()
        parsed = _make_parsed_lead(listing_city="Original City")
        matched = {"title": "Casa", "city": "Matched City"}

        result = InfocasasService._build_recurrente_reenviado_content_vars(contact, parsed, matched)

        assert result["4"] == "Matched City"

    def test_template4_all_strings(self) -> None:
        """All returned values must be strings (never None)."""
        contact = _make_contact()
        parsed = _make_parsed_lead()
        matched = {"title": "Depto", "city": "Asuncion"}

        result = InfocasasService._build_recurrente_reenviado_content_vars(contact, parsed, matched)

        for k, v in result.items():
            assert isinstance(v, str), f"Var {k} must be str, got {type(v)}: {v!r}"


# ---------------------------------------------------------------------------
# Test: _send_whatsapp_recurrente_directo
# ---------------------------------------------------------------------------


class TestSendWhatsappRecurrenteDirecto:
    """_send_whatsapp_recurrente_directo: sends template 3, skips without SID."""

    def _make_svc(self) -> InfocasasService:
        factory = _make_session_factory()
        svc, _, _ = _make_service_helper(session=factory._mock_return_value.__aenter__.return_value)
        svc._session_factory = factory
        return svc

    @pytest.mark.asyncio
    async def test_skips_when_no_sid(self) -> None:
        """wa_tpl_ic_recurrente_directo_v2 key absent → skip silently, no HTTP call."""
        from tests.bot.test_infocasas_service import _make_service as _make_service_orig
        svc, _, _ = _make_service_orig()
        contact = _make_contact()
        parsed = _make_parsed_lead()

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=AsyncMock(return_value=None),  # no SID
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_cls:
            await svc._send_whatsapp_recurrente_directo(contact, parsed, None)

        mock_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_template_with_correct_content_sid(self) -> None:
        """When SID configured, sends HTTP POST with correct ContentSid."""
        from tests.bot.test_infocasas_service import _make_service as _make_service_orig
        svc, _, _ = _make_service_orig()
        contact = _make_contact()
        parsed = _make_parsed_lead()

        template_sid = "HXrecurrente_directo_001"

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        async def fake_get_value(session, key):
            if key == "wa_tpl_ic_recurrente_directo_v2":
                return template_sid
            if key in ("infocasas_wa_delay_min", "infocasas_wa_delay_max"):
                return "0"
            return None

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=fake_get_value,
        ), patch(
            "app.bot.services.infocasas.infocasas_service.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_cls, patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
            new=AsyncMock(),
        ), patch.object(svc, "_preload_search_context", new=AsyncMock()):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            await svc._send_whatsapp_recurrente_directo(contact, parsed, None)

        mock_http.post.assert_awaited_once()
        call_kwargs = mock_http.post.call_args.kwargs
        assert call_kwargs["data"]["ContentSid"] == template_sid

    @pytest.mark.asyncio
    async def test_uses_contact_phone(self) -> None:
        """Uses contact.phone for the WA destination, not parsed.phone."""
        from tests.bot.test_infocasas_service import _make_service as _make_service_orig
        svc, _, _ = _make_service_orig()
        contact = _make_contact(phone="+595987654321")
        parsed = _make_parsed_lead(phone="+595981234567")

        template_sid = "HXtest_recurrente"

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        async def fake_get_value(session, key):
            if key == "wa_tpl_ic_recurrente_directo_v2":
                return template_sid
            if key in ("infocasas_wa_delay_min", "infocasas_wa_delay_max"):
                return "0"
            return None

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=fake_get_value,
        ), patch(
            "app.bot.services.infocasas.infocasas_service.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_cls, patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
            new=AsyncMock(),
        ), patch.object(svc, "_preload_search_context", new=AsyncMock()):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            await svc._send_whatsapp_recurrente_directo(contact, parsed, None)

        call_kwargs = mock_http.post.call_args.kwargs
        assert call_kwargs["data"]["To"] == "whatsapp:+595987654321"

    @pytest.mark.asyncio
    async def test_http_error_does_not_raise(self) -> None:
        """Network errors are swallowed — never raised."""
        from tests.bot.test_infocasas_service import _make_service as _make_service_orig
        svc, _, _ = _make_service_orig()
        contact = _make_contact()
        parsed = _make_parsed_lead()

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(side_effect=Exception("connection refused"))

        async def fake_get_value(session, key):
            if key == "wa_tpl_ic_recurrente_directo_v2":
                return "HXtest"
            return "0"

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=fake_get_value,
        ), patch(
            "app.bot.services.infocasas.infocasas_service.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_cls, patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
            new=AsyncMock(),
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            # Must not raise
            await svc._send_whatsapp_recurrente_directo(contact, parsed, None)

    @pytest.mark.asyncio
    async def test_calls_preload_search_context_on_success(self) -> None:
        """On successful send, _preload_search_context is called."""
        from tests.bot.test_infocasas_service import _make_service as _make_service_orig
        svc, _, _ = _make_service_orig()
        contact = _make_contact()
        parsed = _make_parsed_lead()

        template_sid = "HXrecurrente_directo_002"

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        async def fake_get_value(session, key):
            if key == "wa_tpl_ic_recurrente_directo_v2":
                return template_sid
            if key in ("infocasas_wa_delay_min", "infocasas_wa_delay_max"):
                return "0"
            return None

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=fake_get_value,
        ), patch(
            "app.bot.services.infocasas.infocasas_service.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_cls, patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
            new=AsyncMock(),
        ), patch.object(svc, "_preload_search_context", new=AsyncMock()) as mock_preload:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            await svc._send_whatsapp_recurrente_directo(contact, parsed, None)

        mock_preload.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_content_variables_have_4_vars(self) -> None:
        """ContentVariables JSON must contain exactly 4 keys."""
        from tests.bot.test_infocasas_service import _make_service as _make_service_orig
        svc, _, _ = _make_service_orig()
        contact = _make_contact(name="Paula Gomez")
        parsed = _make_parsed_lead(name="Paula Gomez", listing_city="Encarnacion")

        template_sid = "HXrecurrente_directo_003"

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        async def fake_get_value(session, key):
            if key == "wa_tpl_ic_recurrente_directo_v2":
                return template_sid
            if key in ("infocasas_wa_delay_min", "infocasas_wa_delay_max"):
                return "0"
            return None

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=fake_get_value,
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_cls, patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
            new=AsyncMock(),
        ), patch.object(svc, "_preload_search_context", new=AsyncMock()), \
           patch(
               "app.bot.services.infocasas.infocasas_service.PropertyRepository.get_ic_by_ref",
               new=AsyncMock(return_value=None),
           ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            await svc._send_whatsapp_recurrente_directo(contact, parsed, None)

        call_kwargs = mock_http.post.call_args.kwargs
        content_vars = json.loads(call_kwargs["data"]["ContentVariables"])
        assert set(content_vars.keys()) == {"1", "2", "3", "4"}


# ---------------------------------------------------------------------------
# Test: _send_whatsapp_recurrente_reenviado
# ---------------------------------------------------------------------------


class TestSendWhatsappRecurrenteReenviado:
    """_send_whatsapp_recurrente_reenviado: sends template 4, skips without SID."""

    @pytest.mark.asyncio
    async def test_skips_when_no_sid(self) -> None:
        """wa_tpl_ic_recurrente_reenviado_v2 key absent → skip silently, no HTTP call."""
        from tests.bot.test_infocasas_service import _make_service as _make_service_orig
        svc, _, _ = _make_service_orig()
        contact = _make_contact()
        parsed = _make_parsed_lead(is_reassigned=True)

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_cls:
            await svc._send_whatsapp_recurrente_reenviado(contact, parsed, None)

        mock_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_template_with_correct_content_sid(self) -> None:
        """When SID configured, sends HTTP POST with correct ContentSid."""
        from tests.bot.test_infocasas_service import _make_service as _make_service_orig
        svc, _, _ = _make_service_orig()
        contact = _make_contact()
        parsed = _make_parsed_lead(is_reassigned=True)

        template_sid = "HXrecurrente_reenviado_001"

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        async def fake_get_value(session, key):
            if key == "wa_tpl_ic_recurrente_reenviado_v2":
                return template_sid
            if key in ("infocasas_wa_delay_min", "infocasas_wa_delay_max"):
                return "0"
            return None

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=fake_get_value,
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_cls, patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
            new=AsyncMock(),
        ), patch.object(svc, "_preload_reenviado_context", new=AsyncMock()):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            await svc._send_whatsapp_recurrente_reenviado(contact, parsed, None)

        mock_http.post.assert_awaited_once()
        call_kwargs = mock_http.post.call_args.kwargs
        assert call_kwargs["data"]["ContentSid"] == template_sid

    @pytest.mark.asyncio
    async def test_content_variables_have_5_vars(self) -> None:
        """ContentVariables JSON must contain exactly 5 keys."""
        from tests.bot.test_infocasas_service import _make_service as _make_service_orig
        svc, _, _ = _make_service_orig()
        contact = _make_contact(name="Roberto Diaz")
        parsed = _make_parsed_lead(is_reassigned=True, listing_city="Capiata")

        template_sid = "HXrecurrente_reenviado_002"

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        async def fake_get_value(session, key):
            if key == "wa_tpl_ic_recurrente_reenviado_v2":
                return template_sid
            if key in ("infocasas_wa_delay_min", "infocasas_wa_delay_max"):
                return "0"
            return None

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=fake_get_value,
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_cls, patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
            new=AsyncMock(),
        ), patch.object(svc, "_preload_reenviado_context", new=AsyncMock()):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            await svc._send_whatsapp_recurrente_reenviado(contact, parsed, None)

        call_kwargs = mock_http.post.call_args.kwargs
        content_vars = json.loads(call_kwargs["data"]["ContentVariables"])
        assert set(content_vars.keys()) == {"1", "2", "3", "4", "5"}

    @pytest.mark.asyncio
    async def test_http_error_does_not_raise(self) -> None:
        """Network errors are swallowed."""
        from tests.bot.test_infocasas_service import _make_service as _make_service_orig
        svc, _, _ = _make_service_orig()
        contact = _make_contact()
        parsed = _make_parsed_lead(is_reassigned=True)

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(side_effect=Exception("network error"))

        async def fake_get_value(session, key):
            if key == "wa_tpl_ic_recurrente_reenviado_v2":
                return "HXtest_reenviado"
            return "0"

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=fake_get_value,
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_cls, patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
            new=AsyncMock(),
        ):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            # Must not raise
            await svc._send_whatsapp_recurrente_reenviado(contact, parsed, None)

    @pytest.mark.asyncio
    async def test_calls_preload_reenviado_context_on_success(self) -> None:
        """On successful send, _preload_reenviado_context is called."""
        from tests.bot.test_infocasas_service import _make_service as _make_service_orig
        svc, _, _ = _make_service_orig()
        contact = _make_contact()
        parsed = _make_parsed_lead(is_reassigned=True)

        template_sid = "HXrecurrente_reenviado_003"

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        async def fake_get_value(session, key):
            if key == "wa_tpl_ic_recurrente_reenviado_v2":
                return template_sid
            if key in ("infocasas_wa_delay_min", "infocasas_wa_delay_max"):
                return "0"
            return None

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=fake_get_value,
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_cls, patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
            new=AsyncMock(),
        ), patch.object(svc, "_preload_reenviado_context", new=AsyncMock()) as mock_preload:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            await svc._send_whatsapp_recurrente_reenviado(contact, parsed, None)

        mock_preload.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_uses_parsed_phone_for_destination(self) -> None:
        """Uses parsed.phone (same as contact.phone from context) for WA To field."""
        from tests.bot.test_infocasas_service import _make_service as _make_service_orig
        svc, _, _ = _make_service_orig()
        contact = _make_contact(phone="+595981111111")
        parsed = _make_parsed_lead(is_reassigned=True, phone="+595981111111")

        template_sid = "HXrecurrente_reenviado_004"

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)

        async def fake_get_value(session, key):
            if key == "wa_tpl_ic_recurrente_reenviado_v2":
                return template_sid
            if key in ("infocasas_wa_delay_min", "infocasas_wa_delay_max"):
                return "0"
            return None

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=fake_get_value,
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_cls, patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
            new=AsyncMock(),
        ), patch.object(svc, "_preload_reenviado_context", new=AsyncMock()):
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            await svc._send_whatsapp_recurrente_reenviado(contact, parsed, None)

        call_kwargs = mock_http.post.call_args.kwargs
        assert call_kwargs["data"]["To"] == "whatsapp:+595981111111"


# ---------------------------------------------------------------------------
# Internal helper (mirrors test_infocasas_service.py's _make_service)
# ---------------------------------------------------------------------------


def _make_service_helper(
    *,
    session: AsyncMock | None = None,
) -> tuple[InfocasasService, MagicMock, MagicMock]:
    """Thin wrapper over the test_infocasas_service helper."""
    from tests.bot.test_infocasas_service import _make_service
    return _make_service(session=session)
