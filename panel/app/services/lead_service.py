import json
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.contact_repo import contact_repo
from app.repositories.lead_event_repo import lead_event_repo
from app.repositories.lead_repo import lead_repo, TAB_WHERE_CLAUSES
from app.models.contact import Contact
from app.constants import VALID_STATUSES

logger = logging.getLogger(__name__)

# Plan 111-03 — keys soportadas por list_leads_by_tab.
VALID_LEAD_TABS: frozenset[str] = frozenset(TAB_WHERE_CLAUSES.keys())

# LEADS-03 — hard cap del export xlsx: un export desbocado no puede OOMear
# el worker (mismo valor que tenía el viejo get_for_export).
EXPORT_MAX_ROWS = 10000

# LEADS-02 — nested keys reales de conversations.search_context['filtros']
# (en orden de render). NO existe 'zona'; variantes plurales se ignoran.
_SEARCH_CONTEXT_KEYS = ("tipo", "operacion", "ciudad", "barrio")


def summarize_search_context(ctx) -> str | None:
    """LEADS-02 — resumen corto del search_context de una conversación.

    Lee las keys nested bajo ``ctx['filtros']`` (``tipo``/``operacion``/
    ``ciudad``/``barrio``), salta los None/vacíos, capitaliza la primera
    letra preservando el resto ("Villa Morra" no se aplasta) y une con
    " · ". El JSONB puede llegar como dict o como string JSON (driver/
    subquery) — se manejan ambos. Devuelve None si no hay nada que mostrar.
    """
    if not ctx:
        return None
    if isinstance(ctx, (str, bytes)):
        try:
            ctx = json.loads(ctx)
        except (ValueError, TypeError):
            return None
    if not isinstance(ctx, dict):
        return None
    filtros = ctx.get("filtros") or {}
    if not isinstance(filtros, dict):
        return None
    parts: list[str] = []
    for key in _SEARCH_CONTEXT_KEYS:
        value = filtros.get(key)
        if value is None:
            continue
        value = str(value).strip()
        if not value:
            continue
        parts.append(value[:1].upper() + value[1:])
    return " · ".join(parts) if parts else None


# LEADS-01 — statuses que SIEMPRE esperan acción humana. interested cuenta
# solo cuando NO tiene agente asignado (se evalúa en compute_waiting).
WAITING_ACTION_STATUSES = frozenset({"new", "bot_replied", "no_response"})


def compute_waiting(
    status: str | None,
    agent_user_id: int | None,
    last_activity_at: datetime | None,
    now: datetime | None = None,
) -> tuple[str | None, datetime | None]:
    """LEADS-01 — bucket de urgencia (semáforo) para un lead.

    Aplica cuando el lead requiere acción humana:
    status ∈ {new, bot_replied, no_response} o (interested sin agente).
    Buckets por tiempo desde last_activity_at:
        <1h → "verde", 1-24h → "ambar", >24h → "rojo".

    Returns:
        (bucket, waiting_since) — (None, None) si no aplica urgencia
        o no hay last_activity_at.
    """
    needs_human = (
        status in WAITING_ACTION_STATUSES
        or (status == "interested" and agent_user_id is None)
    )
    if not needs_human or last_activity_at is None:
        return None, None
    since = last_activity_at
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    if now is None:
        now = datetime.now(timezone.utc)
    hours = (now - since).total_seconds() / 3600
    if hours < 1:
        bucket = "verde"
    elif hours <= 24:
        bucket = "ambar"
    else:
        bucket = "rojo"
    return bucket, last_activity_at


def format_relative_es(dt: datetime | None, now: datetime | None = None) -> str:
    """Tiempo relativo corto en español: "hace 25 min" / "hace 3 h" / "hace 2 días"."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if now is None:
        now = datetime.now(timezone.utc)
    seconds = max(0.0, (now - dt).total_seconds())
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"hace {max(1, minutes)} min"
    hours = int(seconds // 3600)
    if hours < 24:
        return f"hace {hours} h"
    days = int(seconds // 86400)
    return f"hace {days} día" + ("s" if days != 1 else "")


def _enrich_lead_row(row: dict, now: datetime | None = None) -> dict:
    """Derive presentation fields for one lead row dict (in place)."""
    row["interest_summary"] = summarize_search_context(row.get("search_context"))
    bucket, since = compute_waiting(
        row.get("status"), row.get("agent_user_id"),
        row.get("last_activity_at"), now=now,
    )
    row["waiting_bucket"] = bucket
    row["waiting_since"] = since
    row["waiting_label"] = format_relative_es(since, now=now) if since else None
    # Limpieza (c) — "Último contacto" relativo unificado en la tabla.
    # IC leads siguen mostrando la fecha de la consulta; el resto usa
    # last_activity_at con fallback a created_at. El template renderiza
    # last_contact_label ("hace 2 h") con title= la fecha completa.
    if row.get("source") == "infocasas" and row.get("consulta_date"):
        last_contact = row["consulta_date"]
    else:
        last_contact = row.get("last_activity_at") or row.get("created_at")
    row["last_contact_at"] = last_contact
    row["last_contact_label"] = (
        format_relative_es(last_contact, now=now) if last_contact else None
    )
    return row


class LeadService:
    @staticmethod
    async def get_leads(db: AsyncSession, limit: int = 50) -> list[Contact]:
        return await contact_repo.get_hot_leads(db, limit)

    @staticmethod
    async def get_interested(db: AsyncSession) -> list[dict]:
        """Fetch interested leads with property/IC joins."""
        return await lead_repo.get_interested(db)

    @staticmethod
    async def get_all_leads(
        db: AsyncSession,
        source: str | None = None,
        status: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> list[dict]:
        """Fetch all leads with optional filters, paginated."""
        return await lead_repo.get_all(db, source, status, limit, offset)

    @staticmethod
    async def count_leads(
        db: AsyncSession,
        source: str | None = None,
        status: str | None = None,
    ) -> int:
        """Count leads with optional filters."""
        return await lead_repo.count(db, source, status)

    @staticmethod
    async def get_lead_with_property(
        db: AsyncSession, contact_id: int,
    ) -> dict | None:
        """Fetch a single lead with joined property/IC data."""
        row = await lead_repo.get_lead_with_property(db, contact_id)
        return _enrich_lead_row(row) if row is not None else None

    @staticmethod
    async def change_status(
        db: AsyncSession, contact_id: int, new_status: str, user_id: int
    ) -> tuple[Contact | None, str | None]:
        """Change a contact's status.

        Returns:
            (contact, None) on success or same-status no-op.
            (None, "invalid_status") if new_status is not a valid status.
            (None, "not_found") if the contact does not exist.
        """
        if new_status not in VALID_STATUSES:
            logger.warning("Invalid status change requested: contact_id=%s, status=%s", contact_id, new_status)
            return None, "invalid_status"
        contact = await contact_repo.get_by_id(db, contact_id)
        if not contact:
            return None, "not_found"
        if contact.baja_at is not None and new_status != "discarded":
            return None, "baja"
        old_status = contact.status
        if old_status == new_status:
            return contact, None
        contact.status = new_status
        logger.info("Lead status changed: contact_id=%s, %s -> %s, by user_id=%s", contact_id, old_status, new_status, user_id)
        await lead_event_repo.create(
            db=db,
            contact_id=contact_id,
            event_type="status_change",
            old_status=old_status,
            new_status=new_status,
            triggered_by=f"user:{user_id}",
            metadata={"source": "panel_leads"},
        )
        return contact, None

    @staticmethod
    async def list_leads_by_tab(
        db: AsyncSession,
        tab: str,
        agent_filter: int | None = None,
        page: int = 1,
        per_page: int = 25,
        q: str | None = None,
        source: str | None = None,
        agent_id: int | None = None,
    ) -> tuple[list[dict], int]:
        """Plan 111-03 §6.1 — fetch (rows, total) for the given M6.1 tab.

        Args:
            tab: "leads" | "interesados" | "asignados" | "sin_respuesta".
            agent_filter: when set (Plan 111-04 vista agent), agrega
                AND c.agent_user_id = :agent_filter al WHERE.
            page: 1-indexed.
            per_page: page size; total_count is independent of pagination.
            q/source/agent_id: LEADS-03 search/filters — parameterized extra
                clauses over the tab predicate; `total` respects them so the
                pagination matches the filtered set.
        Raises:
            ValueError on unknown tab — el router lo traduce a 400.
        """
        if tab not in VALID_LEAD_TABS:
            raise ValueError(f"Unknown leads tab: {tab!r}")
        offset = max(0, (page - 1) * per_page)
        rows = await lead_repo.get_by_tab(
            db, tab, agent_filter=agent_filter, limit=per_page, offset=offset,
            q=q, source=source, agent_id=agent_id,
        )
        total = await lead_repo.count_by_tab(
            db, tab, agent_filter=agent_filter,
            q=q, source=source, agent_id=agent_id,
        )
        return [_enrich_lead_row(r) for r in rows], total

    @staticmethod
    async def list_leads_for_export(
        db: AsyncSession,
        *,
        tab: str,
        agent_filter: int | None = None,
        q: str | None = None,
        source: str | None = None,
        agent_id: int | None = None,
    ) -> list[dict]:
        """LEADS-03 — filas para el export xlsx de /leads.

        MISMO WHERE-building que list_leads_by_tab (tab M6.1 + q/source/
        agent_id vía lead_repo.get_by_tab), sin paginación: el archivo
        refleja exactamente lo que la página muestra filtrado, capped a
        EXPORT_MAX_ROWS. Sin _enrich_lead_row — el xlsx no usa los campos
        derivados de presentación.
        """
        if tab not in VALID_LEAD_TABS:
            raise ValueError(f"Unknown leads tab: {tab!r}")
        return await lead_repo.get_by_tab(
            db, tab, agent_filter=agent_filter,
            limit=EXPORT_MAX_ROWS, offset=0,
            q=q, source=source, agent_id=agent_id,
        )

    @staticmethod
    async def count_leads_per_tab(db: AsyncSession) -> dict[str, int]:
        """Badge counters for /leads admin header — one count per tab.

        Plan 111-03: 3 COUNT(*) queries; cardinality is small enough that
        a single CASE-WHEN aggregate is not worth the readability tradeoff.
        """
        return {
            tab: await lead_repo.count_by_tab(db, tab)
            for tab in TAB_WHERE_CLAUSES
        }


lead_service = LeadService()
