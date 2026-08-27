"""Canonical amenities whitelist for the panel NL property search (M6.5).

Shared by the panel chatbot parser (app/bot/ai/property_chatbot.py) and the
property search repository. Canonical values are lowercase ASCII (no accents).
"""
from __future__ import annotations

import unicodedata

ALLOWED_AMENITIES: frozenset[str] = frozenset(
    {
        "piscina",
        "parrilla",
        "quincho",
        "gimnasio",
        "garage",
        "cochera",
        "ascensor",
        "balcon",
        "terraza",
        "vista",
    }
)


def normalize_amenity(value: str) -> str | None:
    """Normalize a raw amenity value to its canonical form.

    Lowercases, strips whitespace and accents (NFD decomposition, drop
    combining marks), then checks against ALLOWED_AMENITIES.
    Returns the canonical amenity or None if not whitelisted / not a string.
    """
    if not isinstance(value, str):
        return None
    decomposed = unicodedata.normalize("NFD", value.strip().lower())
    normalized = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return normalized if normalized in ALLOWED_AMENITIES else None
