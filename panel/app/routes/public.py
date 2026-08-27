"""Public-facing property routes (M6.4 Vista Publica + M6.4b Portal).

Endpoints (no authentication required):
  GET /prop/{prop_ref}   — canonical property detail page
  GET /p/{prop_id}       — short URL → 301 to canonical
  GET /propiedades       — public listing portal (filters + pagination)
  GET /sitemap.xml       — XML sitemap for search engine crawlers
  GET /robots.txt        — crawler directives

These routes delegate all business logic to PublicPropertyService.
No SQL, no auth dependencies.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

from app.database import get_db
from app.repositories.user_repo import user_repo
from app.services.public_property_service import (
    PORTAL_TIPO_OPTIONS,
    _PORTAL_TIPO_LABELS,
    PublicPropertyService,
    wa_link,
)
from app.tz import get_templates

router = APIRouter()
templates = get_templates()

# Base URL used in sitemap <loc> and OG meta tags.
PUBLIC_BASE_URL = "https://onnix.com.py"

# Portal listing URLs included at the top of the sitemap (no lastmod — dynamic pages).
_PORTAL_SITEMAP_PATHS = (
    "/propiedades",
    "/propiedades?operacion=venta",
    "/propiedades?operacion=alquiler",
)

# Regex: optional slug portion after the mandatory numeric id.
_PROP_REF_RE = re.compile(r"^(\d+)(?:-(.*))?$")

# Maximum number of digits for a valid int4 property id (2^31-1 = 2147483647 = 10 digits).
# We reject anything with more than 9 digits to stay well within int4 range.
_MAX_ID_DIGITS = 9

# Roles allowed to act as asesor in the attribution link.
_ASESOR_ROLES = frozenset(("admin", "agent"))

_ROBOTS_BODY = """\
User-agent: *
Allow: /prop/
Allow: /propiedades
Allow: /sitemap.xml
Disallow: /

Sitemap: https://onnix.com.py/sitemap.xml\
"""


async def _resolve_asesor_wa(
    db: AsyncSession,
    a_param: str | None,
) -> tuple[str | None, str | None]:
    """Resolve the ``?a=`` attribution param to an asesor's wa.me phone number.

    Returns ``(wa_phone, asesor_name)`` where ``wa_phone`` is the normalized
    phone (digits only, no '+') ready for use in ``wa.me/{wa_phone}``, or
    ``(None, None)`` when the param is absent / invalid / fails any guard.

    Guards (all must pass — if any fails → ``(None, None)``):
    1. ``a_param`` is a non-empty string of digits with ≤ 9 characters
       (same overflow bound as ``_MAX_ID_DIGITS``).
    2. User exists with that id.
    3. ``is_active`` is True.
    4. ``role`` is in ``_ASESOR_ROLES`` (admin or agent).
    5. ``phone`` is a non-empty string.

    This function NEVER raises — all validation degrades silently to None.
    """
    if not a_param:
        return None, None
    # Guard: only digits, max length to prevent int overflow
    if not a_param.isdigit() or len(a_param) > _MAX_ID_DIGITS:
        return None, None
    user_id = int(a_param)
    try:
        user = await user_repo.get_by_id(db, user_id)
    except Exception:
        return None, None
    if user is None:
        return None, None
    if not user.is_active:
        return None, None
    if user.role not in _ASESOR_ROLES:
        return None, None
    if not user.phone:
        return None, None
    # Normalize phone for wa.me: strip '+' and any non-digit characters
    wa_phone = "".join(c for c in user.phone if c.isdigit())
    if not wa_phone:
        return None, None
    asesor_name = user.display_name or user.name or ""
    return wa_phone, asesor_name


def _public_404(request: Request):
    """Return a branded public 404 response with a short cache TTL.

    Cloudflare caches 404s aggressively by default; max-age=60 keeps the
    window short so a property that becomes available shows up quickly.
    """
    return templates.TemplateResponse(
        "public/404.html",
        {"request": request},
        status_code=404,
        headers={"Cache-Control": "public, max-age=60"},
    )


@router.get("/prop/{prop_ref}")
async def public_property_detail(
    prop_ref: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    a: str | None = None,
):
    """Canonical public property detail page.

    prop_ref must match ``^(digits+)(?:-(.*))$``.  If the numeric id has more than
    9 digits we reject immediately (avoids int4 overflow before hitting the DB).
    When the id/slug pair doesn't match the canonical form we 301 redirect.

    ``a`` (optional): asesor attribution param.  When present and valid (active
    admin/agent with phone), the WhatsApp CTA points to the asesor's number
    instead of the default office number.  Invalid values degrade silently to
    the default — never 422, never 500.
    """
    m = _PROP_REF_RE.match(prop_ref)
    if not m:
        return _public_404(request)

    id_str = m.group(1)
    if len(id_str) > _MAX_ID_DIGITS:
        return _public_404(request)

    prop_id = int(id_str)
    detail = await PublicPropertyService.get_public_detail(db, prop_id)
    if detail is None:
        return _public_404(request)

    canonical_path = detail["canonical_path"]
    # Strip leading "/prop/" from canonical_path to get the expected prop_ref.
    expected_ref = canonical_path[len("/prop/"):]

    if prop_ref != expected_ref:
        response = RedirectResponse(canonical_path, status_code=301)
        response.headers["Cache-Control"] = "public, max-age=300"
        return response

    # Resolve asesor attribution — silently degrades to (None, None) on any failure.
    asesor_wa_phone, asesor_name = await _resolve_asesor_wa(db, a)

    if asesor_wa_phone:
        # Build asesor-specific wa.me URL with enriched message text.
        title = detail.get("title") or ""
        ref_id = detail.get("external_id") or str(detail.get("id", ""))
        asesor_wa_url = wa_link(
            asesor_wa_phone,
            f"Hola! Me interesa esta propiedad: {title} (Ref. {ref_id})",
        )
        # Si la ficha no tiene fotos, la acción que las pide va al mismo asesor
        # que trajo al visitante — mandarlo a la oficina rompería la atribución
        # justo en el caso donde alguien ya se tomó el trabajo de contactarlo.
        asesor_wa_url_fotos = wa_link(
            asesor_wa_phone,
            f"Hola! ¿Me pasás las fotos de esta propiedad: {title} (Ref. {ref_id})?",
        )
        asesor_wa_url_datos = wa_link(
            asesor_wa_phone,
            f"Hola! ¿Me pasás los datos de esta propiedad: {title} (Ref. {ref_id})?",
        )
    else:
        asesor_wa_url = None
        asesor_wa_url_fotos = None
        asesor_wa_url_datos = None

    return templates.TemplateResponse(
        "public/property.html",
        {
            "request": request,
            "prop": detail,
            "base_url": PUBLIC_BASE_URL,
            # feat/asesor-link: when set, overrides prop.wa_url in template.
            "asesor_wa_url": asesor_wa_url,
            "asesor_wa_url_fotos": asesor_wa_url_fotos,
            "asesor_wa_url_datos": asesor_wa_url_datos,
        },
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/p/{prop_id}")
async def public_short_url(
    prop_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Short URL redirect: /p/{id} → 301 to canonical /prop/{id}-{slug}.

    prop_id is declared as str (not int) so FastAPI does not emit a 422 JSON
    validation error for non-numeric inputs — the spec requires a public 404.
    """
    if not prop_id.isdigit() or len(prop_id) > _MAX_ID_DIGITS:
        return _public_404(request)

    detail = await PublicPropertyService.get_public_detail(db, int(prop_id))
    if detail is None:
        return _public_404(request)

    response = RedirectResponse(detail["canonical_path"], status_code=301)
    response.headers["Cache-Control"] = "public, max-age=300"
    return response


_PORTAL_OPERACIONES = ("venta", "alquiler")
_PORTAL_CIUDAD_MAX_LEN = 80

# Upper bound for public price filters (10^10). Real max price in data is
# ~1.4e9, so this rejects absurd values (1E1000000) without losing anything real.
_PORTAL_PRECIO_CAP = Decimal("10000000000")


def _parse_portal_params(
    tipo: str | None,
    ciudad: str | None,
    operacion: str | None,
    precio_min: str | None,
    precio_max: str | None,
) -> dict:
    """Sanitize public query params. Invalid values degrade to None (never 4xx)."""
    out: dict = {"tipo": None, "ciudad": None, "operacion": None,
                 "precio_min": None, "precio_max": None}
    if tipo in _PORTAL_TIPO_LABELS:
        out["tipo"] = tipo
    if ciudad:
        cleaned = ciudad.strip()[:_PORTAL_CIUDAD_MAX_LEN]
        out["ciudad"] = cleaned or None
    if operacion in _PORTAL_OPERACIONES:
        out["operacion"] = operacion
    for key, raw in (("precio_min", precio_min), ("precio_max", precio_max)):
        if raw:
            try:
                value = Decimal(raw)
                # is_finite() guards NaN (comparisons raise InvalidOperation)
                # and Infinity; the cap kills finite-but-absurd values.
                if value.is_finite() and 0 <= value <= _PORTAL_PRECIO_CAP:
                    out[key] = value
            except (InvalidOperation, ValueError):
                continue
    return out


@router.get("/propiedades")
async def portal_listing(
    request: Request,
    db: AsyncSession = Depends(get_db),
    tipo: str | None = None,
    ciudad: str | None = None,
    operacion: str | None = None,
    precio_min: str | None = None,
    precio_max: str | None = None,
    page: str = "1",
):
    """Public property listing portal (M6.4b). Server-side, onnixpy only.

    ``page`` is declared as str (not int) so FastAPI does not emit a 422 JSON
    validation error for non-numeric inputs — the spec requires a branded
    public 404 (same pattern as /p/{prop_id}). 404s ALWAYS return
    ``_public_404(request)`` directly, never ``raise HTTPException`` (the
    global handler renders the unbranded panel 404).
    """
    # len(page) > 9: same _MAX_ID_DIGITS criterion as /p and /prop — also keeps
    # int(page) from raising ValueError on huge inputs (Python 3.11+ caps
    # str→int conversion at 4300 digits).
    if not page.isdigit() or len(page) > _MAX_ID_DIGITS or int(page) < 1:
        return _public_404(request)
    page_num = int(page)

    params = _parse_portal_params(tipo, ciudad, operacion, precio_min, precio_max)
    listing = await PublicPropertyService.get_portal_listing(db, page=page_num, **params)

    if page_num > 1 and page_num > listing["total_pages"]:
        return _public_404(request)

    return templates.TemplateResponse(
        "public/propiedades.html",
        {
            "request": request,
            "listing": listing,
            "params": params,
            "base_url": PUBLIC_BASE_URL,
            "tipo_options": PORTAL_TIPO_OPTIONS,
        },
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/sitemap.xml")
async def sitemap(db: AsyncSession = Depends(get_db)):
    """Generate an XML sitemap for all public-eligible properties."""
    entries = await PublicPropertyService.get_sitemap_entries(db)

    url_elements: list[str] = []
    for path in _PORTAL_SITEMAP_PATHS:
        url_elements.append(f"  <url>\n    <loc>{PUBLIC_BASE_URL}{path}</loc>\n  </url>")
    for entry in entries:
        loc = f"{PUBLIC_BASE_URL}{entry['loc']}"
        lastmod_tag = ""
        if entry.get("lastmod") is not None:
            lastmod_tag = f"\n    <lastmod>{entry['lastmod']}</lastmod>"
        url_elements.append(f"  <url>\n    <loc>{loc}</loc>{lastmod_tag}\n  </url>")

    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(url_elements)
        + "\n</urlset>"
    )

    return Response(
        content=body,
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/robots.txt")
async def robots_txt():
    """Serve robots.txt with crawler directives for the public property pages."""
    return PlainTextResponse(
        _ROBOTS_BODY,
        headers={"Cache-Control": "public, max-age=86400"},
    )
