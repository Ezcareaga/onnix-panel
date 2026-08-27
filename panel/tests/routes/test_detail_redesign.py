"""TDD — M6.5 redesign del detalle interno /properties/{id} (T7).

Diseño fijado:
  - Galería hero con primera foto server-rendered (id="hero-ssr") visible
    sin JS; Alpine la oculta al inicializar (:class hidden). El carousel
    Alpine va envuelto en x-cloak → invisible hasta que Alpine cargue.
    Resultado: nunca se ven las dos a la vez, y sin JS siempre hay foto.
  - Sin fotos locales pero con main_image_url → hero SSR hotlink.
  - Sin fotos y sin main_image_url → placeholder server-rendered.
  - Datos clave above the fold: precio grande + dorm/baños/m².
  - Botones existentes intactos: Copiar link público + Ver original +
    WhatsApp del agente.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

_PHOTOS = [
    "/images/onnixpy/Onnix-42/1.webp",
    "/images/onnixpy/Onnix-42/2.webp",
    "/images/onnixpy/Onnix-42/3.webp",
]

_BASE_PROP = {
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
    "description": "Hermoso departamento.\nCon vista a la ciudad.",
    "agent_name": "María López",
    "agent_phone": "+595981000000",
    "agent_whatsapp": "+595981000000",
    "is_active": True,
    "on_hold": False,
    "local_image_count": 3,
    "main_image_url": None,
    "latitude": -25.2921,
    "longitude": -57.5759,
    "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
    "updated_at": datetime(2025, 3, 1, tzinfo=timezone.utc),
    "last_scraped_at": datetime(2025, 3, 1, tzinfo=timezone.utc),
    "portal_listed_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
    "portal_expires_at": datetime(2025, 7, 1, tzinfo=timezone.utc),
    "photo_urls": list(_PHOTOS),
    "public_path": "/prop/42-departamento-en-villa-morra-asuncion",
}


def _prop(**overrides) -> dict:
    base = dict(_BASE_PROP)
    base.update(overrides)
    return base


def _patch_detail(prop: dict):
    return patch(
        "app.routes.properties.property_service.get_property_detail",
        new=AsyncMock(return_value=prop),
    )


def _tag_of(html: str, marker: str) -> str:
    """Return the full opening tag that contains ``marker``."""
    pos = html.index(marker)
    start = html.rindex("<", 0, pos)
    end = html.index(">", pos)
    return html[start : end + 1]


class TestHeroGalleryDegradation:
    async def test_detail_first_photo_server_rendered(self, admin_client):
        with _patch_detail(_prop()):
            resp = await admin_client.get("/properties/42")
        assert resp.status_code == 200
        html = resp.text

        assert 'id="hero-ssr"' in html
        tag = _tag_of(html, 'id="hero-ssr"')
        # src PLANO (server-rendered), no binding Alpine
        assert 'src="/images/onnixpy/Onnix-42/1.webp"' in tag
        assert ':src=' not in tag
        # FUERA de <template> — los <template>/<\template> previos al hero
        # deben estar balanceados (si estuviera adentro habría uno abierto).
        pos = html.index('id="hero-ssr"')
        assert html.count("<template", 0, pos) == html.count("</template>", 0, pos)

    async def test_detail_no_photos_placeholder(self, admin_client):
        with _patch_detail(
            _prop(photo_urls=[], local_image_count=0, main_image_url=None)
        ):
            resp = await admin_client.get("/properties/42")
        assert resp.status_code == 200
        html = resp.text
        # Placeholder elegante server-rendered, sin <img> hero roto
        assert "Sin fotos" in html
        assert 'id="hero-ssr"' not in html

    async def test_detail_hotlink_fallback(self, admin_client):
        hotlink = "https://cdn.onnix.com.py/fotos/Onnix-42/main.jpg"
        with _patch_detail(
            _prop(photo_urls=[], local_image_count=0, main_image_url=hotlink)
        ):
            resp = await admin_client.get("/properties/42")
        assert resp.status_code == 200
        html = resp.text
        assert 'id="hero-ssr"' in html
        tag = _tag_of(html, 'id="hero-ssr"')
        assert f'src="{hotlink}"' in tag

    async def test_detail_carousel_has_x_cloak(self, admin_client):
        with _patch_detail(_prop()):
            resp = await admin_client.get("/properties/42")
        assert resp.status_code == 200
        html = resp.text
        assert 'id="gallery-carousel"' in html
        tag = _tag_of(html, 'id="gallery-carousel"')
        assert "x-cloak" in tag


class TestAboveTheFold:
    async def test_detail_key_data_above_fold(self, admin_client):
        with _patch_detail(_prop()):
            resp = await admin_client.get("/properties/42")
        assert resp.status_code == 200
        html = resp.text
        # Precio grande USD, con el separador de miles que se usa en Paraguay
        assert "USD 120.000" in html
        # Strip de datos clave: dorm · baños · m²
        assert "dorm" in html
        assert "baños" in html
        assert "m²" in html
        assert "90" in html


class TestExistingActionsPreserved:
    async def test_detail_keeps_copy_buttons(self, admin_client):
        with _patch_detail(_prop()):
            resp = await admin_client.get("/properties/42")
        assert resp.status_code == 200
        html = resp.text
        assert "Copiar link público" in html
        # Prefijo, sin comilla de cierre: el template agrega `asesor_a_suffix`
        # (`?a={user.id}` si el usuario tiene teléfono, properties.py:300) y
        # `admin_client` entra como el admin real, que hoy tiene teléfono. El
        # sufijo lo cubre test_asesor_link.py con usuarios construidos a
        # propósito; acoplarlo al seed acá hace el test frágil.
        assert (
            'data-public-url="https://onnix.com.py/prop/42-departamento-en-villa-morra-asuncion'
            in html
        )
        assert "Ver original" in html
        assert 'data-original-url="https://onnix.com.py/propiedad/Onnix-42"' in html
        # WhatsApp del agente (wa.me)
        assert "wa.me/595981000000" in html


class TestXCloakCss:
    async def test_x_cloak_css_rule_present(self, admin_client):
        # La regla debe llegar al browser via el CSS servido por el panel.
        resp = await admin_client.get("/static/css/custom.css")
        assert resp.status_code == 200
        css = resp.text.replace(" ", "")
        assert "[x-cloak]" in css
        assert "display:none!important" in css


class TestUnSoloCaminoPorAccion:
    """`ui.md`: nunca dos botones que hagan lo mismo en una vista.

    La ficha tenía tres caminos al WhatsApp del agente (header, CTA y card del
    agente) y dos al aviso de origen (header «Ver original» y el CTA de acento
    «Ver en portal»). Y el acento —la única primaria— se lo llevaba justamente el
    que manda al asesor al portal de la competencia, no el que produce trabajo.
    """

    async def test_un_solo_link_al_whatsapp_del_agente(self, admin_client):
        with _patch_detail(_prop()):
            resp = await admin_client.get("/properties/42")
        assert resp.text.count("wa.me/") == 1

    async def test_un_solo_camino_al_aviso_de_origen(self, admin_client):
        with _patch_detail(_prop()):
            html = (await admin_client.get("/properties/42")).text
        assert html.count("https://onnix.com.py/propiedad/Onnix-42") == 1
        # El CTA de acento que llevaba al portal de origen ya no existe.
        assert "Ver en portal" not in html

    async def test_un_solo_boton_de_copiar_link(self, admin_client):
        with _patch_detail(_prop()):
            html = (await admin_client.get("/properties/42")).text
        assert html.count("Copiar link público") == 1

    async def test_la_primaria_es_copiar_link_publico(self, admin_client):
        """El único fondo de acento de la vista es el botón de copiar el link."""
        with _patch_detail(_prop()):
            html = (await admin_client.get("/properties/42")).text
        # `_tag_of` no sirve acá: la expresión de Alpine tiene un `=>` y el
        # primer `>` cae dentro del atributo. Se recorta del marcador al label.
        boton = html[html.index("data-public-url=") : html.index("Copiar link público")]
        # Token exacto, no substring: `bg-onnix-accent-dark` (el estado «ya está en
        # la lista») CONTIENE `bg-onnix-accent`, así que un `in` pelado seguía verde
        # con el fondo primario borrado. Lo confirmó la mutación de sanidad.
        assert re.search(r"[\s'\"]bg-onnix-accent[\s'\"]", boton), boton
        # 44px: la ficha se abre desde el celular, parado frente a la casa.
        assert "min-h-[44px]" in boton
        # Y el otro CTA del bloque, el de WhatsApp, NO se pinta de primario.
        # Se mira el bloque de CTAs y no la página: el shell (sidebar, topbar)
        # también usa el acento y no es asunto de esta vista.
        secundario = html[html.index("wa.me/") : html.index("Contactar agente")]
        assert "bg-onnix-accent" not in secundario
