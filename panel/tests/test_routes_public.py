"""TDD — Public routes (M6.4 Vista Publica)

Covers:
- GET /prop/{prop_ref}: canonical match → 200, slug mismatch → 301, malformed → 404,
  overflow id → 404 (no 500), service None → 404.
- GET /p/{prop_id}: short URL → 301 canonical, non-numeric → 404 (not 422).
- GET /sitemap.xml: absolute locs, lastmod optional, correct XML structure.
- GET /robots.txt: exact expected content.
- Cache-Control headers for all route types.
- Public routes require no authentication (no redirect to /login).
- CSP frame-src directive present alongside connect-src.

All PublicPropertyService calls are mocked via patch on the route module path
(app.routes.public.PublicPropertyService) so these tests never touch the DB.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PUBLIC_BASE_URL = "https://onnix.com.py"


def _make_detail(
    *,
    prop_id: int = 123,
    title: str = "Casa en Luque",
    city: str = "Luque",
    source: str = "remax",
    description: str = "Hermosa casa",
    bedrooms: int = 3,
    bathrooms: int = 2,
    parking: int = 1,
    total_area_m2: float = 150.0,
    construction_state: str = "usado",
    operation: str = "venta",
    property_type: str = "casa",
    latitude: float | None = -25.3,
    longitude: float | None = -57.5,
    updated_at=None,
    photo_urls: list[str] | None = None,
    price_display: str = "USD 120.000",
    wa_url: str = "https://wa.me/595900000000?text=Hola",
    wa_message: str = "Hola!",
    slug: str = "casa-en-luque-luque",
    public_code: str = "00123",
    canonical_path: str = "/prop/123-casa-en-luque-luque",
) -> dict:
    """Build a minimal detail dict as returned by PublicPropertyService.get_public_detail."""
    if updated_at is None:
        updated_at = datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc)
    if photo_urls is None:
        photo_urls = ["/images/remax/EXT001/1.webp", "/images/remax/EXT001/2.webp"]
    return {
        "id": prop_id,
        "title": title,
        "city": city,
        "source": source,
        "description": description,
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "parking": parking,
        "total_area_m2": total_area_m2,
        "construction_state": construction_state,
        "operation": operation,
        "property_type": property_type,
        "latitude": latitude,
        "longitude": longitude,
        "updated_at": updated_at,
        "photo_urls": photo_urls,
        "price_display": price_display,
        "wa_url": wa_url,
        # La ficha sin foto convierte la ausencia en una acción y usa este link;
        # el fixture lo trae porque el service lo devuelve siempre.
        "wa_url_fotos": wa_url.replace("Hola", "Hola-fotos"),
        "wa_url_datos": wa_url.replace("Hola", "Hola-datos"),
        "wa_message": wa_message,
        "slug": slug,
        "public_code": public_code,
        "canonical_path": canonical_path,
    }


def _patch_detail(return_value):
    """Patch PublicPropertyService.get_public_detail in the route module."""
    return patch(
        "app.routes.public.PublicPropertyService.get_public_detail",
        new=AsyncMock(return_value=return_value),
    )


def _patch_sitemap(return_value):
    """Patch PublicPropertyService.get_sitemap_entries in the route module."""
    return patch(
        "app.routes.public.PublicPropertyService.get_sitemap_entries",
        new=AsyncMock(return_value=return_value),
    )


# ---------------------------------------------------------------------------
# /prop/{prop_ref} — canonical detail page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_property_visible_for_remax(client):
    """A remax property with canonical URL returns 200 with title in body."""
    detail = _make_detail(source="remax")
    with _patch_detail(detail):
        response = await client.get("/prop/123-casa-en-luque-luque")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Casa en Luque" in response.text


@pytest.mark.asyncio
async def test_public_property_visible_for_onnixpy(client):
    """A onnixpy property with canonical URL returns 200 with title in body."""
    detail = _make_detail(
        source="onnixpy",
        title="Apartamento Onnix",
        city="Asuncion",
        slug="apartamento-onnix-asuncion",
        canonical_path="/prop/123-apartamento-onnix-asuncion",
    )
    with _patch_detail(detail):
        response = await client.get("/prop/123-apartamento-onnix-asuncion")
    assert response.status_code == 200
    assert "Apartamento Onnix" in response.text


@pytest.mark.asyncio
async def test_public_property_404_for_source_infocasas(client):
    """Service returns None for infocasas source → route returns 404.

    The eligibility filter (infocasas excluded) lives in PublicPropertyService
    and is tested in test_public_property_service.py. Here we verify the route
    correctly maps a None response to a public 404.
    """
    with _patch_detail(None):
        response = await client.get("/prop/99-some-infocasas-property")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_public_property_404_for_inactive(client):
    """Service returns None for inactive property → route returns 404.

    The is_active=False exclusion lives in PublicPropertyService and is tested
    in test_public_property_service.py.
    """
    with _patch_detail(None):
        response = await client.get("/prop/55-propiedad-inactiva")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_public_property_404_for_on_hold(client):
    """Service returns None for on_hold property → route returns 404.

    The on_hold exclusion lives in PublicPropertyService and is tested in
    test_public_property_service.py.
    """
    with _patch_detail(None):
        response = await client.get("/prop/77-propiedad-pausada")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_public_property_404_for_nonexistent(client):
    """Service returns None for a nonexistent id → route returns 404."""
    with _patch_detail(None):
        response = await client.get("/prop/99999999-nada")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_public_property_404_for_malformed_ref(client):
    """/prop/abc-def has no numeric prefix → 404 without calling service."""
    with _patch_detail(None):
        response = await client.get("/prop/abc-def")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_public_property_404_for_overflow_id(client):
    """/prop/99999999999999999999-x has too many digits → 404, not 500.

    Without the len>9 guard, int() would silently overflow int4 and produce
    an unhandled 500 from the DB layer.
    """
    response = await client.get("/prop/99999999999999999999-x")
    assert response.status_code == 404
    # Must NOT be 500
    assert response.status_code != 500


@pytest.mark.asyncio
async def test_canonical_redirect_when_slug_mismatch(client):
    """When prop_ref has correct id but wrong slug → 301 to canonical path."""
    detail = _make_detail(prop_id=123, canonical_path="/prop/123-casa-en-luque-luque")
    with _patch_detail(detail):
        response = await client.get("/prop/123-wrong-slug")
    assert response.status_code == 301
    assert response.headers["location"] == "/prop/123-casa-en-luque-luque"


# ---------------------------------------------------------------------------
# /p/{prop_id} — short URL redirect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p_short_404_for_non_numeric(client):
    """/p/abc must return 404, NOT 422 (which would be a JSON validation error)."""
    response = await client.get("/p/abc")
    assert response.status_code == 404
    # Must not be 422 (FastAPI validation error)
    assert response.status_code != 422


@pytest.mark.asyncio
async def test_canonical_url_redirect_from_short(client):
    """/p/123 → 301 to canonical_path from service."""
    detail = _make_detail(prop_id=123, canonical_path="/prop/123-casa-en-luque-luque")
    with _patch_detail(detail):
        response = await client.get("/p/123")
    assert response.status_code == 301
    assert response.headers["location"] == "/prop/123-casa-en-luque-luque"


@pytest.mark.asyncio
async def test_p_short_404_when_service_returns_none(client):
    """/p/123 → 404 when service returns None (property not found or ineligible)."""
    with _patch_detail(None):
        response = await client.get("/p/123")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_p_short_404_for_overflow_id(client):
    """/p/99999999999 has too many digits → 404, not 500."""
    response = await client.get("/p/99999999999")
    assert response.status_code == 404
    assert response.status_code != 500


# ---------------------------------------------------------------------------
# /sitemap.xml
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sitemap_includes_only_eligible(client):
    """Sitemap contains absolute locs for eligible entries."""
    entries = [
        {"loc": "/prop/1-casa-en-luque-luque", "lastmod": "2025-06-01"},
        {"loc": "/prop/2-apartamento-asuncion-asuncion", "lastmod": None},
    ]
    with _patch_sitemap(entries):
        response = await client.get("/sitemap.xml")
    assert response.status_code == 200
    assert "application/xml" in response.headers["content-type"]
    assert "https://onnix.com.py/prop/1-casa-en-luque-luque" in response.text
    assert "https://onnix.com.py/prop/2-apartamento-asuncion-asuncion" in response.text
    # lastmod present for first entry
    assert "2025-06-01" in response.text
    # urlset namespace present
    assert "sitemaps.org/schemas/sitemap/0.9" in response.text


@pytest.mark.asyncio
async def test_sitemap_excludes_ic(client):
    """When service returns empty list (all IC filtered out), sitemap has no /prop/ entries.

    Portal listing URLs (/propiedades + variantes) are always present (M6.4b Task 5),
    so we assert the absence of property entries, not of <loc> tags altogether.
    """
    with _patch_sitemap([]):
        response = await client.get("/sitemap.xml")
    assert response.status_code == 200
    assert "/prop/" not in response.text


@pytest.mark.asyncio
async def test_sitemap_lastmod_absent_when_none(client):
    """When lastmod is None for an entry, <lastmod> tag should NOT appear for that entry."""
    entries = [{"loc": "/prop/5-sin-fecha-asuncion", "lastmod": None}]
    with _patch_sitemap(entries):
        response = await client.get("/sitemap.xml")
    assert response.status_code == 200
    assert "<lastmod>" not in response.text


# ---------------------------------------------------------------------------
# /robots.txt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_robots_allows_public_disallows_panel(client):
    """robots.txt must allow /prop/ and /sitemap.xml, disallow everything else."""
    response = await client.get("/robots.txt")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    body = response.text
    assert "Allow: /prop/" in body
    assert "Allow: /propiedades" in body
    assert "Allow: /sitemap.xml" in body
    assert "Disallow: /" in body
    assert "Sitemap: https://onnix.com.py/sitemap.xml" in body


# ---------------------------------------------------------------------------
# Cache-Control headers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_headers_present(client):
    """Verify Cache-Control on all public route types."""
    detail = _make_detail(prop_id=123, canonical_path="/prop/123-casa-en-luque-luque")

    # 200 prop detail → max-age=300
    with _patch_detail(detail):
        resp_200 = await client.get("/prop/123-casa-en-luque-luque")
    assert resp_200.status_code == 200
    cc_200 = resp_200.headers.get("cache-control", "")
    assert "max-age=300" in cc_200

    # 301 from /p/123 → max-age=300
    with _patch_detail(detail):
        resp_301 = await client.get("/p/123")
    assert resp_301.status_code == 301
    cc_301 = resp_301.headers.get("cache-control", "")
    assert "max-age=300" in cc_301

    # sitemap → max-age=3600
    with _patch_sitemap([]):
        resp_sitemap = await client.get("/sitemap.xml")
    assert resp_sitemap.status_code == 200
    cc_sitemap = resp_sitemap.headers.get("cache-control", "")
    assert "max-age=3600" in cc_sitemap

    # robots → max-age=86400
    resp_robots = await client.get("/robots.txt")
    assert resp_robots.status_code == 200
    cc_robots = resp_robots.headers.get("cache-control", "")
    assert "max-age=86400" in cc_robots

    # 404 → max-age=60 (short so Cloudflare doesn't cache long)
    with _patch_detail(None):
        resp_404 = await client.get("/prop/99-no-existe")
    assert resp_404.status_code == 404
    cc_404 = resp_404.headers.get("cache-control", "")
    assert "max-age=60" in cc_404


# ---------------------------------------------------------------------------
# No auth required
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_routes_require_no_auth(client):
    """Public routes return non-redirect responses without any auth cookie."""
    detail = _make_detail()
    with _patch_detail(detail):
        resp_prop = await client.get("/prop/123-casa-en-luque-luque")
    # Must be 200, NOT 302/303 to /login
    assert resp_prop.status_code == 200
    assert resp_prop.status_code not in (302, 303)

    resp_robots = await client.get("/robots.txt")
    assert resp_robots.status_code == 200

    with _patch_sitemap([]):
        resp_sitemap = await client.get("/sitemap.xml")
    assert resp_sitemap.status_code == 200


# ---------------------------------------------------------------------------
# CSP frame-src
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_csp_has_frame_src(client):
    """CSP header must contain frame-src allowing Google Maps iframe.

    After the M6.4 CSP update, frame-src 'self' https://www.google.com must be
    appended, and connect-src 'self' must still be present (no accidental removal).
    """
    detail = _make_detail()
    with _patch_detail(detail):
        response = await client.get("/prop/123-casa-en-luque-luque")
    csp = response.headers.get("content-security-policy", "")
    assert csp, "CSP header missing"
    assert "frame-src 'self' https://www.google.com" in csp
    assert "connect-src 'self'" in csp
    # Verify they are in separate directives (separated by ";")
    directives = [d.strip() for d in csp.split(";")]
    frame_src_dir = next((d for d in directives if d.startswith("frame-src")), None)
    connect_src_dir = next((d for d in directives if d.startswith("connect-src")), None)
    assert frame_src_dir is not None, f"frame-src directive missing from CSP: {csp!r}"
    assert connect_src_dir is not None, f"connect-src directive missing from CSP: {csp!r}"
    assert "https://www.google.com" in frame_src_dir
    assert "'self'" in connect_src_dir


# ---------------------------------------------------------------------------
# M6.4 C1 — New tests for luxury template (append-only, do NOT modify above)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seo_og_tags_present(client):
    """og:title, og:image (absolute URL starting with base), and canonical link all present."""
    detail = _make_detail(
        prop_id=123,
        title="Casa en Luque",
        canonical_path="/prop/123-casa-en-luque-luque",
        photo_urls=["/images/remax/EXT001/1.webp"],
    )
    with _patch_detail(detail):
        response = await client.get("/prop/123-casa-en-luque-luque")
    assert response.status_code == 200
    html = response.text
    # og:title
    assert 'property="og:title"' in html
    assert "Casa en Luque" in html
    # og:image absolute URL
    assert 'property="og:image"' in html
    assert "https://onnix.com.py/images/" in html
    # canonical
    assert 'rel="canonical"' in html
    assert "https://onnix.com.py/prop/123-casa-en-luque-luque" in html


@pytest.mark.asyncio
async def test_og_image_absent_when_no_photos(client):
    """og:image and twitter:image must be absent when photo_urls is empty."""
    detail = _make_detail(
        prop_id=200,
        title="Sin fotos",
        canonical_path="/prop/200-sin-fotos-luque",
        photo_urls=[],
    )
    with _patch_detail(detail):
        response = await client.get("/prop/200-sin-fotos-luque")
    assert response.status_code == 200
    html = response.text
    assert 'property="og:image"' not in html
    assert 'name="twitter:image"' not in html


@pytest.mark.asyncio
async def test_no_link_to_original_portal_in_public_view(client):
    """The rendered page must not expose any reference to the original portal URL or brand."""
    detail = _make_detail(
        prop_id=77,
        title="Casa remax test",
        source="remax",
        canonical_path="/prop/77-casa-remax-test-luque",
        # Ensure no url_original field leaks — service dict does NOT include it
        photo_urls=[],
    )
    with _patch_detail(detail):
        response = await client.get("/prop/77-casa-remax-test-luque")
    assert response.status_code == 200
    html = response.text.lower()
    # No visible portal brand links
    assert "remax.com" not in html
    assert "infocasas.com" not in html
    assert "coldwell" not in html
    assert "http://portal-original" not in html


@pytest.mark.asyncio
async def test_wa_cta_prefill_correct(client):
    """WhatsApp CTA href must contain wa.me/595900000000 and the property code."""
    code = "00123"
    wa_url = f"https://wa.me/595900000000?text=Hola%2C%20vi%20la%20propiedad%20{code}%20que%20vi"
    detail = _make_detail(
        prop_id=123,
        canonical_path="/prop/123-casa-en-luque-luque",
        public_code=code,
        wa_url=wa_url,
    )
    with _patch_detail(detail):
        response = await client.get("/prop/123-casa-en-luque-luque")
    assert response.status_code == 200
    html = response.text
    assert "wa.me/595900000000" in html
    assert code in html


@pytest.mark.asyncio
async def test_map_present_with_coords(client):
    """Google Maps iframe is rendered when both latitude and longitude are present."""
    detail = _make_detail(
        prop_id=123,
        canonical_path="/prop/123-casa-en-luque-luque",
        latitude=-25.3,
        longitude=-57.5,
    )
    with _patch_detail(detail):
        response = await client.get("/prop/123-casa-en-luque-luque")
    assert response.status_code == 200
    html = response.text
    assert "google.com/maps" in html
    assert "<iframe" in html


@pytest.mark.asyncio
async def test_map_absent_when_no_coords(client):
    """Google Maps iframe must be absent when latitude or longitude is None."""
    detail = _make_detail(
        prop_id=300,
        canonical_path="/prop/300-sin-coords-luque",
        latitude=None,
        longitude=None,
    )
    with _patch_detail(detail):
        response = await client.get("/prop/300-sin-coords-luque")
    assert response.status_code == 200
    html = response.text
    assert "google.com/maps" not in html


@pytest.mark.asyncio
async def test_construction_state_label_spanish(client):
    """construction_state='en_construccion' must render as 'En construcción' (with tilde)."""
    detail = _make_detail(
        prop_id=400,
        canonical_path="/prop/400-obra-nueva-asuncion",
        construction_state="en_construccion",
        city="Asuncion",
        slug="obra-nueva-asuncion",
    )
    with _patch_detail(detail):
        response = await client.get("/prop/400-obra-nueva-asuncion")
    assert response.status_code == 200
    assert "En construcción" in response.text


@pytest.mark.asyncio
async def test_404_page_branded(client):
    """404 page must contain 'Onnix SA' branding."""
    with _patch_detail(None):
        response = await client.get("/prop/99-no-existe")
    assert response.status_code == 404
    assert "Onnix SA" in response.text


@pytest.mark.asyncio
async def test_public_description_cleans_html_artifacts(client):
    """Literal <br /> and \\r from portal ingestion must not be visible in public view.

    96% of active remax props store literal '<br />' in description; without
    the clean_description filter Jinja escapes them to '&lt;br /&gt;' and the
    user sees them as text.
    """
    detail = _make_detail(
        prop_id=500,
        canonical_path="/prop/500-casa-br-luque",
        slug="casa-br-luque",
        description="Hermosa casa<br />con piscina\r",
    )
    with _patch_detail(detail):
        response = await client.get("/prop/500-casa-br-luque")
    assert response.status_code == 200
    html = response.text
    assert "&lt;br" not in html
    assert "<br />" not in html
    assert "\r" not in html
    assert "Hermosa casa" in html
    assert "con piscina" in html
