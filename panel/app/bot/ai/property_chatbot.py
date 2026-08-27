"""One-shot NL query parser for the properties panel.

Calls Claude Haiku to extract structured search filters from a free-text
Spanish query. Returns (parsed_dict, None) on success or (None, error_str)
on any failure so callers never see raw exceptions.
"""
from __future__ import annotations

import json
import logging
import os
import re

from app.bot.ai.claude_client import ClaudeClient
from app.utils.amenities import normalize_amenity

logger = logging.getLogger(__name__)

_MAX_QUERY_CHARS = 500
_HAIKU_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
_ERROR_MSG = "No pude entender la consulta. Probá con más detalles: zona, tipo, precio."

_SYSTEM_PROMPT = """\
Sos un extractor de filtros de búsqueda inmobiliaria para Paraguay.
El usuario escribe en español rioplatense. Tu tarea es extraer filtros estructurados de su consulta y devolver ÚNICAMENTE JSON válido, sin explicaciones ni texto extra.

Campos posibles (todos opcionales):
- property_type: "departamento" | "casa" | "terreno" | "oficina" | "local" | "duplex"
- operation: "venta" | "alquiler"
- city: string en minúsculas (ej: "asuncion", "luque", "san lorenzo")
- neighborhood: string en minúsculas (ej: "villa morra", "recoleta", "las mercedes")
- price_min: número (USD por defecto salvo que diga Gs. o PYG)
- price_max: número (USD por defecto salvo que diga Gs. o PYG)
- currency: "USD" | "PYG" (default "USD" si el usuario escribe un número sin unidad)
- bedrooms_min: entero
- bathrooms_min: entero (ej: "con 3 baños" → 3)
- construction_state: "en_pozo" | "en_construccion" | "a_estrenar" | "terminado"
- amenities: lista con valores ÚNICAMENTE de: "piscina", "parrilla", "quincho", "gimnasio", "garage", "cochera", "ascensor", "balcon", "terraza", "vista". Usar exactamente esos valores (minúsculas, sin acentos). Si el usuario menciona otra comodidad, NO incluirla.
- barato: true SOLO si la query dice "barato", "económico", "economico" o "accesible". No incluir el campo en otro caso.
- descripcion_libre: string con características cualitativas que NO mapean a ningún filtro de arriba (ej: "con vista al río, luminoso", "estilo moderno cerca del shopping"). OMITIR si toda la query mapea a filtros estructurados.

Reglas:
- "en pozo" → construction_state: "en_pozo"
- "200k" o "200.000" → price_max: 200000
- "depto con balcón y piscina" → amenities: ["balcon", "piscina"]
- "casa económica en luque" → city: "luque", barato: true
- NO repetir en descripcion_libre lo que ya mapea a amenities u otro filtro: "casa con piscina luminosa y amplia" → {"amenities": ["piscina"], "descripcion_libre": "luminosa y amplia"}
- Si no hay suficiente información para un campo, omitirlo del JSON.
- Responder SOLO con el JSON, sin markdown, sin ``` ni explicaciones.\
"""


def _sanitize_enriched_fields(parsed: dict) -> dict:
    """Server-side validation of the M6.5 enrichment fields (never trust the LLM).

    - amenities: normalize each value (lowercase + strip accents) and keep only
      whitelisted canonicals, deduped preserving order. Key dropped if empty.
    - barato: strict bool — only a JSON `true` survives; any other value
      ("yes", 1, false, etc.) is silently discarded.
    - descripcion_libre: stripped string; discarded if shorter than 3 chars.
    """
    raw_amenities = parsed.pop("amenities", None)
    if isinstance(raw_amenities, list):
        canonical: list[str] = []
        for item in raw_amenities:
            normalized = normalize_amenity(item)
            if normalized and normalized not in canonical:
                canonical.append(normalized)
        if canonical:
            parsed["amenities"] = canonical

    if parsed.pop("barato", None) is True:
        parsed["barato"] = True

    raw_desc = parsed.pop("descripcion_libre", None)
    if isinstance(raw_desc, str):
        desc = raw_desc.strip()
        if len(desc) >= 3:
            parsed["descripcion_libre"] = desc

    return parsed


def _strip_fences(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` wrappers if present."""
    stripped = text.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
    stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


async def parse(query: str) -> tuple[dict | None, str | None]:
    """Extract structured filters from a free-text property search query.

    Returns (parsed_dict, None) on success, (None, error_str) on any failure.
    """
    truncated = query[:_MAX_QUERY_CHARS]
    logger.info(
        "property_chatbot.parse — query=%.200s",
        truncated,
    )

    client = ClaudeClient(
        api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        model=_HAIKU_MODEL,
    )

    try:
        response = await client.send_message(
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": truncated}],
            # 384 (era 256): los campos M6.5 (amenities list + descripcion_libre)
            # pueden empujar el JSON de salida por encima de 256 tokens en
            # queries densas — margen para no truncar y romper el json.loads.
            max_tokens=384,
            temperature=0.0,
            _tracking_source="properties_chatbot",
        )
    except Exception as exc:
        logger.warning("property_chatbot: Claude error — %s", exc)
        return None, _ERROR_MSG

    raw = response.text
    if not raw:
        logger.warning("property_chatbot: empty response from Claude")
        return None, _ERROR_MSG

    try:
        parsed = json.loads(_strip_fences(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("property_chatbot: JSON parse error — %s | raw=%.200s", exc, raw)
        return None, _ERROR_MSG

    if not isinstance(parsed, dict):
        logger.warning(
            "property_chatbot: non-dict JSON from Claude — raw=%.200s", raw
        )
        return None, _ERROR_MSG

    parsed = _sanitize_enriched_fields(parsed)

    logger.info("property_chatbot.parse — parsed=%s", parsed)
    return parsed, None
