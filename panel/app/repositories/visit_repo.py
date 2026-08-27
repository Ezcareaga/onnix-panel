"""Repository for the `visits` table (M6.2 — Plan 114-01 §2.3).

Pure CRUD. NO business logic, NO commit (caller — VisitService — commits).
Mirrors the static-method-with-singleton pattern used by ContactRepository
and LeadEventRepository.
"""
from datetime import datetime

from sqlalchemy import select, exists, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.visit import Visit


class VisitRepo:
    @staticmethod
    async def get_by_id(db: AsyncSession, visit_id: int) -> Visit | None:
        result = await db.execute(select(Visit).where(Visit.id == visit_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_contact(
        db: AsyncSession, contact_id: int,
    ) -> list[Visit]:
        """SELECT * FROM visits WHERE contact_id=:cid ORDER BY scheduled_at DESC.

        Eagerly loads `Visit.property` so the UI can render
        "{property_type} — {city}" without an N+1 fetch (Phase 116 UAT fix).
        """
        result = await db.execute(
            select(Visit)
            .where(Visit.contact_id == contact_id)
            .options(selectinload(Visit.property))
            .order_by(Visit.scheduled_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def has_active_for_contact(
        db: AsyncSession, contact_id: int,
    ) -> bool:
        """SELECT EXISTS(SELECT 1 FROM visits WHERE contact_id=:cid AND status='scheduled')."""
        result = await db.execute(
            select(
                exists().where(
                    Visit.contact_id == contact_id,
                    Visit.status == "scheduled",
                )
            )
        )
        return bool(result.scalar())

    @staticmethod
    async def insert(
        db: AsyncSession,
        *,
        contact_id: int,
        property_id: int | None,
        agent_user_id: int | None,
        scheduled_at: datetime,
        status: str,
        source: str,
        notes: str | None,
    ) -> Visit:
        """INSERT … RETURNING; DOES NOT COMMIT — caller (service) commits."""
        visit = Visit(
            contact_id=contact_id,
            property_id=property_id,
            agent_user_id=agent_user_id,
            scheduled_at=scheduled_at,
            status=status,
            source=source,
            notes=notes,
        )
        db.add(visit)
        await db.flush()
        return visit

    @staticmethod
    async def update_status(
        db: AsyncSession,
        *,
        visit_id: int,
        new_status: str,
    ) -> Visit | None:
        """UPDATE visits SET status=:new_status WHERE id=:visit_id RETURNING …; no commit.

        Returns None if the row doesn't exist (don't raise — caller decides).
        """
        result = await db.execute(
            update(Visit)
            .where(Visit.id == visit_id)
            .values(status=new_status)
            .returning(Visit)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        # Refresh ORM identity so callers see the new status on the existing
        # instance if they had one cached. Use flush to ensure the UPDATE
        # is sent and timestamps (updated_at via trigger) are persisted.
        await db.flush()
        # Re-fetch via PK for a fully-populated, attached instance.
        fresh = await db.execute(select(Visit).where(Visit.id == visit_id))
        return fresh.scalar_one_or_none()


visit_repo = VisitRepo()
