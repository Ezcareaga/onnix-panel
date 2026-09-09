"""Cold lead check — marks stale contacts as 'no_response'.

Contacts with status 'new' or 'bot_replied' that have had no activity
for a configurable number of hours are transitioned to 'no_response'
and a LeadEvent is recorded for each transition.

El aviso por Telegram se fue el 2026-09-09 con el canal. El trabajo de
verdad —la transicion y su LeadEvent— es lo que quedaba de valor: la cola de
Leads muestra el resultado sin que nadie tenga que avisar nada.

Plan 67-02: SCHED-TASK-01.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models.contact import Contact
from app.models.lead_event import LeadEvent

logger = logging.getLogger(__name__)

# Statuses eligible for cold-lead transition
_STALE_STATUSES = ("new", "bot_replied")
# Las fuentes que barre. 'telegram' se fue con el canal; 'infocasas' y
# 'vista_publica' se fueron con el vertical y no llegan mas, pero quedan en la
# lista porque hay contactos viejos con esa `source` que igual se enfrian.
_BOT_SOURCES = ("whatsapp", "infocasas", "vista_publica")


class ColdLeadChecker:
    """Finds stale leads and transitions them to ``no_response``.

    Parameters
    ----------
    stale_hours:
        Number of hours of inactivity before a lead is considered stale.
        Defaults to 24.
    session_factory:
        Optional async session factory override (for testing).
    """

    def __init__(
        self,
        stale_hours: int = 24,
        *,
        session_factory=None,
    ) -> None:
        self.stale_hours = stale_hours
        self._session_factory = session_factory or async_session_factory

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> dict:
        """Execute the cold-lead check.

        Returns a dict with ``checked`` (total stale found) and
        ``updated`` (number transitioned) counts.
        """
        start = time.monotonic()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.stale_hours)

        async with self._session_factory() as session:
            stale_contacts = await self._find_stale(session, cutoff)
            if not stale_contacts:
                elapsed_ms = (time.monotonic() - start) * 1000
                logger.info(
                    'Job executed — {"task": "cold_lead_check", "duration_ms": %.0f, "checked": 0, "updated": 0}',
                    elapsed_ms,
                )
                return {"checked": 0, "updated": 0}

            updated = await self._transition(session, stale_contacts)
            await session.commit()

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            'Job executed — {"task": "cold_lead_check", "duration_ms": %.0f, "checked": %d, "updated": %d}',
            elapsed_ms, len(stale_contacts), updated,
        )
        return {"checked": len(stale_contacts), "updated": updated}

    # ------------------------------------------------------------------
    # Private: query
    # ------------------------------------------------------------------

    async def _find_stale(
        self, session: AsyncSession, cutoff: datetime
    ) -> list[tuple[int, str]]:
        """Return list of (contact_id, old_status) for stale contacts."""
        stmt = (
            select(Contact.id, Contact.status)
            .where(
                Contact.status.in_(_STALE_STATUSES),
                Contact.source.in_(_BOT_SOURCES),
                Contact.last_activity_at.isnot(None),
                Contact.last_activity_at < cutoff,
                Contact.baja_at.is_(None),  # Rule #4: opt-out contacts are untouchable
            )
        )
        result = await session.execute(stmt)
        return [(row[0], row[1]) for row in result.fetchall()]

    # ------------------------------------------------------------------
    # Private: transition
    # ------------------------------------------------------------------

    async def _transition(
        self,
        session: AsyncSession,
        stale_contacts: list[tuple[int, str]],
    ) -> int:
        """Update statuses and insert LeadEvents. Returns count updated."""
        contact_ids = [cid for cid, _ in stale_contacts]

        # Bulk update status
        stmt = (
            update(Contact)
            .where(Contact.id.in_(contact_ids))
            .values(status="no_response")
        )
        result = await session.execute(stmt)
        updated_count = result.rowcount

        # Insert individual LeadEvents
        now = datetime.now(timezone.utc)
        for contact_id, old_status in stale_contacts:
            event = LeadEvent(
                contact_id=contact_id,
                event_type="status_change",
                old_status=old_status,
                new_status="no_response",
                triggered_by="cold_lead_check",
                created_at=now,
            )
            session.add(event)

        return updated_count


# ------------------------------------------------------------------
# Module-level factory
# ------------------------------------------------------------------


async def run_cold_lead_check() -> dict:
    """Factory function invoked by the scheduler."""
    checker = ColdLeadChecker()
    return await checker.run()
