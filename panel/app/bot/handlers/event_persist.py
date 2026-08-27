"""Post-AI event persistence (M4 Fase 6.C).

Side-effects that every turn must emit after the Claude loop finishes,
separate from the lead-specific flow in ``lead_persist``:

- ``persist_opt_out`` — three SQL statements that finalize an opt-out
  (flip ``contacts.baja_at`` + ``status='discarded'``, disable the bot
  on the conversation, emit an ``opt_out`` lead_event). Triggered when
  the ``process_opt_out`` tool call succeeded.

- ``persist_turn_events`` — writes to ``lead_events`` for the current
  turn. Either the search/detail events collected during the tool loop,
  or a single ``bot_interaction`` row when the turn was purely
  conversational (no tool calls, no lead, no opt-out).

All SQL is wrapped in try/except so failures are logged but never
propagate — losing a lead_event row is acceptable, dropping a user
reply because of event insert failure is not.

Extracted from ``Orchestrator.handle_message``.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from sqlalchemy import text as sa_text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.bot.core.types import ContactInfo, ConversationInfo

logger = logging.getLogger(__name__)


async def persist_opt_out(
    session: "AsyncSession",
    contact: "ContactInfo",
) -> None:
    """Finalize an opt-out: set baja_at, flip conversation inactive, log event.

    Idempotent via ``baja_at IS NULL`` guard — safe to re-run if the
    same turn triggers opt-out twice.
    """
    await session.execute(sa_text(
        "UPDATE contacts SET baja_at = NOW(), status = 'discarded' "
        "WHERE id = :id AND baja_at IS NULL"
    ), {"id": contact.id})
    await session.execute(sa_text(
        "UPDATE conversations SET is_bot_active = false "
        "WHERE contact_id = :id"
    ), {"id": contact.id})
    await session.execute(sa_text(
        "INSERT INTO lead_events "
        "(contact_id, event_type, old_status, new_status, triggered_by, metadata, created_at) "
        "VALUES (:id, 'opt_out', :old_status, 'discarded', 'bot', '{}', NOW())"
    ), {"id": contact.id, "old_status": contact.status})


async def persist_turn_events(
    session: "AsyncSession",
    contact: "ContactInfo",
    conversation: "ConversationInfo",
    events_to_record: list[dict],
    *,
    is_lead: bool,
    is_opt_out: bool,
) -> None:
    """Write lead_events for the current turn.

    - If the tool loop produced ``search`` / ``detail_view`` events,
      insert one row per event with their metadata.
    - Otherwise, if the turn was a plain conversacional exchange
      (no events, no lead, no opt-out), insert a single
      ``bot_interaction`` row so the panel can still surface the turn.
    """
    for evt in events_to_record:
        try:
            await session.execute(sa_text(
                "INSERT INTO lead_events "
                "(contact_id, event_type, triggered_by, metadata, created_at) "
                "VALUES (:cid, :etype, 'bot', :meta, NOW())"
            ), {
                "cid": contact.id,
                "etype": evt["event_type"],
                "meta": json.dumps(evt["metadata"], ensure_ascii=False),
            })
        except Exception:
            logger.warning(
                "Failed to record %s event for contact=%d",
                evt["event_type"], contact.id, exc_info=True,
            )

    if not events_to_record and not is_lead and not is_opt_out:
        try:
            await session.execute(sa_text(
                "INSERT INTO lead_events "
                "(contact_id, event_type, triggered_by, metadata, created_at) "
                "VALUES (:cid, 'bot_interaction', 'bot', :meta, NOW())"
            ), {
                "cid": contact.id,
                "meta": json.dumps(
                    {"conversation_id": conversation.id}, ensure_ascii=False
                ),
            })
        except Exception:
            logger.warning(
                "Failed to record bot_interaction event for contact=%d",
                contact.id, exc_info=True,
            )
