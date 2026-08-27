import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text as sa_text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import VALID_STATUSES

# ---------------------------------------------------------------------------
# In-process COUNT cache (C1.2)
# ---------------------------------------------------------------------------
# Key: frozenset of (filter_key, value) pairs that identify a unique filter
# combination.  Value: (count: int, expires_at: float) where expires_at is
# time.monotonic() + TTL.
#
# Only populated for queries WITHOUT a search term (searches are
# unpredictable; caching them adds complexity for little gain on ~10K rows).
# The agent_user_id is included in the key so each agent's quota is cached
# separately and admin/agent reads never collide.
#
# Invalidation: TTL (30 s) + explicit call to _invalidate_count_cache()
# on create / delete / status-change — the three service methods that
# mutate the result of count_all.
#
# NOTE: the bot creates contacts via conversation_manager directly through the
# repository layer, bypassing this service entirely.  Those writes are NOT
# reflected in the cache until the TTL expires (up to 30 s).  This is
# acceptable by design — the panel count drifts by at most one TTL window.

import time as _time

_COUNT_CACHE: dict[frozenset, tuple[int, float]] = {}
_COUNT_TTL_SECS: int = 30


def _count_cache_key(
    status: str | None,
    source: str | None,
    phone_filter: str | None,
    agent_user_id: int | None,
) -> frozenset:
    """Build a hashable cache key from the non-search filters."""
    return frozenset({
        ("status", status),
        ("source", source),
        ("phone_filter", phone_filter),
        ("agent_user_id", agent_user_id),
    })


def _invalidate_count_cache() -> None:
    """Clear all cached COUNT entries.

    Called after any write that changes the total: create, delete,
    status change.  TTL guards against stale reads between writes, but
    explicit invalidation keeps the cache fresh on the happy path.
    """
    _COUNT_CACHE.clear()


def clear_count_cache() -> None:
    """Public helper to clear the COUNT cache.

    Intended for use in tests: add an autouse fixture that calls this
    before each test so that a cached count from one test cannot bleed
    into the next (tests often insert rows directly via the repository,
    bypassing the service-level invalidation).

    Also safe to call from application code if a full cache flush is
    ever needed outside of the normal write paths.
    """
    _COUNT_CACHE.clear()


from app.models.contact import Contact
from app.models.contact_note import ContactNote
from app.models.conversation import Conversation
from app.models.infocasas_property import InfocasasProperty
from app.models.lead_event import LeadEvent
from app.models.property import Property
from app.models.visit import Visit
from app.repositories.contact_note_repo import contact_note_repo
from app.repositories.contact_repo import contact_repo
from app.repositories.conversation_repo import conversation_repo
from app.repositories.inquiry_history_repo import inquiry_history_repo
from app.repositories.lead_event_repo import lead_event_repo
from app.repositories.property_repo import property_repo
from app.repositories.visit_repo import visit_repo
from app.utils.pagination import calculate_total_pages
from app.utils.phone_utils import parse_phone, validate_phone

logger = logging.getLogger(__name__)

BOT_EVENT_TYPES: frozenset = frozenset({
    "search", "detail_view", "auto_status_change", "bot_interaction", "notified_ez",
})

BUSINESS_EVENT_TYPES: frozenset = frozenset({
    "status_change", "new_contact", "new_lead", "created", "updated", "deleted",
    "note_created", "note_edited", "note_deleted", "lead_registered", "linked_existing",
})

_SESSION_GAP_HOURS: int = 4

_COLLAPSIBLE_TYPES: frozenset = frozenset({"bot_toggle", "agent_assigned"})
_COLLAPSE_WINDOW_SECS: int = 120


def _collapse_rapid_repeats(items: list[dict]) -> list[dict]:
    """Collapse individual events of same type+triggered_by within COLLAPSE_WINDOW into one."""
    if not items:
        return items
    result: list[dict] = []
    used: set[int] = set()
    for i, item in enumerate(items):
        if i in used:
            continue
        if item["type"] != "individual":
            result.append(item)
            continue
        ev = item["event"]
        if ev.event_type not in _COLLAPSIBLE_TYPES:
            result.append(item)
            continue
        anchor_dt = ev.created_at
        group_indices: list[int] = [i]
        for j, other in enumerate(items):
            if j == i or j in used:
                continue
            if other["type"] != "individual":
                continue
            other_ev = other["event"]
            if other_ev.event_type != ev.event_type or other_ev.triggered_by != ev.triggered_by:
                continue
            other_dt = other_ev.created_at
            if anchor_dt and other_dt:
                try:
                    gap = abs((anchor_dt - other_dt).total_seconds())
                except TypeError:
                    continue
                if gap <= _COLLAPSE_WINDOW_SECS:
                    group_indices.append(j)
        for idx in group_indices:
            used.add(idx)
        if len(group_indices) > 1:
            result.append({
                "type": "collapsed",
                "event_type": ev.event_type,
                "triggered_by": ev.triggered_by,
                "count": len(group_indices),
                "representative": ev,
            })
        else:
            result.append(item)
    return result


class ContactService:
    # ------------------------------------------------------------------
    # List / detail
    # ------------------------------------------------------------------

    @staticmethod
    async def get_contacts(
        db: AsyncSession,
        status: str | None,
        source: str | None,
        search: str | None,
        phone_filter: str | None,
        page: int,
        per_page: int,
        agent_user_id: int | None = None,
    ) -> dict:
        """Return paginated contacts list with linked properties and total count.

        agent_user_id: when provided, restrict results to contacts assigned to
        that agent (feat/authz ROLE-agent-list).
        """
        offset = (page - 1) * per_page
        contacts = await contact_repo.get_all(
            db,
            status=status,
            source=source,
            search=search,
            phone_filter=phone_filter,
            limit=per_page,
            offset=offset,
            agent_user_id=agent_user_id,
        )

        # COUNT cache: only when no free-text search (C1.2).
        if not search:
            cache_key = _count_cache_key(status, source, phone_filter, agent_user_id)
            cached = _COUNT_CACHE.get(cache_key)
            if cached is not None and _time.monotonic() < cached[1]:
                total = cached[0]
            else:
                total = await contact_repo.count_all(
                    db,
                    status=status,
                    source=source,
                    search=None,
                    phone_filter=phone_filter,
                    agent_user_id=agent_user_id,
                )
                _COUNT_CACHE[cache_key] = (total, _time.monotonic() + _COUNT_TTL_SECS)
        else:
            total = await contact_repo.count_all(
                db,
                status=status,
                source=source,
                search=search,
                phone_filter=phone_filter,
                agent_user_id=agent_user_id,
            )
        total_pages = calculate_total_pages(total, per_page)

        prop_ids = [c.property_id for c in contacts if c.property_id]
        props_map: dict[int, Property] = (
            await property_repo.get_by_ids(db, prop_ids) if prop_ids else {}
        )

        ic_refs = [
            c.infocasas_ref
            for c in contacts
            if c.source == "infocasas" and getattr(c, "infocasas_ref", None)
        ]
        infocasas_props_map: dict[str, InfocasasProperty] = (
            await property_repo.get_ic_by_refs(db, ic_refs) if ic_refs else {}
        )

        return {
            "contacts": contacts,
            "props_map": props_map,
            "infocasas_props_map": infocasas_props_map,
            "total": total,
            "total_pages": total_pages,
        }

    @staticmethod
    async def get_contact(db: AsyncSession, contact_id: int) -> Contact | None:
        return await contact_repo.get_by_id(db, contact_id)

    # ------------------------------------------------------------------
    # M6.1 — ROLE-13/15: mark_seen_by_agent (Plan 111-07)
    # ------------------------------------------------------------------

    @staticmethod
    async def mark_seen_by_agent(
        db: AsyncSession, contact_id: int, user_id: int
    ) -> None:
        """Set contacts.agent_seen_at = now() if user_id is the assigned agent.

        Idempotent: if the contact does not belong to the user, the UPDATE
        matches 0 rows (no-op). Spec §10.5 — no lock required, last-write-wins;
        millisecond differences between concurrent agent opens are acceptable.

        Called as a side-effect of GET /contacts/{id} when the requesting user
        has role='agent' and is the assigned owner — drives the "Nuevo" badge
        on /leads (badge visible while agent_assigned_at > agent_seen_at).
        """
        await db.execute(
            sa_text(
                "UPDATE contacts "
                "   SET agent_seen_at = now() "
                " WHERE id = :contact_id "
                "   AND agent_user_id = :user_id"
            ),
            {"contact_id": contact_id, "user_id": user_id},
        )
        await db.commit()

    @staticmethod
    async def get_contact_detail(db: AsyncSession, contact_id: int) -> dict | None:
        """Return full contact detail including events, conversations, and linked property."""
        contact = await contact_repo.get_by_id(db, contact_id)
        if not contact:
            return None

        all_events = await lead_event_repo.get_all_events(db, contact_id)
        grouped_events = ContactService.build_grouped_timeline(all_events)
        notes = await contact_note_repo.get_by_contact(db, contact_id)
        conversations = await conversation_repo.get_by_contact(db, contact_id)

        # Resolve viewed properties from detail_view events (Phase 92 — VIEWS-01)
        detail_view_events = await lead_event_repo.get_detail_views(db, contact_id)
        viewed_property_ids: list[int] = [
            ev.event_metadata["property_id"]
            for ev in detail_view_events
            if isinstance(ev.event_metadata, dict) and "property_id" in ev.event_metadata
        ]
        viewed_props_map: dict[int, Property] = (
            await property_repo.get_by_ids(db, viewed_property_ids)
            if viewed_property_ids
            else {}
        )
        viewed_properties: list[dict] = []
        for ev in detail_view_events:
            if not isinstance(ev.event_metadata, dict):
                continue
            pid = ev.event_metadata.get("property_id")
            if not pid:
                continue
            prop = viewed_props_map.get(pid)
            if prop is None:
                continue
            viewed_properties.append({
                "id": prop.id,
                "title": prop.title or "",
                "city": prop.city or "",
                "neighborhood": prop.neighborhood or "",
                "price_usd": prop.price_usd,
                "price_currency": prop.price_currency or "USD",
                "url": prop.url or "",
                "viewed_at": ev.created_at,
            })

        linked_property: Property | None = None
        ic_property: InfocasasProperty | None = None
        if contact.source == "infocasas" and getattr(contact, "infocasas_ref", None):
            ic_property = await property_repo.get_ic_by_ref(db, contact.infocasas_ref)
        if not ic_property and contact.property_id:
            linked_property = await property_repo.get_by_id(db, contact.property_id)

        phone_info = parse_phone(contact.phone)

        # Inquiry history for IC contacts
        inquiry_history: list = []
        if contact.source == "infocasas":
            inquiry_history = await inquiry_history_repo.get_by_contact(db, contact_id)

        # M6.2 §5.10 — has_active_visit drives the status-dropdown lockout
        # in the detail page (Plan 114 §5.10/§5.11/§5.12). True iff at least
        # one visit row for this contact has status='scheduled'.
        has_active_visit = await visit_repo.has_active_for_contact(db, contact_id)

        return {
            "contact": contact,
            "grouped_events": grouped_events,
            "notes": notes,
            "conversations": conversations,
            "linked_property": linked_property,
            "ic_property": ic_property,
            "phone_info": phone_info,
            "viewed_properties": viewed_properties,
            "inquiry_history": inquiry_history,
            "has_active_visit": has_active_visit,
        }

    # ------------------------------------------------------------------
    # Events partial
    # ------------------------------------------------------------------

    @staticmethod
    async def get_events(db: AsyncSession, contact_id: int) -> list[LeadEvent]:
        return await lead_event_repo.get_by_contact(db, contact_id)

    @staticmethod
    async def get_all_events(db: AsyncSession, contact_id: int) -> list:
        return await lead_event_repo.get_all_events(db, contact_id)

    @staticmethod
    def build_grouped_timeline(events: list) -> list[dict]:
        """Group bot events into sessions; business events stay individual.

        Args:
            events: LeadEvent list in any order. Sorted ASC internally before processing.
        Returns:
            List of timeline item dicts sorted DESC (newest first) for display.
        """
        if not events:
            return []

        def _safe_dt(ev):
            return ev.created_at if ev.created_at is not None else datetime.max.replace(tzinfo=None)

        sorted_evs = sorted(events, key=_safe_dt)

        items: list[dict] = []
        session: list = []
        session_last_dt = None

        def _flush_session():
            if session:
                items.append({
                    "type": "session",
                    "events": list(session),
                    "last_activity": session[-1].created_at,
                    "count": len(session),
                })
                session.clear()

        for ev in sorted_evs:
            if ev.event_type in BOT_EVENT_TYPES:
                if ev.created_at is None:
                    _flush_session()
                    items.append({"type": "individual", "event": ev})
                    continue
                if not session:
                    session.append(ev)
                    session_last_dt = ev.created_at
                else:
                    same_day = ev.created_at.date() == session_last_dt.date()
                    gap_ok = (ev.created_at - session_last_dt) < timedelta(hours=_SESSION_GAP_HOURS)
                    if same_day and gap_ok:
                        session.append(ev)
                        session_last_dt = ev.created_at
                    else:
                        _flush_session()
                        session.append(ev)
                        session_last_dt = ev.created_at
            else:
                _flush_session()
                items.append({"type": "individual", "event": ev})

        _flush_session()

        def _item_dt(item):
            if item["type"] == "session":
                dt = item["last_activity"]
            else:
                dt = item["event"].created_at
            if dt is None:
                return datetime.min.replace(tzinfo=None)
            # Strip tz for comparison if needed
            if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
                from datetime import timezone
                return dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt

        items = _collapse_rapid_repeats(sorted(items, key=_item_dt, reverse=True))
        return items

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    @staticmethod
    async def create_contact(
        db: AsyncSession,
        name: str,
        phone: str,
        email: str | None,
        status: str,
        operacion: str | None,
        zona: str | None,
        presupuesto_raw: str,
        dormitorios_raw: str,
        user_id: int,
        user_email: str,
        user_role: str,
        property_id: int | None = None,
    ) -> tuple[Contact | None, str | None]:
        """Validate, create contact, emit lead_event.

        Returns (contact, error_message).  On success error_message is None.
        """
        if not name:
            return None, "El nombre es requerido"
        if not phone:
            return None, "El teléfono es requerido"
        valid, phone_err = validate_phone(phone)
        if not valid:
            return None, phone_err
        if status not in VALID_STATUSES:
            status = "new"

        existing = await contact_repo.get_by_phone(db, phone)
        if existing:
            return None, "Este teléfono ya está registrado"

        prefs: dict = {}
        if operacion:
            prefs["operacion"] = operacion
        if zona:
            prefs["zona"] = zona
        if presupuesto_raw:
            try:
                prefs["presupuesto"] = float(presupuesto_raw)
            except ValueError:
                pass
        if dormitorios_raw:
            try:
                prefs["dormitorios"] = int(dormitorios_raw)
            except ValueError:
                pass

        if property_id is not None:
            prop = await property_repo.get_by_id(db, property_id)
            if not prop or not prop.is_active:
                raise ValueError(f"Property {property_id} not found or inactive")

        contact = await contact_repo.create(
            db,
            name=name,
            phone=phone,
            email=email,
            source="manual",
            status=status,
            preferences=prefs or None,
            property_id=property_id,
        )
        phone_truncated = phone[:6] + "..."
        logger.info(
            "Contact created: id=%d phone=%s status=%s user=%s",
            contact.id,
            phone_truncated,
            status,
            user_email,
        )
        await lead_event_repo.create(
            db=db,
            contact_id=contact.id,
            event_type="new_contact",
            old_status=None,
            new_status=status,
            triggered_by=f"user:{user_id}",
            metadata={"source": "manual", "created_by_role": user_role},
        )
        await db.commit()
        _invalidate_count_cache()
        return contact, None

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    @staticmethod
    async def update_contact(
        db: AsyncSession,
        contact_id: int,
        name: str | None,
        phone: str | None,
        email: str | None,
        operacion: str | None,
        zona: str | None,
        presupuesto_raw: str,
        dormitorios_raw: str,
        user_id: int,
        user_email: str,
        property_id: int | None = None,
    ) -> tuple[bool, str | None, bool]:
        """Apply edits to a contact.

        Returns (ok, error_message, has_changes).
        ok=False also if contact not found; has_changes is True when at least
        one field value actually differed from the stored value.
        """
        contact = await contact_repo.get_by_id(db, contact_id)
        if not contact:
            return False, "Contacto no encontrado", False

        if phone and phone != contact.phone:
            valid, phone_err = validate_phone(phone)
            if not valid:
                return False, phone_err, False

        if phone and phone != contact.phone:
            dup = await contact_repo.get_by_phone(db, phone)
            if dup and dup.id != contact_id:
                return False, "Ese teléfono ya pertenece a otro contacto", False

        prefs = dict(contact.preferences or {})
        if operacion is not None:
            prefs["operacion"] = operacion
        if zona is not None:
            prefs["zona"] = zona
        if presupuesto_raw:
            try:
                prefs["presupuesto"] = float(presupuesto_raw)
            except ValueError:
                pass
        elif "presupuesto" in prefs and not presupuesto_raw:
            prefs.pop("presupuesto", None)
        if dormitorios_raw:
            try:
                prefs["dormitorios"] = int(dormitorios_raw)
            except ValueError:
                pass
        elif "dormitorios" in prefs and not dormitorios_raw:
            prefs.pop("dormitorios", None)

        if property_id is not None:
            prop = await property_repo.get_by_id(db, property_id)
            if not prop or not prop.is_active:
                raise ValueError(f"Property {property_id} not found or inactive")

        fields: dict = {"preferences": prefs}
        if name:
            fields["name"] = name
        if phone:
            fields["phone"] = phone
            fields["phone_normalized"] = phone
        if email is not None:
            fields["email"] = email or None
        if property_id is not None:
            fields["property_id"] = property_id

        # Build audit diff (before/after)
        changed: list[str] = []
        before: dict = {}
        after: dict = {}
        old_prefs = dict(contact.preferences or {})

        if name and name != contact.name:
            changed.append("name")
            before["name"] = contact.name or ""
            after["name"] = name
        if phone and phone != contact.phone:
            changed.append("phone")
            before["phone"] = contact.phone or ""
            after["phone"] = phone
        if email != contact.email:
            changed.append("email")
            before["email"] = contact.email or ""
            after["email"] = email or ""
        if property_id is not None and property_id != contact.property_id:
            changed.append("property_id")
            before["property_id"] = contact.property_id
            after["property_id"] = property_id
        pref_fields = ["operacion", "zona", "presupuesto", "dormitorios"]
        for pf in pref_fields:
            old_val = old_prefs.get(pf)
            new_val = prefs.get(pf)
            if str(old_val or "") != str(new_val or ""):
                key = f"preferences.{pf}"
                changed.append(key)
                before[key] = str(old_val) if old_val is not None else ""
                after[key] = str(new_val) if new_val is not None else ""

        await contact_repo.update(db, contact_id, fields)

        if changed:
            await lead_event_repo.create(
                db=db,
                contact_id=contact_id,
                event_type="updated",
                old_status=contact.status,
                new_status=contact.status,
                triggered_by=f"user:{user_id}",
                metadata={
                    "changed": changed,
                    "before": before,
                    "after": after,
                    "updated_by": "admin",
                    "source": "panel",
                },
            )

        await db.commit()
        logger.info(
            "Contact updated: id=%d changed=%s user=%s",
            contact_id,
            changed,
            user_email,
        )
        return True, None, bool(changed)

    # ------------------------------------------------------------------
    # Status change
    # ------------------------------------------------------------------

    @staticmethod
    async def update_status(
        db: AsyncSession,
        contact_id: int,
        new_status: str,
        user_id: int,
        user_email: str,
        user_role: str,
    ) -> tuple[Contact | None, str | None]:
        """Change contact status with validation and audit event.

        Returns (updated_contact, error_message).
        """
        if new_status not in VALID_STATUSES:
            return None, "Status inválido"

        contact = await contact_repo.get_by_id(db, contact_id)
        if not contact:
            return None, "No encontrado"

        if contact.baja_at is not None and new_status != "discarded":
            return None, "Contacto con baja: el opt-out es irreversible"

        # M6.2 §5.12 (VISIT-05) — bloquear cambio de status si hay visitas
        # activas. Excepción: el sentinel 'deleted' (soft-delete) siempre
        # pasa para que delete_contact()/route DELETE no quede atrapado.
        if new_status != "deleted":
            has_active = await visit_repo.has_active_for_contact(db, contact_id)
            if has_active:
                return None, "Contacto tiene visitas activas; cancelar primero"

        old_status = contact.status
        phone_truncated = (contact.phone or "")[:6] + "..."
        logger.info(
            "Contact status changed: id=%d phone=%s old_status=%s new_status=%s user=%s",
            contact_id,
            phone_truncated,
            old_status,
            new_status,
            user_email,
        )
        updated = await contact_repo.update_status(db, contact_id, new_status)
        await lead_event_repo.create(
            db=db,
            contact_id=contact_id,
            event_type="status_change",
            old_status=old_status,
            new_status=new_status,
            triggered_by=f"user:{user_id}",
            metadata={"source": "panel", "changed_by_role": user_role},
        )
        await db.commit()
        _invalidate_count_cache()
        return updated, None

    # ------------------------------------------------------------------
    # Delete (soft)
    # ------------------------------------------------------------------

    @staticmethod
    async def delete_contact(
        db: AsyncSession,
        contact_id: int,
        user_id: int,
        user_email: str,
        user_role: str,
    ) -> tuple[bool, str | None]:
        """Soft-delete a contact (sets status='deleted').

        Returns (ok, error_message).
        """
        contact = await contact_repo.get_by_id(db, contact_id)
        if not contact:
            return False, "No encontrado"

        old_status = contact.status
        phone_truncated = (contact.phone or "")[:6] + "..."
        logger.info(
            "Contact deleted: id=%d phone=%s old_status=%s user=%s",
            contact_id,
            phone_truncated,
            old_status,
            user_email,
        )

        # STAB-07 (TD-116-01): cancel any `scheduled` visits so a deleted
        # contact leaves no phantom scheduled visit. We flip the visit rows
        # directly (status='cancelled', notes='contact deleted') and emit a
        # `visit_cancelled` lead_event per visit — mirroring the manual
        # cancel path's timeline — WITHOUT calling VisitService.cancel_visit,
        # whose _sync_contact_status would fight the 'deleted' status we set
        # below. Terminal visits (done/cancelled/no_show) are left untouched.
        visits = await visit_repo.list_by_contact(db, contact_id)
        for visit in visits:
            if visit.status != "scheduled":
                continue
            await db.execute(
                update(Visit)
                .where(Visit.id == visit.id)
                .values(status="cancelled", notes="contact deleted")
            )
            await lead_event_repo.create(
                db=db,
                contact_id=contact_id,
                event_type="visit_cancelled",
                old_status=None,
                new_status=None,
                triggered_by=f"user:{user_id}",
                metadata={"visit_id": visit.id, "reason": "contact deleted"},
            )

        await contact_repo.update_status(db, contact_id, "deleted")
        await lead_event_repo.create(
            db=db,
            contact_id=contact_id,
            event_type="deleted",
            old_status=old_status,
            new_status="deleted",
            triggered_by=f"user:{user_id}",
            metadata={"source": "panel", "deleted_by_role": user_role},
        )
        await db.commit()
        _invalidate_count_cache()
        return True, None

    # ------------------------------------------------------------------
    # CSV export (C1.4)
    # ------------------------------------------------------------------

    @staticmethod
    async def export_csv(
        db: AsyncSession,
        status: str | None,
        source: str | None,
        search: str | None,
        phone_filter: str | None,
        agent_user_id: int | None,
    ) -> tuple[str, str]:
        """Return (csv_bytes_as_str, filename) for the filtered contact list.

        Columns: id, nombre, telefono, email, estado, fuente,
                 asesor_asignado, creado, ultima_actividad.
        Encoding: UTF-8 BOM (Excel compatibility).
        Limit: 20 000 rows.
        Filename: contactos_YYYYMMDD.csv (date = today in local time).
        """
        import csv
        import io
        from datetime import datetime as _dt

        contacts = await contact_repo.get_for_export(
            db,
            status=status,
            source=source,
            search=search,
            phone_filter=phone_filter,
            agent_user_id=agent_user_id,
        )

        # Build a user display map for assigned agents
        agent_ids = list({c.agent_user_id for c in contacts if c.agent_user_id})
        from app.repositories.user_repo import user_repo as _user_repo
        all_users = await _user_repo.get_all(db, active=None) if agent_ids else []
        users_map = {u.id: (u.display_name or u.name or u.email or "") for u in all_users}

        headers = [
            "id", "nombre", "telefono", "email", "estado", "fuente",
            "asesor_asignado", "creado", "ultima_actividad",
        ]

        def _fmt(dt) -> str:
            if dt is None:
                return ""
            return dt.strftime("%d/%m/%Y %H:%M")

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=headers, lineterminator="\r\n")
        writer.writeheader()
        for c in contacts:
            writer.writerow({
                "id": c.id,
                "nombre": c.name or "",
                "telefono": c.phone or "",
                "email": c.email or "",
                "estado": c.status or "",
                "fuente": c.source or "",
                "asesor_asignado": users_map.get(c.agent_user_id, "") if c.agent_user_id else "",
                "creado": _fmt(c.created_at),
                "ultima_actividad": _fmt(c.last_activity_at),
            })

        csv_content = "﻿" + output.getvalue()  # UTF-8 BOM for Excel
        filename = f"contactos_{_dt.now().strftime('%Y%m%d')}.csv"
        return csv_content, filename

    # ------------------------------------------------------------------
    # Bulk status change (C1.3)
    # ------------------------------------------------------------------

    @staticmethod
    async def bulk_update_status(
        db: AsyncSession,
        contact_ids: list[int],
        new_status: str,
        user_id: int,
        user_email: str,
        user_role: str,
    ) -> dict:
        """Apply a status change to multiple contacts in one call.

        Validation rules (applied per contact):
        - new_status must be in VALID_STATUSES (never 'deleted').
        - Contacts with baja_at set (opt-out) are skipped — opt-out is
          irreversible and must never be overridden by a bulk action.
        - For role='agent', contacts not owned by the user are skipped.
        - Per-contact logic reuses update_status() to guarantee the same
          visit-lock check, audit event, and cache invalidation.

        Returns a dict:
            {
              "updated": int,
              "skipped_optout": int,
              "skipped_permission": int,
            }
        """
        if new_status not in VALID_STATUSES:
            raise ValueError(f"Status inválido: {new_status}")

        updated = 0
        skipped_optout = 0
        skipped_permission = 0

        for contact_id in contact_ids:
            contact = await contact_repo.get_by_id(db, contact_id)
            if contact is None:
                continue

            # opt-out: baja_at — irreversible, skip silently
            if contact.baja_at is not None:
                skipped_optout += 1
                continue

            # skip deleted contacts (soft-deleted — must not be re-activated via bulk)
            if contact.status == "deleted":
                skipped_optout += 1
                continue

            # agent ownership — skip contacts not owned by the agent
            if user_role == "agent" and contact.agent_user_id != user_id:
                skipped_permission += 1
                continue

            result, error = await ContactService.update_status(
                db,
                contact_id=contact_id,
                new_status=new_status,
                user_id=user_id,
                user_email=user_email,
                user_role=user_role,
            )
            if result is not None:
                updated += 1
            # If error (e.g. active visit lock), count as skipped_optout for simplicity
            # so callers see total = updated + skipped_*

        return {
            "updated": updated,
            "skipped_optout": skipped_optout,
            "skipped_permission": skipped_permission,
        }

    # ------------------------------------------------------------------
    # Conversations (for detail page)
    # ------------------------------------------------------------------

    @staticmethod
    async def get_conversations(
        db: AsyncSession, contact_id: int
    ) -> list[Conversation]:
        return await conversation_repo.get_by_contact(db, contact_id)

    # ------------------------------------------------------------------
    # Notes (Phase 93)
    # ------------------------------------------------------------------

    @staticmethod
    async def get_notes(db: AsyncSession, contact_id: int) -> list[ContactNote]:
        return await contact_note_repo.get_by_contact(db, contact_id)

    @staticmethod
    async def create_note(
        db: AsyncSession,
        contact_id: int,
        content: str,
        user_id: int,
    ) -> tuple[ContactNote | None, str | None]:
        contact = await contact_repo.get_by_id(db, contact_id)
        if not contact:
            return None, "Contacto no encontrado"
        if not content.strip():
            return None, "El contenido no puede estar vacío"
        note = await contact_note_repo.create(db, contact_id, user_id, content.strip())
        await lead_event_repo.create(
            db, contact_id, "note_created",
            old_status=None, new_status=None,
            triggered_by=f"user:{user_id}",
            metadata={"note_id": note.id, "content": note.content[:200]},
        )
        await db.commit()
        return note, None

    @staticmethod
    async def update_note(
        db: AsyncSession,
        note_id: int,
        content: str,
        user_id: int,
    ) -> tuple[ContactNote | None, str | None]:
        if not content.strip():
            return None, "El contenido no puede estar vacío"
        note = await contact_note_repo.get_by_id(db, note_id)
        if note is None:
            return None, "Nota no encontrada"
        old_content = note.content
        updated = await contact_note_repo.update(db, note_id, content.strip())
        await lead_event_repo.create(
            db, note.contact_id, "note_edited",
            old_status=None, new_status=None,
            triggered_by=f"user:{user_id}",
            metadata={"note_id": note_id, "old": old_content[:200], "new": content.strip()[:200]},
        )
        await db.commit()
        return updated, None

    @staticmethod
    async def delete_note(
        db: AsyncSession,
        note_id: int,
        user_id: int,
    ) -> tuple[bool, str | None]:
        note = await contact_note_repo.get_by_id(db, note_id)
        if note is None:
            return False, "Nota no encontrada"
        contact_id = note.contact_id
        deleted_content = note.content
        await contact_note_repo.delete(db, note_id)
        await lead_event_repo.create(
            db, contact_id, "note_deleted",
            old_status=None, new_status=None,
            triggered_by=f"user:{user_id}",
            metadata={"note_id": note_id, "content": deleted_content[:200]},
        )
        await db.commit()
        return True, None


contact_service = ContactService()
