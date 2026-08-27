from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func, or_, not_, and_, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.conversation import Conversation
from app.models.contact import Contact
from app.models.message import Message

# Una conversacion esta TRABADA cuando el ultimo mensaje es entrante y llego
# hace mas de STUCK_MINUTES. La ventana de 24 horas deja afuera el abandono
# historico —el cliente escribio hace tres semanas y no volvio—, que no es
# trabajo pendiente de hoy.
STUCK_MINUTES = 10
STUCK_WINDOW = timedelta(hours=24)


def _del_ultimo_mensaje(columna):
    """Subconsulta correlacionada: ``columna`` del ultimo mensaje de la conversacion."""
    return (
        select(columna)
        .where(Message.conversation_id == Conversation.id)
        .order_by(Message.created_at.desc())
        .limit(1)
        .correlate(Conversation)
        .scalar_subquery()
    )


class ConversationRepository:

    # Correlated subquery: latest message body preview (first 80 chars)
    _last_msg_preview = _del_ultimo_mensaje(
        func.substr(Message.body, 1, 80)
    ).label("last_message_preview")

    # Correlated subquery: latest message direction (inbound / outbound)
    _last_msg_direction = _del_ultimo_mensaje(Message.direction).label(
        "last_message_direction"
    )

    @staticmethod
    def stuck_clause():
        """El predicado de «conversacion trabada», en un solo lugar.

        Lo consultan dos pantallas: el KPI de /stats/health, que las cuenta, y
        la lista de /conversations, que las muestra. Escrito dos veces se
        separa —y un contador que dice 4 sobre una lista que muestra 6 es peor
        que no tener el contador.

        El corte se calcula en Python y no con ``NOW()`` para que un test
        pueda afirmar resultados exactos.
        """
        ahora = datetime.now(timezone.utc)
        return and_(
            Conversation.is_open.is_(True),
            _del_ultimo_mensaje(Message.direction) == "inbound",
            _del_ultimo_mensaje(Message.created_at)
            < ahora - timedelta(minutes=STUCK_MINUTES),
            _del_ultimo_mensaje(Message.created_at) >= ahora - STUCK_WINDOW,
        )

    @staticmethod
    def _filtros_de_lista(agent_filter: int | None, channel: str | None, stuck: bool):
        """El WHERE que comparten la lista y la busqueda.

        Estaba escrito dos veces —una en get_with_contacts y otra en
        search_with_contacts— con el mismo bloque de fantasmas y los mismos
        dos filtros copiados. Cada filtro nuevo habia que acordarse de
        agregarlo en los dos lados.
        """
        # Fantasmas: sin mensajes y sin marca de ultimo mensaje.
        clausula = not_(
            and_(
                Conversation.message_count == 0,
                Conversation.last_message_at.is_(None),
            )
        )
        if agent_filter is not None:
            clausula = and_(clausula, Contact.agent_user_id == agent_filter)
        if channel:
            clausula = and_(clausula, Conversation.channel == channel)
        if stuck:
            clausula = and_(clausula, ConversationRepository.stuck_clause())
        return clausula

    @staticmethod
    async def get_all(db: AsyncSession, limit: int = 50) -> list[Conversation]:
        """Get all conversations, most recent first."""
        result = await db.execute(
            select(Conversation)
            .order_by(Conversation.last_message_at.desc().nullslast(), Conversation.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_by_id(db: AsyncSession, conv_id: int) -> Conversation | None:
        result = await db.execute(
            select(Conversation).where(Conversation.id == conv_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_contact(db: AsyncSession, contact_id: int) -> list[Conversation]:
        result = await db.execute(
            select(Conversation)
            .where(Conversation.contact_id == contact_id)
            .order_by(Conversation.last_message_at.desc().nullslast())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_with_contacts(
        db: AsyncSession,
        limit: int = 50,
        offset: int = 0,
        agent_filter: int | None = None,
        channel: str | None = None,
        stuck: bool = False,
    ) -> list[dict]:
        """Get conversations with contact info and last message preview.

        Returns list of dicts with keys: conversation, contact_name, contact_phone,
        last_message_preview, last_message_direction.
        Eliminates the N+1 problem of fetching each contact individually.

        agent_filter: if provided, restrict to conversations whose contact is
        assigned to that user (contacts.agent_user_id = agent_filter).
        channel: if provided ('whatsapp' or 'telegram'), filter by conversation.channel.
        stuck: solo las trabadas — ver ConversationRepository.stuck_clause().
        offset: number of rows to skip (for load-more pagination).
        """
        repo = ConversationRepository
        where_clause = repo._filtros_de_lista(agent_filter, channel, stuck)
        result = await db.execute(
            select(
                Conversation,
                Contact.name,
                Contact.phone,
                repo._last_msg_preview,
                repo._last_msg_direction,
            )
            .outerjoin(Contact, Conversation.contact_id == Contact.id)
            .where(where_clause)
            .order_by(
                Conversation.last_message_at.desc().nullslast(),
                Conversation.created_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        rows = result.all()
        return [
            {
                "conversation": row[0],
                "contact_name": row[1] or "Desconocido",
                "contact_phone": row[2] or "",
                "last_message_preview": (row[3] or "")[:60],
                "last_message_direction": row[4] or "",
            }
            for row in rows
        ]

    @staticmethod
    async def search_with_contacts(
        db: AsyncSession,
        query: str,
        limit: int = 50,
        offset: int = 0,
        agent_filter: int | None = None,
        channel: str | None = None,
        stuck: bool = False,
    ) -> list[dict]:
        """Search conversations by contact name or message body content.

        Uses func.unaccent() on both column and pattern for accent-insensitive
        matching. Returns same format as get_with_contacts.

        agent_filter: if provided, restrict results to the agent's assigned contacts.
        channel: if provided ('whatsapp' or 'telegram'), filter by conversation.channel.
        stuck: solo las trabadas — ver ConversationRepository.stuck_clause().
        offset: number of rows to skip (for load-more pagination).
        """
        pattern = f"%{query}%"

        # Subquery: conversation IDs that have a message body matching the query
        msg_subquery = (
            select(Message.conversation_id)
            .where(
                func.unaccent(Message.body).ilike(func.unaccent(pattern))
            )
            .distinct()
            .subquery()
        )

        repo = ConversationRepository
        search_clause = and_(
            repo._filtros_de_lista(agent_filter, channel, stuck),
            or_(
                func.unaccent(Contact.name).ilike(func.unaccent(pattern)),
                Conversation.id.in_(select(msg_subquery)),
            ),
        )
        result = await db.execute(
            select(
                Conversation,
                Contact.name,
                Contact.phone,
                repo._last_msg_preview,
                repo._last_msg_direction,
            )
            .outerjoin(Contact, Conversation.contact_id == Contact.id)
            .where(search_clause)
            .order_by(
                Conversation.last_message_at.desc().nullslast(),
                Conversation.created_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        rows = result.all()
        return [
            {
                "conversation": row[0],
                "contact_name": row[1] or "Desconocido",
                "contact_phone": row[2] or "",
                "last_message_preview": (row[3] or "")[:60],
                "last_message_direction": row[4] or "",
            }
            for row in rows
        ]

    @staticmethod
    async def get_demand_filter_rows(
        db: AsyncSession, days: int = 30,
    ) -> list[dict]:
        """Demanda bot — lo que la gente PIDE al bot (search_context.filtros).

        Una fila por conversación con filtros no vacíos actualizada dentro
        de la ventana. ``source`` es el canal (whatsapp/telegram).
        ``*_key`` con lower(unaccent(trim(...))) (regla 7) para agrupar
        variantes con/sin tilde; el spelling original queda para mostrar.
        """
        sql = text(
            """
            SELECT co.search_context->'filtros'->>'ciudad' AS city,
                   NULLIF(lower(unaccent(trim(
                       co.search_context->'filtros'->>'ciudad'))), '') AS city_key,
                   co.search_context->'filtros'->>'tipo' AS ptype,
                   NULLIF(lower(unaccent(trim(
                       co.search_context->'filtros'->>'tipo'))), '') AS ptype_key,
                   NULLIF(lower(trim(
                       co.search_context->'filtros'->>'operacion')), '') AS operation,
                   co.channel AS source
            FROM conversations co
            WHERE jsonb_exists(co.search_context, 'filtros')
              AND co.search_context->'filtros' <> '{}'::jsonb
              AND co.updated_at >= now() - make_interval(days => :days)
            """
        )
        result = await db.execute(sql, {"days": days})
        return [dict(r._mapping) for r in result]

    @staticmethod
    async def get_demand_filter_monthly_counts(
        db: AsyncSession, months: int = 6,
    ) -> list[dict]:
        """Serie mensual de busquedas al bot — [{month, n}] por mes calendario.

        Mismo universo que get_demand_filter_rows (conversaciones con
        filtros no vacios), agrupado por mes de updated_at. Caveat: una
        conversacion actualizada despues cambia de bucket — coherente con
        la ventana movil de la seccion Demanda.

        Mes CALENDARIO PARAGUAYO. El borde de la ventana vuelve a
        ``timestamptz`` con el mismo ``AT TIME ZONE``: dejado como
        ``timestamp`` pelado, Postgres lo leeria con el huso de la sesion
        (UTC) y la ventana quedaria tres horas corrida del agrupamiento.
        """
        sql = text(
            """
            SELECT date_trunc('month',
                       co.updated_at AT TIME ZONE 'America/Asuncion') AS month,
                   count(*) AS n
            FROM conversations co
            WHERE jsonb_exists(co.search_context, 'filtros')
              AND co.search_context->'filtros' <> '{}'::jsonb
              AND co.updated_at >= (
                      date_trunc('month', now() AT TIME ZONE 'America/Asuncion')
                      - make_interval(months => :back)
                  ) AT TIME ZONE 'America/Asuncion'
            GROUP BY 1
            """
        )
        result = await db.execute(sql, {"back": months - 1})
        return [dict(r._mapping) for r in result]


conversation_repo = ConversationRepository()
