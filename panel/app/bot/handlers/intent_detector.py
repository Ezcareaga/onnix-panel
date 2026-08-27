"""Intent detection helpers extracted from orchestrator (M4 Task 3.3).

Two independent heuristics:

- ``is_pagination_text(text)`` — checked BEFORE sending to AI. Detects
  free-text pagination requests ("mostrame más", "las que faltan", etc.).
- ``detect_intent_from_text(text)`` — fallback classifier. Used when
  Claude returns only text (no tool calls). Assigns one of 5 intents
  based on keyword presence.

Both previously lived as static methods on Orchestrator.
"""
from __future__ import annotations

import re


# Patterns that indicate the user wants to see more results from the
# current search (free-text pagination).  Checked BEFORE sending to AI.
_PAGINATION_RE = re.compile(
    r"(?:"
    r"ver\s*m[aá]s"
    r"|m[aá]s\s*opcio"
    r"|m[aá]s\s*resultado"
    r"|m[aá]s\s*propiedad"
    r"|mu[eé]str\w*\s*m[aá]s"
    r"|mu[eé]str\w*\s+(?:las?\s*)?\d+\s*opcio"
    r"|mu[eé]str\w*\s*(?:otra|resto|dem[aá]s)"
    r"|mostr\w*\s*m[aá]s"
    r"|(?:las?|los)\s*(?:que\s*)?falta"
    r"|(?:las?|los)\s*dem[aá]s"
    r"|(?:las?|los)\s*otra"
    r"|(?:el|lo)\s*rest"
    r"|siguiente"
    r"|pr[oó]xim"
    r"|ver\s*toda"
    r"|dame\s*m[aá]s"
    r")",
    re.IGNORECASE,
)


def is_pagination_text(text: str) -> bool:
    """Detect pagination intent from free text.

    Returns True when the user's message looks like a request to see more
    results from the current search (e.g. "muéstrame más", "las que faltan",
    "muéstrame las 14 opciones").
    """
    return bool(_PAGINATION_RE.search(text))


def detect_intent_from_text(text: str) -> str:
    """Simple heuristic intent detection from response text.

    Returns a best-guess intent based on keywords. The Claude tool-use
    loop usually makes this unnecessary (intents come from tool calls),
    but it serves as a fallback for text-only responses.
    """
    lower = text.lower()
    if any(w in lower for w in ["hola", "bienvenido", "buenos"]):
        return "saludo"
    if any(w in lower for w in ["asesor", "contactar", "registr"]):
        return "lead"
    # Incomplete search: Claude is asking about operation type
    if any(w in lower for w in ["comprar", "alquilar", "venta o alquiler", "operaci"]):
        return "busqueda_incompleta_operacion"
    # Incomplete search: Claude is asking about location
    if any(w in lower for w in ["que zona", "qué zona", "que ciudad", "qué ciudad", "que barrio", "qué barrio", "donde busc"]):
        return "busqueda_incompleta_zona"
    return "conversacion"
