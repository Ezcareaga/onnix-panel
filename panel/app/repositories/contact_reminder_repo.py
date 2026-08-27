"""CRUD repository for contact_reminders (C2.2).

No business logic lives here — only SQL/ORM operations.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact_reminder import ContactReminder


class ContactReminderRepository:
    @staticmethod
    async def create(
        db: AsyncSession,
        contact_id: int,
        user_id: int,
        due_at: datetime,
        note: str,
    ) -> ContactReminder:
        reminder = ContactReminder(
            contact_id=contact_id,
            user_id=user_id,
            due_at=due_at,
            note=note,
        )
        db.add(reminder)
        await db.flush()
        return reminder

    @staticmethod
    async def get_by_id(db: AsyncSession, reminder_id: int) -> ContactReminder | None:
        return await db.get(ContactReminder, reminder_id)

    @staticmethod
    async def list_by_contact(
        db: AsyncSession, contact_id: int
    ) -> list[ContactReminder]:
        """All reminders for a contact ordered by due_at ASC (open first, done after)."""
        result = await db.execute(
            select(ContactReminder)
            .where(ContactReminder.contact_id == contact_id)
            .order_by(
                ContactReminder.done_at.is_(None).desc(),  # open before done
                ContactReminder.due_at.asc(),
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_due(db: AsyncSession) -> list[ContactReminder]:
        """Reminders past their due_at that have not been marked done."""
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(ContactReminder)
            .where(
                ContactReminder.due_at <= now,
                ContactReminder.done_at.is_(None),
            )
            .order_by(ContactReminder.due_at.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def count_overdue_for_contacts(
        db: AsyncSession, contact_ids: list[int]
    ) -> set[int]:
        """Return the set of contact_ids that have at least one overdue open reminder.

        Used by the contacts list to show the overdue dot in O(1) queries
        instead of N+1 per row.
        """
        if not contact_ids:
            return set()
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(ContactReminder.contact_id)
            .where(
                ContactReminder.contact_id.in_(contact_ids),
                ContactReminder.due_at <= now,
                ContactReminder.done_at.is_(None),
            )
            .distinct()
        )
        return {row[0] for row in result.all()}

    @staticmethod
    async def count_open_for_contact(db: AsyncSession, contact_id: int) -> int:
        """Count open (not done) reminders for a contact."""
        result = await db.execute(
            select(func.count())
            .select_from(ContactReminder)
            .where(
                ContactReminder.contact_id == contact_id,
                ContactReminder.done_at.is_(None),
            )
        )
        return result.scalar() or 0

    @staticmethod
    async def mark_done(
        db: AsyncSession, reminder_id: int
    ) -> ContactReminder | None:
        reminder = await db.get(ContactReminder, reminder_id)
        if reminder is None:
            return None
        reminder.done_at = datetime.now(timezone.utc)
        await db.flush()
        return reminder

    @staticmethod
    async def delete(db: AsyncSession, reminder_id: int) -> bool:
        reminder = await db.get(ContactReminder, reminder_id)
        if reminder is None:
            return False
        await db.delete(reminder)
        await db.flush()
        return True


contact_reminder_repo = ContactReminderRepository()
