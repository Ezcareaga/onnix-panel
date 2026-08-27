"""Business logic for contact reminders (C2.2).

Validation rules:
  - due_at must be in the future (at creation time).
  - note must not be empty.
  - max 20 open reminders per contact.

No SQL in this module — all DB calls delegate to contact_reminder_repo.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact_reminder import ContactReminder
from app.repositories.contact_reminder_repo import contact_reminder_repo
from app.repositories.contact_repo import contact_repo

logger = logging.getLogger(__name__)

_MAX_OPEN = 20


class ContactReminderService:

    @staticmethod
    async def create_reminder(
        db: AsyncSession,
        contact_id: int,
        user_id: int,
        due_at: datetime,
        note: str,
    ) -> tuple[ContactReminder | None, str | None]:
        """Validate and create a new reminder.

        Returns (reminder, None) on success, (None, error_message) on failure.
        """
        # Verify the contact exists
        contact = await contact_repo.get_by_id(db, contact_id)
        if contact is None:
            return None, "Contacto no encontrado"

        # due_at must be in the future
        now = datetime.now(timezone.utc)
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=timezone.utc)
        if due_at <= now:
            return None, "La fecha debe ser en el futuro"

        # note non-empty
        note = note.strip()
        if not note:
            return None, "La nota no puede estar vacía"

        # max 20 open reminders
        open_count = await contact_reminder_repo.count_open_for_contact(db, contact_id)
        if open_count >= _MAX_OPEN:
            return None, f"Límite de {_MAX_OPEN} recordatorios abiertos por contacto"

        reminder = await contact_reminder_repo.create(
            db, contact_id=contact_id, user_id=user_id, due_at=due_at, note=note
        )
        await db.commit()
        logger.info(
            "Reminder created: id=%d contact_id=%d user_id=%d due_at=%s",
            reminder.id, contact_id, user_id, due_at.isoformat(),
        )
        return reminder, None

    @staticmethod
    async def list_reminders(
        db: AsyncSession, contact_id: int
    ) -> list[ContactReminder]:
        return await contact_reminder_repo.list_by_contact(db, contact_id)

    @staticmethod
    async def list_due(db: AsyncSession) -> list[ContactReminder]:
        """All overdue open reminders (for dashboard / cron use)."""
        return await contact_reminder_repo.list_due(db)

    @staticmethod
    async def mark_done(
        db: AsyncSession,
        reminder_id: int,
        user_id: int,
    ) -> tuple[ContactReminder | None, str | None]:
        reminder = await contact_reminder_repo.get_by_id(db, reminder_id)
        if reminder is None:
            return None, "Recordatorio no encontrado"
        if reminder.done_at is not None:
            return reminder, None  # idempotent
        updated = await contact_reminder_repo.mark_done(db, reminder_id)
        await db.commit()
        logger.info(
            "Reminder done: id=%d contact_id=%d user_id=%d",
            reminder_id, reminder.contact_id, user_id,
        )
        return updated, None

    @staticmethod
    async def delete_reminder(
        db: AsyncSession,
        reminder_id: int,
        user_id: int,
    ) -> tuple[bool, str | None]:
        reminder = await contact_reminder_repo.get_by_id(db, reminder_id)
        if reminder is None:
            return False, "Recordatorio no encontrado"
        await contact_reminder_repo.delete(db, reminder_id)
        await db.commit()
        logger.info(
            "Reminder deleted: id=%d contact_id=%d user_id=%d",
            reminder_id, reminder.contact_id, user_id,
        )
        return True, None

    @staticmethod
    async def get_overdue_contacts(
        db: AsyncSession, contact_ids: list[int]
    ) -> set[int]:
        """Return contact_ids that have at least one overdue open reminder."""
        return await contact_reminder_repo.count_overdue_for_contacts(db, contact_ids)


contact_reminder_service = ContactReminderService()
