from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inquiry_history import InquiryHistory


class InquiryHistoryRepository:

    @staticmethod
    async def create(db: AsyncSession, **kwargs) -> InquiryHistory:
        """Insert a new inquiry history entry."""
        entry = InquiryHistory(**kwargs)
        db.add(entry)
        await db.flush()
        return entry

    @staticmethod
    async def get_by_contact(
        db: AsyncSession, contact_id: int, limit: int = 20
    ) -> list[InquiryHistory]:
        """Fetch inquiry history for a contact, ordered by archived_at DESC."""
        result = await db.execute(
            select(InquiryHistory)
            .where(InquiryHistory.contact_id == contact_id)
            .order_by(InquiryHistory.archived_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


inquiry_history_repo = InquiryHistoryRepository()
