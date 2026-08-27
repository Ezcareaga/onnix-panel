"""Tests for InfocasasService._send_whatsapp_reenviado_welcome() and toggle wiring.

Covers:
- Template SID lookup and Twilio POST
- Skip when template not configured
- Context pre-loading with etapa and filtros
- Toggle ic_autoreply_reenviados_enabled on/off for is_reassigned=True
- Toggle for directo sin match (fallback reenviado path)
- _build_reenviado_content_vars: IC-prop path and fallback path
- _send_whatsapp_reenviado_welcome uses IC property record when infocasas_ref set
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.services.infocasas.infocasas_service import InfocasasService
from app.bot.services.infocasas.lead_parser import ParsedLead


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_parsed_lead_reenviado(
    *,
    name: str = "Daniel Perez",
    phone: str | None = "+595981500746",
    listing_type: str | None = "casa",
    listing_operation: str | None = "venta",
    listing_bedrooms: int | None = 3,
    listing_city: str | None = "Luque",
    listing_price: float | None = 90000.0,
    listing_currency: str | None = "usd",
    is_reassigned: bool = True,
) -> ParsedLead:
    """Build a ParsedLead with reenviado data and characteristics fields."""
    return ParsedLead(
        consulta_id="99990001",
        name=name,
        phone=phone,
        email="daniel@example.com",
        message="consulta reenviada",
        consulta_date=datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc),
        property_code=None,
        property_title="Casa en Luque",
        listing_city=listing_city,
        has_whatsapp=False,
        is_reassigned=is_reassigned,
        listing_type=listing_type,
        listing_operation=listing_operation,
        listing_bedrooms=listing_bedrooms,
        listing_area_m2=None,
        listing_price=listing_price,
        listing_currency=listing_currency,
    )


def _make_mock_contact(contact_id: int = 42, infocasas_ref: str | None = None) -> MagicMock:
    contact = MagicMock()
    contact.id = contact_id
    contact.phone = "+595981500746"
    # Explicitly set infocasas_ref so getattr() doesn't return a truthy MagicMock.
    contact.infocasas_ref = infocasas_ref
    return contact


def _make_mock_ic_prop(
    *,
    property_type: str | None = "casa",
    operation: str | None = "venta",
    city: str | None = "Los Laureles",
    neighborhood: str | None = None,
    title: str | None = "Amplia casa en esquina en Los Laureles",
    price_sale: float | None = 150000.0,
    currency_sale: str | None = "usd",
    price_rent: float | None = None,
    currency_rent: str | None = None,
    property_id: int | None = None,
) -> MagicMock:
    """Build a mock InfocasasProperty ORM object."""
    prop = MagicMock()
    prop.property_type = property_type
    prop.operation = operation
    prop.city = city
    prop.neighborhood = neighborhood
    prop.title = title
    prop.price_sale = price_sale
    prop.currency_sale = currency_sale
    prop.price_rent = price_rent
    prop.currency_rent = currency_rent
    prop.property_id = property_id
    return prop


def _make_session_factory_with_mock(mock_session: AsyncMock) -> MagicMock:
    """Return an async context manager factory wrapping mock_session."""
    mock_session.add = MagicMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=mock_ctx)
    return factory


def _make_service(session_factory=None) -> InfocasasService:
    sm = AsyncMock()
    nf = AsyncMock()
    svc = InfocasasService(
        session_manager=sm,
        notification_fetcher=nf,
        notifier=None,
        session_factory=session_factory or _make_session_factory_with_mock(AsyncMock()),
    )
    return svc


# ---------------------------------------------------------------------------
# Test 6: Envía template correcto via Twilio
# ---------------------------------------------------------------------------

class TestReenviadoWelcomeEnviaTemplate:
    """_send_whatsapp_reenviado_welcome sends correct template via Twilio."""

    @pytest.mark.asyncio
    async def test_twilio_called_with_correct_content_sid(self):
        """When wa_tpl_ic_reenviado_welcome_v3 is configured, Twilio POST is made."""
        parsed = _make_parsed_lead_reenviado()
        contact = _make_mock_contact()
        mock_session = AsyncMock()
        svc = _make_service(_make_session_factory_with_mock(mock_session))

        mock_resp = MagicMock()
        mock_resp.status_code = 201

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=AsyncMock(side_effect=lambda _s, key: "HXreenviado_sid123" if key == "wa_tpl_ic_reenviado_welcome_v3" else None),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
            new=AsyncMock(),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_http, patch.object(
            svc, "_save_reenviado_message", new_callable=AsyncMock
        ), patch.object(
            svc, "_preload_reenviado_context", new_callable=AsyncMock
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_http.return_value = mock_client

            await svc._send_whatsapp_reenviado_welcome(contact, parsed, mock_session)

        mock_client.post.assert_awaited_once()
        call_kwargs = mock_client.post.call_args
        data_sent = call_kwargs[1]["data"] if "data" in call_kwargs[1] else call_kwargs[0][1]
        # ContentSid must match configured template
        assert data_sent["ContentSid"] == "HXreenviado_sid123"

    @pytest.mark.asyncio
    async def test_content_variables_contain_name_and_zona(self):
        """ContentVariables must have {1: name, 2: titulo, 3: ciudad, 4: precio}."""
        parsed = _make_parsed_lead_reenviado(name="Daniel Perez", listing_city="Luque")
        contact = _make_mock_contact()
        mock_session = AsyncMock()
        svc = _make_service(_make_session_factory_with_mock(mock_session))

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=AsyncMock(side_effect=lambda _s, key: "HXreenviado_sid123" if key == "wa_tpl_ic_reenviado_welcome_v3" else None),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
            new=AsyncMock(),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_http, patch.object(
            svc, "_save_reenviado_message", new_callable=AsyncMock
        ), patch.object(
            svc, "_preload_reenviado_context", new_callable=AsyncMock
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_http.return_value = mock_client

            await svc._send_whatsapp_reenviado_welcome(contact, parsed, mock_session)

        call_kwargs = mock_client.post.call_args
        data_sent = call_kwargs[1]["data"]
        content_vars = json.loads(data_sent["ContentVariables"])
        assert content_vars["1"] == "Daniel Perez"  # nombre
        assert "2" in content_vars                   # titulo (any value)
        assert content_vars["3"] == "Luque"          # ciudad
        assert "4" in content_vars                   # precio (any value)
        assert "5" not in content_vars               # operacion removed

    @pytest.mark.asyncio
    async def test_content_variables_4_vars_all_fields(self):
        """ContentVariables must have 4 keys: name, titulo, ciudad, precio."""
        parsed = _make_parsed_lead_reenviado(
            name="Daniel Perez",
            listing_city="Luque",
            listing_type="casa",
            listing_operation="venta",
            listing_price=90000.0,
            listing_currency="usd",
        )
        contact = _make_mock_contact()
        mock_session = AsyncMock()
        svc = _make_service(_make_session_factory_with_mock(mock_session))

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=AsyncMock(side_effect=lambda _s, key: "HXreenviado_sid123" if key == "wa_tpl_ic_reenviado_welcome_v3" else None),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
            new=AsyncMock(),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_http, patch.object(
            svc, "_save_reenviado_message", new_callable=AsyncMock
        ), patch.object(
            svc, "_preload_reenviado_context", new_callable=AsyncMock
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_http.return_value = mock_client

            await svc._send_whatsapp_reenviado_welcome(contact, parsed, mock_session)

        call_kwargs = mock_client.post.call_args
        data_sent = call_kwargs[1]["data"]
        content_vars = json.loads(data_sent["ContentVariables"])
        # Must have exactly 4 keys
        assert set(content_vars.keys()) == {"1", "2", "3", "4"}
        assert content_vars["1"] == "Daniel Perez"       # nombre
        assert content_vars["2"] == "Casa en Luque"      # titulo (from parsed.property_title)
        assert content_vars["3"] == "Luque"              # ciudad
        assert content_vars["4"] == "USD 90.000"         # precio

    @pytest.mark.asyncio
    async def test_content_variables_5_vars_gs_price(self):
        """When currency is gs, price is formatted as Gs. X.XXX."""
        parsed = _make_parsed_lead_reenviado(
            name="Maria Lopez",
            listing_city="Asuncion",
            listing_type="departamento",
            listing_operation="alquiler",
            listing_price=1500000.0,
            listing_currency="gs",
        )
        contact = _make_mock_contact()
        mock_session = AsyncMock()
        svc = _make_service(_make_session_factory_with_mock(mock_session))

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=AsyncMock(side_effect=lambda _s, key: "HXreenviado_sid123" if key == "wa_tpl_ic_reenviado_welcome_v3" else None),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
            new=AsyncMock(),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_http, patch.object(
            svc, "_save_reenviado_message", new_callable=AsyncMock
        ), patch.object(
            svc, "_preload_reenviado_context", new_callable=AsyncMock
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_http.return_value = mock_client

            await svc._send_whatsapp_reenviado_welcome(contact, parsed, mock_session)

        call_kwargs = mock_client.post.call_args
        data_sent = call_kwargs[1]["data"]
        content_vars = json.loads(data_sent["ContentVariables"])
        assert set(content_vars.keys()) == {"1", "2", "3", "4"}
        assert content_vars["1"] == "Maria Lopez"
        assert "2" in content_vars                   # titulo (any value)
        assert content_vars["3"] == "Asuncion"
        assert content_vars["4"] == "Gs. 1.500.000"

    @pytest.mark.asyncio
    async def test_content_variables_5_vars_no_price_fallback(self):
        """When no price available, var 4 is 'consultar precio'."""
        parsed = _make_parsed_lead_reenviado(
            listing_price=None,
            listing_currency=None,
        )
        contact = _make_mock_contact()
        mock_session = AsyncMock()
        svc = _make_service(_make_session_factory_with_mock(mock_session))

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=AsyncMock(side_effect=lambda _s, key: "HXreenviado_sid123" if key == "wa_tpl_ic_reenviado_welcome_v3" else None),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
            new=AsyncMock(),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_http, patch.object(
            svc, "_save_reenviado_message", new_callable=AsyncMock
        ), patch.object(
            svc, "_preload_reenviado_context", new_callable=AsyncMock
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_http.return_value = mock_client

            await svc._send_whatsapp_reenviado_welcome(contact, parsed, mock_session)

        call_kwargs = mock_client.post.call_args
        data_sent = call_kwargs[1]["data"]
        content_vars = json.loads(data_sent["ContentVariables"])
        assert set(content_vars.keys()) == {"1", "2", "3", "4"}
        assert content_vars["4"] == "consultar precio"


# ---------------------------------------------------------------------------
# Test 7: Skip when template not configured
# ---------------------------------------------------------------------------

class TestReenviadoWelcomeTemplateNoConfig:
    """_send_whatsapp_reenviado_welcome skips when template key missing."""

    @pytest.mark.asyncio
    async def test_returns_without_calling_twilio(self):
        parsed = _make_parsed_lead_reenviado()
        contact = _make_mock_contact()
        mock_session = AsyncMock()
        svc = _make_service(_make_session_factory_with_mock(mock_session))

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=AsyncMock(return_value=None),  # no template configured
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_http:
            await svc._send_whatsapp_reenviado_welcome(contact, parsed, mock_session)

        mock_http.assert_not_called()


# ---------------------------------------------------------------------------
# Test 8: Preload filtros en search_context
# ---------------------------------------------------------------------------

class TestReenviadoWelcomePreloadFiltros:
    """After successful send, _preload_search_context is called (not _preload_reenviado_context).

    This ensures last_detalle_id is set from ic_prop.property_id when available,
    so VER_DETALLES shows the specific IC-assigned property rather than a generic search.
    """

    @pytest.mark.asyncio
    async def test_preload_search_context_called_on_success(self):
        """_preload_search_context is called after successful send, not _preload_reenviado_context."""
        parsed = _make_parsed_lead_reenviado(listing_city="Luque", listing_operation="venta")
        contact = _make_mock_contact(infocasas_ref=None)  # no IC ref → ic_prop_full=None
        mock_session = AsyncMock()
        svc = _make_service(_make_session_factory_with_mock(mock_session))

        mock_resp = MagicMock()
        mock_resp.status_code = 201

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=AsyncMock(side_effect=lambda _s, key: "HXreenviado_sid" if key == "wa_tpl_ic_reenviado_welcome_v3" else None),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
            new=AsyncMock(),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_http, patch.object(
            svc, "_save_reenviado_message", new_callable=AsyncMock
        ), patch.object(
            svc, "_preload_search_context", new_callable=AsyncMock
        ) as mock_preload, patch.object(
            svc, "_preload_reenviado_context", new_callable=AsyncMock
        ) as mock_old_preload:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_http.return_value = mock_client

            await svc._send_whatsapp_reenviado_welcome(contact, parsed, mock_session)

        mock_preload.assert_awaited_once()
        mock_old_preload.assert_not_awaited()
        call_args = mock_preload.call_args
        assert call_args[1]["contact_id"] == contact.id or call_args[0][0] == contact.id

    @pytest.mark.asyncio
    async def test_preload_search_context_receives_ic_prop_full(self):
        """When infocasas_ref resolves to an IC property, ic_prop_full is passed to preload."""
        parsed = _make_parsed_lead_reenviado()
        ic_prop = _make_mock_ic_prop(property_id=752429)
        contact = _make_mock_contact(infocasas_ref="K75763")
        mock_session = AsyncMock()
        svc = _make_service(_make_session_factory_with_mock(mock_session))

        mock_resp = MagicMock()
        mock_resp.status_code = 201

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=AsyncMock(side_effect=lambda _s, key: "HXreenviado_sid" if key == "wa_tpl_ic_reenviado_welcome_v3" else None),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
            new=AsyncMock(),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=ic_prop),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_http, patch.object(
            svc, "_save_reenviado_message", new_callable=AsyncMock
        ), patch.object(
            svc, "_preload_search_context", new_callable=AsyncMock
        ) as mock_preload:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_http.return_value = mock_client

            await svc._send_whatsapp_reenviado_welcome(contact, parsed, mock_session)

        mock_preload.assert_awaited_once()
        call_kwargs = mock_preload.call_args[1]
        assert call_kwargs["ic_prop_full"] is ic_prop


# ---------------------------------------------------------------------------
# Test 9: Toggle reenviados off → skip is_reassigned
# ---------------------------------------------------------------------------

class TestToggleReenviadosOff:
    """ic_autoreply_reenviados_enabled=false → skip _send_whatsapp_reenviado_welcome."""

    @pytest.mark.asyncio
    async def test_reenviado_welcome_not_called_when_toggle_off(self):
        """When toggle=false, is_reassigned=True → reenviado welcome NOT called."""
        parsed = _make_parsed_lead_reenviado(is_reassigned=True)
        svc = _make_service(None)

        with patch.object(svc, "_notify_new_lead", new_callable=AsyncMock), \
             patch.object(svc, "_send_whatsapp_welcome", new_callable=AsyncMock) as mock_direct, \
             patch.object(svc, "_send_whatsapp_reenviado_welcome", new_callable=AsyncMock) as mock_reenviado, \
             patch.object(svc, "_upsert_contact", new_callable=AsyncMock) as mock_upsert, \
             patch.object(svc, "_match_property", new_callable=AsyncMock) as mock_match, \
             patch.object(svc, "_log_lead_event", new_callable=AsyncMock):

            from app.models.contact import Contact
            contact = MagicMock(spec=Contact)
            contact.id = 1
            mock_upsert.return_value = (True, True, contact)
            mock_match.return_value = {"city": "Luque", "matched_by": "infocasas_ref"}

            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session.commit = AsyncMock()
            svc._session_factory = MagicMock(return_value=mock_session)

            # Toggle is off
            with patch(
                "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
                new=AsyncMock(return_value="false"),
            ), patch("app.bot.services.infocasas.infocasas_service.parse_lead", return_value=parsed), \
               patch.object(svc._fetcher, "fetch_lead_details", new_callable=AsyncMock, return_value={"id": "1"}):
                await svc._process_lead("tok", "1")

        mock_reenviado.assert_not_called()
        mock_direct.assert_not_called()


# ---------------------------------------------------------------------------
# Test 10: Toggle reenviados on → calls reenviado welcome for is_reassigned
# ---------------------------------------------------------------------------

class TestToggleReenviadosOn:
    """ic_autoreply_reenviados_enabled=true → calls _send_whatsapp_reenviado_welcome."""

    @pytest.mark.asyncio
    async def test_reenviado_welcome_called_when_toggle_on(self):
        """When toggle=true, is_reassigned=True → reenviado welcome IS called."""
        parsed = _make_parsed_lead_reenviado(is_reassigned=True)
        svc = _make_service(None)

        with patch.object(svc, "_notify_new_lead", new_callable=AsyncMock), \
             patch.object(svc, "_send_whatsapp_welcome", new_callable=AsyncMock) as mock_direct, \
             patch.object(svc, "_send_whatsapp_reenviado_welcome", new_callable=AsyncMock) as mock_reenviado, \
             patch.object(svc, "_upsert_contact", new_callable=AsyncMock) as mock_upsert, \
             patch.object(svc, "_match_property", new_callable=AsyncMock) as mock_match, \
             patch.object(svc, "_log_lead_event", new_callable=AsyncMock):

            from app.models.contact import Contact
            contact = MagicMock(spec=Contact)
            contact.id = 1
            mock_upsert.return_value = (True, True, contact)
            mock_match.return_value = None

            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session.commit = AsyncMock()
            svc._session_factory = MagicMock(return_value=mock_session)

            # Toggle is on ("true")
            with patch(
                "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
                new=AsyncMock(return_value="true"),
            ), patch("app.bot.services.infocasas.infocasas_service.parse_lead", return_value=parsed), \
               patch.object(svc._fetcher, "fetch_lead_details", new_callable=AsyncMock, return_value={"id": "1"}):
                await svc._process_lead("tok", "1")

        mock_reenviado.assert_called_once()
        mock_direct.assert_not_called()


# ---------------------------------------------------------------------------
# Test 11: Directo sin match + toggle off → skip reenviado welcome
# ---------------------------------------------------------------------------

class TestDirectoSinMatchToggleOff:
    """Direct lead with no matched property and toggle=false → skip reenviado welcome."""

    @pytest.mark.asyncio
    async def test_reenviado_welcome_not_called_directo_sin_match_toggle_off(self):
        parsed = _make_parsed_lead_reenviado(is_reassigned=False)  # direct lead, no match
        svc = _make_service(None)

        with patch.object(svc, "_notify_new_lead", new_callable=AsyncMock), \
             patch.object(svc, "_send_whatsapp_welcome", new_callable=AsyncMock) as mock_direct, \
             patch.object(svc, "_send_whatsapp_reenviado_welcome", new_callable=AsyncMock) as mock_reenviado, \
             patch.object(svc, "_upsert_contact", new_callable=AsyncMock) as mock_upsert, \
             patch.object(svc, "_match_property", new_callable=AsyncMock) as mock_match, \
             patch.object(svc, "_log_lead_event", new_callable=AsyncMock):

            from app.models.contact import Contact
            contact = MagicMock(spec=Contact)
            contact.id = 1
            mock_upsert.return_value = (True, True, contact)
            mock_match.return_value = None  # no matched property

            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session.commit = AsyncMock()
            svc._session_factory = MagicMock(return_value=mock_session)

            # Toggle is off
            with patch(
                "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
                new=AsyncMock(return_value="false"),
            ), patch("app.bot.services.infocasas.infocasas_service.parse_lead", return_value=parsed), \
               patch.object(svc._fetcher, "fetch_lead_details", new_callable=AsyncMock, return_value={"id": "1"}):
                await svc._process_lead("tok", "1")

        mock_reenviado.assert_not_called()
        mock_direct.assert_not_called()


# ---------------------------------------------------------------------------
# Test 12: Directo sin match + toggle on → calls reenviado welcome
# ---------------------------------------------------------------------------

class TestDirectoSinMatchToggleOn:
    """Direct lead with no matched property and toggle=true → calls reenviado welcome."""

    @pytest.mark.asyncio
    async def test_reenviado_welcome_called_directo_sin_match_toggle_on(self):
        parsed = _make_parsed_lead_reenviado(is_reassigned=False)  # direct lead, no match
        svc = _make_service(None)

        with patch.object(svc, "_notify_new_lead", new_callable=AsyncMock), \
             patch.object(svc, "_send_whatsapp_welcome", new_callable=AsyncMock) as mock_direct, \
             patch.object(svc, "_send_whatsapp_reenviado_welcome", new_callable=AsyncMock) as mock_reenviado, \
             patch.object(svc, "_upsert_contact", new_callable=AsyncMock) as mock_upsert, \
             patch.object(svc, "_match_property", new_callable=AsyncMock) as mock_match, \
             patch.object(svc, "_log_lead_event", new_callable=AsyncMock):

            from app.models.contact import Contact
            contact = MagicMock(spec=Contact)
            contact.id = 1
            mock_upsert.return_value = (True, True, contact)
            mock_match.return_value = None  # no matched property

            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session.commit = AsyncMock()
            svc._session_factory = MagicMock(return_value=mock_session)

            # Toggle is on
            with patch(
                "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
                new=AsyncMock(return_value="true"),
            ), patch("app.bot.services.infocasas.infocasas_service.parse_lead", return_value=parsed), \
               patch.object(svc._fetcher, "fetch_lead_details", new_callable=AsyncMock, return_value={"id": "1"}):
                await svc._process_lead("tok", "1")

        mock_reenviado.assert_called_once()
        mock_direct.assert_not_called()


# ---------------------------------------------------------------------------
# Test 13: _build_reenviado_content_vars — IC-prop path and fallback
# ---------------------------------------------------------------------------

class TestBuildReenviadoContentVars:
    """Unit tests for InfocasasService._build_reenviado_content_vars (static method)."""

    def test_venta_usd_price_uses_ic_prop_data(self):
        """operation=venta with USD price → 'USD X.XXX', city used as ciudad."""
        parsed = _make_parsed_lead_reenviado(
            name="Geronimo Vera",
            listing_city="Luque",          # should NOT appear — ic_prop overrides
            listing_type="terreno",        # should NOT appear
            listing_operation="alquiler",  # should NOT appear
            listing_price=50000.0,
            listing_currency="usd",
        )
        ic_prop = _make_mock_ic_prop(
            property_type="casa",
            operation="venta",
            city="Los Laureles",
            title="Amplia casa en esquina en Los Laureles",
            price_sale=150000.0,
            currency_sale="usd",
        )

        result = InfocasasService._build_reenviado_content_vars(parsed, ic_prop)

        assert result["1"] == "Geronimo Vera"
        assert result["2"] == "Amplia casa en esquina en Los Laureles"  # titulo from ic_prop
        assert result["3"] == "Los Laureles"                            # ciudad from ic_prop
        assert result["4"] == "USD 150.000"
        assert "5" not in result

    def test_alquiler_pyg_price(self):
        """operation=alquiler with PYG price → 'Gs. X.XXX.XXX' from price_rent."""
        parsed = _make_parsed_lead_reenviado(name="Maria Lopez")
        ic_prop = _make_mock_ic_prop(
            property_type="departamento",
            operation="alquiler",
            city="Asuncion",
            title="Departamento en alquiler en Asuncion",
            price_sale=None,
            currency_sale=None,
            price_rent=1500000.0,
            currency_rent="gs",
        )

        result = InfocasasService._build_reenviado_content_vars(parsed, ic_prop)

        assert result["2"] == "Departamento en alquiler en Asuncion"  # titulo
        assert result["3"] == "Asuncion"                              # ciudad
        assert result["4"] == "Gs. 1.500.000"
        assert "5" not in result

    def test_no_city_falls_back_to_neighborhood(self):
        """When ic_prop.city is None, uses neighborhood as ciudad."""
        parsed = _make_parsed_lead_reenviado(name="Test User")
        ic_prop = _make_mock_ic_prop(
            city=None,
            neighborhood="Villa Morra",
            title="Casa linda en Villa Morra",
        )

        result = InfocasasService._build_reenviado_content_vars(parsed, ic_prop)

        assert result["3"] == "Villa Morra"

    def test_no_city_no_neighborhood_falls_back_to_tu_zona(self):
        """When city and neighborhood are None, ciudad defaults to 'tu zona'."""
        parsed = _make_parsed_lead_reenviado(name="Test User")
        ic_prop = _make_mock_ic_prop(
            city=None,
            neighborhood=None,
            title="Amplia casa en esquina en Los Laureles con piscina",
        )

        result = InfocasasService._build_reenviado_content_vars(parsed, ic_prop)

        assert result["3"] == "tu zona"
        # titulo still comes from ic_prop.title[:60]
        assert result["2"] == "Amplia casa en esquina en Los Laureles con piscina"

    def test_missing_price_defaults_to_consultar_precio(self):
        """When no price field is set, var 4 is 'consultar precio'."""
        parsed = _make_parsed_lead_reenviado(name="Test User")
        ic_prop = _make_mock_ic_prop(
            operation="venta",
            price_sale=None,
            currency_sale=None,
        )

        result = InfocasasService._build_reenviado_content_vars(parsed, ic_prop)

        assert result["4"] == "consultar precio"

    def test_missing_ic_prop_title_falls_back_to_parsed_property_title(self):
        """When ic_prop.title is None, var 2 uses parsed.property_title."""
        parsed = _make_parsed_lead_reenviado(name="Test User")
        ic_prop = _make_mock_ic_prop(title=None)

        result = InfocasasService._build_reenviado_content_vars(parsed, ic_prop)

        # parsed.property_title is "Casa en Luque" (from _make_parsed_lead_reenviado default)
        assert result["2"] == "Casa en Luque"

    def test_ic_prop_none_falls_back_to_parsed_listing_data(self):
        """When ic_prop is None, falls back to parsed.listing_* and property_title."""
        parsed = _make_parsed_lead_reenviado(
            name="Daniel Perez",
            listing_city="Luque",
            listing_type="casa",
            listing_operation="venta",
            listing_price=90000.0,
            listing_currency="usd",
        )

        result = InfocasasService._build_reenviado_content_vars(parsed, None)

        assert result["1"] == "Daniel Perez"
        assert result["2"] == "Casa en Luque"  # from parsed.property_title
        assert result["3"] == "Luque"           # from parsed.listing_city
        assert result["4"] == "USD 90.000"
        assert "5" not in result

    def test_ic_prop_none_fallback_gs_price(self):
        """When ic_prop is None and currency is gs, price is 'Gs. X.XXX.XXX'."""
        parsed = _make_parsed_lead_reenviado(
            listing_price=1500000.0,
            listing_currency="gs",
            listing_operation="alquiler",
        )

        result = InfocasasService._build_reenviado_content_vars(parsed, None)

        assert result["4"] == "Gs. 1.500.000"
        assert "5" not in result

    def test_ic_prop_none_fallback_no_price(self):
        """When ic_prop is None and no listing price, var 4 is 'consultar precio'."""
        parsed = _make_parsed_lead_reenviado(
            listing_price=None,
            listing_currency=None,
        )

        result = InfocasasService._build_reenviado_content_vars(parsed, None)

        assert result["4"] == "consultar precio"


# ---------------------------------------------------------------------------
# Test 14: _send_whatsapp_reenviado_welcome uses IC property when infocasas_ref set
# ---------------------------------------------------------------------------

class TestReenviadoWelcomeUsesIcPropData:
    """When contact.infocasas_ref is set, content_vars come from IC property record."""

    @pytest.mark.asyncio
    async def test_content_vars_from_ic_prop_when_ref_set(self):
        """content_vars must use ic_prop fields, not parsed.listing_* when ref is set."""
        # parsed has client search preferences — these should NOT appear in template
        parsed = _make_parsed_lead_reenviado(
            name="Geronimo Vera",
            listing_city="Luque",          # client searched in Luque
            listing_type="terreno",        # client searched for terreno
            listing_operation="alquiler",  # client wanted alquiler
            listing_price=50000.0,
            listing_currency="usd",
        )
        # contact.infocasas_ref points to the actual assigned property
        contact = _make_mock_contact(contact_id=99, infocasas_ref="IC-98765")
        mock_session = AsyncMock()
        svc = _make_service(_make_session_factory_with_mock(mock_session))

        # IC property: the real listing Geronimo was assigned
        ic_prop = _make_mock_ic_prop(
            property_type="casa",
            operation="venta",
            city="Los Laureles",
            price_sale=150000.0,
            currency_sale="usd",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 201

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=AsyncMock(side_effect=lambda _s, key: "HXreenviado_sid" if key == "wa_tpl_ic_reenviado_welcome_v3" else None),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
            new=AsyncMock(),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=ic_prop),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_http, patch.object(
            svc, "_save_reenviado_message", new_callable=AsyncMock
        ), patch.object(
            svc, "_preload_reenviado_context", new_callable=AsyncMock
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_http.return_value = mock_client

            await svc._send_whatsapp_reenviado_welcome(contact, parsed, mock_session)

        call_kwargs = mock_client.post.call_args
        data_sent = call_kwargs[1]["data"]
        content_vars = json.loads(data_sent["ContentVariables"])

        assert content_vars["1"] == "Geronimo Vera"
        assert content_vars["2"] == "Amplia casa en esquina en Los Laureles"  # titulo from ic_prop
        assert content_vars["3"] == "Los Laureles"  # ciudad from ic_prop, not "Luque"
        assert content_vars["4"] == "USD 150.000"   # from ic_prop, not "50.000"
        assert "5" not in content_vars              # operacion removed

    @pytest.mark.asyncio
    async def test_fallback_to_parsed_when_ic_prop_not_found(self):
        """When infocasas_ref set but get_ic_by_ref returns None, falls back to parsed."""
        parsed = _make_parsed_lead_reenviado(
            name="Ana Martinez",
            listing_city="Asuncion",
            listing_type="departamento",
            listing_operation="alquiler",
            listing_price=2000000.0,
            listing_currency="gs",
        )
        contact = _make_mock_contact(contact_id=55, infocasas_ref="IC-99999")
        mock_session = AsyncMock()
        svc = _make_service(_make_session_factory_with_mock(mock_session))

        mock_resp = MagicMock()
        mock_resp.status_code = 201

        with patch(
            "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
            new=AsyncMock(side_effect=lambda _s, key: "HXreenviado_sid" if key == "wa_tpl_ic_reenviado_welcome_v3" else None),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
            new=AsyncMock(),
        ), patch(
            "app.bot.services.infocasas.infocasas_service.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=None),  # not found
        ), patch(
            "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient"
        ) as mock_http, patch.object(
            svc, "_save_reenviado_message", new_callable=AsyncMock
        ), patch.object(
            svc, "_preload_reenviado_context", new_callable=AsyncMock
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_http.return_value = mock_client

            await svc._send_whatsapp_reenviado_welcome(contact, parsed, mock_session)

        call_kwargs = mock_client.post.call_args
        data_sent = call_kwargs[1]["data"]
        content_vars = json.loads(data_sent["ContentVariables"])

        # Fallback to parsed.listing_*
        assert content_vars["1"] == "Ana Martinez"
        assert "2" in content_vars                   # titulo from parsed.property_title
        assert content_vars["3"] == "Asuncion"       # ciudad from parsed.listing_city
        assert content_vars["4"] == "Gs. 2.000.000"
        assert "5" not in content_vars               # operacion removed
