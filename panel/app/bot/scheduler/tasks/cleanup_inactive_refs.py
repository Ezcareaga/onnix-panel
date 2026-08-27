"""Cleanup inactive property cross-refs — scheduled task.

Runs daily at 02:00 UTC.
- Clears infocasas_properties.property_id when the linked property is inactive.
- Clears contacts.property_id when the linked property is inactive.

This prevents stale cross-refs from polluting lead matching results.
"""
from __future__ import annotations

import logging

from app.database import async_session_factory

logger = logging.getLogger(__name__)


async def run_cleanup_inactive_refs() -> dict:
    """Clear cross-refs pointing to inactive properties.

    Returns
    -------
    dict
        Metrics: ic_cleared, contacts_cleared.
    """
    try:
        async with async_session_factory() as session:
            from sqlalchemy import text

            # Clear infocasas_properties.property_id → inactive properties
            result_ic = await session.execute(
                text(
                    """
                    UPDATE infocasas_properties
                    SET property_id = NULL
                    WHERE property_id IN (
                        SELECT id FROM properties WHERE is_active = FALSE
                    )
                    """
                )
            )
            ic_cleared = result_ic.rowcount

            # Clear contacts.property_id → inactive properties
            result_contacts = await session.execute(
                text(
                    """
                    UPDATE contacts
                    SET property_id = NULL
                    WHERE property_id IN (
                        SELECT id FROM properties WHERE is_active = FALSE
                    )
                    """
                )
            )
            contacts_cleared = result_contacts.rowcount

            await session.commit()

        logger.info(
            "cleanup_inactive_refs: ic_cleared=%d, contacts_cleared=%d",
            ic_cleared,
            contacts_cleared,
        )
        return {"status": "ok", "ic_cleared": ic_cleared, "contacts_cleared": contacts_cleared}

    except Exception:
        logger.exception("cleanup_inactive_refs: unhandled error")
        return {"status": "error", "ic_cleared": 0, "contacts_cleared": 0}
