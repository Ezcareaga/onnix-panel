from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.contact_note import ContactNote


class ContactNoteRepository:
    @staticmethod
    async def create(db: AsyncSession, contact_id: int, user_id: int | None, content: str) -> ContactNote:
        note = ContactNote(
            contact_id=contact_id,
            user_id=user_id,
            content=content,
        )
        db.add(note)
        await db.flush()
        return note

    @staticmethod
    async def get_by_contact(db: AsyncSession, contact_id: int) -> list[ContactNote]:
        result = await db.execute(
            select(ContactNote)
            .where(ContactNote.contact_id == contact_id)
            .order_by(ContactNote.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_by_id(db: AsyncSession, note_id: int) -> ContactNote | None:
        return await db.get(ContactNote, note_id)

    @staticmethod
    async def update(db: AsyncSession, note_id: int, content: str) -> ContactNote | None:
        note = await db.get(ContactNote, note_id)
        if note is None:
            return None
        note.content = content
        note.updated_at = datetime.now(timezone.utc)
        await db.flush()
        return note

    @staticmethod
    async def delete(db: AsyncSession, note_id: int) -> bool:
        note = await db.get(ContactNote, note_id)
        if note is None:
            return False
        await db.delete(note)
        await db.flush()
        return True


contact_note_repo = ContactNoteRepository()
