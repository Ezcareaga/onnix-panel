import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Request, Depends, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_admin
from app.models.user import User
from app.repositories.bot_setting_repo import BotSettingRepository
from app.routes.public import PUBLIC_BASE_URL
from app.services import panel_hybrid_search
from app.services.property_service import property_service, PropertyFilters
from app.utils.money import miles, MONEDA_PYG, MONEDA_USD
from app.utils.pagination import calculate_total_pages
from app.tz import get_templates
from app.bot.ai import property_chatbot

logger = logging.getLogger(__name__)

router = APIRouter()
templates = get_templates()

_SOURCE_OPTIONS = ["infocasas", "onnixpy"]
_STATE_OPTIONS = [
    ("active", "Activas"),
    ("on_hold", "Pausadas"),
    ("inactive", "Inactivas"),
    ("all", "Todas"),
]
_OPERATION_OPTIONS = [
    ("venta", "Venta"),
    ("alquiler", "Alquiler"),
]
_PROPERTY_TYPE_OPTIONS = [
    "departamento", "casa", "local_comercial", "oficina",
    "terreno", "galpon", "campo",
]
_CURRENCY_OPTIONS = ["USD", "PYG"]
_CONSTRUCTION_STATE_OPTIONS = [
    ("en_construccion", "En construcción"),
    ("entrega_inmediata", "Entrega inmediata"),
    ("pozo", "En pozo"),
    ("usado", "Usado"),
]
_UPDATED_WITHIN_OPTIONS = [
    (1, "Hoy"),
    (7, "Últimos 7 días"),
    (30, "Último mes"),
    (90, "Últimos 3 meses"),
]


_CONSTRUCTION_STATE_LABELS = dict(_CONSTRUCTION_STATE_OPTIONS)
_OPERATION_LABELS = dict(_OPERATION_OPTIONS)
_STATE_LABELS = dict(_STATE_OPTIONS)


def _format_price(raw: str, currency: str | None = None) -> str:
    """Chip de filtro. Mismo formato y mismos símbolos que la celda Precio.

    El rango se compara contra `price_pyg` cuando la moneda es PYG, así que el
    chip tiene que decir guaraníes: rotular `USD 350.000.000` un rango escrito
    en guaraníes es la misma confusión que el filtro tenía en SQL.
    """
    simbolo = MONEDA_PYG if (currency or "").upper() == "PYG" else MONEDA_USD
    return f"{simbolo} {miles(raw)}"


def _build_active_chips(qs: dict[str, str]) -> list[dict[str, str]]:
    """Return one chip dict per active filter, rendered under the top bar.

    The URL is the source of truth: chips reflect what is currently in
    `qs` (the same dict used to repopulate the form). A chip with `param`
    can be removed client-side by stripping that param from the URL.

    state == "active" is the default and intentionally produces no chip,
    so the chip row is not noisy when nothing was filtered.
    """
    # La moneda no es un filtro por sí sola —sin rango no saca ninguna fila—
    # así que deja de tener chip propio: viaja adentro del chip de precio.
    moneda = (qs.get("currency") or "").strip()

    specs: list[tuple[str, str, callable[[str], str]]] = [
        ("search_text", "Texto", lambda v: v),
        ("ia_query", "IA", lambda v: v),
        ("operation", "Operación", lambda v: _OPERATION_LABELS.get(v, v.title())),
        ("property_type", "Tipo", lambda v: v.replace("_", " ").title()),
        ("city", "Ciudad", lambda v: v.title()),
        ("neighborhood", "Barrio", lambda v: v.title()),
        ("price_min", "Precio mín", lambda v: _format_price(v, moneda)),
        ("price_max", "Precio máx", lambda v: _format_price(v, moneda)),
        ("bedrooms_min", "Dormitorios", lambda v: f"{v}+"),
        ("bathrooms_min", "Baños", lambda v: f"{v}+"),
        ("construction_state", "Estado obra", lambda v: _CONSTRUCTION_STATE_LABELS.get(v, v.replace("_", " ").title())),
        ("source", "Fuente", lambda v: v),
        ("updated_within_days", "Actualizada", lambda v: f"≤ {v} d"),
        # M6.5: filtros que solo llegan via buscador IA (parse-query)
        ("amenities", "Amenities", lambda v: ", ".join(
            p.strip().replace("_", " ").title() for p in v.split(",") if p.strip()
        )),
        ("barato", "Precio", lambda v: "Barato (P25)"),
    ]
    chips: list[dict[str, str]] = []
    for param, label, formatter in specs:
        value = (qs.get(param) or "").strip()
        if value:
            chips.append({"param": param, "label": label, "value": formatter(value)})

    state = (qs.get("state") or "").strip()
    if state and state != "active":
        chips.append({
            "param": "state",
            "label": "Estado",
            "value": _STATE_LABELS.get(state, state.title()),
        })
    return chips


def _build_public_url_for_user(
    base_url: str,
    public_path: str | None,
    user: "User",
) -> str | None:
    """Return the full public URL for a property, appending ``?a={user.id}``
    only when the current panel user has a non-empty phone number.

    Used by both the listing and the detail views so the copied link carries
    asesor attribution.  When ``public_path`` is None (property not public-
    eligible) the function also returns None.
    """
    if public_path is None:
        return None
    full = f"{base_url}{public_path}"
    if user.phone:
        full = f"{full}?a={user.id}"
    return full


def _parse_decimal(value: str | None) -> Decimal | None:
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _parse_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _parse_amenities(raw: list[str]) -> list[str] | None:
    """Flatten amenities query values into a plain list.

    Accepts BOTH formats:
      - repeatable param: ?amenities=piscina&amenities=garage
      - single CSV param: ?amenities=piscina,garage — the frontend submit()
        in index.html does params.set(k, v), so a JS array serializes as
        "piscina,garage" in ONE param.
    Whitelist validation happens downstream in the repository
    (normalize_amenity); here we only parse the transport format.
    """
    values: list[str] = []
    for item in raw:
        parts = item.split(",") if "," in item else [item]
        values.extend(p.strip() for p in parts if p.strip())
    return values or None


@router.get("/properties", response_class=HTMLResponse)
async def properties_page(
    request: Request,
    page: int = Query(1, ge=1),
    search_text: str = Query(None),
    operation: str = Query(None),
    property_type: str = Query(None),
    city: str = Query(None),
    neighborhood: str = Query(None),
    price_min: str = Query(None),
    price_max: str = Query(None),
    currency: str = Query(None),
    bedrooms_min: str = Query(None),
    bathrooms_min: str = Query(None),
    state: str = Query("active"),
    source: str = Query(None),
    construction_state: str = Query(None),
    updated_within_days: str = Query(None),
    amenities: list[str] = Query(default_factory=list),
    barato: bool = Query(False),
    ia_query: str | None = Query(None, max_length=300),
    # Marca que pone el buscador IA al caer a `search_text` porque no pudo
    # interpretar la consulta (key sin renovar, chatbot apagado, timeout).
    # Sin ella la pantalla devuelve una lista rara y no dice por que.
    ia_unavailable: bool = Query(False),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    per_page = 50
    amenities_list = _parse_amenities(amenities)
    filters = PropertyFilters(
        search_text=search_text or None,
        operation=operation or None,
        property_type=property_type or None,
        city=city or None,
        neighborhood=neighborhood or None,
        price_min=_parse_decimal(price_min),
        price_max=_parse_decimal(price_max),
        currency=currency or None,
        bedrooms_min=_parse_int(bedrooms_min),
        bathrooms_min=_parse_int(bathrooms_min),
        state=state or "active",
        source=source or None,
        construction_state=construction_state or None,
        updated_within_days=_parse_int(updated_within_days),
        amenities=amenities_list,
        barato=barato,
    )

    chatbot_enabled = await _is_chatbot_enabled(db)
    ia_query_clean = (ia_query or "").strip()
    ia_active = bool(ia_query_clean)
    if ia_active:
        # Modo IA (M6.5): SQL + pgvector fusionados con RRF, paginación en
        # memoria sobre el ranking fusionado. Degrada a SQL puro sin Gemini.
        rows, total = await panel_hybrid_search.search(
            db, filters, ia_query_clean, page, per_page
        )
    else:
        rows, total = await property_service.get_properties(db, filters, page, per_page)
    total_pages = calculate_total_pages(total, per_page)
    state_counts = await property_service.get_state_counts(db)
    empty_hints = (
        await property_service.get_empty_hints(db, filters)
        if total == 0 and page == 1
        else None
    )

    logger.info(
        "Properties listed: user=%s filters=%s page=%d total=%d",
        user.email,
        {k: v for k, v in vars(filters).items() if v is not None},
        page,
        total,
    )

    any_filter_active = any([
        search_text, operation, property_type, city, neighborhood,
        price_min, price_max, currency, bedrooms_min, bathrooms_min,
        state not in (None, "active", ""),
        source, construction_state, updated_within_days,
        amenities_list, barato, ia_active,
    ])

    qs = {
        "search_text": search_text or "",
        "operation": operation or "",
        "property_type": property_type or "",
        "city": city or "",
        "neighborhood": neighborhood or "",
        "price_min": price_min or "",
        "price_max": price_max or "",
        "currency": currency or "",
        "bedrooms_min": bedrooms_min or "",
        "bathrooms_min": bathrooms_min or "",
        "state": state or "active",
        "source": source or "",
        "construction_state": construction_state or "",
        "updated_within_days": updated_within_days or "",
        "ia_query": ia_query_clean,
        # M6.5: presentes en qs para chips + hidden inputs del form top-bar
        # (asi un cambio de filtro HTMX no descarta el contexto IA).
        "amenities": ",".join(amenities_list) if amenities_list else "",
        "barato": "true" if barato else "",
    }
    active_chips = _build_active_chips(qs)

    context = {
        "request": request,
        "user": user,
        "now": datetime.now(timezone.utc),
        "rows": rows,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "filters": filters,
        # Raw query-string values for re-populating form inputs
        "qs": qs,
        "active_chips": active_chips,
        "source_options": _SOURCE_OPTIONS,
        "state_options": _STATE_OPTIONS,
        "operation_options": _OPERATION_OPTIONS,
        "property_type_options": _PROPERTY_TYPE_OPTIONS,
        "currency_options": _CURRENCY_OPTIONS,
        "construction_state_options": _CONSTRUCTION_STATE_OPTIONS,
        "updated_within_options": _UPDATED_WITHIN_OPTIONS,
        "any_filter_active": any_filter_active,
        "ia_active": ia_active,
        "ia_unavailable": ia_unavailable,
        "chatbot_enabled": chatbot_enabled,
        "state_counts": state_counts,
        "empty_hints": empty_hints,
        # M6.5: base absoluta para "Copiar link público" — misma fuente que
        # usa la vista pública M6.4 (constante del módulo de routes públicas).
        "public_base_url": PUBLIC_BASE_URL,
        # feat/asesor-link: suffix appended to public URLs copied from panel.
        # Empty string when user has no phone (link attribution opt-out).
        "asesor_a_suffix": f"?a={user.id}" if user.phone else "",
    }

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "properties/partials/properties_table.html", context
        )

    return templates.TemplateResponse("properties/index.html", context)


@router.get("/properties/{property_id}", response_class=HTMLResponse)
async def property_detail(
    request: Request,
    property_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    prop = await property_service.get_property_detail(db, property_id)
    if prop is None:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")

    logger.info(
        "Property detail viewed: user=%s property_id=%d source=%s",
        user.email,
        property_id,
        prop.get("source"),
    )

    return templates.TemplateResponse(
        "properties/detail.html",
        {
            "request": request,
            "user": user,
            "prop": prop,
            # M6.5: el detalle también tendrá botón "Copiar link público".
            "public_base_url": PUBLIC_BASE_URL,
            # feat/asesor-link: suffix appended to public URLs copied from panel.
            "asesor_a_suffix": f"?a={user.id}" if user.phone else "",
        },
    )


@router.post("/properties/{property_id}/toggle-active")
async def toggle_property_active(
    request: Request,
    property_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Flip is_active for a property. Admin-only. Redirects back to detail."""
    prop = await property_service.get_property_detail(db, property_id)
    if prop is None:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")

    new_value = not bool(prop.get("is_active"))
    await property_service.set_active(db, property_id, new_value)

    logger.info(
        "Property is_active toggled: user=%s property_id=%d new=%s",
        user.email,
        property_id,
        new_value,
    )

    return JSONResponse(
        {"id": property_id, "is_active": new_value},
        status_code=303,
        headers={"Location": f"/properties/{property_id}"},
    )


async def _is_chatbot_enabled(db: AsyncSession) -> bool:
    return await BotSettingRepository.get_bool(db, "properties_chatbot_enabled", default=True)


@router.post("/api/properties/parse-query")
async def parse_query(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    body = await request.json()
    query = (body.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "Query is required")
    if not await _is_chatbot_enabled(db):
        raise HTTPException(503, "Chatbot disabled")
    parsed, error = await property_chatbot.parse(query)
    if error:
        return JSONResponse({"parsed": None, "error": error}, status_code=200)
    return {"parsed": parsed, "error": None}
