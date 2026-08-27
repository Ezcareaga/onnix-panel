"""Utilidades compartidas entre handlers (M4 Task 3.6).

Funciones puras extraídas de orchestrator.py. No deben importar nada
del orchestrator ni mantener estado global.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.utils.money import precio

if TYPE_CHECKING:
    from app.bot.search.search_service import SearchResult
    from app.bot.search.sql_filters import SearchFilters


_PLURAL_MAP: dict[str, str] = {
    "casa": "casas",
    "departamento": "departamentos",
    "duplex": "d\u00faplex",
    "terreno": "terrenos",
    "oficina": "oficinas",
    "local": "locales",
    "deposito": "dep\u00f3sitos",
    "quinta": "quintas",
    "campo": "campos",
    "edificio": "edificios",
    "otro": "otros",
    "ph": "PHs",
}


def build_context_desc(filtros: dict) -> str:
    """Build a contextual description from search filters.

    Examples:
        {"operacion": "alquiler", "tipo": "departamento", "ciudad": "Lambare"}
          -> "departamentos en alquiler en Lambaré"
        {"operacion": "venta", "tipo": "casa"}
          -> "casas en venta"
        {} -> "opciones"
    """
    parts: list[str] = []

    operacion = filtros.get("operacion")
    tipo = filtros.get("tipo")

    if operacion:
        tipo_plural = _PLURAL_MAP.get(tipo, (tipo + "s") if tipo else "opciones")
        parts.append(f"{tipo_plural} en {operacion}")
    elif tipo:
        parts.append(_PLURAL_MAP.get(tipo, tipo + "s"))

    # Zone: prefer barrio over ciudad
    zona = filtros.get("barrio") or filtros.get("ciudad")
    if zona:
        parts.append(f"en {zona}")

    return " ".join(parts) if parts else "opciones"


def no_results_text(result: "SearchResult", filters: "SearchFilters") -> str:
    """Build the no-results message, optionally including min available price.

    Three branches (in priority order):
    1. min_price found → contextual message with tipo + zona + min price.
    2. Relaxation reached level >= 3 (zones exhausted) → offer nearby zones.
    3. Generic fallback → invite the user to adjust zone or budget.

    Note: min_price_in_zone is always in USD (queries MIN(price_usd)).
    """
    min_price = result.degradation.min_price_in_zone if result.degradation else None
    degradation = result.degradation

    tipo = filters.tipo or "propiedad"
    zona = filters.barrio or filters.ciudad or "esa zona"

    if min_price is not None and filters.precio_max is not None:
        price_str = precio(price_usd=min_price)
        return (
            f"No encontré {tipo}s en {zona} dentro de tu presupuesto. "
            f"La opción más económica arranca en {price_str}. "
            f"¿Querés que busque desde ese precio?"
        )

    if degradation is not None and degradation.level >= 3:
        return (
            f"No encontré {tipo}s en {zona} por ahora. "
            f"Puedo buscar en zonas cercanas o con otros filtros. "
            f"¿Qué preferís?"
        )

    return (
        f"No encontré {tipo}s disponibles con esos filtros. "
        f"¿Querés ajustar la zona o el presupuesto?"
    )
