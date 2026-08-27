from datetime import datetime, timezone
from typing import TypedDict
from sqlalchemy import select, func, text, case, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.contact import Contact
from app.tz import pyt_day_start


class ContactUpdateFields(TypedDict, total=False):
    """Typed fields accepted by ContactRepository.update()."""

    name: str
    phone: str
    phone_normalized: str
    email: str | None
    preferences: dict
    property_id: int | None


class ContactRepository:
    @staticmethod
    async def count_by_status(db: AsyncSession) -> dict:
        """Contactos vivos por estado, sin import:excel y sin los eliminados.

        `deleted` es el sentinel del borrado blando (regla 3 del CLAUDE.md):
        un contacto eliminado no es un lead. Contarlo hacia que «Total leads»
        del dashboard fuera un numero distinto del total del embudo, que
        nunca lo conto — dos totales distintos en la misma columna.

        La columna es NOT NULL DEFAULT 'new' con CHECK sobre VALID_STATUSES +
        'deleted', asi que el `!=` no puede tragarse una fila con NULL.
        """
        result = await db.execute(
            select(Contact.status, func.count())
            .where(
                Contact.source != "import:excel",
                Contact.status != "deleted",
            )
            .group_by(Contact.status)
        )
        return dict(result.all())

    @staticmethod
    async def get_hot_leads(db: AsyncSession, limit: int = 50) -> list[Contact]:
        result = await db.execute(
            select(Contact)
            .where(
                Contact.source != "import:excel",
                Contact.status.in_(["interested", "visit_scheduled", "new"]),
            )
            .order_by(Contact.last_activity_at.desc().nullslast(), Contact.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_by_id(db: AsyncSession, contact_id: int) -> Contact | None:
        result = await db.execute(select(Contact).where(Contact.id == contact_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def count_today(db: AsyncSession) -> int:
        result = await db.execute(
            select(func.count()).select_from(Contact).where(
                Contact.source != "import:excel",
                # current_date() corta el dia en UTC: entre las 21:00 y las
                # 23:59 PYT «hoy» ya era manana y el contador arrancaba de cero.
                Contact.created_at >= pyt_day_start(),
            )
        )
        return result.scalar() or 0

    @staticmethod
    async def count_by_status_for_source(db: AsyncSession, source: str) -> dict:
        """Count contacts grouped by status for a specific source."""
        result = await db.execute(
            select(Contact.status, func.count())
            .where(Contact.source == source)
            .group_by(Contact.status)
        )
        return dict(result.all())

    @staticmethod
    async def count_by_source(db: AsyncSession) -> dict:
        result = await db.execute(
            select(Contact.source, func.count()).group_by(Contact.source)
        )
        return dict(result.all())

    @staticmethod
    async def weekly_evolution(db: AsyncSession, days: int = 7) -> list[tuple]:
        """Contactos por dia calendario PARAGUAYO, ventana de *days* dias.

        El bucket y el borde de la ventana usan el mismo huso: el borde
        viaja como ``timestamptz`` desde Python, no como ``CURRENT_DATE``.
        """
        result = await db.execute(
            text(
                "SELECT (created_at AT TIME ZONE 'America/Asuncion')::date AS day, "
                "COUNT(*) FROM contacts "
                "WHERE created_at >= :since "
                "GROUP BY day ORDER BY day"
            ),
            {"since": pyt_day_start(days_ago=int(days) - 1)},
        )
        return list(result.all())

    @staticmethod
    def _build_filter_clause(
        q,
        status: str | None = None,
        source: str | None = None,
        search: str | None = None,
        phone_filter: str | None = None,
        agent_user_id: int | None = None,
    ):
        """Apply shared WHERE clauses to a contacts query.

        Called by both get_all() and count_all() to avoid duplicating
        filter logic between the two methods.

        agent_user_id: when provided, restrict results to contacts where
        contacts.agent_user_id == agent_user_id (feat/authz ROLE-agent-list).
        """
        if status:
            q = q.where(Contact.status == status)
        if source:
            q = q.where(Contact.source == source)
        if phone_filter == "with":
            q = q.where(Contact.phone.isnot(None))
        elif phone_filter == "without":
            q = q.where(Contact.phone.is_(None))
        if search:
            like = f"%{search}%"
            # Regla 7: SIEMPRE unaccent() para búsquedas en español.
            # Aplicado a ambos lados (columna y término) para que
            # "asuncion" encuentre "Asunción". func.unaccent() coincide
            # con el patrón de conversation_repo.search_with_contacts.
            q = q.where(or_(
                func.unaccent(Contact.name).ilike(func.unaccent(like)),
                Contact.phone.ilike(like),
                Contact.email.ilike(like),
            ))
        if agent_user_id is not None:
            q = q.where(Contact.agent_user_id == agent_user_id)
        return q

    @staticmethod
    async def get_all(
        db: AsyncSession,
        status: str | None = None,
        source: str | None = None,
        search: str | None = None,
        phone_filter: str | None = None,
        limit: int = 25,
        offset: int = 0,
        agent_user_id: int | None = None,
    ) -> list[Contact]:
        q = select(Contact)
        q = ContactRepository._build_filter_clause(
            q, status, source, search, phone_filter, agent_user_id
        )
        has_phone = case((Contact.phone.isnot(None), 0), else_=1)
        deleted_last = case((Contact.status == "deleted", 1), else_=0)
        q = q.order_by(deleted_last, has_phone, Contact.created_at.desc()).limit(limit).offset(offset)
        result = await db.execute(q)
        return list(result.scalars().all())

    @staticmethod
    async def count_all(
        db: AsyncSession,
        status: str | None = None,
        source: str | None = None,
        search: str | None = None,
        phone_filter: str | None = None,
        agent_user_id: int | None = None,
    ) -> int:
        q = select(func.count()).select_from(Contact)
        q = ContactRepository._build_filter_clause(
            q, status, source, search, phone_filter, agent_user_id
        )
        result = await db.execute(q)
        return result.scalar() or 0

    @staticmethod
    async def get_for_export(
        db: AsyncSession,
        status: str | None = None,
        source: str | None = None,
        search: str | None = None,
        phone_filter: str | None = None,
        agent_user_id: int | None = None,
        max_rows: int = 20_000,
    ) -> list[Contact]:
        """Fetch contacts for CSV export — same filters as get_all, no pagination."""
        q = select(Contact)
        q = ContactRepository._build_filter_clause(
            q, status, source, search, phone_filter, agent_user_id
        )
        has_phone = case((Contact.phone.isnot(None), 0), else_=1)
        deleted_last = case((Contact.status == "deleted", 1), else_=0)
        q = q.order_by(deleted_last, has_phone, Contact.created_at.desc()).limit(max_rows)
        result = await db.execute(q)
        return list(result.scalars().all())

    @staticmethod
    async def get_by_phone(db: AsyncSession, phone: str) -> "Contact | None":
        result = await db.execute(select(Contact).where(Contact.phone == phone))
        return result.scalar_one_or_none()

    @staticmethod
    async def create(
        db: AsyncSession,
        name: str,
        phone: str,
        email: str | None = None,
        source: str = "manual",
        status: str = "new",
        preferences: dict | None = None,
        property_id: int | None = None,
    ) -> "Contact":
        now = datetime.now(timezone.utc)
        contact = Contact(
            name=name,
            phone=phone,
            phone_normalized=phone,
            email=email,
            source=source,
            status=status,
            preferences=preferences or {},
            property_id=property_id,
            created_at=now,
            updated_at=now,
        )
        db.add(contact)
        await db.flush()
        return contact

    @staticmethod
    async def update(
        db: AsyncSession, contact_id: int, fields: ContactUpdateFields
    ) -> "Contact | None":
        contact = await db.get(Contact, contact_id)
        if not contact:
            return None
        for key, value in fields.items():
            setattr(contact, key, value)
        contact.updated_at = datetime.now(timezone.utc)
        await db.flush()
        return contact

    @staticmethod
    async def update_status(db: AsyncSession, contact_id: int, new_status: str) -> Contact | None:
        contact = await db.get(Contact, contact_id)
        if not contact:
            return None
        contact.status = new_status
        await db.flush()
        return contact

contact_repo = ContactRepository()
