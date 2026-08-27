from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func, cast, Date, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message
from app.tz import PYT_SQL_ZONE, pyt_day_start

class MessageRepository:
    @staticmethod
    async def get_by_conversation(db: AsyncSession, conversation_id: int, limit: int = 200) -> list[Message]:
        # Subquery: get the latest N messages by DESC, then order ASC for chat display
        latest = (
            select(Message.id)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
            .subquery()
        )
        result = await db.execute(
            select(Message)
            .where(Message.id.in_(select(latest.c.id)))
            .order_by(Message.created_at.asc(), Message.id.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def create(
        db: AsyncSession,
        conversation_id: int,
        contact_id: int,
        direction: str,
        sender_type: str,
        body: str,
        content: str,
        external_id: str,
        status: str = "sent",
        intent: str | None = None,
        created_at: datetime | None = None,
    ) -> Message:
        """Insert a new message and return it with DB-generated fields.

        Parameters ``intent`` and ``created_at`` are optional — when provided
        they are set on the Message object, otherwise the DB defaults apply.
        """
        msg = Message(
            conversation_id=conversation_id,
            contact_id=contact_id,
            direction=direction,
            sender_type=sender_type,
            body=body,
            content=content,
            external_id=external_id,
            status=status,
        )
        if intent is not None:
            msg.intent = intent
        if created_at is not None:
            msg.created_at = created_at
        db.add(msg)
        await db.flush()
        await db.refresh(msg)
        # Atomically increment message_count on the parent conversation
        await db.execute(
            text(
                "UPDATE conversations SET message_count = message_count + 1,"
                " last_message_at = NOW()"
                " WHERE id = :conv_id"
            ),
            {"conv_id": conversation_id},
        )
        return msg

    @staticmethod
    async def get_last_inbound_at(db: AsyncSession, contact_id: int) -> datetime | None:
        """Return the timestamp of the most recent inbound message from a contact.

        Used as fallback when contact.last_user_message_at is NULL (legacy contacts
        that predate the field being written by the bot).
        """
        result = await db.execute(
            select(func.max(Message.created_at))
            .where(
                Message.contact_id == contact_id,
                Message.direction == "inbound",
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_external_id(db: AsyncSession, external_id: str) -> "Message | None":
        """Return a Message by its external_id (MessageSid), or None if not found."""
        result = await db.execute(
            select(Message).where(Message.external_id == external_id)
        )
        return result.scalars().first()

    @staticmethod
    async def update_status(
        db: AsyncSession,
        msg: "Message",
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> "Message":
        """Update the delivery status of a message in-place and flush.

        Optionally sets error_code / error_message for failed/undelivered states.
        """
        msg.status = status
        if error_code is not None:
            msg.error_code = error_code
        if error_message is not None:
            msg.error_message = error_message
        await db.flush()
        return msg

    @staticmethod
    async def count_recent(db: AsyncSession, hours: int = 24) -> int:
        """Count messages in the last N hours."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        result = await db.execute(
            select(func.count()).select_from(Message).where(
                Message.created_at > cutoff
            )
        )
        return result.scalar() or 0

    @staticmethod
    async def count_per_day(
        db: AsyncSession, days: int = 7
    ) -> list[dict]:
        """Count messages per day for last N days.

        Returns list of {"day": "YYYY-MM-DD", "count": int} sorted ascending.
        Days with no messages NO aparecen: los rellena StatsService, que es
        donde vive el computo.

        El corte es el arranque del dia PARAGUAYO de hace ``days - 1`` dias,
        asi la ventana son exactamente ``days`` dias de calendario. Con un
        ``now - timedelta(days=days)`` eran ``days + 1`` fechas distintas: un
        pedazo del dia mas viejo entraba y la card decia otra cosa que el titulo.
        """
        cutoff = pyt_day_start(days_ago=days - 1)
        result = await db.execute(
            select(
                # cast(created_at, Date) cortaba el dia en UTC: todo lo
                # posterior a las 21:00 PYT caia en la fecha siguiente.
                cast(func.timezone(PYT_SQL_ZONE, Message.created_at), Date).label("day"),
                func.count().label("cnt"),
            )
            .where(Message.created_at >= cutoff)
            .group_by("day")
            .order_by("day")
        )
        return [{"day": str(row.day), "count": row.cnt} for row in result]

message_repo = MessageRepository()
