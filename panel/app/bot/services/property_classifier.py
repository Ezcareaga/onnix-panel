"""Hybrid property type classifier for Onnix SA.

Three-stage pipeline (highest priority first):
  1. Seed  — pre-audited classifications from audit_classifications.jsonl (486 entries)
  2. Remap — deterministic rules for known scraper type strings (~98.9% coverage)
  3. LLM   — Claude Haiku fallback for uncovered cases

Usage::

    from app.bot.services.property_classifier import classify_one, classify_batch

    result = await classify_one({"id": 123, "property_type": "bodega", "title": "..."})
    # ClassificationResult(type_id=7, confidence=1.0, reason="...", method="remap")
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Catalogue IDs (mirrors property_types table)
# ---------------------------------------------------------------------------
#  1=CASA  2=DEPARTAMENTO  3=DUPLEX  4=TERRENO  5=OFICINA
#  6=LOCAL 7=DEPOSITO  8=QUINTA  9=CAMPO  10=EDIFICIO  99=OTRO

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ClassificationResult:
    """Result from the classification pipeline.

    Attributes:
        type_id:    FK → property_types.id; None when confidence is too low.
        confidence: Float in [0.0, 1.0].
        reason:     Human-readable justification.
        method:     One of "remap", "seed", "llm", "null".
    """

    type_id: Optional[int]
    confidence: float
    reason: str
    method: str


# ---------------------------------------------------------------------------
# Stage 1 — Deterministic remap rules
# ---------------------------------------------------------------------------

REMAP_RULES: dict[str, int] = {
    # Direct catalogue types
    "casa": 1,
    "departamento": 2,
    "duplex": 3,
    "terreno": 4,
    "oficina": 5,
    "local": 6,
    "deposito": 7,
    "quinta": 8,
    "campo": 9,
    "edificio": 10,
    # Scraper synonyms / variants
    "casa-duplex": 3,
    "departamento-en-pozo": 2,
    "casa-en-condominio": 1,
    "oficinas": 5,
    "bodega": 7,
    "nave": 7,
    "livestock farm": 9,
    "hacienda": 9,
    "propiedad-agricola": 9,
    "departamento con jardin": 2,
    "departamento con servicio de hotel": 2,
    "casa para estudiantes": 1,
}

# Types that are classified but with reduced confidence
REVIEW_REQUIRED: dict[str, tuple[int, float]] = {
    "fraccionamiento": (99, 0.7),
    "inmueble-productivo": (99, 0.7),
    "estacionamiento": (99, 0.8),
    "fabrica": (7, 0.7),
    "restaurant with rooms": (99, 0.7),
}

# ---------------------------------------------------------------------------
# Stage 2 — Seed cache (loaded once at module import)
# ---------------------------------------------------------------------------

_SEED_PATH = Path(__file__).resolve().parents[4] / "docs" / "audit_classifications.jsonl"
_CONFIDENCE_THRESHOLD = 0.75


def _load_seed() -> dict[int, ClassificationResult]:
    """Load pre-audited classifications from JSONL file.

    Format per line:
        {"id": N, "source": "...", "tipo_declarado": "...",
         "codigo_llm": N, "confianza": 0.XX, "razon": "..."}

    Only entries with confianza >= 0.75 are loaded.

    Returns:
        Mapping of property_id → ClassificationResult with method="seed".
    """
    cache: dict[int, ClassificationResult] = {}
    if not _SEED_PATH.exists():
        logger.warning("Seed file not found: %s", _SEED_PATH)
        return cache

    loaded = skipped = errors = 0
    with _SEED_PATH.open(encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
                prop_id: int = int(entry["id"])
                type_id: int = int(entry["codigo_llm"])
                confidence: float = float(entry["confianza"])
                reason: str = entry.get("razon", "seed")

                if confidence < _CONFIDENCE_THRESHOLD:
                    skipped += 1
                    continue

                cache[prop_id] = ClassificationResult(
                    type_id=type_id,
                    confidence=confidence,
                    reason=reason,
                    method="seed",
                )
                loaded += 1
            except (KeyError, ValueError, json.JSONDecodeError) as exc:
                errors += 1
                logger.debug("Seed parse error at line %d: %s", lineno, exc)

    logger.info(
        "Seed loaded: %d entries, %d skipped (low confidence), %d errors",
        loaded,
        skipped,
        errors,
    )
    return cache


# Load at module import time (fast, ~486 lines)
_SEED_CACHE: dict[int, ClassificationResult] = _load_seed()

# ---------------------------------------------------------------------------
# Stage 3 — LLM fallback via Claude Haiku
# ---------------------------------------------------------------------------


async def _call_haiku(property_dict: dict[str, Any]) -> dict[str, Any]:
    """Call Claude Haiku to classify a single property.

    Args:
        property_dict: Dict with keys: id, property_type, title, description,
            total_area_m2, built_area_m2, bedrooms, bathrooms.

    Returns:
        Dict with keys: ``codigo`` (int), ``confianza`` (float), ``razon`` (str).

    Raises:
        Exception: Any error from the Anthropic SDK or JSON parsing.
    """
    from anthropic import AsyncAnthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    client = AsyncAnthropic(api_key=api_key)

    prompt = (
        "Clasifica esta propiedad inmobiliaria en UNA de estas categorías:\n"
        "1=CASA, 2=DEPARTAMENTO, 3=DUPLEX, 4=TERRENO, 5=OFICINA, 6=LOCAL,\n"
        "7=DEPOSITO, 8=QUINTA, 9=CAMPO, 10=EDIFICIO, 99=OTRO\n\n"
        "Reglas:\n"
        "- QUINTA: terreno >1000m2 con amenidades (piscina, quincho, country)\n"
        "- CAMPO: >5 hectáreas, uso agropecuario/forestal\n"
        "- DUPLEX: 2+ niveles con acceso independiente\n"
        "- EDIFICIO: edificio completo en venta, no unidad individual\n"
        "- DEPOSITO: incluye nave, galpón, bodega, fábrica\n\n"
        f"Propiedad:\n"
        f"- Tipo declarado: {property_dict.get('property_type')}\n"
        f"- Título: {property_dict.get('title', '')}\n"
        f"- Descripción: {str(property_dict.get('description', ''))[:300]}\n"
        f"- Área total: {property_dict.get('total_area_m2')}m2\n"
        f"- Área construida: {property_dict.get('built_area_m2')}m2\n"
        f"- Dormitorios: {property_dict.get('bedrooms')}\n"
        f"- Baños: {property_dict.get('bathrooms')}\n\n"
        'Responde SOLO JSON: {"codigo": N, "confianza": 0.XX, "razon": "..."}'
    )

    from app.bot.observability.anthropic_tracker import track_anthropic_call

    async with track_anthropic_call("property_classifier") as tracker:
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        tracker.set_response(response)
    raw_text = response.content[0].text.strip()
    return json.loads(raw_text)


# ---------------------------------------------------------------------------
# Main classifier
# ---------------------------------------------------------------------------


async def classify_one(property_dict: dict[str, Any]) -> ClassificationResult:
    """Classify a single property through the three-stage pipeline.

    Priority: seed > remap > LLM

    Args:
        property_dict: At minimum ``{"id": N, "property_type": "..."}``; richer
            dicts (title, description, areas, bedrooms, bathrooms) improve LLM
            accuracy.

    Returns:
        ClassificationResult with type_id, confidence, reason, method.
    """
    prop_id: Optional[int] = property_dict.get("id")
    raw_type: str = (property_dict.get("property_type") or "").lower().strip()

    # --- Stage 2: Seed has highest priority ---
    if prop_id is not None and prop_id in _SEED_CACHE:
        return _SEED_CACHE[prop_id]

    # --- Stage 1: Deterministic remap ---
    if raw_type in REMAP_RULES:
        return ClassificationResult(
            type_id=REMAP_RULES[raw_type],
            confidence=1.0,
            reason=f"deterministic remap: {raw_type}",
            method="remap",
        )

    if raw_type in REVIEW_REQUIRED:
        type_id, confidence = REVIEW_REQUIRED[raw_type]
        # REVIEW_REQUIRED entries always receive a type_id — they are classified
        # but with reduced confidence to flag them for manual review.
        # Only LLM results below _CONFIDENCE_THRESHOLD lose their type_id.
        return ClassificationResult(
            type_id=type_id,
            confidence=confidence,
            reason=f"review_required remap: {raw_type}",
            method="remap",
        )

    # --- Stage 3: LLM fallback ---
    try:
        llm_result = await _call_haiku(property_dict)
        confidence = float(llm_result["confianza"])
        return ClassificationResult(
            type_id=int(llm_result["codigo"]) if confidence >= _CONFIDENCE_THRESHOLD else None,
            confidence=confidence,
            reason=llm_result.get("razon", ""),
            method="llm",
        )
    except Exception as exc:
        logger.error(
            "LLM classification failed for property %s: %s",
            prop_id,
            exc,
        )
        return ClassificationResult(
            type_id=None,
            confidence=0.0,
            reason=f"LLM error: {exc}",
            method="null",
        )


# ---------------------------------------------------------------------------
# Batch migration helper
# ---------------------------------------------------------------------------


async def classify_batch(
    session: Any,
    batch_size: int = 100,
    dry_run: bool = False,
) -> dict[str, int]:
    """Classify all properties where property_type_normalized IS NULL.

    Iterates in offset pages of ``batch_size``, calling :func:`classify_one`
    for each row. Skips properties with low-confidence results (type_id=None).
    Commits each page unless ``dry_run=True``.

    Args:
        session:    SQLAlchemy ``AsyncSession``.
        batch_size: Number of properties per page (default 100).
        dry_run:    If True, do not UPDATE or commit (audit mode).

    Returns:
        Stats dict::

            {
                "total": N,
                "classified": N,
                "skipped_low_confidence": N,
                "errors": N,
            }
    """
    from sqlalchemy import text

    stats: dict[str, int] = {
        "total": 0,
        "classified": 0,
        "skipped_low_confidence": 0,
        "errors": 0,
    }
    offset = 0

    while True:
        result = await session.execute(
            text("""
                SELECT id, property_type, title, description,
                       total_area_m2, built_area_m2, bedrooms, bathrooms
                FROM properties
                WHERE is_active = true
                  AND property_type_normalized IS NULL
                ORDER BY id
                LIMIT :limit OFFSET :offset
            """),
            {"limit": batch_size, "offset": offset},
        )
        rows = result.fetchall()
        if not rows:
            break

        stats["total"] += len(rows)

        for row in rows:
            prop_dict = dict(row._mapping)
            try:
                classified = await classify_one(prop_dict)
                if classified.type_id is not None:
                    if not dry_run:
                        await session.execute(
                            text(
                                "UPDATE properties "
                                "SET property_type_normalized = :tid "
                                "WHERE id = :pid"
                            ),
                            {"tid": classified.type_id, "pid": prop_dict["id"]},
                        )
                    stats["classified"] += 1
                else:
                    stats["skipped_low_confidence"] += 1
                    logger.warning(
                        "Low confidence for property %s: %.2f (%s)",
                        prop_dict["id"],
                        classified.confidence,
                        classified.reason,
                    )
            except Exception as exc:
                stats["errors"] += 1
                logger.error(
                    "Error classifying property %s: %s",
                    prop_dict.get("id"),
                    exc,
                )

        if not dry_run:
            await session.commit()

        offset += batch_size
        logger.info(
            "Classified batch: offset=%d, stats=%s",
            offset,
            stats,
        )

    return stats
