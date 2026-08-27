"""Tests unitarios para _handle_ver_detalles (Orchestrator) y handle_ver_detalles_ic (handlers.detail_ic).

Cubren el comportamiento del código existente:
- ``Orchestrator._handle_ver_detalles`` — todavía en orchestrator (Task 3.12).
- ``handlers.detail_ic.handle_ver_detalles_ic`` — extraído en M4 Task 3.11.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.core.orchestrator import Orchestrator
from app.bot.core.types import (
    BotRequest, BotResponse, ContactInfo, ConversationInfo, ConversationState,
)
from app.bot.handlers._types import HandlerResult
from app.bot.handlers.detail import handle_ver_detalles
from app.bot.handlers.detail_ic import handle_ver_detalles_ic
from app.bot.search.search_service import SearchResult


# ---------------------------------------------------------------------------
# Helpers — copiados de test_orchestrator.py (TODO misma nota que Task 0.5.1)
# ---------------------------------------------------------------------------

def _make_search_result(count: int = 1, is_active: bool = True) -> SearchResult:
    """Construye un SearchResult con *count* propiedades."""
    props = []
    for i in range(count):
        props.append({
            "id": 100 + i,
            "title": f"Casa test {i + 1}",
            "city": "Asuncion",
            "operation": "venta",
            "property_type": "casa",
            "price_usd": 150_000 + i * 10_000,
            "bedrooms": 3,
            "bathrooms": 2,
            "total_area_m2": 200,
            "source": "onnix",
            "external_id": f"ext_{100 + i}",
            "local_image_count": 3,
            "is_active": is_active,
        })
    return SearchResult(properties=props, total_found=count)


def _make_orchestrator():
    """Crea un Orchestrator con todas las dependencias mockeadas.

    TODO(M4-refactor): actualizar cuando _handle_ver_detalles se mueva a handler propio.
    """
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
        "search_service": search_service,
        "conversation_manager": conversation_manager,
    }


def _default_contact(infocasas_ref: str | None = None) -> ContactInfo:
    """ContactInfo de prueba con infocasas_ref opcional."""
    return ContactInfo(
        id=1,
        name="Test User",
        status="new",
        is_baja=False,
        platform="whatsapp",
        source_id="+595981000001",
        infocasas_ref=infocasas_ref,
    )


def _default_conversation() -> ConversationInfo:
    """ConversationInfo de prueba."""
    return ConversationInfo(
        id=10,
        contact_id=1,
        platform="whatsapp",
        chat_id="+595981000001",
    )


def _default_request() -> BotRequest:
    """BotRequest de prueba."""
    return BotRequest(
        platform="whatsapp",
        chat_id="+595981000001",
        user_id="+595981000001",
        user_name="Test User",
        text="ver detalles",
        external_id="msg_001",
    )


def _default_search_context(**kwargs) -> ConversationState:
    """ConversationState vacío con overrides opcionales."""
    return ConversationState(**kwargs)


# ===========================================================================
# Tests para handle_ver_detalles (handlers.detail, M4 Task 3.12)
# ===========================================================================

class TestHandleVerDetalles:
    """Tests unitarios para ``handlers.detail.handle_ver_detalles``."""

    @pytest.mark.asyncio
    async def test_ver_detalles_with_active_property_returns_detalle(self):
        """Happy path: propiedad activa → BotResponse con intent='detalle'."""
        _, deps = _make_orchestrator()
        session = AsyncMock()
        contact = _default_contact()
        conversation = _default_conversation()
        search_ctx = _default_search_context(last_detalle_id=100)

        deps["search_service"].get_by_ids.return_value = _make_search_result(1, is_active=True)

        result = await handle_ver_detalles(
            _default_request(), session, contact, conversation, search_ctx,
            search_service=deps["search_service"],
            conversation_manager=deps["conversation_manager"],
        )
        response = result.response

        assert response.intent == "detalle"
        assert len(response.properties) == 1
        assert response.shown_ids == [100]
        deps["search_service"].get_by_ids.assert_awaited_once_with([100], session)

    @pytest.mark.asyncio
    async def test_ver_detalles_appends_shown_property_when_not_seen(self):
        """Mutación: prop_id no en shown_properties → se agrega y llama update_search_context."""
        _, deps = _make_orchestrator()
        session = AsyncMock()
        contact = _default_contact()
        conversation = _default_conversation()
        search_ctx = _default_search_context(last_detalle_id=100, shown_properties=[])

        deps["search_service"].get_by_ids.return_value = _make_search_result(1, is_active=True)

        await handle_ver_detalles(
            _default_request(), session, contact, conversation, search_ctx,
            search_service=deps["search_service"],
            conversation_manager=deps["conversation_manager"],
        )

        assert 100 in search_ctx.shown_properties
        deps["conversation_manager"].update_search_context.assert_awaited_once_with(
            session, conversation.id, search_ctx
        )

    @pytest.mark.asyncio
    async def test_ver_detalles_skips_update_when_already_in_shown(self):
        """Sin mutación: prop_id ya en shown_properties → update_search_context no se llama."""
        _, deps = _make_orchestrator()
        session = AsyncMock()
        contact = _default_contact()
        conversation = _default_conversation()
        search_ctx = _default_search_context(last_detalle_id=100, shown_properties=[100])

        deps["search_service"].get_by_ids.return_value = _make_search_result(1, is_active=True)

        await handle_ver_detalles(
            _default_request(), session, contact, conversation, search_ctx,
            search_service=deps["search_service"],
            conversation_manager=deps["conversation_manager"],
        )

        deps["conversation_manager"].update_search_context.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ver_detalles_with_inactive_property_returns_conversacion(self):
        """Propiedad inactiva → BotResponse con intent='conversacion' y mensaje adecuado."""
        _, deps = _make_orchestrator()
        session = AsyncMock()
        contact = _default_contact()
        conversation = _default_conversation()
        search_ctx = _default_search_context(last_detalle_id=100)

        deps["search_service"].get_by_ids.return_value = _make_search_result(1, is_active=False)

        result = await handle_ver_detalles(
            _default_request(), session, contact, conversation, search_ctx,
            search_service=deps["search_service"],
            conversation_manager=deps["conversation_manager"],
        )
        response = result.response

        assert response.intent == "conversacion"
        assert "ya no está disponible" in response.text
        deps["conversation_manager"].save_outbound_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ver_detalles_with_property_not_found_returns_conversacion(self):
        """Propiedad no encontrada → BotResponse con intent='conversacion'."""
        _, deps = _make_orchestrator()
        session = AsyncMock()
        contact = _default_contact()
        conversation = _default_conversation()
        search_ctx = _default_search_context(last_detalle_id=999)

        deps["search_service"].get_by_ids.return_value = SearchResult(properties=[], total_found=0)

        result = await handle_ver_detalles(
            _default_request(), session, contact, conversation, search_ctx,
            search_service=deps["search_service"],
            conversation_manager=deps["conversation_manager"],
        )
        response = result.response

        assert response.intent == "conversacion"
        assert "No pude encontrar" in response.text
        deps["conversation_manager"].save_outbound_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ver_detalles_no_id_no_filtros_returns_conversacion(self):
        """Sin last_detalle_id y sin filtros útiles → fallback conversacion."""
        _, deps = _make_orchestrator()
        session = AsyncMock()
        contact = _default_contact()
        conversation = _default_conversation()
        search_ctx = _default_search_context(last_detalle_id=None, filtros={})

        result = await handle_ver_detalles(
            _default_request(), session, contact, conversation, search_ctx,
            search_service=deps["search_service"],
            conversation_manager=deps["conversation_manager"],
        )
        response = result.response

        assert response.intent == "conversacion"
        assert "No tengo información" in response.text
        deps["conversation_manager"].save_outbound_message.assert_awaited_once()
        deps["search_service"].get_by_ids.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ver_detalles_no_id_with_ciudad_filtro_delegates_to_reenviado(self):
        """Sin last_detalle_id pero con filtro ciudad → delega a handle_si_mostrame_reenviado."""
        _, deps = _make_orchestrator()
        session = AsyncMock()
        contact = _default_contact()
        conversation = _default_conversation()
        search_ctx = _default_search_context(
            last_detalle_id=None,
            filtros={"ciudad": "asuncion"},
        )

        expected = BotResponse(text="resultados", intent="busqueda")
        reenviado_mock = AsyncMock(
            return_value=HandlerResult(response=expected, search_context=search_ctx)
        )
        with patch("app.bot.handlers.detail.handle_si_mostrame_reenviado", new=reenviado_mock):
            result = await handle_ver_detalles(
                _default_request(), session, contact, conversation, search_ctx,
                search_service=deps["search_service"],
                conversation_manager=deps["conversation_manager"],
            )

        assert result.response is expected
        reenviado_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ver_detalles_no_id_with_barrio_filtro_delegates_to_reenviado(self):
        """Sin last_detalle_id pero con filtro barrio → delega a handle_si_mostrame_reenviado."""
        _, deps = _make_orchestrator()
        session = AsyncMock()
        contact = _default_contact()
        conversation = _default_conversation()
        search_ctx = _default_search_context(
            last_detalle_id=None,
            filtros={"barrio": "villa morra"},
        )

        expected = BotResponse(text="resultados barrio", intent="busqueda")
        reenviado_mock = AsyncMock(
            return_value=HandlerResult(response=expected, search_context=search_ctx)
        )
        with patch("app.bot.handlers.detail.handle_si_mostrame_reenviado", new=reenviado_mock):
            result = await handle_ver_detalles(
                _default_request(), session, contact, conversation, search_ctx,
                search_service=deps["search_service"],
                conversation_manager=deps["conversation_manager"],
            )

        assert result.response is expected
        reenviado_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ver_detalles_with_ic_prop_id_routes_to_ic_handler(self):
        """IC path via last_ic_prop_id → delega a handle_ver_detalles_ic con ese id."""
        _, deps = _make_orchestrator()
        session = AsyncMock()
        contact = _default_contact()
        conversation = _default_conversation()
        search_ctx = _default_search_context(last_ic_prop_id=42)

        expected = BotResponse(text="", intent="detalle", properties=[{"id": 42}])
        ic_mock = AsyncMock(return_value=HandlerResult(response=expected, search_context=search_ctx))
        with patch("app.bot.handlers.detail.handle_ver_detalles_ic", new=ic_mock):
            result = await handle_ver_detalles(
                _default_request(), session, contact, conversation, search_ctx,
                search_service=deps["search_service"],
                conversation_manager=deps["conversation_manager"],
            )

        assert result.response is expected
        ic_mock.assert_awaited_once_with(
            42, session, contact, conversation, search_ctx,
            conversation_manager=deps["conversation_manager"],
        )

    @pytest.mark.asyncio
    async def test_ver_detalles_with_infocasas_ref_routes_to_ic_handler(self):
        """IC path via contact.infocasas_ref → delega a handle_ver_detalles_ic con id=None."""
        _, deps = _make_orchestrator()
        session = AsyncMock()
        contact = _default_contact(infocasas_ref="REF-ABC-123")
        conversation = _default_conversation()
        # last_ic_prop_id es None pero infocasas_ref está presente
        search_ctx = _default_search_context(last_ic_prop_id=None)

        expected = BotResponse(text="", intent="detalle", properties=[{"id": 77}])
        ic_mock = AsyncMock(return_value=HandlerResult(response=expected, search_context=search_ctx))
        with patch("app.bot.handlers.detail.handle_ver_detalles_ic", new=ic_mock):
            result = await handle_ver_detalles(
                _default_request(), session, contact, conversation, search_ctx,
                search_service=deps["search_service"],
                conversation_manager=deps["conversation_manager"],
            )

        assert result.response is expected
        ic_mock.assert_awaited_once_with(
            None, session, contact, conversation, search_ctx,
            conversation_manager=deps["conversation_manager"],
        )


# ===========================================================================
# Tests para handle_ver_detalles_ic (handlers.detail_ic, M4 Task 3.11)
# ===========================================================================

class TestHandleVerDetallesIc:
    """Tests unitarios para ``handlers.detail_ic.handle_ver_detalles_ic``."""

    def _make_ic_prop(self, id: int = 42, property_id: int | None = None) -> MagicMock:
        """Construye un mock de InfocasasProperty con atributos esenciales."""
        ic_prop = MagicMock()
        ic_prop.id = id
        ic_prop.property_id = property_id
        ic_prop.address = "Av. España 123"
        ic_prop.description = None
        ic_prop.latitude = None
        ic_prop.longitude = None
        ic_prop.main_image_url = None
        ic_prop.image_urls = []
        ic_prop.local_image_count = 0
        return ic_prop

    def _make_canonical_prop(self) -> MagicMock:
        """Construye un mock de Property (tabla canónica) con todos los campos de enriquecimiento."""
        prop = MagicMock()
        prop.description = "Hermosa casa con jardín"
        prop.address = "Av. España 123, Asuncion"
        prop.latitude = -25.2867
        prop.longitude = -57.6470
        prop.main_image_url = "https://onnix.com.py/images/prop_100.webp"
        prop.image_urls = ["https://onnix.com.py/images/prop_100_1.webp"]
        prop.local_image_count = 5
        return prop

    @pytest.mark.asyncio
    async def test_ic_with_ic_prop_id_fetches_by_id_and_returns_detalle(self):
        """ic_prop_id presente → llama get_ic_by_id y devuelve BotResponse intent='detalle'."""
        orch, deps = _make_orchestrator()
        session = AsyncMock()
        contact = _default_contact()
        conversation = _default_conversation()
        search_ctx = _default_search_context()
        ic_prop = self._make_ic_prop(id=42, property_id=None)

        with patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_id",
            new=AsyncMock(return_value=ic_prop),
        ), patch(
            "app.bot.core.response_builder.ic_prop_to_detail_dict",
            return_value={"id": 42, "title": "IC test prop"},
        ):
            result = await handle_ver_detalles_ic(
                42, session, contact, conversation, search_ctx,
                conversation_manager=deps["conversation_manager"],
            )
            response = result.response

        assert response.intent == "detalle"
        assert len(response.properties) == 1
        assert response.properties[0]["id"] == 42
        assert response.shown_ids == [42]

    @pytest.mark.asyncio
    async def test_ic_falls_back_to_ref_lookup_when_id_missing(self):
        """ic_prop_id=None con infocasas_ref → llama get_ic_by_ref como fallback."""
        orch, deps = _make_orchestrator()
        session = AsyncMock()
        contact = _default_contact(infocasas_ref="REF-XYZ")
        conversation = _default_conversation()
        search_ctx = _default_search_context()
        ic_prop = self._make_ic_prop(id=77, property_id=None)

        with patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_id",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=ic_prop),
        ), patch(
            "app.bot.core.response_builder.ic_prop_to_detail_dict",
            return_value={"id": 77, "title": "IC ref prop"},
        ):
            result = await handle_ver_detalles_ic(
                None, session, contact, conversation, search_ctx,
                conversation_manager=deps["conversation_manager"],
            )
            response = result.response

        assert response.intent == "detalle"
        assert response.properties[0]["id"] == 77

    @pytest.mark.asyncio
    async def test_ic_caches_id_after_ref_lookup_succeeds(self):
        """Después de ref lookup exitoso → cachea last_ic_prop_id y llama update_search_context."""
        orch, deps = _make_orchestrator()
        session = AsyncMock()
        contact = _default_contact(infocasas_ref="REF-XYZ")
        conversation = _default_conversation()
        search_ctx = _default_search_context(last_ic_prop_id=None)
        ic_prop = self._make_ic_prop(id=77, property_id=None)

        with patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_id",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=ic_prop),
        ), patch(
            "app.bot.core.response_builder.ic_prop_to_detail_dict",
            return_value={"id": 77, "title": "IC cached"},
        ):
            await handle_ver_detalles_ic(
                None, session, contact, conversation, search_ctx,
                conversation_manager=deps["conversation_manager"],
            )

        assert search_ctx.last_ic_prop_id == 77
        deps["conversation_manager"].update_search_context.assert_awaited_once_with(
            session, conversation.id, search_ctx
        )

    @pytest.mark.asyncio
    async def test_ic_with_no_match_returns_conversacion(self):
        """Ni get_ic_by_id ni get_ic_by_ref encuentran nada → intent='conversacion'."""
        orch, deps = _make_orchestrator()
        session = AsyncMock()
        contact = _default_contact(infocasas_ref="REF-MISSING")
        conversation = _default_conversation()
        search_ctx = _default_search_context()

        with patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_id",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_ref",
            new=AsyncMock(return_value=None),
        ):
            result = await handle_ver_detalles_ic(
                99, session, contact, conversation, search_ctx,
                conversation_manager=deps["conversation_manager"],
            )
            response = result.response

        assert response.intent == "conversacion"
        assert "No pude encontrar" in response.text
        deps["conversation_manager"].save_outbound_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ic_cross_ref_enrichment_merges_canonical_fields(self):
        """Cuando property_id no es None → enriquece prop_dict con campos de la tabla canónica."""
        orch, deps = _make_orchestrator()
        session = AsyncMock()
        contact = _default_contact()
        conversation = _default_conversation()
        search_ctx = _default_search_context()

        ic_prop = self._make_ic_prop(id=42, property_id=100)
        canonical = self._make_canonical_prop()

        with patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_id",
            new=AsyncMock(return_value=ic_prop),
        ), patch(
            "app.repositories.property_repo.PropertyRepository.get_by_id",
            new=AsyncMock(return_value=canonical),
        ), patch(
            "app.bot.core.response_builder.ic_prop_to_detail_dict",
            return_value={"id": 42, "title": "IC prop"},
        ):
            result = await handle_ver_detalles_ic(
                42, session, contact, conversation, search_ctx,
                conversation_manager=deps["conversation_manager"],
            )
            response = result.response

        enriched = response.properties[0]
        assert enriched["description"] == "Hermosa casa con jardín"
        assert enriched["address"] == "Av. España 123, Asuncion"
        assert enriched["latitude"] == -25.2867
        assert enriched["longitude"] == -57.6470
        assert enriched["main_image_url"] == "https://onnix.com.py/images/prop_100.webp"
        assert enriched["local_image_count"] == 5

    @pytest.mark.asyncio
    async def test_ic_cross_ref_skips_fields_that_are_none_or_empty(self):
        """Campos None, '' o [] en canonical NO sobreescriben el prop_dict."""
        orch, deps = _make_orchestrator()
        session = AsyncMock()
        contact = _default_contact()
        conversation = _default_conversation()
        search_ctx = _default_search_context()

        ic_prop = self._make_ic_prop(id=42, property_id=100)

        # canonical con todos los campos vacíos/None
        canonical = MagicMock()
        canonical.description = None
        canonical.address = ""
        canonical.latitude = None
        canonical.longitude = None
        canonical.main_image_url = None
        canonical.image_urls = []
        canonical.local_image_count = None

        original_dict = {"id": 42, "title": "IC prop original", "address": "IC address"}

        with patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_id",
            new=AsyncMock(return_value=ic_prop),
        ), patch(
            "app.repositories.property_repo.PropertyRepository.get_by_id",
            new=AsyncMock(return_value=canonical),
        ), patch(
            "app.bot.core.response_builder.ic_prop_to_detail_dict",
            return_value=dict(original_dict),
        ):
            result = await handle_ver_detalles_ic(
                42, session, contact, conversation, search_ctx,
                conversation_manager=deps["conversation_manager"],
            )
            response = result.response

        enriched = response.properties[0]
        # Ningún campo vacío del canonical debe haber sobreescrito el dict original
        assert enriched.get("description") is None or "description" not in enriched
        assert enriched.get("address") == "IC address"  # valor original preservado

    @pytest.mark.asyncio
    async def test_ic_without_property_id_skips_enrichment(self):
        """ic_prop.property_id=None → no llama get_by_id (no hay cross-ref)."""
        orch, deps = _make_orchestrator()
        session = AsyncMock()
        contact = _default_contact()
        conversation = _default_conversation()
        search_ctx = _default_search_context()

        ic_prop = self._make_ic_prop(id=42, property_id=None)

        get_by_id_mock = AsyncMock(return_value=None)

        with patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_id",
            new=AsyncMock(return_value=ic_prop),
        ), patch(
            "app.repositories.property_repo.PropertyRepository.get_by_id",
            new=get_by_id_mock,
        ), patch(
            "app.bot.core.response_builder.ic_prop_to_detail_dict",
            return_value={"id": 42, "title": "IC no cross-ref"},
        ):
            result = await handle_ver_detalles_ic(
                42, session, contact, conversation, search_ctx,
                conversation_manager=deps["conversation_manager"],
            )
            response = result.response

        get_by_id_mock.assert_not_awaited()
        assert response.intent == "detalle"

    @pytest.mark.asyncio
    async def test_ic_happy_path_calls_save_outbound_message(self):
        """Happy path completo → save_outbound_message llamado con properties_shown."""
        orch, deps = _make_orchestrator()
        session = AsyncMock()
        contact = _default_contact()
        conversation = _default_conversation()
        search_ctx = _default_search_context()
        ic_prop = self._make_ic_prop(id=42, property_id=None)

        with patch(
            "app.repositories.property_repo.PropertyRepository.get_ic_by_id",
            new=AsyncMock(return_value=ic_prop),
        ), patch(
            "app.bot.core.response_builder.ic_prop_to_detail_dict",
            return_value={"id": 42, "title": "IC test"},
        ):
            await handle_ver_detalles_ic(
                42, session, contact, conversation, search_ctx,
                conversation_manager=deps["conversation_manager"],
            )

        deps["conversation_manager"].save_outbound_message.assert_awaited_once_with(
            session,
            conversation.id,
            contact.id,
            "",
            "detalle",
            properties_shown=[42],
        )
