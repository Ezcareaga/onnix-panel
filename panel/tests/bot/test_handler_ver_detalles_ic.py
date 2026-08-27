"""Tests for VER_DETALLES IC direct path.

Bug fix: VER_DETALLES was always fetching from the `properties` table using
last_detalle_id, but 39.6% of IC leads have property_id=NULL (no cross-ref).
Fix: when last_ic_prop_id is set, fetch directly from infocasas_properties.

TDD RED phase — all tests in this file should FAIL before the fix is applied.
"""
from __future__ import annotations

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
from app.models.infocasas_property import InfocasasProperty


# ---------------------------------------------------------------------------
# Helpers
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


def _default_contact(status="contacted", is_baja=False, infocasas_ref=None):
    return ContactInfo(
        id=1, name="Test User", status=status, is_baja=is_baja,
        platform="whatsapp", source_id="+595981000001",
        infocasas_ref=infocasas_ref,
    )


def _default_conversation(is_bot_active=True):
    return ConversationInfo(
        id=10, contact_id=1, platform="whatsapp", chat_id="+595981000001",
        is_bot_active=is_bot_active,
    )


def _ver_detalles_request():
    """Build a BotRequest with VER_DETALLES callback."""
    return BotRequest(
        platform="whatsapp", chat_id="+595981000001", user_id="+595981000001",
        user_name="Test User", text="VER_DETALLES",
        external_id="msg_vd_ic_001", callback_data="VER_DETALLES",
    )


def _make_ic_prop_obj(
    id: int = 77,
    title: str = "Casa en Luque",
    city: str = "Luque",
    neighborhood: str | None = "Centro",
    property_type: str = "casa",
    operation: str = "venta",
    bedrooms: int | None = 3,
    bathrooms: int | None = 2,
    total_area_m2=None,
    built_area_m2=None,
    price_sale=None,
    currency_sale: str | None = "USD",
    price_rent=None,
    currency_rent: str | None = None,
    url: str = "https://www.infocasas.com.py/prop/123",
    property_id: int | None = None,
) -> MagicMock:
    """Build a MagicMock representing an InfocasasProperty ORM object."""
    prop = MagicMock(spec=InfocasasProperty)
    prop.id = id
    prop.title = title
    prop.city = city
    prop.neighborhood = neighborhood
    prop.property_type = property_type
    prop.operation = operation
    prop.bedrooms = bedrooms
    prop.bathrooms = bathrooms
    prop.total_area_m2 = total_area_m2
    prop.built_area_m2 = built_area_m2
    prop.price_sale = price_sale
    prop.currency_sale = currency_sale
    prop.price_rent = price_rent
    prop.currency_rent = currency_rent
    prop.url = url
    prop.property_id = property_id
    return prop


def _setup_ic_path(mocks, search_context: ConversationState):
    """Configure shared contact/conversation mocks for IC path tests."""
    mocks["conversation_manager"].resolve_contact.return_value = _default_contact()
    mocks["conversation_manager"].get_or_create_conversation.return_value = _default_conversation()
    mocks["conversation_manager"].check_human_cooldown.return_value = False
    mocks["conversation_manager"].get_history.return_value = []
    mocks["conversation_manager"].get_search_context.return_value = search_context


# ===========================================================================
# TestVERDETALLES_ICDirect
# ===========================================================================

class TestVERDETALLES_ICDirect:
    """VER_DETALLES shortcut fetches from infocasas_properties when last_ic_prop_id is set."""

    @pytest.mark.asyncio
    async def test_ic_lead_null_property_id_shows_ic_detail(self):
        """IC lead with property_id=NULL: VER_DETALLES returns IC property detail directly.

        ConversationState has last_ic_prop_id=77, last_detalle_id=None.
        Repo returns ic_prop with id=77. Result must be intent='detalle',
        one property, with title from IC data. Claude must NOT be called.
        """
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(
            last_ic_prop_id=77,
            last_detalle_id=None,
            etapa="esperando_confirmacion_busqueda",
        )
        _setup_ic_path(mocks, ctx)

        ic_prop = _make_ic_prop_obj(
            id=77,
            title="Casa en Luque",
            city="Luque",
            price_sale=150_000,
            currency_sale="USD",
        )

        with patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_id",
            new_callable=AsyncMock,
        ) as mock_get_ic:
            mock_get_ic.return_value = ic_prop

            result = await orch.handle_message(_ver_detalles_request(), AsyncMock())

        assert result is not None
        assert result.intent == "detalle"
        assert len(result.properties) == 1
        assert result.properties[0]["title"] == "Casa en Luque"

        # Claude must NOT have been called
        mocks["claude"].send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_ic_lead_with_property_id_also_shows_ic_detail(self):
        """IC lead with property_id set: VER_DETALLES still uses IC data (last_ic_prop_id wins).

        Even when last_detalle_id=99 is present, if last_ic_prop_id=88 is set,
        we should fetch from infocasas_properties and return that data.
        """
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(
            last_ic_prop_id=88,
            last_detalle_id=99,
            etapa="viendo_detalle",
        )
        _setup_ic_path(mocks, ctx)

        ic_prop = _make_ic_prop_obj(
            id=88,
            title="Depto Asuncion",
            city="Asuncion",
        )

        with patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_id",
            new_callable=AsyncMock,
        ) as mock_get_ic:
            mock_get_ic.return_value = ic_prop

            result = await orch.handle_message(_ver_detalles_request(), AsyncMock())

        assert result is not None
        assert result.intent == "detalle"
        assert len(result.properties) == 1
        assert result.properties[0]["title"] == "Depto Asuncion"

        # Must NOT have fetched from properties table
        mocks["search_service"].get_by_ids.assert_not_called()

    @pytest.mark.asyncio
    async def test_ic_lead_ic_prop_not_found_returns_fallback(self):
        """IC lead with last_ic_prop_id=999 but repo returns None → fallback conversacion."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(
            last_ic_prop_id=999,
            last_detalle_id=None,
            etapa="esperando_confirmacion_busqueda",
        )
        _setup_ic_path(mocks, ctx)

        with patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_id",
            new_callable=AsyncMock,
        ) as mock_get_ic:
            mock_get_ic.return_value = None

            result = await orch.handle_message(_ver_detalles_request(), AsyncMock())

        assert result is not None
        assert result.intent == "conversacion"
        assert any(word in result.text.lower() for word in ("encontrar", "opcion", "encontr"))


# ===========================================================================
# TestIcPropToDetailDict
# ===========================================================================

class TestIcPropToDetailDict:
    """Unit tests for ic_prop_to_detail_dict function in response_builder."""

    def test_ic_prop_to_detail_dict_usd_sale_price(self):
        """price_sale with currency_sale=USD → price_usd set, price_pyg is None."""
        from app.bot.core.response_builder import ic_prop_to_detail_dict

        ic_prop = _make_ic_prop_obj(
            id=77,
            title="Casa en Luque",
            city="Luque",
            price_sale=150_000,
            currency_sale="USD",
            price_rent=None,
            currency_rent=None,
        )
        result = ic_prop_to_detail_dict(ic_prop)

        assert result["price_usd"] == 150_000
        assert result["price_pyg"] is None
        assert result["id"] == 77
        assert result["title"] == "Casa en Luque"
        assert result["city"] == "Luque"

    def test_ic_prop_to_detail_dict_pyg_rent_price(self):
        """price_rent with currency_rent=PYG → price_pyg set, price_usd is None."""
        from app.bot.core.response_builder import ic_prop_to_detail_dict

        ic_prop = _make_ic_prop_obj(
            id=88,
            title="Depto centro",
            city="Asuncion",
            price_sale=None,
            currency_sale=None,
            price_rent=3_000_000,
            currency_rent="PYG",
        )
        result = ic_prop_to_detail_dict(ic_prop)

        assert result["price_pyg"] == 3_000_000
        assert result["price_usd"] is None
        assert result["id"] == 88

    def test_ic_prop_to_detail_dict_passes_through_fields(self):
        """All passthrough fields are present in output dict."""
        from app.bot.core.response_builder import ic_prop_to_detail_dict

        ic_prop = _make_ic_prop_obj(
            id=55,
            title="Terreno Luque",
            city="Luque",
            neighborhood="Barrio Sur",
            operation="venta",
            property_type="terreno",
            bedrooms=None,
            bathrooms=None,
            total_area_m2=500,
            url="https://www.infocasas.com.py/prop/555",
        )
        result = ic_prop_to_detail_dict(ic_prop)

        assert result["neighborhood"] == "Barrio Sur"
        assert result["operation"] == "venta"
        assert result["property_type"] == "terreno"
        assert result["total_area_m2"] == 500
        assert result["url"] == "https://www.infocasas.com.py/prop/555"

    def test_ic_prop_to_detail_dict_maps_built_area_m2(self):
        """M2.F5: built_area_m2 existe en el modelo IC pero se omitía en el adapter."""
        from app.bot.core.response_builder import ic_prop_to_detail_dict

        ic_prop = _make_ic_prop_obj(total_area_m2=250, built_area_m2=180)
        result = ic_prop_to_detail_dict(ic_prop)

        assert result["built_area_m2"] == 180


# ===========================================================================
# TestPreloadSavesLastIcPropId
# ===========================================================================

class TestPreloadSavesLastIcPropId:
    """_preload_search_context saves last_ic_prop_id in both property_id branches."""

    def _make_preload_service(self):
        """Build InfocasasService with mocked session factory."""
        from tests.bot.test_infocasas_service import _make_service, _make_session_factory
        factory = _make_session_factory()
        svc, _, _ = _make_service(session=factory._mock_return_value.__aenter__.return_value)
        svc._session_factory = factory
        return svc

    @pytest.mark.asyncio
    async def test_preload_saves_last_ic_prop_id_when_property_id_none(self):
        """property_id=None → saved state has last_ic_prop_id == ic_prop.id."""
        from unittest.mock import patch as _patch
        svc = self._make_preload_service()

        ic_prop = MagicMock()
        ic_prop.id = 77
        ic_prop.property_id = None
        ic_prop.city = "Luque"
        ic_prop.neighborhood = "Centro"
        ic_prop.operation = "venta"
        ic_prop.property_type = "casa"
        ic_prop.price_sale = 150_000
        ic_prop.price_rent = None
        ic_prop.currency_sale = "USD"
        ic_prop.currency_rent = None
        ic_prop.bedrooms = 3

        mock_conv_mgr = AsyncMock()
        mock_conv_mgr.get_or_create_conversation = AsyncMock(
            return_value=MagicMock(id=100)
        )
        mock_conv_mgr.update_search_context = AsyncMock()

        with _patch(
            "app.bot.services.infocasas.infocasas_service.ConversationManager",
            return_value=mock_conv_mgr,
        ):
            await svc._preload_search_context(
                contact_id=1,
                phone="+595981000001",
                ic_prop_full=ic_prop,
            )

        mock_conv_mgr.update_search_context.assert_awaited_once()
        _, _, saved_state = mock_conv_mgr.update_search_context.call_args[0]
        assert saved_state.last_ic_prop_id == 77, (
            "last_ic_prop_id must be set to ic_prop.id when property_id IS NULL"
        )
        assert saved_state.last_detalle_id is None, (
            "last_detalle_id must remain None when property_id IS NULL"
        )

    @pytest.mark.asyncio
    async def test_preload_saves_last_ic_prop_id_when_property_id_set(self):
        """property_id=99 → saved state has last_ic_prop_id == ic_prop.id AND last_detalle_id == 99."""
        from unittest.mock import patch as _patch
        svc = self._make_preload_service()

        ic_prop = MagicMock()
        ic_prop.id = 88
        ic_prop.property_id = 99
        ic_prop.city = "Asuncion"
        ic_prop.neighborhood = None
        ic_prop.operation = "venta"
        ic_prop.property_type = "casa"
        ic_prop.price_sale = 250_000
        ic_prop.price_rent = None
        ic_prop.currency_sale = "USD"
        ic_prop.currency_rent = None
        ic_prop.bedrooms = 4

        mock_conv_mgr = AsyncMock()
        mock_conv_mgr.get_or_create_conversation = AsyncMock(
            return_value=MagicMock(id=101)
        )
        mock_conv_mgr.update_search_context = AsyncMock()

        with _patch(
            "app.bot.services.infocasas.infocasas_service.ConversationManager",
            return_value=mock_conv_mgr,
        ):
            await svc._preload_search_context(
                contact_id=2,
                phone="+595982000002",
                ic_prop_full=ic_prop,
            )

        _, _, saved_state = mock_conv_mgr.update_search_context.call_args[0]
        assert saved_state.last_ic_prop_id == 88, (
            "last_ic_prop_id must be set to ic_prop.id even when property_id is set"
        )
        assert saved_state.last_detalle_id == 99, (
            "last_detalle_id must be set to property_id when it exists"
        )


# ===========================================================================
# TestVERDETALLES_ICRefFallback
# ===========================================================================

class TestVERDETALLES_ICRefFallback:
    """VER_DETALLES fallback: lookup IC property by infocasas_ref when last_ic_prop_id is None."""

    def _setup_ref_fallback(self, mocks, search_context: ConversationState, infocasas_ref=None):
        """Configure mocks for the infocasas_ref fallback path."""
        mocks["conversation_manager"].resolve_contact.return_value = _default_contact(
            infocasas_ref=infocasas_ref
        )
        mocks["conversation_manager"].get_or_create_conversation.return_value = _default_conversation()
        mocks["conversation_manager"].check_human_cooldown.return_value = False
        mocks["conversation_manager"].get_history.return_value = []
        mocks["conversation_manager"].get_search_context.return_value = search_context

    @pytest.mark.asyncio
    async def test_ic_fallback_by_ref_when_no_ic_prop_id(self):
        """last_ic_prop_id=None + contact.infocasas_ref set → lookup by ref, return detalle.

        When last_ic_prop_id is None but contact.infocasas_ref is set, the orchestrator
        must call get_ic_by_ref and return a detalle response. get_ic_by_id must NOT
        be called (there is no ID to look up). Claude must NOT be called.
        """
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(
            last_ic_prop_id=None,
            last_detalle_id=None,
            etapa="inicio",
        )
        self._setup_ref_fallback(mocks, ctx, infocasas_ref="K75763")

        ic_prop = _make_ic_prop_obj(
            id=55,
            title="Casa en Luque",
            city="Luque",
            price_sale=120_000,
            currency_sale="USD",
        )

        with patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_id",
            new_callable=AsyncMock,
        ) as mock_get_ic_id, patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_ref",
            new_callable=AsyncMock,
        ) as mock_get_ic_ref:
            mock_get_ic_ref.return_value = ic_prop

            result = await orch.handle_message(_ver_detalles_request(), AsyncMock())

        assert result is not None
        assert result.intent == "detalle"
        assert len(result.properties) == 1
        assert result.properties[0]["title"] == "Casa en Luque"

        # get_ic_by_id must NOT be called — ic_prop_id is None
        mock_get_ic_id.assert_not_called()
        # get_ic_by_ref must have been called with the contact's ref
        mock_get_ic_ref.assert_awaited_once()
        # Claude must NOT have been called
        mocks["claude"].send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_ic_fallback_caches_ic_prop_id_in_context(self):
        """After ref-based lookup, search_context.last_ic_prop_id is saved for future calls.

        After a successful lookup by ref, update_search_context must be called with
        a ConversationState whose last_ic_prop_id == ic_prop.id (55), so subsequent
        VER_DETALLES calls can use the faster ID path.
        """
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(
            last_ic_prop_id=None,
            last_detalle_id=None,
            etapa="inicio",
        )
        self._setup_ref_fallback(mocks, ctx, infocasas_ref="K75763")

        ic_prop = _make_ic_prop_obj(
            id=55,
            title="Casa en Luque",
            city="Luque",
        )

        with patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_id",
            new_callable=AsyncMock,
        ), patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_ref",
            new_callable=AsyncMock,
        ) as mock_get_ic_ref:
            mock_get_ic_ref.return_value = ic_prop

            await orch.handle_message(_ver_detalles_request(), AsyncMock())

        # update_search_context must have been called
        mocks["conversation_manager"].update_search_context.assert_awaited()

        # Find the call that saved last_ic_prop_id (may be any of the update calls)
        found_cache_call = False
        for call_args in mocks["conversation_manager"].update_search_context.call_args_list:
            args = call_args[0]  # positional args
            if len(args) >= 3:
                saved_state = args[2]
                if hasattr(saved_state, "last_ic_prop_id") and saved_state.last_ic_prop_id == 55:
                    found_cache_call = True
                    break
        assert found_cache_call, (
            "update_search_context must be called with ConversationState.last_ic_prop_id == 55"
        )

    @pytest.mark.asyncio
    async def test_ic_fallback_not_triggered_when_no_infocasas_ref(self):
        """last_ic_prop_id=None + contact.infocasas_ref=None → does NOT attempt ref lookup.

        Without a ref, the orchestrator falls through to the standard no-prop-id path
        (filtros fallback or error). get_ic_by_ref must NOT be called.
        """
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(
            last_ic_prop_id=None,
            last_detalle_id=None,
            filtros={},
            etapa="inicio",
        )
        # No infocasas_ref on the contact
        self._setup_ref_fallback(mocks, ctx, infocasas_ref=None)

        with patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_ref",
            new_callable=AsyncMock,
        ) as mock_get_ic_ref:
            result = await orch.handle_message(_ver_detalles_request(), AsyncMock())

        # get_ic_by_ref must NOT be called
        mock_get_ic_ref.assert_not_called()
        # Result should be a conversacion fallback (no prop_id, no filtros, no ref)
        assert result.intent != "detalle"

    def test_contactinfo_has_infocasas_ref_field(self):
        """ContactInfo accepts and stores infocasas_ref."""
        contact_with_ref = ContactInfo(
            id=1, name="X", status="new", infocasas_ref="K75763"
        )
        assert contact_with_ref.infocasas_ref == "K75763"

        contact_without_ref = ContactInfo(id=1, name="X", status="new")
        assert contact_without_ref.infocasas_ref is None


# ===========================================================================
# M2.F5 — TestIcCrossRefFallback
# ===========================================================================

def _make_matched_property(
    description: str | None = None,
    address: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    main_image_url: str | None = None,
    image_urls: list | None = None,
    local_image_count: int = 0,
) -> MagicMock:
    """Build a MagicMock of a canonical `properties` table row (cross-ref target)."""
    prop = MagicMock()
    prop.description = description
    prop.address = address
    prop.latitude = latitude
    prop.longitude = longitude
    prop.main_image_url = main_image_url
    prop.image_urls = image_urls if image_urls is not None else []
    prop.local_image_count = local_image_count
    return prop


class TestIcCrossRefFallback:
    """M2.F5: cuando una IC property tiene cross-ref a properties
    (property_id IS NOT NULL), el detalle se enriquece con los campos
    no disponibles en infocasas_properties (description, address,
    lat/lng, fotos).
    """

    @pytest.mark.asyncio
    async def test_cross_ref_enriches_description_address_and_coords(self):
        """IC con property_id → detalle incluye description, address, lat/lng de properties."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(last_ic_prop_id=77, etapa="esperando_confirmacion_busqueda")
        _setup_ic_path(mocks, ctx)

        ic_prop = _make_ic_prop_obj(id=77, property_id=5001)
        matched = _make_matched_property(
            description="Hermosa casa remodelada con patio amplio",
            address="Av. Mariscal Lopez 1234",
            latitude=-25.2868,
            longitude=-57.6466,
            main_image_url="https://onnix.com.py/images/onnixpy/abc/1.webp",
            image_urls=["https://onnix.com.py/images/onnixpy/abc/1.webp"],
            local_image_count=1,
        )

        with patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_id",
            new_callable=AsyncMock,
        ) as mock_get_ic, patch(
            "app.repositories.property_repo.PropertyRepository.get_by_id",
            new_callable=AsyncMock,
        ) as mock_get_by_id:
            mock_get_ic.return_value = ic_prop
            mock_get_by_id.return_value = matched

            result = await orch.handle_message(_ver_detalles_request(), AsyncMock())

        mock_get_by_id.assert_awaited_once_with(mock_get_by_id.call_args.args[0], 5001)
        p = result.properties[0]
        assert p["description"] == "Hermosa casa remodelada con patio amplio"
        assert p["address"] == "Av. Mariscal Lopez 1234"
        assert p["latitude"] == -25.2868
        assert p["longitude"] == -57.6466
        assert p["main_image_url"] == "https://onnix.com.py/images/onnixpy/abc/1.webp"
        assert p["local_image_count"] == 1

    @pytest.mark.asyncio
    async def test_no_cross_ref_keeps_ic_only(self):
        """IC sin property_id (NULL) → no se llama get_by_id, solo campos IC."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(last_ic_prop_id=77, etapa="esperando_confirmacion_busqueda")
        _setup_ic_path(mocks, ctx)

        ic_prop = _make_ic_prop_obj(id=77, property_id=None)

        with patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_id",
            new_callable=AsyncMock,
        ) as mock_get_ic, patch(
            "app.repositories.property_repo.PropertyRepository.get_by_id",
            new_callable=AsyncMock,
        ) as mock_get_by_id:
            mock_get_ic.return_value = ic_prop

            result = await orch.handle_message(_ver_detalles_request(), AsyncMock())

        mock_get_by_id.assert_not_awaited()
        p = result.properties[0]
        assert p.get("description") in (None, "", [])
        assert p.get("address") in (None, "", [])

    @pytest.mark.asyncio
    async def test_cross_ref_miss_graceful(self):
        """IC con property_id pero properties row no existe → graceful, no crashea."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(last_ic_prop_id=77, etapa="esperando_confirmacion_busqueda")
        _setup_ic_path(mocks, ctx)

        ic_prop = _make_ic_prop_obj(id=77, property_id=9999)

        with patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_id",
            new_callable=AsyncMock,
        ) as mock_get_ic, patch(
            "app.repositories.property_repo.PropertyRepository.get_by_id",
            new_callable=AsyncMock,
        ) as mock_get_by_id:
            mock_get_ic.return_value = ic_prop
            mock_get_by_id.return_value = None  # property not found (deleted / inactive)

            result = await orch.handle_message(_ver_detalles_request(), AsyncMock())

        mock_get_by_id.assert_awaited_once()
        assert result.intent == "detalle"
        assert result.properties  # no crash, IC data still shown
        # description no enriquecida — IC no la tiene, properties tampoco
        p = result.properties[0]
        assert p.get("description") in (None, "", [])

    @pytest.mark.asyncio
    async def test_cross_ref_empty_fields_not_overwritten(self):
        """properties con description=None no debe sobreescribir valor pre-existente."""
        orch, mocks = _make_orchestrator()
        ctx = ConversationState(last_ic_prop_id=77, etapa="esperando_confirmacion_busqueda")
        _setup_ic_path(mocks, ctx)

        ic_prop = _make_ic_prop_obj(id=77, property_id=5001)
        matched = _make_matched_property(
            description=None,  # properties.description está vacío
            address="Av. Test 100",  # este sí llena
            latitude=None,
            longitude=None,
            image_urls=[],
        )

        with patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_id",
            new_callable=AsyncMock,
        ) as mock_get_ic, patch(
            "app.repositories.property_repo.PropertyRepository.get_by_id",
            new_callable=AsyncMock,
        ) as mock_get_by_id:
            mock_get_ic.return_value = ic_prop
            mock_get_by_id.return_value = matched

            result = await orch.handle_message(_ver_detalles_request(), AsyncMock())

        p = result.properties[0]
        # address sí se enriqueció
        assert p["address"] == "Av. Test 100"
        # description quedó como IC default (no overwrite con None)
        assert p.get("description") in (None, "", [])
