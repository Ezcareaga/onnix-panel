from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# M6.1 (Plan 111-03 §6.1) — predicates por tab para /leads admin.
# - "leads"       → contacts con status new/bot_replied SIN agent asignado
# - "interesados" → contacts con status interested SIN agent asignado
# - "asignados"   → contacts con agent_user_id IS NOT NULL (any status, ROLE-10)
TAB_WHERE_CLAUSES: dict[str, str] = {
    "leads":         "c.status IN ('new', 'bot_replied') AND c.agent_user_id IS NULL",
    "interesados":   "c.status = 'interested' AND c.agent_user_id IS NULL",
    "asignados":     "c.agent_user_id IS NOT NULL",
    "sin_respuesta": "c.status = 'no_response' AND c.agent_user_id IS NULL",
}


_BASE_COLUMNS = """
    c.id, c.name, c.phone, c.email, c.source, c.status,
    c.agent_user_id, c.agent_assigned_at, c.agent_seen_at,
    c.created_at, c.last_activity_at, c.property_id, c.consulta_date,
    c.first_message,
    p.title as property_title, p.city as property_city,
    p.neighborhood as property_neighborhood,
    p.price_usd as property_price, p.operation as property_operation,
    ip.title as ic_title, ip.city as ic_city,
    ip.price_sale as ic_price_sale, ip.price_rent as ic_price_rent,
    ip.currency_sale as ic_currency_sale, ip.currency_rent as ic_currency_rent,
    ip.infocasas_ref as ic_ref,
    ip.url as ic_url,
    p.url as property_url,
    (SELECT conv.id FROM conversations conv WHERE conv.contact_id = c.id ORDER BY conv.created_at DESC LIMIT 1) as conversation_id,
    (SELECT conv2.search_context FROM conversations conv2 WHERE conv2.contact_id = c.id ORDER BY conv2.created_at DESC LIMIT 1) as search_context,
    CASE WHEN c.source = 'infocasas' AND c.preferences->>'ic_type' = 'reenviada' THEN false
         WHEN c.source = 'infocasas' THEN true
         ELSE NULL END as is_direct_ic
    ,EXISTS (SELECT 1 FROM infocasas_inquiry_history ih WHERE ih.contact_id = c.id) as has_inquiry_history
"""

_BASE_FROM = """
    FROM contacts c
    LEFT JOIN properties p ON c.property_id = p.id
    LEFT JOIN infocasas_properties ip
        ON ip.infocasas_ref = c.infocasas_ref AND c.source = 'infocasas'
"""

_EXCLUDE_EXCEL = "c.source != 'import:excel'"


def _build_where(source: str | None, status: str | None) -> tuple[str, dict]:
    """Build parameterized WHERE clauses for lead queries."""
    clauses: list[str] = []
    params: dict = {}
    if source:
        clauses.append("AND c.source = :source")
        params["source"] = source
    if status:
        clauses.append("AND c.status = :status")
        params["status"] = status
    return " ".join(clauses), params


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards so user input matches literally.

    PostgreSQL's default LIKE/ILIKE escape character is backslash.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_tab_filters(
    q: str | None, source: str | None, agent_id: int | None,
) -> tuple[str, dict]:
    """LEADS-03 — extra parameterized clauses ANDed over TAB_WHERE_CLAUSES.

    - q        → unaccent(name) ILIKE unaccent(pattern) OR phone LIKE pattern
                 (regla 7: SIEMPRE unaccent para búsquedas en español)
    - source   → exact match (covers import:* values too — they are stored
                 verbatim, e.g. 'import:excel')
    - agent_id → c.agent_user_id = :agent (filtro de asesor, vista admin)

    Everything is parameterized — NEVER interpolate user input into SQL.
    """
    clauses: list[str] = []
    params: dict = {}
    if q:
        clauses.append(
            "(unaccent(c.name) ILIKE unaccent(:q_like)"
            " OR c.phone LIKE :q_like)"
        )
        params["q_like"] = f"%{_escape_like(q)}%"
    if source:
        clauses.append("c.source = :source_f")
        params["source_f"] = source
    if agent_id is not None:
        clauses.append("c.agent_user_id = :agent_id_f")
        params["agent_id_f"] = agent_id
    return "".join(f" AND {c}" for c in clauses), params


class LeadRepository:

    @staticmethod
    async def get_interested(db: AsyncSession) -> list[dict]:
        """Fetch interested leads with property/IC joins, max 50."""
        sql = text(
            f"SELECT {_BASE_COLUMNS} {_BASE_FROM}"
            f" WHERE {_EXCLUDE_EXCEL}"
            "  AND c.status = 'interested'"
            " ORDER BY c.last_activity_at DESC NULLS LAST, c.created_at DESC"
            " LIMIT 50"
        )
        result = await db.execute(sql)
        return [dict(r._mapping) for r in result]

    @staticmethod
    async def get_all(
        db: AsyncSession,
        source: str | None = None,
        status: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> list[dict]:
        """Fetch all leads with optional filters, paginated."""
        filter_clause, params = _build_where(source, status)
        params["limit"] = limit
        params["offset"] = offset
        sql = text(
            f"SELECT {_BASE_COLUMNS} {_BASE_FROM}"
            f" WHERE {_EXCLUDE_EXCEL} {filter_clause}"
            " ORDER BY (c.status = 'deleted') ASC, c.last_activity_at DESC NULLS LAST, c.created_at DESC"
            " LIMIT :limit OFFSET :offset"
        )
        result = await db.execute(sql, params)
        return [dict(r._mapping) for r in result]

    @staticmethod
    async def count(
        db: AsyncSession,
        source: str | None = None,
        status: str | None = None,
    ) -> int:
        """Count leads with optional filters."""
        filter_clause, params = _build_where(source, status)
        sql = text(
            f"SELECT COUNT(*) FROM contacts c"
            f" WHERE {_EXCLUDE_EXCEL} {filter_clause}"
        )
        result = await db.execute(sql, params)
        return result.scalar() or 0

    @staticmethod
    async def get_lead_with_property(
        db: AsyncSession, contact_id: int
    ) -> dict | None:
        """Fetch a single lead with joined property/IC data."""
        sql = text(
            f"SELECT {_BASE_COLUMNS} {_BASE_FROM}"
            f" WHERE {_EXCLUDE_EXCEL} AND c.id = :contact_id"
        )
        result = await db.execute(sql, {"contact_id": contact_id})
        row = result.first()
        if row is None:
            return None
        return dict(row._mapping)

    # get_for_export fue eliminado (Chunk 2 LEADS-03): el export de /leads
    # ahora usa get_by_tab con limit grande — mismo WHERE-building que la
    # página, vocabulario M6.1 (ver lead_service.list_leads_for_export).

    @staticmethod
    async def get_by_tab(
        db: AsyncSession,
        tab: str,
        agent_filter: int | None = None,
        limit: int = 25,
        offset: int = 0,
        q: str | None = None,
        source: str | None = None,
        agent_id: int | None = None,
    ) -> list[dict]:
        """Fetch leads filtered by M6.1 tab predicate.

        Plan 111-03 §6.1. `agent_filter` is reserved for Plan 111-04
        (vista agent "Mis Asignados"): cuando se pasa un user_id,
        agrega AND c.agent_user_id = :agent_filter al WHERE.

        LEADS-03: `q`/`source`/`agent_id` add parameterized search/filter
        clauses on top of the tab predicate (see _build_tab_filters).
        `agent_id` is the admin-picked asesor filter; `agent_filter` is the
        role-forced one — both compose if present.
        """
        if tab not in TAB_WHERE_CLAUSES:
            raise ValueError(f"Unknown leads tab: {tab!r}")
        base_where = TAB_WHERE_CLAUSES[tab]
        params: dict = {"limit": limit, "offset": offset}
        agent_clause = ""
        if agent_filter is not None:
            agent_clause = " AND c.agent_user_id = :agent_filter"
            params["agent_filter"] = agent_filter
        filter_clause, filter_params = _build_tab_filters(q, source, agent_id)
        params.update(filter_params)
        if tab == "leads":
            # «Nuevos» es bandeja de entrada, no cola de trabajo (decisión de
            # Ez, 2026-08-24). 18aad2c la había puesto ASC a propósito —
            # LEADS-01, "el que más tiempo lleva esperando va primero"— y ese
            # ASC hacía que el lead más reciente saliera octavo de nueve y que
            # una consulta nueva, al subir last_activity_at, EMPUJARA al lead
            # al fondo. En un negocio donde contesta primero el que se lleva la
            # operación, eso ordena al revés de lo que importa.
            #
            # Riesgo que se acepta al invertirlo: con más de una página de
            # «Nuevos», el lead que más espera deja de estar arriba y puede
            # caerse de la página 1. Lo que lo compensa hoy: el semáforo de
            # urgencia (waiting_bucket, lead_item.html) sigue pintando la
            # espera en cada fila, y la cola de trabajo real —«Sin respuesta»,
            # 872 filas— conserva el ASC justamente por eso.
            order_by = (
                " ORDER BY c.last_activity_at DESC NULLS LAST,"
                "          c.created_at DESC"
            )
        elif tab == "sin_respuesta":
            # LEADS-01 — cola de trabajo: el lead que MÁS tiempo lleva
            # esperando va primero. Acá el ASC SÍ es lo que se quiere: es un
            # backlog de recuperación, no una bandeja; nadie espera un evento
            # nuevo y el trabajo se hace del más viejo al más nuevo.
            order_by = (
                " ORDER BY c.last_activity_at ASC NULLS LAST,"
                "          c.created_at ASC"
            )
        else:
            order_by = (
                " ORDER BY (c.status = 'deleted') ASC,"
                "          c.last_activity_at DESC NULLS LAST,"
                "          c.created_at DESC"
            )
        sql = text(
            f"SELECT {_BASE_COLUMNS} {_BASE_FROM}"
            f" WHERE {_EXCLUDE_EXCEL} AND ({base_where}){agent_clause}{filter_clause}"
            f"{order_by}"
            " LIMIT :limit OFFSET :offset"
        )
        result = await db.execute(sql, params)
        return [dict(r._mapping) for r in result]

    @staticmethod
    async def count_by_tab(
        db: AsyncSession,
        tab: str,
        agent_filter: int | None = None,
        q: str | None = None,
        source: str | None = None,
        agent_id: int | None = None,
    ) -> int:
        """Count leads matching the given tab predicate (+LEADS-03 filters)."""
        if tab not in TAB_WHERE_CLAUSES:
            raise ValueError(f"Unknown leads tab: {tab!r}")
        base_where = TAB_WHERE_CLAUSES[tab]
        params: dict = {}
        agent_clause = ""
        if agent_filter is not None:
            agent_clause = " AND c.agent_user_id = :agent_filter"
            params["agent_filter"] = agent_filter
        filter_clause, filter_params = _build_tab_filters(q, source, agent_id)
        params.update(filter_params)
        sql = text(
            "SELECT COUNT(*) FROM contacts c"
            f" WHERE {_EXCLUDE_EXCEL} AND ({base_where}){agent_clause}{filter_clause}"
        )
        result = await db.execute(sql, params)
        return result.scalar() or 0

    @staticmethod
    async def get_demand_rows(db: AsyncSession, days: int = 30) -> list[dict]:
        """Demanda — una fila por consulta de lead dentro de la ventana.

        Dos fuentes en UNION ALL:
        - Leads InfoCasas (c.source='infocasas' JOIN infocasas_properties):
          ciudad/tipo/operación de la propiedad consultada en el portal.
        - Leads del bot con property_id (source whatsapp/telegram):
          la propiedad por la que dejaron lead.

        Las columnas ``*_key`` vienen con lower(unaccent(trim(...))) (regla 7)
        para que el service agrupe 'Asunción' y 'Asuncion' como la misma
        ciudad. ``city``/``ptype`` conservan el spelling original para mostrar.
        """
        sql = text(
            """
            SELECT ip.city AS city,
                   NULLIF(lower(unaccent(trim(ip.city))), '') AS city_key,
                   ip.property_type AS ptype,
                   NULLIF(lower(unaccent(trim(ip.property_type))), '') AS ptype_key,
                   NULLIF(lower(trim(ip.operation)), '') AS operation,
                   'infocasas' AS source
            FROM contacts c
            JOIN infocasas_properties ip ON ip.infocasas_ref = c.infocasas_ref
            WHERE c.source = 'infocasas'
              AND c.created_at >= now() - make_interval(days => :days)
            UNION ALL
            SELECT p.city,
                   NULLIF(lower(unaccent(trim(p.city))), ''),
                   p.property_type,
                   NULLIF(lower(unaccent(trim(p.property_type))), ''),
                   NULLIF(lower(trim(p.operation)), ''),
                   c.source
            FROM contacts c
            JOIN properties p ON p.id = c.property_id
            WHERE c.source IN ('whatsapp', 'telegram')
              AND c.created_at >= now() - make_interval(days => :days)
            """
        )
        result = await db.execute(sql, {"days": days})
        return [dict(r._mapping) for r in result]

    @staticmethod
    async def get_demand_monthly_counts(
        db: AsyncSession, months: int = 6,
    ) -> list[dict]:
        """Serie mensual de demanda de leads — [{month, n}] por mes calendario.

        Mismo universo que get_demand_rows (leads InfoCasas + leads bot con
        property_id), agrupado con date_trunc('month'). La ventana arranca
        en el primer dia del mes (months-1) hacia atras: meses calendario
        completos, el actual parcial. Meses sin datos NO vienen en el
        resultado — los rellena el service con 0.

        Mes CALENDARIO PARAGUAYO. El borde vuelve a ``timestamptz`` con el
        mismo ``AT TIME ZONE`` que el agrupamiento: como ``timestamp``
        pelado Postgres lo leeria en UTC y la ventana quedaria corrida.
        """
        sql = text(
            """
            SELECT date_trunc('month',
                       sub.created_at AT TIME ZONE 'America/Asuncion') AS month,
                   count(*) AS n
            FROM (
                SELECT c.created_at
                FROM contacts c
                JOIN infocasas_properties ip ON ip.infocasas_ref = c.infocasas_ref
                WHERE c.source = 'infocasas'
                  AND c.created_at >= (
                          date_trunc('month', now() AT TIME ZONE 'America/Asuncion')
                          - make_interval(months => :back)
                      ) AT TIME ZONE 'America/Asuncion'
                UNION ALL
                SELECT c.created_at
                FROM contacts c
                JOIN properties p ON p.id = c.property_id
                WHERE c.source IN ('whatsapp', 'telegram')
                  AND c.created_at >= (
                          date_trunc('month', now() AT TIME ZONE 'America/Asuncion')
                          - make_interval(months => :back)
                      ) AT TIME ZONE 'America/Asuncion'
            ) sub
            GROUP BY 1
            """
        )
        result = await db.execute(sql, {"back": months - 1})
        return [dict(r._mapping) for r in result]


lead_repo = LeadRepository()
