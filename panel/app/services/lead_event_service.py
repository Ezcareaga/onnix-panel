"""lead_event_service — shared helper to record lead_events rows.

Wraps LeadEventRepository.create with a defensive try/except so that a
failed INSERT never breaks the bot request pipeline.

Usage::

    from app.services.lead_event_service import record_event

    await record_event(
        session,
        contact_id=contact.id,
        conversation_id=conversation.id,
        event_type="zero_results_offered",
        trigger="zero_results",
        metadata={"filters": {...}, "alternatives_count": 2, "alt_ids": [...]},
    )
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.repositories.lead_event_repo import LeadEventRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def record_event(
    session: "AsyncSession",
    *,
    contact_id: int,
    conversation_id: int | None = None,
    event_type: str,
    trigger: str | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Insert a lead_events row.

    Defensive: logs a warning and returns silently on any exception so
    that metric collection never interrupts the bot message pipeline.

    Args:
        session: active async SQLAlchemy session.
        contact_id: FK to contacts.id.
        conversation_id: optional, stored in metadata when provided.
        event_type: one of the canonical event type strings.
        trigger: optional free-text trigger label (e.g. "zero_results",
            "callback", "text").
        from_status: previous contact status, if applicable.
        to_status: new contact status, if applicable.
        metadata: arbitrary JSONB payload stored in lead_events.metadata.
    """
    try:
        enriched_meta: dict = dict(metadata or {})
        if conversation_id is not None:
            enriched_meta.setdefault("conversation_id", conversation_id)

        await LeadEventRepository.create(
            session,
            contact_id=contact_id,
            event_type=event_type,
            old_status=from_status,
            new_status=to_status,
            triggered_by=trigger or "system",
            metadata=enriched_meta,
        )
    except Exception:
        logger.warning(
            "record_event failed — event_type=%s contact_id=%s",
            event_type, contact_id,
            exc_info=True,
        )
