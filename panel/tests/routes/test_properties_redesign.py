"""TDD — M6.5 redesign del listado /properties (T5).

Layout objetivo: fuera sidebar y stat-cards; resultados full-width con una
barra superior sticky de filtros (id="filters-topbar") que contiene los 6
controles primarios + panel colapsable "Más filtros" (id="more-filters").
Chips de filtros aplicados bajo la barra (ahora también amenities/barato).
Microcopy de stats en texto plano (nunca cards).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch


def _patch_service(total: int = 0):
    """Patch property_service.get_properties so we never hit the props query."""
    return patch(
        "app.routes.properties.property_service.get_properties",
        new=AsyncMock(return_value=([], total)),
    )


def _row(**overrides) -> dict:
    """Minimal listing-row dict con todos los campos que usa el partial."""
    base = {
        "id": 1,
        "source": "onnixpy",
        "external_id": "Onnix-1",
        "title": "Casa en Lambaré",
        "url": "https://onnix.com.py/propiedad/Onnix-1",
        "price_usd": 120000,
        "price_pyg": None,
        "city": "Lambaré",
        "neighborhood": "Centro",
        "operation": "venta",
        "property_type": "casa",
        "bedrooms": 3,
        "bathrooms": 2,
        "total_area_m2": 120,
        "is_active": True,
        "on_hold": False,
        "updated_at": datetime.now(timezone.utc) - timedelta(days=2),
        "local_image_count": 0,
        "main_image_url": None,
        # El service (attach_public_paths) setea esto; el route está patcheado
        # así que el row mockeado debe traerlo.
        "public_path": "/prop/1-casa-en-lambare-lambare",
    }
    base.update(overrides)
    return base


def _patch_rows(rows: list[dict]):
    return patch(
        "app.routes.properties.property_service.get_properties",
        new=AsyncMock(return_value=(rows, len(rows))),
    )


class TestTopBarLayout:
    async def test_properties_filters_top_bar_renders(self, admin_client):
        with _patch_service():
            resp = await admin_client.get("/properties")
        assert resp.status_code == 200
        html = resp.text
        # Barra superior sticky presente
        assert 'id="filters-topbar"' in html
        # Sidebar de filtros eliminado — el único <aside> que queda es la
        # nav global del layout (partials/sidebar.html, id="sidebar").
        assert html.count("<aside") == 1
        assert 'id="sidebar"' in html
        assert "lg:w-72" not in html  # marker del aside de filtros viejo
        # Stat-cards eliminadas (markers de las 4 cards viejas)
        assert "en portal" not in html
        assert "en espera" not in html
        assert "en resultado" not in html

    async def test_htmx_form_attrs_preserved(self, admin_client):
        with _patch_service():
            resp = await admin_client.get("/properties")
        assert resp.status_code == 200
        html = resp.text
        assert 'hx-get="/properties"' in html
        assert 'hx-target="#properties-table"' in html
        assert 'hx-push-url="true"' in html

    async def test_state_filter_defaults_active(self, admin_client):
        # Sin ?state en la URL → el select state arranca en Activas
        with _patch_service():
            resp = await admin_client.get("/properties")
        assert resp.status_code == 200
        html = resp.text
        assert '<select name="state"' in html
        assert '<option value="active" selected>' in html

    async def test_results_microcopy_renders(self, admin_client):
        # Microcopy "X.XXX resultados · N activas" — texto plano, no cards
        with _patch_service(total=1234):
            resp = await admin_client.get("/properties")
        assert resp.status_code == 200
        html = resp.text
        assert "1.234" in html  # total con separador de miles es-PY
        assert "resultados" in html
        assert "activas" in html


class TestMoreFiltersPanel:
    async def test_more_filters_panel_contains_secondary(self, admin_client):
        with _patch_service():
            resp = await admin_client.get("/properties")
        assert resp.status_code == 200
        html = resp.text
        marker = html.index('id="more-filters"')
        # Los 5 filtros secundarios viven DENTRO del panel colapsable
        # (aparecen después del marker; los primarios aparecen antes).
        for field in (
            'name="neighborhood"',
            'name="source"',
            'name="state"',
            'name="construction_state"',
            'name="updated_within_days"',
        ):
            assert field in html, f"{field} ausente"
            assert html.index(field) > marker, f"{field} fuera del panel more-filters"


class TestCurrencyIntegrated:
    async def test_currency_select_not_standalone(self, admin_client):
        # El select suelto de moneda desaparece — la moneda vive integrada
        # en el popover de precio.
        with _patch_service():
            resp = await admin_client.get("/properties")
        assert resp.status_code == 200
        assert '<select name="currency"' not in resp.text


class TestAmenitiesBaratoChips:
    async def test_amenities_barato_chips(self, admin_client):
        with _patch_service():
            resp = await admin_client.get(
                "/properties?amenities=piscina&barato=true"
            )
        assert resp.status_code == 200
        html = resp.text
        assert 'data-chip-param="amenities"' in html
        assert 'data-chip-param="barato"' in html
        assert "Piscina" in html
        assert "Barato" in html


# ── M6.5 T6 — copiar link público + ver original + acumulador ──────────────

_DETAIL_PROP = {
    "id": 42,
    "source": "onnixpy",
    "external_id": "Onnix-42",
    "title": "Departamento en Villa Morra",
    "url": "https://onnix.com.py/propiedad/Onnix-42",
    "price_usd": 120000,
    "price_pyg": None,
    "price_currency": "USD",
    "operation": "venta",
    "property_type": "departamento",
    "city": "Asunción",
    "neighborhood": "Villa Morra",
    "bedrooms": 3,
    "bathrooms": 2,
    "parking": 1,
    "total_area_m2": 90,
    "construction_state": "usado",
    "description": "Hermoso departamento.",
    "agent_name": "María López",
    "agent_phone": "+595981000000",
    "agent_whatsapp": "+595981000000",
    "is_active": True,
    "on_hold": False,
    "local_image_count": 0,
    "main_image_url": None,
    "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
    "updated_at": datetime(2025, 3, 1, tzinfo=timezone.utc),
    "last_scraped_at": datetime(2025, 3, 1, tzinfo=timezone.utc),
    "portal_listed_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
    "portal_expires_at": datetime(2025, 7, 1, tzinfo=timezone.utc),
    "photo_urls": [],
    "public_path": "/prop/42-departamento-en-villa-morra-asuncion",
}


class TestLinkBasket:
    async def test_copy_button_only_for_eligible_rows(self, admin_client):
        rows = [
            _row(),  # elegible: trae public_path
            _row(
                id=2,
                external_id="IC-2",
                source="infocasas",
                title="Depto InfoCasas",
                url="https://infocasas.com.py/prop/IC-2",
                public_path=None,  # NO elegible
            ),
        ]
        with _patch_rows(rows):
            resp = await admin_client.get("/properties")
        assert resp.status_code == 200
        html = resp.text
        # Solo la fila elegible expone data-public-url (URL absoluta).
        # Se compara el PREFIJO, sin la comilla de cierre: el template agrega
        # `asesor_a_suffix` (`?a={user.id}` si el usuario tiene teléfono,
        # properties.py:300) y `admin_client` entra como el admin real, que hoy
        # tiene teléfono. Ese sufijo es de la feature de atribución por asesor y
        # ya lo cubre test_asesor_link.py con usuarios construidos a propósito
        # con y sin teléfono; acá acoplarlo al seed hace el test frágil.
        # Dos marcadores, no dos filas elegibles: desde el carril J la misma
        # fila se renderiza dos veces —tabla (md:block) y card (md:hidden)—
        # y el navegador muestra una sola según el viewport.
        assert html.count("data-public-url=") == 2
        assert 'data-public-url="https://onnix.com.py/prop/1-casa-en-lambare-lambare' in html
        # La fila sin public_path no expone el botón en NINGUNA de las dos.
        assert 'data-prop-id="2"' not in html

    async def test_original_url_link_shown_internally(self, admin_client):
        with _patch_rows([_row()]):
            resp = await admin_client.get("/properties")
        assert resp.status_code == 200
        html = resp.text
        assert "Ver original" in html
        assert 'data-original-url="https://onnix.com.py/propiedad/Onnix-1"' in html

    async def test_price_label_data_attr_matches_cell(self, admin_client):
        with _patch_rows([_row(price_usd=120000)]):
            resp = await admin_client.get("/properties")
        assert resp.status_code == 200
        # priceLabel = EXACTAMENTE lo que muestra la celda de precio, y ese
        # texto se va al portapapeles y de ahí a un WhatsApp: tiene que estar
        # escrito igual que en la página pública que el cliente abre después.
        assert 'data-price-label="USD 120.000"' in resp.text

    async def test_price_label_pyg_and_consultar(self, admin_client):
        rows = [
            _row(id=3, external_id="Onnix-3", price_usd=None, price_pyg=500000000,
                 public_path="/prop/3-x"),
            _row(id=4, external_id="Onnix-4", price_usd=None, price_pyg=None,
                 public_path="/prop/4-x"),
        ]
        with _patch_rows(rows):
            resp = await admin_client.get("/properties")
        assert resp.status_code == 200
        assert 'data-price-label="₲ 500.000.000"' in resp.text
        assert 'data-price-label="A consultar"' in resp.text

    async def test_detail_has_copy_buttons(self, admin_client):
        with patch(
            "app.routes.properties.property_service.get_property_detail",
            new=AsyncMock(return_value=dict(_DETAIL_PROP)),
        ):
            resp = await admin_client.get("/properties/42")
        assert resp.status_code == 200
        html = resp.text
        assert "Copiar link público" in html
        # Prefijo, sin comilla de cierre: ver la nota de
        # test_copy_button_only_for_eligible_rows sobre asesor_a_suffix.
        assert 'data-public-url="https://onnix.com.py/prop/42-departamento-en-villa-morra-asuncion' in html
        assert "Ver original" in html
        assert 'data-original-url="https://onnix.com.py/propiedad/Onnix-42"' in html

    async def test_basket_bar_in_base(self, admin_client):
        # La barra flotante + el módulo link_basket viven en base.html →
        # presentes en cualquier página del panel.
        with _patch_service():
            resp = await admin_client.get("/properties")
        assert resp.status_code == 200
        html = resp.text
        assert 'id="link-basket-bar"' in html
        assert "/static/js/link_basket.js" in html


class TestVistaMovilDeLaTabla:
    """La tabla tenía `min-w-[760px]` y ninguna vista alternativa.

    Siete columnas empujando scroll horizontal en la pantalla que el asesor
    abre parado frente a una casa. Patrón de `partials/leads_views.html`:
    tabla ≥768px, cards <768px, sobre el MISMO `rows` del contexto.
    """

    async def test_hay_cards_ademas_de_la_tabla(self, admin_client):
        with _patch_rows([_row(), _row(id=2, external_id="Onnix-2")]):
            html = (await admin_client.get("/properties")).text
        assert "data-properties-cards" in html
        # Una card por fila, no una sola ni el doble.
        assert html.count('href="/properties/1"') == 1
        assert html.count('href="/properties/2"') == 1

    async def test_la_tabla_no_se_muestra_en_movil(self, admin_client):
        with _patch_rows([_row()]):
            html = (await admin_client.get("/properties")).text
        tabla = html[: html.index("<table")]
        assert "hidden md:block" in tabla.rsplit("<div", 1)[1]

    async def test_las_cards_no_se_muestran_en_escritorio(self, admin_client):
        with _patch_rows([_row()]):
            html = (await admin_client.get("/properties")).text
        contenedor = html[html.rindex("<div", 0, html.index("data-properties-cards")):
                          html.index("data-properties-cards")]
        assert "md:hidden" in contenedor

    async def test_la_card_pone_el_precio_antes_que_el_titulo(self, admin_client):
        # Jerarquía del carril J: el título del aviso va último.
        with _patch_rows([_row(title="Hermosa casa en venta zona Villa Morra")]):
            html = (await admin_client.get("/properties")).text
        card = html[html.index("data-properties-cards"):]
        assert card.index("USD 120.000") < card.index("Hermosa casa en venta")

    async def test_los_botones_de_la_card_miden_44px(self, admin_client):
        with _patch_rows([_row()]):
            html = (await admin_client.get("/properties")).text
        card = html[html.index("data-properties-cards"):]
        # w-11 h-11 = 44px en la escala de Tailwind (4px * 11).
        assert card.count("w-11 h-11") == 2

    async def test_los_botones_quedan_por_encima_del_link_estirado(self, admin_client):
        """Sin `relative z-10` el `after:inset-0` del título se los come."""
        with _patch_rows([_row()]):
            html = (await admin_client.get("/properties")).text
        card = html[html.index("data-properties-cards"):]
        assert "after:absolute after:inset-0" in card
        assert "relative z-10" in card

    async def test_la_card_no_repite_el_formato_viejo_de_numero(self, admin_client):
        with _patch_rows([_row(price_usd=1200000)]):
            html = (await admin_client.get("/properties")).text
        card = html[html.index("data-properties-cards"):]
        assert "USD 1.200.000" in card
        assert "1,200,000" not in card
