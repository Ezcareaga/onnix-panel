#!/usr/bin/env python3
"""One-time backfill: fix IC source filter + ic_type in contact preferences.

FIX 1: Reset infocasas_properties.property_id where the linked property is
       not from source='onnixpy' or is inactive, then re-match via trigram.

FIX 4d: Backfill contacts.preferences['ic_type'] based on first_message
        for all contacts with source='infocasas'.

Usage:
    cd /home/onnix/panel
    python ../scripts/backfill_ic_source_and_ic_type.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

# Allow importing app modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "panel"))

from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")
logger = logging.getLogger(__name__)


async def fix1_reset_wrong_source(session) -> int:
    """Reset property_id in infocasas_properties where linked property is
    not from onnixpy or is inactive."""
    result = await session.execute(
        text(
            """
            UPDATE infocasas_properties
            SET property_id = NULL
            WHERE property_id IN (
                SELECT id FROM properties
                WHERE source != 'onnixpy' OR is_active = FALSE
            )
            """
        )
    )
    return result.rowcount


async def fix1_rematch_trigram(session) -> int:
    """Re-match infocasas_properties rows with NULL property_id via trigram.

    Only matches against properties WHERE source='onnixpy' AND is_active=TRUE.
    Uses the same 0.55 similarity threshold as the live service.
    """
    # Fetch rows that need matching
    rows = await session.execute(
        text(
            """
            SELECT id, title, city
            FROM infocasas_properties
            WHERE property_id IS NULL
              AND title IS NOT NULL
              AND title != ''
            """
        )
    )
    candidates = rows.fetchall()
    logger.info("FIX 1: %d IC rows need re-matching", len(candidates))

    matched = 0
    for row in candidates:
        result = await session.execute(
            text(
                """
                SELECT id
                FROM properties
                WHERE source = 'onnixpy'
                  AND is_active = TRUE
                  AND similarity(
                      unaccent(lower(title)),
                      unaccent(lower(:title))
                  ) > 0.55
                ORDER BY similarity(
                    unaccent(lower(title)),
                    unaccent(lower(:title))
                ) DESC
                LIMIT 1
                """
            ),
            {"title": row.title},
        )
        match = result.fetchone()
        if match:
            await session.execute(
                text(
                    "UPDATE infocasas_properties SET property_id = :pid WHERE id = :id"
                ),
                {"pid": match.id, "id": row.id},
            )
            matched += 1

    return matched


async def fix4d_backfill_ic_type(session) -> tuple[int, int]:
    """Backfill preferences['ic_type'] for all source='infocasas' contacts."""
    # Mark reenviadas
    result_r = await session.execute(
        text(
            """
            UPDATE contacts
            SET preferences = jsonb_set(
                COALESCE(preferences, '{}'::jsonb),
                '{ic_type}',
                '"reenviada"'::jsonb
            )
            WHERE source = 'infocasas'
              AND first_message ILIKE '%reenviada%'
              AND (preferences IS NULL OR preferences->>'ic_type' IS NULL)
            """
        )
    )
    reenviadas = result_r.rowcount

    # Mark directas (everything else)
    result_d = await session.execute(
        text(
            """
            UPDATE contacts
            SET preferences = jsonb_set(
                COALESCE(preferences, '{}'::jsonb),
                '{ic_type}',
                '"directa"'::jsonb
            )
            WHERE source = 'infocasas'
              AND first_message NOT ILIKE '%reenviada%'
              AND (preferences IS NULL OR preferences->>'ic_type' IS NULL)
            """
        )
    )
    directas = result_d.rowcount

    return reenviadas, directas


async def verify(session) -> None:
    """Print verification queries."""
    logger.info("=== VERIFICATION ===")

    result = await session.execute(
        text(
            """
            SELECT p.source, COUNT(*) as cnt
            FROM infocasas_properties ip
            JOIN properties p ON ip.property_id = p.id
            GROUP BY p.source
            ORDER BY cnt DESC
            """
        )
    )
    rows = result.fetchall()
    logger.info("IC cross-refs by source (should be 100%% onnixpy):")
    for row in rows:
        logger.info("  source=%s  count=%d", row.source, row.cnt)

    result2 = await session.execute(
        text(
            """
            SELECT preferences->>'ic_type' as ic_type, COUNT(*) as cnt
            FROM contacts
            WHERE source = 'infocasas'
            GROUP BY preferences->>'ic_type'
            ORDER BY cnt DESC
            """
        )
    )
    rows2 = result2.fetchall()
    logger.info("IC contacts by ic_type:")
    for row in rows2:
        logger.info("  ic_type=%s  count=%d", row.ic_type, row.cnt)


async def main() -> None:
    from app.database import async_session_factory

    async with async_session_factory() as session:
        logger.info("=== FIX 1: Reset wrong-source cross-refs ===")
        reset_count = await fix1_reset_wrong_source(session)
        logger.info("FIX 1: %d rows reset (non-onnixpy or inactive)", reset_count)

        logger.info("=== FIX 1: Re-match via trigram ===")
        matched = await fix1_rematch_trigram(session)
        logger.info("FIX 1: %d rows re-matched", matched)

        logger.info("=== FIX 4d: Backfill ic_type in preferences ===")
        reenviadas, directas = await fix4d_backfill_ic_type(session)
        logger.info("FIX 4d: %d reenviadas, %d directas tagged", reenviadas, directas)

        await session.commit()

        await verify(session)

    logger.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
