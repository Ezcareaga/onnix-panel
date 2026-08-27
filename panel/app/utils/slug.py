"""URL-safe slug generation for property public URLs."""

import re
import unicodedata

_FALLBACK = "propiedad"


def slugify(text: str | None, max_len: int = 80) -> str:
    """Convert a property title into a URL-safe ASCII slug.

    Strips accents, lowercases, collapses non-alphanumeric sequences to a
    single hyphen, and truncates to *max_len* without leaving a trailing
    hyphen. Returns ``"propiedad"`` when the result would be empty.

    Args:
        text: Raw title string, e.g. ``"¡Terreno en Obligado, Itapúa!"``.
        max_len: Maximum slug length (default 80).

    Returns:
        A lowercase ASCII slug, e.g. ``"terreno-en-obligado-itapua"``.
    """
    if not text:
        return _FALLBACK

    # Decompose Unicode → strip combining marks (accents, tildes, etc.)
    nfd = unicodedata.normalize("NFD", text)
    ascii_text = "".join(c for c in nfd if unicodedata.category(c) != "Mn")

    # Collapse anything that isn't alphanumeric to a single hyphen
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")

    if not slug:
        return _FALLBACK

    return slug[:max_len].rstrip("-")
