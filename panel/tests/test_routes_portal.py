"""TDD — Portal público GET /propiedades (M6.4b).

Covers:
- Listing 200 with cards linking to canonical /prop/{id}-{slug}.
- No auth required.
- Query param validation: whitelist tipo, operacion {venta, alquiler},
  precios numéricos >= 0, ciudad strip/truncate — invalid values degrade
  to None (never 4xx).
- page: non-digit / < 1 / > total_pages (when page > 1) → branded 404.
- Empty results on page 1 → 200 with "Sin resultados".
- Cache-Control public, max-age=300.
- SEO meta: title, canonical, og:title, robots index/follow.
- Pagination links preserving active filters.
- Photo placeholder when photo_url is None.
- source query param is ignored (route signature does not accept it).

All PublicPropertyService calls are mocked via patch on the route module path
(app.routes.public.PublicPropertyService.get_portal_listing) so these tests
never touch the DB.
"""
from __future__ import annotations

import inspect
import pathlib
import urllib.parse
from decimal import Decimal
from html.parser import HTMLParser
from unittest.mock import AsyncMock, patch

import pytest

# El repo, no la copia desplegada. La ruta absoluta anterior
# (/home/onnix/landing/index.html) es lo que nginx sirve, asi que en el VPS
# el test pasaba contra el artefacto viejo y no contra lo que dice la rama:
# verde sin haber mirado el codigo que se iba a desplegar. En el host de
# desarrollo directamente no existe y el test se saltaba siempre.
LANDING_INDEX = pathlib.Path(__file__).resolve().parents[2] / "landing" / "index.html"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _listing(cards=None, total=0, total_pages=0, page=1):
    return {"cards": cards or [], "total": total, "total_pages": total_pages, "page": page}


def _card(**overrides):
    card = {
        "id": 42, "title": "Casa en Asunción",
        "public_path": "/prop/42-casa-en-asuncion-asuncion",
        "photo_url": "/images/onnixpy/Onnix-123/1.webp",
        "price_display": "USD 120.000", "city": "Asuncion",
        "neighborhood": None, "tipo_label": "Casa", "operation": "venta",
        "bedrooms": 3, "bathrooms": 2, "total_area_m2": 250,
        # Las trae `get_portal_listing` desde que el portal colapsa proyectos.
        # El stub tiene que parecerse a lo que el servicio devuelve, o los
        # tests de ruta prueban una tarjeta que no existe.
        "unidades": 1, "precio_desde_display": "USD 120.000",
    }
    card.update(overrides)
    return card


def _patch_listing(return_value):
    """Patch PublicPropertyService.get_portal_listing in the route module."""
    return patch(
        "app.routes.public.PublicPropertyService.get_portal_listing",
        new=AsyncMock(return_value=return_value),
    )


# ---------------------------------------------------------------------------
# Listing + cards
# ---------------------------------------------------------------------------


async def test_portal_card_links_to_prop_canonical(client):
    """[spec] GET /propiedades renders cards linking to the canonical /prop/ URL."""
    listing = _listing(cards=[_card()], total=1, total_pages=1, page=1)
    with _patch_listing(listing):
        response = await client.get("/propiedades")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    assert "Casa en Asunción" in html
    assert 'href="/prop/42-casa-en-asuncion-asuncion"' in html


async def test_portal_no_auth_required(client):
    """Portal listing is public: no cookies → 200, no redirect to /login."""
    with _patch_listing(_listing(cards=[_card()], total=1, total_pages=1)):
        response = await client.get("/propiedades")
    assert response.status_code == 200
    assert response.status_code not in (302, 303)


# ---------------------------------------------------------------------------
# Param validation
# ---------------------------------------------------------------------------


async def test_portal_passes_validated_params_to_service(client):
    """Valid query params are forwarded to the service (Decimals for prices)."""
    listing = _listing(cards=[_card()], total=100, total_pages=5, page=2)
    with _patch_listing(listing) as mock_listing:
        response = await client.get(
            "/propiedades?tipo=departamento&ciudad=Luque&operacion=alquiler"
            "&precio_min=50000&precio_max=100000&page=2"
        )
    assert response.status_code == 200
    mock_listing.assert_awaited_once()
    kwargs = mock_listing.await_args.kwargs
    assert kwargs["page"] == 2
    assert kwargs["tipo"] == "departamento"
    assert kwargs["ciudad"] == "Luque"
    assert kwargs["operacion"] == "alquiler"
    assert kwargs["precio_min"] == Decimal("50000")
    assert kwargs["precio_max"] == Decimal("100000")


async def test_portal_invalid_tipo_ignored(client):
    """tipo outside the whitelist degrades to None (never 4xx)."""
    with _patch_listing(_listing()) as mock_listing:
        response = await client.get("/propiedades?tipo=<script>")
    assert response.status_code == 200
    assert mock_listing.await_args.kwargs["tipo"] is None


async def test_portal_invalid_operacion_ignored(client):
    """operacion outside {venta, alquiler} degrades to None."""
    with _patch_listing(_listing()) as mock_listing:
        response = await client.get("/propiedades?operacion=foo")
    assert response.status_code == 200
    assert mock_listing.await_args.kwargs["operacion"] is None


async def test_portal_invalid_precio_ignored(client):
    """Non-numeric or negative prices degrade to None."""
    with _patch_listing(_listing()) as mock_listing:
        response = await client.get("/propiedades?precio_min=abc&precio_max=-5")
    assert response.status_code == 200
    kwargs = mock_listing.await_args.kwargs
    assert kwargs["precio_min"] is None
    assert kwargs["precio_max"] is None


async def test_portal_nonfinite_precio_ignored(client):
    """NaN / Infinity / absurdly large prices degrade to None — never 500."""
    # NaN constructs a valid Decimal but comparisons raise InvalidOperation;
    # Infinity compares fine but is a nonsense bind param. Both must degrade.
    with _patch_listing(_listing()) as mock_listing:
        response = await client.get("/propiedades?precio_min=NaN&precio_max=Infinity")
    assert response.status_code == 200
    kwargs = mock_listing.await_args.kwargs
    assert kwargs["precio_min"] is None
    assert kwargs["precio_max"] is None

    # Finite but beyond any real price (cap 10^10) → None as well.
    with _patch_listing(_listing()) as mock_listing:
        response = await client.get("/propiedades?precio_min=1E1000000")
    assert response.status_code == 200
    assert mock_listing.await_args.kwargs["precio_min"] is None


# ---------------------------------------------------------------------------
# page validation → branded 404
# ---------------------------------------------------------------------------


async def test_portal_404_for_invalid_page(client):
    """[spec] page=0, page=abc, page > total_pages → branded public 404."""
    # NOTE: both the panel error_404.html and the public 404 contain the
    # string "Onnix SA" — "Panel Admin" only appears in the panel
    # version, so its absence proves the branded PUBLIC 404 was rendered.

    # page=0 → 404 (service never reached)
    with _patch_listing(_listing()):
        resp_zero = await client.get("/propiedades?page=0")
    assert resp_zero.status_code == 404
    assert "Onnix SA" in resp_zero.text
    assert "Panel Admin" not in resp_zero.text

    # page=abc → 404, NOT 422 JSON
    with _patch_listing(_listing()):
        resp_abc = await client.get("/propiedades?page=abc")
    assert resp_abc.status_code == 404
    assert resp_abc.status_code != 422
    assert "Onnix SA" in resp_abc.text
    assert "Panel Admin" not in resp_abc.text

    # page=999 with total_pages=5 → 404
    listing = _listing(cards=[], total=120, total_pages=5, page=999)
    with _patch_listing(listing):
        resp_over = await client.get("/propiedades?page=999")
    assert resp_over.status_code == 404
    assert "Onnix SA" in resp_over.text
    assert "Panel Admin" not in resp_over.text

    # page with 5000 digits → 404, never 500 (int() raises ValueError past
    # Python 3.11's 4300-digit conversion cap; len guard must catch it first).
    with _patch_listing(_listing()):
        resp_huge = await client.get("/propiedades?page=" + "9" * 5000)
    assert resp_huge.status_code == 404
    assert "Onnix SA" in resp_huge.text
    assert "Panel Admin" not in resp_huge.text


async def test_portal_empty_results_page1_renders_message(client):
    """Valid filters with zero results on page 1 → 200 with 'Sin resultados'."""
    with _patch_listing(_listing(total=0, total_pages=1, page=1)):
        response = await client.get("/propiedades?ciudad=Nulle")
    assert response.status_code == 200
    assert "Sin resultados" in response.text


# ---------------------------------------------------------------------------
# Headers + SEO
# ---------------------------------------------------------------------------


async def test_portal_cache_headers(client):
    """Listing response carries Cache-Control: public, max-age=300."""
    with _patch_listing(_listing(cards=[_card()], total=1, total_pages=1)):
        response = await client.get("/propiedades")
    assert response.status_code == 200
    cc = response.headers.get("cache-control", "")
    assert "public" in cc
    assert "max-age=300" in cc


async def test_portal_seo_meta(client):
    """Title, canonical link, og:title and robots index/follow present."""
    with _patch_listing(_listing(cards=[_card()], total=1, total_pages=1)):
        response = await client.get("/propiedades")
    assert response.status_code == 200
    html = response.text
    assert "Propiedades en venta y alquiler en Paraguay" in html
    assert '<link rel="canonical"' in html
    assert 'property="og:title"' in html
    assert '<meta name="robots" content="index, follow"' in html


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


async def test_portal_pagination_links(client):
    """page 2 of 3 → links to page 1 and 3 preserving active filters."""
    listing = _listing(cards=[_card()], total=72, total_pages=3, page=2)
    with _patch_listing(listing):
        response = await client.get("/propiedades?tipo=casa&page=2")
    assert response.status_code == 200
    html = response.text
    # Jinja autoescape renders & as &amp; inside href attributes.
    assert 'href="/propiedades?tipo=casa&amp;page=1"' in html
    assert 'href="/propiedades?tipo=casa&amp;page=3"' in html


# ---------------------------------------------------------------------------
# Photo placeholder
# ---------------------------------------------------------------------------


async def test_portal_photo_placeholder_when_none(client):
    """Card without photo_url renders a placeholder div, never a broken <img>."""
    card = _card(photo_url=None)
    with _patch_listing(_listing(cards=[card], total=1, total_pages=1)):
        response = await client.get("/propiedades")
    assert response.status_code == 200
    html = response.text
    assert "<img" not in html
    assert "photo-placeholder" in html
    # The card link itself is still present
    assert 'href="/prop/42-casa-en-asuncion-asuncion"' in html


# ---------------------------------------------------------------------------
# Filter form (Task 4 — regression guards for the styled template)
# ---------------------------------------------------------------------------


async def test_portal_filter_form_fields_present(client):
    """The GET filter form exposes the 5 public field names."""
    with _patch_listing(_listing(cards=[_card()], total=1, total_pages=1)):
        response = await client.get("/propiedades")
    assert response.status_code == 200
    html = response.text
    for field in ("tipo", "ciudad", "operacion", "precio_min", "precio_max"):
        assert f'name="{field}"' in html


async def test_portal_form_preserves_selected_filters(client):
    """Active filters render back into the form (selected options / values)."""
    listing = _listing(cards=[_card()], total=3, total_pages=1, page=1)
    with _patch_listing(listing):
        response = await client.get(
            "/propiedades?tipo=casa&ciudad=Luque&operacion=alquiler"
            "&precio_min=50000&precio_max=120000"
        )
    assert response.status_code == 200
    html = response.text
    assert 'value="casa" selected' in html
    assert 'value="Luque"' in html
    assert 'value="alquiler" selected' in html
    assert 'value="50000"' in html
    assert 'value="120000"' in html


# ---------------------------------------------------------------------------
# source param is not part of the public contract
# ---------------------------------------------------------------------------


async def test_portal_route_ignores_source_param(client):
    """?source=remax is silently ignored — the route signature does not accept it."""
    with _patch_listing(_listing(cards=[_card()], total=1, total_pages=1)) as mock_listing:
        response = await client.get("/propiedades?source=remax")
    assert response.status_code == 200
    mock_listing.assert_awaited_once()
    assert "source" not in mock_listing.await_args.kwargs
    # Only db is positional; no stray positional source either
    assert len(mock_listing.await_args.args) == 1


# ---------------------------------------------------------------------------
# Sitemap includes portal listing URLs
# ---------------------------------------------------------------------------


async def test_sitemap_includes_portal(client):
    """Sitemap lists /propiedades + variantes de operación BEFORE property entries."""
    with patch(
        "app.routes.public.PublicPropertyService.get_sitemap_entries",
        new=AsyncMock(return_value=[{"loc": "/prop/1-x", "lastmod": None}]),
    ):
        response = await client.get("/sitemap.xml")
    body = response.text
    assert "<loc>https://onnix.com.py/propiedades</loc>" in body
    assert "<loc>https://onnix.com.py/propiedades?operacion=venta</loc>" in body
    assert "<loc>https://onnix.com.py/propiedades?operacion=alquiler</loc>" in body
    # Portal entries van ANTES de las props
    first_prop = body.index("/prop/1-x")
    assert body.index("<loc>https://onnix.com.py/propiedades</loc>") < first_prop
    assert body.index("/propiedades?operacion=venta") < first_prop
    assert body.index("/propiedades?operacion=alquiler") < first_prop


# ---------------------------------------------------------------------------
# Landing estático (vive en landing/ del repo, no en el paquete panel/)
# ---------------------------------------------------------------------------


def test_landing_has_propiedades_button():
    """[spec] El landing estático linkea al portal /propiedades (nav + hero CTA)."""
    if not LANDING_INDEX.exists():
        pytest.skip("landing/ no esta en este entorno (imagen Docker del panel)")
    html = LANDING_INDEX.read_text(encoding="utf-8")
    assert 'href="/propiedades"' in html


class _PortalLinkParser(HTMLParser):
    """Saca del landing los <form> y los <a> que apuntan a /propiedades.

    Identifica por destino (``action`` / ``href``), no por clase de estilo: un
    renombre de CSS no debe romper este test, y un cambio de destino sí.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.form_method: str | None = None
        self.fields: list[dict] = []   # {name, value, type, checked}
        self.link_queries: list[dict] = []
        self._in_form = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and a.get("action") == "/propiedades":
            self._in_form = True
            self.form_method = (a.get("method") or "get").lower()
        elif tag == "a":
            parsed = urllib.parse.urlsplit(a.get("href") or "")
            if parsed.path == "/propiedades" and parsed.query:
                self.link_queries.append(
                    dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
                )
        elif self._in_form and tag in ("input", "select", "textarea") and a.get("name"):
            self.fields.append({
                "name": a["name"],
                "value": a.get("value"),
                "type": (a.get("type") or "text").lower(),
                "checked": "checked" in a,
            })

    def handle_endtag(self, tag):
        if tag == "form":
            self._in_form = False


def _parse_landing():
    if not LANDING_INDEX.exists():
        pytest.skip(
            "falta landing/index.html: este test corre desde el repo, y la imagen "
            "Docker del panel no copia landing/"
        )
    parser = _PortalLinkParser()
    parser.feed(LANDING_INDEX.read_text(encoding="utf-8"))
    return parser


def _portal_query_params() -> set[str]:
    """Los nombres de query param que la ruta realmente acepta, leídos de la firma."""
    from app.routes.public import portal_listing

    return {
        name for name, p in inspect.signature(portal_listing).parameters.items()
        if name not in ("request", "db")
    }


def test_landing_hero_search_form_matches_portal_params():
    """[spec] C3 — el buscador del hero postea GET a /propiedades con los names reales.

    Un rename del parámetro en la ruta, o del ``name`` en el HTML, deja la
    búsqueda apuntando a la nada sin que nada falle en runtime: el portal
    ignora lo que no conoce y devuelve el listado completo.
    """
    from app.routes.public import _PORTAL_OPERACIONES

    landing = _parse_landing()
    assert landing.form_method == "get", "el buscador tiene que ser GET, no POST"
    assert landing.fields, "no hay ningún campo en el form del hero"

    accepted = _portal_query_params()
    for field in landing.fields:
        assert field["name"] in accepted, (
            f"el campo {field['name']!r} no lo acepta GET /propiedades "
            f"(acepta {sorted(accepted)})"
        )

    operaciones = [f for f in landing.fields if f["name"] == "operacion"]
    assert operaciones, "el buscador no ofrece elegir operación"
    for field in operaciones:
        assert field["value"] in _PORTAL_OPERACIONES, (
            f"operacion={field['value']!r} la descarta _parse_portal_params"
        )
    assert sum(1 for f in operaciones if f["checked"]) == 1, (
        "tiene que haber exactamente una operación marcada por defecto"
    )


def test_landing_chips_are_queries_the_portal_understands():
    """[spec] C3 — los chips del hero son búsquedas prefiltradas válidas."""
    from app.routes.public import _PORTAL_CIUDAD_MAX_LEN

    landing = _parse_landing()
    assert landing.link_queries, "no hay chips prefiltrados en el landing"

    accepted = _portal_query_params()
    for query in landing.link_queries:
        for key, value in query.items():
            assert key in accepted, f"el chip usa {key!r}, que la ruta no acepta"
            assert value, f"el chip manda {key} vacío"
        ciudad = query.get("ciudad")
        if ciudad is not None:
            # _parse_portal_params trunca la ciudad: un chip más largo buscaría otra cosa.
            assert len(ciudad) <= _PORTAL_CIUDAD_MAX_LEN


# ---------------------------------------------------------------------------
# La tarjeta de proyecto
# ---------------------------------------------------------------------------


async def test_portal_tarjeta_sin_la_clave_unidades_no_revienta(client):
    """Una tarjeta a la que le falta `unidades` no puede dar 500.

    Con StrictUndefined, `card.unidades > 1` sobre una clave ausente levanta
    jinja2.UndefinedError y la página pública devuelve 500. Pasó: diez tests de
    ruta se pusieron rojos de golpe. Es la página que ve el cliente — degrada,
    no revienta.
    """
    tarjeta = _card()
    del tarjeta["unidades"]
    del tarjeta["precio_desde_display"]

    with _patch_listing(_listing(cards=[tarjeta], total=1, total_pages=1)):
        r = await client.get("/propiedades")

    assert r.status_code == 200
    assert "USD 120.000" in r.text


async def test_portal_tarjeta_de_proyecto_dice_cuantas_unidades(client):
    with _patch_listing(_listing(
        cards=[_card(unidades=85, precio_desde_display="USD 21.600")],
        total=1, total_pages=1,
    )):
        r = await client.get("/propiedades")

    assert r.status_code == 200
    assert "85 unidades disponibles" in r.text
    # El «desde» reemplaza al precio de la unidad elegida, no se suma.
    assert "USD 21.600" in r.text
    assert "USD 120.000" not in r.text
    # Y se dice que es un mínimo. Sin esa palabra el número se lee como EL
    # precio: es lo que se vio en el navegador antes de agregarla.
    #
    # Se assertea el ELEMENTO, no la palabra. `"Desde" in r.text` daba verde
    # siempre: el comentario de CSS que explica el estilo dice «Desde» y viaja
    # adentro del <style> de la página. Es la trampa que el repo ya tiene
    # escrita —«si el test prohíbe un patrón, el comentario que lo explica lo
    # contiene»— y volvió a morder acá.
    assert '<span class="card-desde">Desde</span>' in r.text


async def test_portal_tarjeta_de_una_unidad_no_dice_nada_de_unidades(client):
    with _patch_listing(_listing(
        cards=[_card(unidades=1)], total=1, total_pages=1,
    )):
        r = await client.get("/propiedades")

    assert r.status_code == 200
    assert "unidades disponibles" not in r.text
    assert "USD 120.000" in r.text
    # Una propiedad sola tiene un precio, no un mínimo.
    assert 'class="card-desde"' not in r.text
