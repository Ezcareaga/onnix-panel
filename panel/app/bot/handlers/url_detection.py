"""URL detection and lookup for user-shared property links.

Extracted from orchestrator.py in M4 Task 3.5. Two pure functions:

- ``extract_property_url_info(message)`` — regex-based detection of
  InfoCasas / Onnix Paraguay / Remax Paraguay URLs in free text.
- ``lookup_url_property(url_info, session)`` — DB lookup that returns a
  system context note (or a "not found" note) for Claude to consume.

Originally, extract_property_url_info was a module-level function and
lookup_url_property was an instance method on Orchestrator. Both now
live as module functions here; the latter receives ``session`` as a
parameter instead of reading from self.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from app.repositories.property_repo import PropertyRepository
from app.utils.money import precio

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# InfoCasas URL pattern: .../any-slug/189190235 — last numeric path segment.
_IC_URL_RE = re.compile(r'infocasas\.com\.py/[^\s]*/(\d{5,})')
# Onnix Paraguay URL: .../propiedad/39711 or .../propiedad/39711_some-slug
_ONNIX_URL_RE = re.compile(r'onnix\.com\.py/propiedad/(\d+)')
# Remax Paraguay URL: trailing path segment is the external_id with format
# {num}-{num}, e.g. .../mburucuya/some-slug/143014103-209 → '143014103-209'.
# In DB: properties.source='remax', external_id='143014103-209'.
_REMAX_URL_RE = re.compile(r'remax\.com\.py/[^\s]*/(\d{5,}-\d+)')

# Human-readable source labels for the "not found" context note.
_SOURCE_DISPLAY = {
    "infocasas": "InfoCasas",
    "onnixpy": "Onnix",
    "remax": "Remax",
}


def extract_property_url_info(message: str) -> dict | None:
    """Detect property URLs in a user message and return their source/ID.

    Supports:
    - InfoCasas: ``www.infocasas.com.py/slug/189190235``
    - Onnix Paraguay: ``onnix.com.py/propiedad/39711``
    - Remax Paraguay: ``remax.com.py/es-py/propiedades/.../143014103-209``

    Returns ``{"source": str, "property_id": str}`` or ``None`` if no
    recognised property URL is found.
    """
    ic_match = _IC_URL_RE.search(message)
    if ic_match:
        return {"source": "infocasas", "property_id": ic_match.group(1)}

    onnix_match = _ONNIX_URL_RE.search(message)
    if onnix_match:
        return {"source": "onnixpy", "property_id": onnix_match.group(1)}

    remax_match = _REMAX_URL_RE.search(message)
    if remax_match:
        return {"source": "remax", "property_id": remax_match.group(1)}

    return None


async def lookup_url_property(
    url_info: dict,
    session: "AsyncSession",
) -> "tuple[str, int | None]":
    """Look up a property by URL-extracted source/ID and return a context note.

    Returns ``(context_note, db_property_id)``:

    - ``context_note`` — system note to prepend to the AI call so Claude knows
      the property details without trying to visit any external URL. On any
      error or when the property isn't found, it's a "not found" note.
    - ``db_property_id`` — the resolved ``properties.id`` (cross-ref
      ``infocasas_properties.property_id`` for IC links), or ``None`` when the
      property isn't in the DB. The orchestrator uses it to point
      ``search_context.last_detalle_id`` at the property the user shared, so a
      later ``register_lead`` links the right property instead of the residual
      ``last_detalle_id`` of old searches (incidente remax).
    """
    source = url_info["source"]
    property_id = url_info["property_id"]

    try:
        if source == "infocasas":
            ic_prop = await PropertyRepository.get_ic_by_infocasas_id(
                session, property_id
            )
            if ic_prop is not None:
                availability = "Disponible" if ic_prop.is_active else "No disponible"
                price = ic_prop.price_sale or ic_prop.price_rent
                price_str = precio(price_usd=price, vacio="Sin precio")
                area_str = (
                    f" Área: {ic_prop.total_area_m2} m²."
                    if ic_prop.total_area_m2
                    else ""
                )
                note = (
                    f"[Sistema: El usuario compartió un enlace de una propiedad. "
                    f"InfoCasas ID: {property_id}. "
                    f"Título: {ic_prop.title or 'Sin título'}. "
                    f"Tipo: {ic_prop.property_type or 'Sin tipo'}. "
                    f"Operación: {ic_prop.operation or 'Sin operación'}. "
                    f"Precio: {price_str}. "
                    f"Estado: {availability}."
                    f"{area_str} "
                    f"Ciudad: {ic_prop.city or 'Sin ciudad'}. "
                    f"Barrio: {ic_prop.neighborhood or 'Sin barrio'}.]\n"
                )
                # Cross-ref a properties.id — None cuando la prop IC no está
                # matcheada en la tabla properties (mismo dato que usa el
                # preload del flujo directo IC para last_detalle_id).
                return note, ic_prop.property_id
        elif source in ("onnixpy", "remax"):
            prop = await PropertyRepository.get_by_source_external_id(
                session, source, property_id
            )
            if prop is not None:
                availability = "Disponible" if prop.is_active else "No disponible"
                price_str = precio(price_usd=prop.price_usd, vacio="Sin precio")
                note = (
                    f"[Sistema: El usuario compartió un enlace de una propiedad. "
                    f"{_SOURCE_DISPLAY[source]} ID: {property_id}. "
                    f"Título: {prop.title or 'Sin título'}. "
                    f"Precio: {price_str}. "
                    f"Estado: {availability}. "
                    f"Ciudad: {prop.city or 'Sin ciudad'}. "
                    f"Barrio: {prop.neighborhood or 'Sin barrio'}.]\n"
                )
                return note, prop.id
    except Exception:
        logger.warning(
            "URL property lookup failed for source=%s id=%s — skipping",
            source, property_id, exc_info=True,
        )

    # Not found or lookup error
    source_display = _SOURCE_DISPLAY.get(source, source)
    return (
        f"[Sistema: El usuario compartió un enlace de una propiedad con ID {property_id} "
        f"de {source_display}, pero no se encontró en esta base de datos en este momento.]\n"
    ), None
