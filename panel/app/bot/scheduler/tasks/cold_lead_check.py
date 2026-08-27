"""Cold lead check — marks stale contacts as 'no_response'.

Contacts with status 'new' or 'bot_replied' that have had no activity
for a configurable number of hours are transitioned to 'no_response'
and a LeadEvent is recorded for each transition.  A best-effort
Telegram notification is sent to the admin chat via AdminNotifier.

Plan 67-02: SCHED-TASK-01.
Refactored in 71-03: Task 5 — replaced inline httpx with AdminNotifier.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.config import bot_settings
from app.bot.services.admin_notifier import AdminNotifier
from app.database import async_session_factory
from app.models.contact import Contact
from app.models.lead_event import LeadEvent

logger = logging.getLogger(__name__)

# Statuses eligible for cold-lead transition
_STALE_STATUSES = ("new", "bot_replied")
# Only bot-originated contacts.
# M6.3 Plan 123-10 (BOT-16 §9/§11): 'vista_publica' (public-site CTA leads)
# is bot-originated → stale vista_publica leads must be cold-swept too.
_BOT_SOURCES = ("whatsapp", "infocasas", "telegram", "vista_publica")


class ColdLeadChecker:
    """Finds stale leads and transitions them to ``no_response``.

    Parameters
    ----------
    notification_chat_id:
        Telegram chat ID to send the summary notification to.
    telegram_bot_token:
        Telegram Bot API token for sending notifications.
    stale_hours:
        Number of hours of inactivity before a lead is considered stale.
        Defaults to 24.
    session_factory:
        Optional async session factory override (for testing).
    notifier:
        Optional AdminNotifier override (for testing).
    """

    def __init__(
        self,
        notification_chat_id: str,
        telegram_bot_token: str,
        stale_hours: int = 24,
        *,
        session_factory=None,
        notifier: AdminNotifier | None = None,
    ) -> None:
        self.notification_chat_id = notification_chat_id
        self.telegram_bot_token = telegram_bot_token
        self.stale_hours = stale_hours
        self._session_factory = session_factory or async_session_factory
        self._notifier = notifier or AdminNotifier(
            chat_id=notification_chat_id,
            bot_token=telegram_bot_token,
        )

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

        # Best-effort notification — never let it break the task
        await self._notify(updated, stale_contacts)

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
    # Private: notification (delegates to AdminNotifier)
    # ------------------------------------------------------------------

    async def _notify(
        self,
        updated: int,
        stale_contacts: list[tuple[int, str]],
    ) -> None:
        """Send a summary Telegram notification (best-effort)."""
        contact_ids = [cid for cid, _ in stale_contacts]
        await self._notifier.notify_cold_leads(updated, contact_ids)


# ------------------------------------------------------------------
# Module-level factory
# ------------------------------------------------------------------


async def run_cold_lead_check() -> dict:
    """Factory function invoked by the scheduler.

    Reads configuration from ``bot_settings`` and runs the check.
    """
    checker = ColdLeadChecker(
        notification_chat_id=bot_settings.TELEGRAM_EZ_CHAT_ID,
        telegram_bot_token=bot_settings.TELEGRAM_BOT_TOKEN,
    )
    return await checker.run()
