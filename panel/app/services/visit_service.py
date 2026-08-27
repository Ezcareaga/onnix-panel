"""VisitService — business logic for `visits` (M6.2 — Plan 114-01 §2.1-§2.2).

Pattern: tuple-return `(entity | None, error_msg | None)` mirroring
`lead_service.change_status`. Services commit at the end of write methods;
repos NEVER commit.

The heart of M6.2 is `_sync_contact_status` (§2.2 of Plan 114-01):
  contact.status = 'visit_scheduled'  ⇔  EXISTS(visits WHERE contact_id AND status='scheduled')

Implemented via a *filtered UPDATE* on contacts (no SELECT FOR UPDATE).
Concurrency hardening (idempotent retry, savepoints) is deferred to
TECHNICAL_DEBT.md per OQ-3 decision in Plan 114.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.property import Property
from app.models.visit import Visit
from app.repositories.contact_repo import contact_repo
from app.repositories.lead_event_repo import lead_event_repo
from app.repositories.visit_repo import visit_repo

logger = logging.getLogger(__name__)

_VALID_SOURCES = ("panel", "bot", "manual")
_TERMINAL_VISIT_STATUSES = ("done", "no_show")


async def _emit_visit_action_event(
    db: AsyncSession,
    *,
    contact_id: int,
    visit_id: int,
    event_type: str,
    user_id: int | None,
    extra_metadata: dict | None = None,
    triggered_by_label: str = "system",
) -> None:
    """Log a per-visit-action lead_event so the timeline mirrors notes.

    Phase 116 UAT fix-forward: previously only `visit_status_change` was
    emitted, and only when `contact.status` flipped to `visit_scheduled`
    on Case A of `_sync_contact_status`. That left cancel / complete /
    reschedule with zero trace in the timeline.

    `event_type` is one of: visit_created, visit_cancelled,
    visit_completed, visit_rescheduled.
    """
    metadata: dict = {"visit_id": visit_id}
    if extra_metadata:
        metadata.update(extra_metadata)
    # `triggered_by_label` (default "system") lets the bot path (D-1: NULL
    # agent, source='bot') honestly label the action as "bot" instead of
    # "system" when there is no human user_id. Panel/agent callers keep the
    # user:<id> form whenever a user_id is present.
    triggered_by = (
        f"user:{user_id}" if user_id is not None else triggered_by_label
    )
    await lead_event_repo.create(
        db,
        contact_id=contact_id,
        event_type=event_type,
        old_status=None,
        new_status=None,
        triggered_by=triggered_by,
        metadata=metadata,
    )


class VisitService:
    # ------------------------------------------------------------------
    # CREATE
    # ------------------------------------------------------------------
    @staticmethod
    async def create_visit(
        db: AsyncSession,
        *,
        contact_id: int,
        scheduled_at: datetime,
        agent_user_id: int | None,
        property_id: int | None = None,
        notes: str | None = None,
        source: str = "panel",
    ) -> tuple[Visit | None, str | None]:
        """Create a new visit. Sync contact.status='visit_scheduled' if not
        already. Emit lead_event('visit_status_change') iff the sync moves
        the status.

        Returns:
            (visit, None) on success.
            (None, "La fecha debe ser futura") if scheduled_at <= NOW().
            (None, "Contacto no encontrado") if contact_id does not exist.
            (None, "Source inválido") if source not in valid set.
            (None, "Propiedad no encontrada") if property_id given and missing.
        """
        # Validation order per Plan 114-01 §2.1: future date → source → contact → property.
        # (Future-date first so trivially-bad requests fail without a DB lookup.)
        if scheduled_at <= datetime.now(timezone.utc):
            return None, "La fecha debe ser futura"
        if source not in _VALID_SOURCES:
            return None, "Source inválido"

        contact = await contact_repo.get_by_id(db, contact_id)
        if contact is None:
            return None, "Contacto no encontrado"

        if property_id is not None:
            prop = await db.execute(
                select(Property.id).where(Property.id == property_id)
            )
            if prop.scalar_one_or_none() is None:
                return None, "Propiedad no encontrada"

        visit = await visit_repo.insert(
            db,
            contact_id=contact_id,
            property_id=property_id,
            agent_user_id=agent_user_id,
            scheduled_at=scheduled_at,
            status="scheduled",
            source=source,
            notes=notes,
        )

        await _emit_visit_action_event(
            db,
            contact_id=contact_id,
            visit_id=visit.id,
            event_type="visit_created",
            user_id=agent_user_id,
            extra_metadata={
                "scheduled_at": scheduled_at.isoformat(),
                "property_id": property_id,
                "source": source,
            },
            # D-1: bot-created visits have no agent user_id; label the action
            # "bot" (not "system"). Panel/agent callers keep "system" default.
            triggered_by_label="bot" if source == "bot" else "system",
        )
        await VisitService._sync_contact_status(
            db, contact_id=contact_id, triggering_visit_id=visit.id,
            source=source,
        )
        await db.commit()
        return visit, None

    # ------------------------------------------------------------------
    # CANCEL
    # ------------------------------------------------------------------
    @staticmethod
    async def cancel_visit(
        db: AsyncSession,
        *,
        visit_id: int,
        user_id: int,
    ) -> tuple[Visit | None, str | None]:
        """Cancel a scheduled visit. Re-sync contact.status (Case C is a
        no-op per VISIT-05 — even if this was the last active visit, the
        contact stays in 'visit_scheduled' until an agent moves it).
        """
        visit = await visit_repo.get_by_id(db, visit_id)
        if visit is None:
            return None, "Visita no encontrada"
        if visit.status == "cancelled":
            return None, "Visita ya está cancelada"
        if visit.status in _TERMINAL_VISIT_STATUSES:
            return None, "Visita ya completada"

        contact_id = visit.contact_id
        updated = await visit_repo.update_status(
            db, visit_id=visit_id, new_status="cancelled",
        )
        await _emit_visit_action_event(
            db,
            contact_id=contact_id,
            visit_id=visit_id,
            event_type="visit_cancelled",
            user_id=user_id,
        )
        await VisitService._sync_contact_status(
            db, contact_id=contact_id, triggering_visit_id=visit_id,
        )
        await db.commit()
        return updated, None

    # ------------------------------------------------------------------
    # COMPLETE
    # ------------------------------------------------------------------
    @staticmethod
    async def complete_visit(
        db: AsyncSession,
        *,
        visit_id: int,
        result: str,
        user_id: int,
    ) -> tuple[Visit | None, str | None]:
        """Mark a scheduled visit as 'done' or 'no_show'. Sync semantics
        identical to cancel — no auto-transition when all visits go terminal.
        """
        if result not in _TERMINAL_VISIT_STATUSES:
            return None, "Result inválido"
        visit = await visit_repo.get_by_id(db, visit_id)
        if visit is None:
            return None, "Visita no encontrada"
        if visit.status != "scheduled":
            return None, "Visita no está en scheduled"

        contact_id = visit.contact_id
        updated = await visit_repo.update_status(
            db, visit_id=visit_id, new_status=result,
        )
        await _emit_visit_action_event(
            db,
            contact_id=contact_id,
            visit_id=visit_id,
            event_type="visit_completed",
            user_id=user_id,
            extra_metadata={"result": result},
        )
        await VisitService._sync_contact_status(
            db, contact_id=contact_id, triggering_visit_id=visit_id,
        )
        await db.commit()
        return updated, None

    # ------------------------------------------------------------------
    # RESCHEDULE (atomic: cancel + insert in 1 transaction)
    # ------------------------------------------------------------------
    @staticmethod
    async def reschedule_visit(
        db: AsyncSession,
        *,
        visit_id: int,
        scheduled_at: datetime,
        user_id: int,
        notes: str | None = None,
    ) -> tuple[Visit | None, str | None]:
        """Reschedule = mark original 'cancelled' AND insert a new scheduled
        visit, same contact/property/agent, in ONE transaction. If the insert
        raises, the cancel rollbacks (we never commit() until both succeed).
        """
        if scheduled_at <= datetime.now(timezone.utc):
            return None, "La fecha debe ser futura"

        visit = await visit_repo.get_by_id(db, visit_id)
        if visit is None:
            return None, "Visita no encontrada"
        if visit.status != "scheduled":
            return None, "Visita no está en scheduled"

        # Snapshot fields BEFORE mutating (update_status invalidates the
        # original ORM instance via refresh).
        contact_id = visit.contact_id
        original_property_id = visit.property_id
        original_agent_user_id = visit.agent_user_id
        original_notes = visit.notes

        await visit_repo.update_status(
            db, visit_id=visit_id, new_status="cancelled",
        )
        final_notes = notes if notes is not None else original_notes
        new_visit = await visit_repo.insert(
            db,
            contact_id=contact_id,
            property_id=original_property_id,
            agent_user_id=original_agent_user_id,
            scheduled_at=scheduled_at,
            status="scheduled",
            source="panel",
            notes=final_notes,
        )
        # Only include the notes diff in metadata if it actually changed —
        # keeps the timeline metadata pane clean when only the date moved.
        notes_changed = (original_notes or "") != (final_notes or "")
        reschedule_meta: dict = {
            "old_visit_id": visit_id,
            "new_scheduled_at": scheduled_at.isoformat(),
        }
        if notes_changed:
            reschedule_meta["old_notes"] = original_notes or ""
            reschedule_meta["new_notes"] = final_notes or ""
        await _emit_visit_action_event(
            db,
            contact_id=contact_id,
            visit_id=new_visit.id,
            event_type="visit_rescheduled",
            user_id=user_id,
            extra_metadata=reschedule_meta,
        )
        await VisitService._sync_contact_status(
            db, contact_id=contact_id, triggering_visit_id=new_visit.id,
        )
        await db.commit()
        return new_visit, None

    # ------------------------------------------------------------------
    # LIST (read-only)
    # ------------------------------------------------------------------
    @staticmethod
    async def list_visits_for_contact(
        db: AsyncSession,
        *,
        contact_id: int,
    ) -> dict[str, list[Visit]]:
        """Group visits into UI sections (CONTEXT §2.4).

        Returns:
            {
                "proximas":  scheduled, ORDER BY scheduled_at ASC,
                "historico": done|cancelled|no_show, ORDER BY scheduled_at DESC,
            }
        """
        all_visits = await visit_repo.list_by_contact(db, contact_id)
        proximas = sorted(
            [v for v in all_visits if v.status == "scheduled"],
            key=lambda v: v.scheduled_at,
        )
        historico = sorted(
            [v for v in all_visits if v.status in ("done", "cancelled", "no_show")],
            key=lambda v: v.scheduled_at,
            reverse=True,
        )
        return {"proximas": proximas, "historico": historico}

    # ------------------------------------------------------------------
    # PRIVATE — contact.status invariant sync
    # ------------------------------------------------------------------
    @staticmethod
    async def _sync_contact_status(
        db: AsyncSession,
        *,
        contact_id: int,
        triggering_visit_id: int,
        source: str = "panel",
    ) -> None:
        """Sync contact.status with the existence of active visits.

        Logic (Plan 114-01 §2.2):
          Case A: has_active AND contact.status != 'visit_scheduled'
                  → filtered UPDATE; if rowcount==1, emit lead_event.
          Case B: has_active AND contact.status == 'visit_scheduled'
                  → no-op.
          Case C: NO active AND contact.status == 'visit_scheduled'
                  → no-op (no auto-transition per VISIT-05).
          Case D: NO active AND contact.status != 'visit_scheduled'
                  → no-op (not applicable).

        Plan 114 Trust-Filtered-UPDATE: NO SELECT FOR UPDATE, NO retry.
        Concurrency baja (1 admin + 4 agents); race fails silently
        (idempotent). Hardening → TECHNICAL_DEBT.md.

        NO COMMIT here — the caller commits.
        """
        has_active = await visit_repo.has_active_for_contact(db, contact_id)
        contact = await contact_repo.get_by_id(db, contact_id)
        if contact is None:
            # Should never happen — caller validated. Defensive no-op.
            return
        old_status = contact.status

        # Case A — the only case that touches the DB.
        if has_active and old_status != "visit_scheduled":
            result = await db.execute(
                update(Contact)
                .where(
                    Contact.id == contact_id,
                    Contact.status != "visit_scheduled",
                )
                .values(status="visit_scheduled")
            )
            if result.rowcount == 1:
                await lead_event_repo.create(
                    db,
                    contact_id=contact_id,
                    event_type="visit_status_change",
                    old_status=old_status,
                    new_status="visit_scheduled",
                    triggered_by=f"visit_service:{triggering_visit_id}",
                    metadata={
                        "visit_id": triggering_visit_id,
                        # D-1: thread the actual create source through (default
                        # 'panel' preserves behavior for cancel/complete/
                        # reschedule callers that don't pass source).
                        "source": source,
                    },
                )
        # Cases B, C, D: no-op.


visit_service = VisitService()
