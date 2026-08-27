"""Tests for _preload_search_context in InfocasasService.

Covers:
  - last_detalle_id uses property_id (FK to properties), not ic_prop.id
  - Early-return guards (property_id=None, ic_prop_full=None)
  - Currency detection: PYG → moneda="gs", USD → moneda="usd", None → "usd"
  - precio_max computed from raw price (no currency conversion applied here)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.services.infocasas.infocasas_service import InfocasasService
from app.bot.core.types import ConversationState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _make_ic_prop(
    *,
    ic_id: int,
    property_id: int | None,
    price_sale: float | None = 100000.0,
    price_rent: float | None = None,
    currency_sale: str | None = "USD",
    currency_rent: str | None = None,
) -> MagicMock:
    """Build a minimal mock InfocasasProperty ORM object."""
    prop = MagicMock()
    prop.id = ic_id
    prop.property_id = property_id
    prop.property_type = "Casa"
    prop.city = "Asuncion"
    prop.neighborhood = "Centro"
    prop.operation = "venta"
    prop.price_sale = price_sale
    prop.price_rent = price_rent
    prop.currency_sale = currency_sale
    prop.currency_rent = currency_rent
    return prop


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPreloadSearchContext:
    """Unit tests for InfocasasService._preload_search_context."""

    @pytest.mark.asyncio
    async def test_preload_uses_property_id_not_ic_id(self):
        """last_detalle_id must be set to ic_prop.property_id (16357), not
        ic_prop.id (173022)."""
        ic_prop = _make_ic_prop(ic_id=173022, property_id=16357)

        mock_conv = MagicMock()
        mock_conv.id = 42

        mock_update_ctx = AsyncMock()
        svc = _make_service()

        with patch(
            "app.bot.services.infocasas.infocasas_service.ConversationManager"
        ) as mock_conv_mgr_cls:
            mock_conv_mgr = AsyncMock()
            mock_conv_mgr.get_or_create_conversation = AsyncMock(return_value=mock_conv)
            mock_conv_mgr.update_search_context = mock_update_ctx
            mock_conv_mgr_cls.return_value = mock_conv_mgr

            await svc._preload_search_context(
                contact_id=1,
                phone="+595981000001",
                ic_prop_full=ic_prop,
            )

        mock_update_ctx.assert_awaited_once()
        state_arg: ConversationState = mock_update_ctx.call_args.args[2]

        assert isinstance(state_arg, ConversationState)
        assert state_arg.last_detalle_id == 16357, (
            "last_detalle_id should be property_id (16357), not ic_prop.id (173022)"
        )
        assert state_arg.last_detalle_id != 173022

    @pytest.mark.asyncio
    async def test_preload_uses_ic_filtros_when_property_id_is_none(self, caplog):
        """When property_id is None, IC filtros are preloaded without a matched
        property (last_detalle_id=None, etapa=esperando_confirmacion_busqueda)."""
        import logging

        ic_prop = _make_ic_prop(ic_id=173022, property_id=None)

        mock_conv = MagicMock()
        mock_conv.id = 42
        mock_update_ctx = AsyncMock()
        svc = _make_service()

        with patch(
            "app.bot.services.infocasas.infocasas_service.ConversationManager"
        ) as mock_conv_mgr_cls, caplog.at_level(logging.INFO):
            mock_conv_mgr = AsyncMock()
            mock_conv_mgr.get_or_create_conversation = AsyncMock(return_value=mock_conv)
            mock_conv_mgr.update_search_context = mock_update_ctx
            mock_conv_mgr_cls.return_value = mock_conv_mgr

            await svc._preload_search_context(
                contact_id=1,
                phone="+595981000001",
                ic_prop_full=ic_prop,
            )

        # update_search_context IS called — IC filtros populated even without property match
        mock_update_ctx.assert_awaited_once()
        state_arg: ConversationState = mock_update_ctx.call_args.args[2]
        assert state_arg.last_detalle_id is None
        assert state_arg.etapa == "esperando_confirmacion_busqueda"

        # An INFO log must mention preloading IC filtros without property match
        assert any(
            "property match" in record.message.lower() or "property_id" in record.message
            for record in caplog.records
            if record.levelno >= logging.INFO
        ), "Expected a log mentioning IC filtros preloading without property match"

    @pytest.mark.asyncio
    async def test_preload_skips_when_ic_prop_is_none(self):
        """When ic_prop_full is None, no DB operations are called (existing
        early-return guard preserved)."""
        mock_update_ctx = AsyncMock()
        svc = _make_service()

        with patch(
            "app.bot.services.infocasas.infocasas_service.ConversationManager"
        ) as mock_conv_mgr_cls:
            mock_conv_mgr = AsyncMock()
            mock_conv_mgr.get_or_create_conversation = AsyncMock()
            mock_conv_mgr.update_search_context = mock_update_ctx
            mock_conv_mgr_cls.return_value = mock_conv_mgr

            await svc._preload_search_context(
                contact_id=1,
                phone="+595981000001",
                ic_prop_full=None,
            )

        # No DB operations should have been attempted
        mock_update_ctx.assert_not_awaited()
        mock_conv_mgr.get_or_create_conversation.assert_not_awaited()


class TestPreloadCurrency:
    """Tests for currency detection in _preload_search_context."""

    async def _run_preload(self, ic_prop) -> "ConversationState":
        """Helper: run _preload_search_context and return the ConversationState
        passed to update_search_context."""
        mock_conv = MagicMock()
        mock_conv.id = 10
        mock_update_ctx = AsyncMock()
        svc = _make_service()

        with patch(
            "app.bot.services.infocasas.infocasas_service.ConversationManager"
        ) as mock_conv_mgr_cls:
            mock_conv_mgr = AsyncMock()
            mock_conv_mgr.get_or_create_conversation = AsyncMock(return_value=mock_conv)
            mock_conv_mgr.update_search_context = mock_update_ctx
            mock_conv_mgr_cls.return_value = mock_conv_mgr

            await svc._preload_search_context(
                contact_id=1,
                phone="+595981000001",
                ic_prop_full=ic_prop,
            )

        mock_update_ctx.assert_awaited_once()
        return mock_update_ctx.call_args.args[2]

    @pytest.mark.asyncio
    async def test_usd_sale_property_sets_moneda_usd(self):
        """USD currency_sale → moneda="usd"."""
        ic_prop = _make_ic_prop(
            ic_id=1, property_id=100, price_sale=150000.0, currency_sale="USD"
        )
        state = await self._run_preload(ic_prop)
        assert state.filtros["moneda"] == "usd"
        assert state.filtros["precio_max"] == int(150000.0 * 1.3)

    @pytest.mark.asyncio
    async def test_pyg_sale_property_sets_moneda_gs(self):
        """PYG currency_sale → moneda="gs"."""
        ic_prop = _make_ic_prop(
            ic_id=2, property_id=200, price_sale=1_350_000_000.0, currency_sale="PYG"
        )
        state = await self._run_preload(ic_prop)
        assert state.filtros["moneda"] == "gs"
        assert state.filtros["precio_max"] == int(1_350_000_000.0 * 1.3)

    @pytest.mark.asyncio
    async def test_none_currency_sale_defaults_to_usd(self):
        """currency_sale=None → defaults to 'usd'."""
        ic_prop = _make_ic_prop(
            ic_id=3, property_id=300, price_sale=80000.0, currency_sale=None
        )
        state = await self._run_preload(ic_prop)
        assert state.filtros["moneda"] == "usd"

    @pytest.mark.asyncio
    async def test_rent_property_uses_currency_rent(self):
        """No sale price → falls through to rent price and currency_rent."""
        ic_prop = _make_ic_prop(
            ic_id=4,
            property_id=400,
            price_sale=None,
            price_rent=6_500_000.0,
            currency_sale=None,
            currency_rent="PYG",
        )
        state = await self._run_preload(ic_prop)
        assert state.filtros["moneda"] == "gs"
        assert state.filtros["precio_max"] == int(6_500_000.0 * 1.3)


# ===========================================================================
# TestPreloadReenviadoContext — plain 'dormitorios' key must not be written
# ===========================================================================

from datetime import datetime, timezone  # noqa: E402
from app.bot.services.infocasas.lead_parser import ParsedLead  # noqa: E402


class TestPreloadReenviadoContext:
    """Tests for InfocasasService._preload_reenviado_context.

    Ensures the plain 'dormitorios' key is NOT written into filtros
    (dead data — SearchFilters and the prompt only use dormitorios_min /
    dormitorios_max).
    """

    @pytest.mark.asyncio
    async def test_filtros_has_no_plain_dormitorios_key(self):
        """_preload_reenviado_context must not write plain 'dormitorios' into filtros."""
        parsed = ParsedLead(
            consulta_id="66065340",
            name="Alberto Careaga",
            phone="+595981234567",
            email=None,
            message="Hola",
            consulta_date=datetime(2026, 4, 16, tzinfo=timezone.utc),
            property_code=None,
            property_title=None,
            listing_city="Fernando de la Mora",
            has_whatsapp=True,
            is_reassigned=True,
            listing_type="casa",
            listing_operation="venta",
            listing_bedrooms=2,
            listing_price=200_000_000.0,
            listing_currency="gs",
            listing_zone_from_message="Fernando de la Mora",
        )

        mock_conv = MagicMock()
        mock_conv.id = 77
        mock_update_ctx = AsyncMock()
        svc = _make_service()

        with patch(
            "app.bot.services.infocasas.infocasas_service.ConversationManager"
        ) as mock_conv_mgr_cls:
            mock_conv_mgr = AsyncMock()
            mock_conv_mgr.get_or_create_conversation = AsyncMock(return_value=mock_conv)
            mock_conv_mgr.update_search_context = mock_update_ctx
            mock_conv_mgr_cls.return_value = mock_conv_mgr

            await svc._preload_reenviado_context(
                contact_id=1,
                phone="+595981234567",
                parsed=parsed,
            )

        mock_update_ctx.assert_awaited_once()
        state_arg: ConversationState = mock_update_ctx.call_args.args[2]

        assert isinstance(state_arg, ConversationState)
        assert "dormitorios" not in state_arg.filtros, (
            "Plain 'dormitorios' key must not appear in filtros — "
            "it is dead data (neither prompt nor SearchFilters reads it)"
        )
        # Sanity: other keys are present
        assert state_arg.filtros.get("tipo") == "casa"
        assert state_arg.filtros.get("operacion") == "venta"
