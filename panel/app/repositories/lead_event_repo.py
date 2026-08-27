from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.lead_event import LeadEvent
from app.tz import pyt_day_start

class LeadEventRepository:
    @staticmethod
    async def create(db: AsyncSession, contact_id: int | None, event_type: str,
                     old_status: str | None, new_status: str | None,
                     triggered_by: str, metadata: dict | None = None) -> LeadEvent:
        """Inserta un lead_event.

        ``contact_id`` puede ser None desde la mig 046: es el dead-letter de
        una consulta de InfoCasas que llegó sin teléfono ni email y por lo
        tanto no tiene contacto. Todas las lecturas de esta clase filtran por
        un contact_id concreto, así que esas filas quedan fuera de las fichas
        y sólo las ve el dedup del poll.
        """
        event = LeadEvent(
            contact_id=contact_id,
            event_type=event_type,
            old_status=old_status,
            new_status=new_status,
            triggered_by=triggered_by,
            event_metadata=metadata or {},
        )
        db.add(event)
        await db.flush()
        return event

    @staticmethod
    async def count_by_type_this_week(db: AsyncSession) -> dict:
        """Eventos por tipo en los ultimos 7 dias CALENDARIO paraguayos.

        El borde es medianoche PYT, no ``CURRENT_DATE`` (que es UTC): la
        ventana empieza y termina donde el usuario ve que empieza el dia.
        """
        result = await db.execute(
            text("""
                SELECT event_type, COUNT(*)
                FROM lead_events
                WHERE created_at >= :since
                GROUP BY event_type
            """),
            {"since": pyt_day_start(days_ago=7)},
        )
        return dict(result.all())

    @staticmethod
    async def get_by_contact(db: AsyncSession, contact_id: int) -> list[LeadEvent]:
        result = await db.execute(
            select(LeadEvent)
            .where(LeadEvent.contact_id == contact_id)
            .order_by(LeadEvent.created_at.desc())
            .limit(20)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_detail_views(db: AsyncSession, contact_id: int) -> list[LeadEvent]:
        result = await db.execute(
            select(LeadEvent)
            .where(
                LeadEvent.contact_id == contact_id,
                LeadEvent.event_type == "detail_view",
            )
            .order_by(LeadEvent.created_at.desc())
            .limit(50)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_all_events(db: AsyncSession, contact_id: int) -> list[LeadEvent]:
        result = await db.execute(
            select(LeadEvent)
            .where(LeadEvent.contact_id == contact_id)
            .order_by(LeadEvent.created_at.asc())
            .limit(100)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_recent_for_activity(
        db: AsyncSession, contact_id: int, limit: int = 30
    ) -> list[LeadEvent]:
        """Return the most recent lead events for the conversation activity panel.

        Ordered DESC (newest first) so the UI shows the latest activity at top.
        Covers all event types — the rendering layer decides which ones to
        surface with descriptive labels.
        """
        result = await db.execute(
            select(LeadEvent)
            .where(LeadEvent.contact_id == contact_id)
            .order_by(LeadEvent.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

lead_event_repo = LeadEventRepository()
