import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Request, Depends, Query, Form, HTTPException
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.tz import PYT, get_templates
from app.utils.phone_utils import PREFIXES
from app.constants import VALID_STATUSES, BADGE_MAP
from app.repositories.user_repo import user_repo
from app.services.authz_service import ensure_contact_access
from app.services.contact_service import contact_service, ContactService
from app.services.contact_reminder_service import contact_reminder_service

logger = logging.getLogger(__name__)

router = APIRouter()
templates = get_templates()

@router.get("/contacts", response_class=HTMLResponse)
async def contacts_list(
    request: Request,
    page: int = Query(1, ge=1),
    status: str = Query(None),
    source: str = Query(None),
    search: str = Query(None),
    phone: str = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    per_page = 25
    phone_filter = phone if phone in ("with", "without") else None
    # feat(authz): agents only see their assigned contacts (ROLE-agent-list)
    agent_filter = user.id if user.role == "agent" else None
    result = await contact_service.get_contacts(
        db,
        status=status,
        source=source,
        search=search,
        phone_filter=phone_filter,
        page=page,
        per_page=per_page,
        agent_user_id=agent_filter,
    )

    # Ordered list for the status dropdown — excludes 'deleted' (not filterable
    # in the list view deliberately) but includes all VALID_STATUSES.
    _STATUS_ORDER = [
        "new", "bot_replied", "agent_replied", "visit_scheduled",
        "interested", "closed", "no_response", "discarded",
    ]
    filter_statuses = [s for s in _STATUS_ORDER if s in VALID_STATUSES]

    # C2.2 — fetch overdue reminders for current page in one query (no N+1).
    # Guard: if migration 044 has not been applied yet the table is absent;
    # degrade gracefully so the list still renders without the overdue dot.
    contact_ids = [c.id for c in result["contacts"]]
    try:
        overdue_contacts = await contact_reminder_service.get_overdue_contacts(db, contact_ids)
    except Exception:
        overdue_contacts = set()

    return templates.TemplateResponse("contacts.html", {
        "request": request,
        "user": user,
        "contacts": result["contacts"],
        "props_map": result["props_map"],
        "infocasas_props_map": result["infocasas_props_map"],
        "total": result["total"],
        "page": page,
        "per_page": per_page,
        "total_pages": result["total_pages"],
        "status_filter": status,
        "source_filter": source,
        "search": search,
        "phone_filter": phone_filter,
        "phone_prefixes": PREFIXES,
        "filter_statuses": filter_statuses,
        "badge_map": BADGE_MAP,
        "overdue_contacts": overdue_contacts,  # C2.2
    })


@router.get("/contacts/export")
async def export_contacts_csv(
    request: Request,
    status: str = Query(None),
    source: str = Query(None),
    search: str = Query(None),
    phone: str = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Export filtered contacts to CSV (C1.4).

    Respects the same filters as GET /contacts (status, source, search,
    phone, and the implicit agent_user_id authz filter for role='agent').
    Max 20 000 rows.  Role='user' is allowed (same access as the list view).
    """
    phone_filter = phone if phone in ("with", "without") else None
    agent_filter = user.id if user.role == "agent" else None

    csv_content, filename = await contact_service.export_csv(
        db,
        status=status,
        source=source,
        search=search,
        phone_filter=phone_filter,
        agent_user_id=agent_filter,
    )

    return Response(
        content=csv_content.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/contacts/bulk-status", response_class=HTMLResponse)
async def bulk_update_status(
    request: Request,
    ids: list[int] = Form(..., alias="ids[]"),
    new_status: str = Form(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cambiar estado de múltiples contactos en lote (C1.3).

    Form fields:
      - ids[] : lista de contact IDs seleccionados
      - new_status : estado destino (debe ser VALID_STATUSES, sin 'deleted')
    """
    if new_status == "deleted" or new_status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Status inválido para bulk")

    try:
        result = await contact_service.bulk_update_status(
            db,
            contact_ids=ids,
            new_status=new_status,
            user_id=user.id,
            user_email=user.email,
            user_role=user.role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    updated = result["updated"]
    skipped_optout = result["skipped_optout"]
    skipped_permission = result["skipped_permission"]

    parts: list[str] = [f"{updated} actualizado{'s' if updated != 1 else ''}"]
    omitidos: list[str] = []
    if skipped_optout:
        omitidos.append(f"{skipped_optout} opt-out")
    if skipped_permission:
        omitidos.append(f"{skipped_permission} sin permiso")
    if omitidos:
        parts.append(f"{sum([skipped_optout, skipped_permission])} omitido{'s' if sum([skipped_optout, skipped_permission]) != 1 else ''} ({', '.join(omitidos)})")

    summary = ", ".join(parts) + "."
    resp = HTMLResponse(
        f'<div class="text-sm text-green-700 bg-green-50 border border-green-200 rounded px-3 py-2">'
        f'{summary}'
        f'</div>'
    )
    resp.headers["HX-Trigger"] = "contactsTableRefresh"
    return resp


@router.get("/contacts/{contact_id}", response_class=HTMLResponse)
async def contact_detail(
    contact_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    detail = await contact_service.get_contact_detail(db, contact_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Contacto no encontrado")

    # M6.1 ROLE-13 (Plan 111-07) — agent solo puede ver contacts asignados a sí.
    # Admin siempre tiene acceso; role='user' (legacy) también tiene acceso al
    # detail (no es endpoint nuevo, mantener compatibilidad). El bloqueo aplica
    # SOLO a role='agent' que no es dueño del contact.
    if user.role == "agent" and detail["contact"].agent_user_id != user.id:
        raise HTTPException(
            status_code=403,
            detail="No tenés acceso a este contact",
        )

    # Side-effect: si agent dueño abre detail → marcar como visto. Esto hace
    # desaparecer el badge "Nuevo" del row en /leads en próximo render.
    # Solo aplica a role='agent' (admin abre sin alterar agent_seen_at).
    if user.role == "agent":
        await contact_service.mark_seen_by_agent(db, contact_id, user.id)

    # Extract search history from conversations (search_context is loaded on the ORM model)
    search_history: list[dict] = []
    for conv in detail.get("conversations", []):
        ctx = conv.search_context or {}
        historicas = ctx.get("busquedas_historicas")
        if isinstance(historicas, list):
            search_history.extend(historicas)
    search_history.sort(key=lambda x: x.get("fecha", ""), reverse=True)

    all_users = await user_repo.get_all(db, active=None)
    users_map = {u.id: u.name for u in all_users}
    # Phase 116 — UAT: contacts_detail renders the assigned agent's
    # display_name (Ez chose strict display_name, no name/email fallback).
    agents_display_map = {u.id: (u.display_name or "") for u in all_users}

    # Carril G — notas y recordatorios salen del mismo carril cronologico.
    followup_ctx = await _followup_ctx(db, contact_id)

    return templates.TemplateResponse("contacts_detail.html", {
        "request": request,
        "user": user,
        "contact": detail["contact"],
        "grouped_events": detail["grouped_events"],
        "conversations": detail["conversations"],
        "valid_statuses": VALID_STATUSES,
        "linked_property": detail["linked_property"],
        "ic_property": detail["ic_property"],
        "phone_info": detail["phone_info"],
        "phone_prefixes": PREFIXES,
        "search_history": search_history,
        "viewed_properties": detail["viewed_properties"],
        "inquiry_history": detail.get("inquiry_history", []),
        "contact_id": contact_id,
        "users_map": users_map,
        "agents_display_map": agents_display_map,
        "has_active_visit": detail.get("has_active_visit", False),  # M6.2 OQ-14
        **followup_ctx,  # followup (notas + recordatorios), notes, reminders, overdue_ids
    })


@router.get("/contacts/{contact_id}/status-block", response_class=HTMLResponse)
async def contact_status_block(
    contact_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-render the status badge + dropdown + lockout area.

    Used by HX-Trigger=refreshStatusBlock fired from every visit POST
    endpoint so the badge and the has_active_visit lockout flag stay
    in sync with the live `contacts` / `visits` state.
    """
    detail = await contact_service.get_contact_detail(db, contact_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Contacto no encontrado")
    if user.role == "agent" and detail["contact"].agent_user_id != user.id:
        raise HTTPException(status_code=403, detail="No tenés acceso a este contact")

    return templates.TemplateResponse(
        "partials/contact_status_block.html",
        {
            "request": request,
            "contact": detail["contact"],
            "has_active_visit": detail.get("has_active_visit", False),
        },
    )


@router.get("/contacts/{contact_id}/events", response_class=HTMLResponse)
async def contact_events_partial(
    contact_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # feat(authz): agent ownership check (ROLE-agent-read)
    await ensure_contact_access(db, user, contact_id)
    all_events = await contact_service.get_all_events(db, contact_id)
    grouped_events = ContactService.build_grouped_timeline(all_events)
    all_users = await user_repo.get_all(db)
    users_map = {u.id: u.name for u in all_users}
    return templates.TemplateResponse("partials/events_timeline.html", {
        "request": request,
        "grouped_events": grouped_events,
        "users_map": users_map,
    })


@router.post("/contacts/{contact_id}/notes", response_class=HTMLResponse)
async def create_note(
    contact_id: int,
    request: Request,
    content: str = Form(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # feat(authz): agent ownership check (ROLE-agent-write)
    await ensure_contact_access(db, user, contact_id)
    note, error = await contact_service.create_note(db, contact_id, content, user.id)
    if error:
        return HTMLResponse(f'<p class="text-red-500 text-sm px-1">{error}</p>', status_code=400)
    response = templates.TemplateResponse(
        "partials/crm_followup.html",
        {"request": request, **await _followup_ctx(db, contact_id)},
    )
    response.headers["HX-Trigger"] = "refreshEvents"
    return response


@router.patch("/contacts/{contact_id}/notes/{note_id}", response_class=HTMLResponse)
async def update_note(
    contact_id: int,
    note_id: int,
    request: Request,
    content: str = Form(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # feat(authz): agent ownership check (ROLE-agent-write)
    await ensure_contact_access(db, user, contact_id)
    note, error = await contact_service.update_note(db, note_id, content, user.id)
    if error:
        return HTMLResponse(f'<p class="text-red-500 text-sm px-1">{error}</p>', status_code=400)
    response = templates.TemplateResponse(
        "partials/crm_followup.html",
        {"request": request, **await _followup_ctx(db, contact_id)},
    )
    response.headers["HX-Trigger"] = "refreshEvents"
    return response


@router.delete("/contacts/{contact_id}/notes/{note_id}", response_class=HTMLResponse)
async def delete_note(
    contact_id: int,
    note_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # feat(authz): agent ownership check (ROLE-agent-write)
    await ensure_contact_access(db, user, contact_id)
    success, error = await contact_service.delete_note(db, note_id, user.id)
    if error:
        return HTMLResponse(f'<p class="text-red-500 text-sm px-1">{error}</p>', status_code=400)
    response = templates.TemplateResponse(
        "partials/crm_followup.html",
        {"request": request, **await _followup_ctx(db, contact_id)},
    )
    response.headers["HX-Trigger"] = "refreshEvents"
    return response


@router.post("/contacts/{contact_id}/status", response_class=HTMLResponse)
async def update_contact_status(
    contact_id: int,
    request: Request,
    status: str = Form(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # feat(authz): agent ownership check (ROLE-agent-write)
    await ensure_contact_access(db, user, contact_id)
    updated, error = await contact_service.update_status(
        db,
        contact_id=contact_id,
        new_status=status,
        user_id=user.id,
        user_email=user.email,
        user_role=user.role,
    )
    if error:
        return HTMLResponse(f'<span class="text-red-500 text-xs">{error}</span>')

    variante, label = BADGE_MAP.get(status, ("quiet", status))
    html = (
        f'<span class="badge badge--{variante}">{label} ✓</span>'
        f'<span id="current-status-badge" hx-swap-oob="innerHTML">'
        f'<span class="badge badge--{variante}">{label}</span>'
        f'</span>'
    )
    resp = HTMLResponse(html)
    resp.headers["HX-Trigger"] = "refreshEvents"
    return resp


@router.post("/contacts", response_class=HTMLResponse)
async def create_contact(
    request: Request,
    name: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    status: str = Form("new"),
    operacion: str = Form(""),
    zona: str = Form(""),
    presupuesto: str = Form(""),
    dormitorios: str = Form(""),
    property_id: int | None = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    name = name.strip()
    phone = phone.strip()
    email = email.strip() or None
    status = status.strip()
    operacion = operacion.strip() or None
    zona = zona.strip() or None
    presupuesto_raw = presupuesto.strip()
    dormitorios_raw = dormitorios.strip()

    def _validation_error_response(msg: str) -> HTMLResponse:
        return HTMLResponse(f'<p class="text-red-500 text-sm mt-1">{msg}</p>')

    try:
        contact, error = await contact_service.create_contact(
            db,
            name=name,
            phone=phone,
            email=email,
            status=status,
            operacion=operacion,
            zona=zona,
            presupuesto_raw=presupuesto_raw,
            dormitorios_raw=dormitorios_raw,
            user_id=user.id,
            user_email=user.email,
            user_role=user.role,
            property_id=property_id,
        )
    except ValueError as exc:
        return _validation_error_response(str(exc))
    if error:
        return _validation_error_response(error)

    try:
        from app.services.event_bus import event_bus as _event_bus
        await _event_bus.publish("contact.created", {
            "contact_id": contact.id,
            "name": name,
            "source": "manual",
            "status": status,
            "user_id": user.id,
            "user_name": user.name or user.email,
        })
    except Exception:
        pass

    resp = Response(status_code=204)
    resp.headers["HX-Redirect"] = f"/contacts/{contact.id}"
    return resp


@router.post("/contacts/{contact_id}/update", response_class=HTMLResponse)
async def update_contact(
    contact_id: int,
    request: Request,
    name: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    operacion: str = Form(""),
    zona: str = Form(""),
    presupuesto: str = Form(""),
    dormitorios: str = Form(""),
    property_id: int | None = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # feat(authz): agent ownership check (ROLE-agent-write)
    await ensure_contact_access(db, user, contact_id)
    name = name.strip() or None
    phone = phone.strip() or None
    email = email.strip() or None
    operacion = operacion.strip() or None
    zona = zona.strip() or None
    presupuesto_raw = presupuesto.strip()
    dormitorios_raw = dormitorios.strip()

    try:
        ok, error, has_changes = await contact_service.update_contact(
            db,
            contact_id=contact_id,
            name=name,
            phone=phone,
            email=email,
            operacion=operacion,
            zona=zona,
            presupuesto_raw=presupuesto_raw,
            dormitorios_raw=dormitorios_raw,
            user_id=user.id,
            user_email=user.email,
            property_id=property_id,
        )
    except ValueError as exc:
        return HTMLResponse(
            f'<p class="text-red-500 text-sm">{exc}</p>',
            status_code=200,
        )
    if not ok:
        status_code = 404 if error == "Contacto no encontrado" else 200
        return HTMLResponse(
            f'<p class="text-red-500 text-sm">{error}</p>',
            status_code=status_code,
        )

    try:
        from app.services.event_bus import event_bus as _event_bus
        await _event_bus.publish("contact.updated", {
            "contact_id": contact_id,
            "user_id": user.id,
            "user_name": user.name or user.email,
        })
    except Exception:
        pass

    triggers = ["contactUpdated"]
    if has_changes:
        triggers.append("refreshEvents")
    resp = HTMLResponse('<p class="text-green-600 text-sm">Cambios guardados ✓</p>')
    resp.headers["HX-Trigger"] = ", ".join(triggers)
    return resp


def _reminders_ctx(reminders: list, contact_id: int) -> dict:
    """Build template context for crm_followup.html, computing overdue_ids inline."""
    now = datetime.now(timezone.utc)
    overdue_ids = {
        r.id
        for r in reminders
        if r.done_at is None and r.due_at is not None
        and (r.due_at if r.due_at.tzinfo else r.due_at.replace(tzinfo=timezone.utc)) <= now
    }
    return {"reminders": reminders, "contact_id": contact_id, "overdue_ids": overdue_ids}


def _aware(dt: datetime | None) -> datetime:
    """UTC-aware, o el principio de los tiempos si no hay fecha — para ordenar."""
    if dt is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _followup_ctx(db: AsyncSession, contact_id: int) -> dict:
    """Carril G — notas y recordatorios en una sola lista cronologica.

    Eran dos cards con el mismo esqueleto y dos ids duplicados. Se ordenan
    descendente por su fecha propia (la nota por cuando se escribio, el
    recordatorio por cuando vence), asi que lo que todavia no paso queda
    arriba y el historial abajo.

    Guard: si la migracion 044 no corrio, la tabla de recordatorios no
    existe y la pantalla igual tiene que renderizar — el mismo degradado
    que ya hacia el detalle.
    """
    notes = await contact_service.get_notes(db, contact_id)
    try:
        reminders = await contact_reminder_service.list_reminders(db, contact_id)
    except Exception:
        reminders = []
    items = [{"kind": "nota", "at": _aware(n.created_at), "obj": n} for n in notes]
    items += [{"kind": "recordatorio", "at": _aware(r.due_at), "obj": r} for r in reminders]
    items.sort(key=lambda i: i["at"], reverse=True)
    return {"followup": items, "notes": notes, **_reminders_ctx(reminders, contact_id)}


@router.post("/contacts/{contact_id}/reminders", response_class=HTMLResponse)
async def create_reminder(
    contact_id: int,
    request: Request,
    due_at: str = Form(...),
    note: str = Form(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a follow-up reminder for a contact (C2.2)."""
    await ensure_contact_access(db, user, contact_id)

    # El <input type="datetime-local"> manda hora local, no UTC: leerla como
    # UTC corre el recordatorio 3 o 4 h. visits.py:47 ya lo hace asi.
    try:
        due_at_dt = datetime.fromisoformat(due_at).replace(tzinfo=PYT)
    except ValueError:
        return HTMLResponse(
            '<p class="text-red-500 text-sm px-1">Fecha inválida</p>', status_code=400
        )

    reminder, error = await contact_reminder_service.create_reminder(
        db,
        contact_id=contact_id,
        user_id=user.id,
        due_at=due_at_dt,
        note=note,
    )
    if error:
        return HTMLResponse(
            f'<p class="text-red-500 text-sm px-1">{error}</p>', status_code=400
        )
    return templates.TemplateResponse(
        "partials/crm_followup.html",
        {"request": request, **await _followup_ctx(db, contact_id)},
    )


@router.get("/contacts/{contact_id}/reminders", response_class=HTMLResponse)
async def list_reminders(
    contact_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the reminders partial for HTMX refresh (C2.2)."""
    await ensure_contact_access(db, user, contact_id)
    return templates.TemplateResponse(
        "partials/crm_followup.html",
        {"request": request, **await _followup_ctx(db, contact_id)},
    )


@router.post("/contacts/{contact_id}/reminders/{reminder_id}/done", response_class=HTMLResponse)
async def mark_reminder_done(
    contact_id: int,
    reminder_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark a reminder as done (C2.2)."""
    await ensure_contact_access(db, user, contact_id)
    _reminder, error = await contact_reminder_service.mark_done(db, reminder_id, user.id)
    if error:
        return HTMLResponse(
            f'<p class="text-red-500 text-sm px-1">{error}</p>', status_code=404
        )
    return templates.TemplateResponse(
        "partials/crm_followup.html",
        {"request": request, **await _followup_ctx(db, contact_id)},
    )


@router.delete("/contacts/{contact_id}/reminders/{reminder_id}", response_class=HTMLResponse)
async def delete_reminder(
    contact_id: int,
    reminder_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a reminder (C2.2)."""
    await ensure_contact_access(db, user, contact_id)
    ok, error = await contact_reminder_service.delete_reminder(db, reminder_id, user.id)
    if not ok:
        return HTMLResponse(
            f'<p class="text-red-500 text-sm px-1">{error}</p>', status_code=404
        )
    return templates.TemplateResponse(
        "partials/crm_followup.html",
        {"request": request, **await _followup_ctx(db, contact_id)},
    )


@router.post("/contacts/{contact_id}/delete", response_class=HTMLResponse)
async def delete_contact(
    contact_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # feat(authz): agent ownership check (ROLE-agent-write)
    await ensure_contact_access(db, user, contact_id)
    ok, error = await contact_service.delete_contact(
        db,
        contact_id=contact_id,
        user_id=user.id,
        user_email=user.email,
        user_role=user.role,
    )
    if not ok:
        return HTMLResponse(
            f'<p class="text-red-500 text-sm">{error}</p>', status_code=404
        )

    try:
        from app.services.event_bus import event_bus as _event_bus
        await _event_bus.publish("contact.deleted", {
            "contact_id": contact_id,
            "user_id": user.id,
            "user_name": user.name or user.email,
        })
    except Exception:
        pass

    resp = Response(status_code=204)
    resp.headers["HX-Redirect"] = "/contacts"
    return resp
