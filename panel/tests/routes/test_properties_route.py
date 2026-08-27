"""TDD — GET /properties route

Tests: auth guard, full-page vs HTMX partial response, filter pass-through,
       pagination offset calculation.

Mocking pattern mirrors test_routes_leads.py: uses the shared admin_client /
user_client / client fixtures from conftest.py plus patch() for service calls
so tests never hit the DB.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from unittest.mock import AsyncMock, patch


def _fake_row(i: int = 1) -> dict:
    """Fila mínima que el template de la tabla sabe renderizar."""
    return {
        "id": i, "source": "remax", "external_id": f"x{i}", "title": f"t{i}",
        "url": "", "price_usd": None, "price_pyg": None, "price_currency": "USD",
        "city": "", "neighborhood": "", "operation": "venta",
        "property_type": "departamento", "bedrooms": None, "bathrooms": None,
        "total_area_m2": None, "construction_state": None, "is_active": True,
        "on_hold": False, "updated_at": datetime.now(timezone.utc),
        "created_at": datetime.now(timezone.utc), "portal_listed_at": None,
        "portal_expires_at": None, "main_image_url": None, "local_image_count": 0,
        "public_path": None,
    }


class TestGetPropertiesAuth:
    async def test_unauthenticated_redirects_to_login(self, client):
        resp = await client.get("/properties")
        assert resp.status_code == 303
        assert "/login" in resp.headers["location"]

    async def test_admin_gets_200(self, admin_client):
        with patch(
            "app.routes.properties.property_service.get_properties",
            new=AsyncMock(return_value=([], 0)),
        ):
            resp = await admin_client.get("/properties")
        assert resp.status_code == 200

    async def test_user_role_gets_200(self, user_client):
        with patch(
            "app.routes.properties.property_service.get_properties",
            new=AsyncMock(return_value=([], 0)),
        ):
            resp = await user_client.get("/properties")
        assert resp.status_code == 200


class TestGetPropertiesResponse:
    async def test_htmx_returns_partial(self, admin_client):
        with patch(
            "app.routes.properties.property_service.get_properties",
            new=AsyncMock(return_value=([], 0)),
        ):
            resp = await admin_client.get(
                "/properties",
                headers={"HX-Request": "true"},
            )
        assert resp.status_code == 200
        # Partial must NOT include the full base layout chrome
        assert b"<html" not in resp.content

    async def test_full_returns_index_with_html(self, admin_client):
        with patch(
            "app.routes.properties.property_service.get_properties",
            new=AsyncMock(return_value=([], 0)),
        ):
            resp = await admin_client.get("/properties")
        assert resp.status_code == 200
        assert b"<html" in resp.content


class TestGetPropertiesFilterPassThrough:
    async def test_city_filter_passes_to_service(self, admin_client):
        captured = {}

        async def mock_get_properties(db, filters, page, per_page):
            captured["filters"] = filters
            return [], 0

        with patch(
            "app.routes.properties.property_service.get_properties",
            new=mock_get_properties,
        ):
            resp = await admin_client.get("/properties?city=asuncion")

        assert resp.status_code == 200
        assert captured["filters"].city == "asuncion"

    async def test_operation_filter_passes_to_service(self, admin_client):
        captured = {}

        async def mock_get_properties(db, filters, page, per_page):
            captured["filters"] = filters
            return [], 0

        with patch(
            "app.routes.properties.property_service.get_properties",
            new=mock_get_properties,
        ):
            await admin_client.get("/properties?operation=venta")

        assert captured["filters"].operation == "venta"

    async def test_state_filter_all_passes(self, admin_client):
        captured = {}

        async def mock_get_properties(db, filters, page, per_page):
            captured["filters"] = filters
            return [], 0

        with patch(
            "app.routes.properties.property_service.get_properties",
            new=mock_get_properties,
        ):
            await admin_client.get("/properties?state=all")

        assert captured["filters"].state == "all"


class TestGetPropertiesAmenitiesBarato:
    """M6.5 — amenities (repetible y CSV) + barato hacia PropertyFilters."""

    async def test_properties_route_accepts_amenities_params(self, admin_client):
        captured = {}

        async def mock_get_properties(db, filters, page, per_page):
            captured["filters"] = filters
            return [], 0

        with patch(
            "app.routes.properties.property_service.get_properties",
            new=mock_get_properties,
        ):
            # Formato repetible (?amenities=a&amenities=b)
            resp = await admin_client.get(
                "/properties?amenities=piscina&amenities=garage"
            )
            assert resp.status_code == 200
            assert captured["filters"].amenities == ["piscina", "garage"]

            # Formato CSV en un solo param (frontend submit() serializa el
            # array JS como "piscina,garage")
            resp = await admin_client.get("/properties?amenities=piscina,garage")
            assert resp.status_code == 200
            assert captured["filters"].amenities == ["piscina", "garage"]

    async def test_properties_route_barato_param(self, admin_client):
        captured = {}

        async def mock_get_properties(db, filters, page, per_page):
            captured["filters"] = filters
            return [], 0

        with patch(
            "app.routes.properties.property_service.get_properties",
            new=mock_get_properties,
        ):
            resp = await admin_client.get("/properties?barato=true")
            assert resp.status_code == 200
            assert captured["filters"].barato is True

            # Default: sin param → False
            resp = await admin_client.get("/properties")
            assert resp.status_code == 200
            assert captured["filters"].barato is False
            assert captured["filters"].amenities is None


class TestGetPropertiesPagination:
    async def test_page_2_offset_is_50(self, admin_client):
        captured = {}

        async def mock_get_properties(db, filters, page, per_page):
            captured["page"] = page
            captured["per_page"] = per_page
            return [], 0

        with patch(
            "app.routes.properties.property_service.get_properties",
            new=mock_get_properties,
        ):
            resp = await admin_client.get("/properties?page=2")

        assert resp.status_code == 200
        assert captured["page"] == 2
        assert captured["per_page"] == 50
        # offset = (2-1)*50 = 50 — verified via service call args


class TestEmptyHintsRender:
    async def test_empty_state_renders_hints_inline(self, admin_client):
        """Cuando hay 0 resultados y service devuelve hints, se muestran inline."""
        hints = [
            {"drop": "property_type", "count": 8421, "dropped_value": "departamento"},
            {"drop": "city", "count": 15234, "dropped_value": "encarnacion"},
        ]
        with patch(
            "app.routes.properties.property_service.get_properties",
            new=AsyncMock(return_value=([], 0)),
        ), patch(
            "app.routes.properties.property_service.get_empty_hints",
            new=AsyncMock(return_value=hints),
        ):
            resp = await admin_client.get(
                "/properties?property_type=departamento&city=encarnacion"
            )
        assert resp.status_code == 200
        # Marcadores de las dos sugerencias
        assert b'data-empty-hint="property_type"' in resp.content
        assert b'data-empty-hint="city"' in resp.content
        # Conteos formateados con el separador de miles paraguayo
        assert b"8.421" in resp.content
        assert b"15.234" in resp.content
        # Y con el filtro dropeado citado
        assert b"departamento" in resp.content
        assert b"encarnacion" in resp.content

    async def test_hint_href_urlencodea_los_valores(self, admin_client):
        """Un barrio con espacio no puede cortar el href de la sugerencia.

        `k ~ '=' ~ v` producía `?city=Ciudad del Este`: el navegador corta en el
        espacio y la sugerencia lleva a otra búsqueda. Con `&` en el valor es
        peor — inventa un filtro.
        """
        hints = [{"drop": "property_type", "count": 12, "dropped_value": "casa"}]
        with patch(
            "app.routes.properties.property_service.get_properties",
            new=AsyncMock(return_value=([], 0)),
        ), patch(
            "app.routes.properties.property_service.get_empty_hints",
            new=AsyncMock(return_value=hints),
        ):
            resp = await admin_client.get(
                "/properties?property_type=casa&city=Ciudad+del+Este"
            )
        assert resp.status_code == 200
        html = resp.text
        href = html.split('data-empty-hint="property_type"')[0].rsplit('href="', 1)[1]
        href = href.split('"')[0]
        assert " " not in href, href
        assert "city=Ciudad+del+Este" in href or "city=Ciudad%20del%20Este" in href

    async def test_paginacion_urlencodea_los_valores(self, admin_client):
        rows_page = [_fake_row(i) for i in range(3)]
        with patch(
            "app.routes.properties.property_service.get_properties",
            new=AsyncMock(return_value=(rows_page, 300)),
        ):
            resp = await admin_client.get("/properties?city=Ciudad+del+Este")
        assert resp.status_code == 200
        pag = resp.text.rsplit("Siguiente", 1)[0].rsplit('href="', 1)[1].split('"')[0]
        assert " " not in pag, pag
        assert "page=2" in pag

    async def test_empty_state_usa_el_parcial_unico(self, admin_client):
        """El estado vacío sale de partials/empty_state.html, con acción."""
        with patch(
            "app.routes.properties.property_service.get_properties",
            new=AsyncMock(return_value=([], 0)),
        ), patch(
            "app.routes.properties.property_service.get_empty_hints",
            new=AsyncMock(return_value=None),
        ):
            resp = await admin_client.get("/properties?city=luque")
        html = resp.text
        assert "data-empty-state" in html
        # ui.md: estados vacíos CON acción, no disculpas.
        assert "data-empty-action" in html

    async def test_empty_state_no_hints_when_service_returns_none(self, admin_client):
        with patch(
            "app.routes.properties.property_service.get_properties",
            new=AsyncMock(return_value=([], 0)),
        ), patch(
            "app.routes.properties.property_service.get_empty_hints",
            new=AsyncMock(return_value=None),
        ):
            resp = await admin_client.get("/properties")
        assert resp.status_code == 200
        assert b"data-empty-hint" not in resp.content
        # El empty state base sigue ahi
        assert "Sin propiedades".encode() in resp.content

    async def test_get_empty_hints_skipped_when_results_present(self, admin_client):
        """Si hay propiedades, no se calcula empty_hints (gasta una query)."""
        called = {"empty": False}

        async def fake_empty(db, filters):
            called["empty"] = True
            return None

        # Devolvemos 1 fila falsa para que total > 0.
        from datetime import datetime, timezone
        fake_row = {
            "id": 1, "source": "remax", "external_id": "x", "title": "t",
            "url": "", "price_usd": None, "price_pyg": None, "price_currency": "USD",
            "city": "", "neighborhood": "", "operation": "venta",
            "property_type": "departamento", "bedrooms": None, "bathrooms": None,
            "total_area_m2": None, "construction_state": None, "is_active": True,
            "on_hold": False, "updated_at": datetime.now(timezone.utc),
            "created_at": datetime.now(timezone.utc), "portal_listed_at": None,
            "portal_expires_at": None, "main_image_url": None, "local_image_count": 0,
        }
        with patch(
            "app.routes.properties.property_service.get_properties",
            new=AsyncMock(return_value=([fake_row], 1)),
        ), patch(
            "app.routes.properties.property_service.get_empty_hints",
            side_effect=fake_empty,
        ):
            resp = await admin_client.get("/properties?city=asuncion")
        assert resp.status_code == 200
        assert called["empty"] is False  # No se llamo


class TestChatbotWidgetVisibility:
    async def test_search_tab_says_IA_when_flag_enabled(self, admin_client):
        """Cuando el chatbot esta habilitado, la top bar muestra el switch IA."""
        with patch(
            "app.routes.properties.property_service.get_properties",
            new=AsyncMock(return_value=([], 0)),
        ), patch(
            "app.routes.properties._is_chatbot_enabled",
            new=AsyncMock(return_value=True),
        ):
            resp = await admin_client.get("/properties")
        assert resp.status_code == 200
        # La top bar siempre se monta con propertiesBar(); con IA habilitada,
        # el switch IA aparece y el submit hace fetch a parse-query.
        assert b"propertiesBar(" in resp.content
        assert b'role="switch"' in resp.content
        assert b"/api/properties/parse-query" in resp.content

    async def test_search_tab_hides_IA_when_flag_disabled(self, admin_client):
        """Cuando el chatbot esta deshabilitado, no hay switch IA ni parse-query."""
        with patch(
            "app.routes.properties.property_service.get_properties",
            new=AsyncMock(return_value=([], 0)),
        ), patch(
            "app.routes.properties._is_chatbot_enabled",
            new=AsyncMock(return_value=False),
        ):
            resp = await admin_client.get("/properties")
        assert resp.status_code == 200
        assert b"propertiesBar(" in resp.content
        # Sin switch IA
        assert b'role="switch"' not in resp.content
        # Y sin llamada a parse-query
        assert b"/api/properties/parse-query" not in resp.content


class TestIaQueryHybridMode:
    """M6.5 T2 — ia_query activa el modo híbrido SQL+pgvector+RRF."""

    async def test_properties_route_ia_query_uses_hybrid(self, admin_client):
        captured = {}

        async def mock_hybrid(db, filters, ia_query, page=1, per_page=50):
            captured["ia_query"] = ia_query
            captured["filters"] = filters
            return [], 0

        get_props = AsyncMock(return_value=([], 0))
        with patch(
            "app.routes.properties.panel_hybrid_search.search",
            new=mock_hybrid,
        ), patch(
            "app.routes.properties.property_service.get_properties",
            new=get_props,
        ):
            resp = await admin_client.get(
                "/properties?city=asuncion&ia_query=casa+luminosa+con+jardin"
            )

        assert resp.status_code == 200
        assert captured["ia_query"] == "casa luminosa con jardin"
        assert captured["filters"].city == "asuncion"
        get_props.assert_not_awaited()

    async def test_panel_search_uses_ilike_in_filters_mode(self, admin_client):
        """Sin ia_query → camino clásico (get_properties), híbrido NO."""
        hybrid = AsyncMock(return_value=([], 0))
        get_props = AsyncMock(return_value=([], 0))
        with patch(
            "app.routes.properties.panel_hybrid_search.search",
            new=hybrid,
        ), patch(
            "app.routes.properties.property_service.get_properties",
            new=get_props,
        ):
            resp = await admin_client.get("/properties?search_text=casa")

        assert resp.status_code == 200
        get_props.assert_awaited_once()
        hybrid.assert_not_awaited()

    async def test_ia_query_shows_discreet_badge(self, admin_client):
        with patch(
            "app.routes.properties.panel_hybrid_search.search",
            new=AsyncMock(return_value=([], 0)),
        ):
            resp = await admin_client.get("/properties?ia_query=casa+con+quincho")
        assert resp.status_code == 200
        assert "Resultados con búsqueda IA".encode() in resp.content

    async def test_ia_query_renders_removable_chip(self, admin_client):
        """ia_query activo → chip removible (URL source of truth)."""
        with patch(
            "app.routes.properties.panel_hybrid_search.search",
            new=AsyncMock(return_value=([], 0)),
        ):
            resp = await admin_client.get("/properties?ia_query=casa+con+quincho")
        assert resp.status_code == 200
        assert b'data-chip-param="ia_query"' in resp.content
        assert "casa con quincho".encode() in resp.content

    async def test_no_badge_without_ia_query(self, admin_client):
        with patch(
            "app.routes.properties.property_service.get_properties",
            new=AsyncMock(return_value=([], 0)),
        ):
            resp = await admin_client.get("/properties")
        assert resp.status_code == 200
        assert "Resultados con búsqueda IA".encode() not in resp.content

    async def test_frontend_submit_maps_descripcion_libre_to_ia_query(self, admin_client):
        """El submit() del buscador convierte descripcion_libre → ia_query."""
        with patch(
            "app.routes.properties.property_service.get_properties",
            new=AsyncMock(return_value=([], 0)),
        ), patch(
            "app.routes.properties._is_chatbot_enabled",
            new=AsyncMock(return_value=True),
        ):
            resp = await admin_client.get("/properties")
        assert resp.status_code == 200
        assert b"descripcion_libre" in resp.content
        assert b"ia_query" in resp.content


class TestFiltroDeBanos:
    """La asimetría que reportó la auditoría del 24/08: el bot filtraba
    `bathrooms >= n` y el panel no tenía el campo en `PropertyFilters`."""

    async def test_bathrooms_min_llega_al_servicio(self, admin_client):
        captured = {}

        async def fake_get_properties(db, filters, page, per_page):
            captured["filters"] = filters
            return ([], 0)

        with patch(
            "app.routes.properties.property_service.get_properties",
            side_effect=fake_get_properties,
        ):
            resp = await admin_client.get("/properties?bathrooms_min=3")

        assert resp.status_code == 200
        assert captured["filters"].bathrooms_min == 3

    async def test_hay_un_control_de_banos_en_el_formulario(self, admin_client):
        """Sin control en la pantalla el filtro sólo existe para quien escriba
        la URL a mano."""
        with patch(
            "app.routes.properties.property_service.get_properties",
            new=AsyncMock(return_value=([], 0)),
        ):
            resp = await admin_client.get("/properties")
        assert b'name="bathrooms_min"' in resp.content
