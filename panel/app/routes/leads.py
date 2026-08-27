import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Depends, Query, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.dependencies import require_agent_or_admin
from app.repositories.user_repo import user_repo
from app.services.authz_service import ensure_contact_access
from app.services.lead_service import lead_service, VALID_LEAD_TABS
from app.services.lead_export_service import build_leads_xlsx, export_filename
from app.models.user import User
from app.models.contact import Contact
from app.utils.pagination import calculate_total_pages
from app.tz import get_templates

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

logger = logging.getLogger(__name__)

router = APIRouter()
templates = get_templates()


def _sanitize_lead_filters(
    q: str | None, source: str | None, agent_id: str | None,
) -> tuple[str | None, str | None, int | None]:
    """LEADS-03 — normalize the search/filter query params.

    - q: strip + cap at 80 chars; empty → None.
    - source: strip + cap (passthrough — the repo binds it as a parameter,
      so unknown values just match nothing).
    - agent_id: int válido o None (garbage nunca debe romper la página).
    """
    q = (q or "").strip()[:80] or None
    source = (source or "").strip()[:40] or None
    try:
        agent_id_val = int(agent_id) if agent_id not in (None, "") else None
    except (TypeError, ValueError):
        agent_id_val = None
    return q, source, agent_id_val


def _filter_qs(q: str | None, source: str | None, agent_id: int | None) -> str:
    """Querystring suffix ('&q=…&source=…&agent_id=…') que tabs, paginación
    y export anteponen a sus links para preservar los filtros activos."""
    params = {}
    if q:
        params["q"] = q
    if source:
        params["source"] = source
    if agent_id is not None:
        params["agent_id"] = agent_id
    return "&" + urlencode(params) if params else ""


@router.get("/leads", response_class=HTMLResponse)
async def leads_page(
    request: Request,
    tab: str = Query("interesados"),
    page: int = Query(1, ge=1),
    q: str | None = Query(None),
    source: str | None = Query(None),
    agent_id: str | None = Query(None),
    user: User = Depends(require_agent_or_admin),
    db: AsyncSession = Depends(get_db),
):
    """Plan 111-03 + 111-04 — Leads UI con 4 tabs M6.1.

    Tabs (admin):
        - leads        → status IN ('new','bot_replied') AND agent_user_id IS NULL
                         (renombrado visualmente a "Nuevos"; el param NO cambia)
        - interesados  → status='interested' AND agent_user_id IS NULL
        - asignados    → agent_user_id IS NOT NULL (any status — ROLE-10)

    Default sin ?tab= : 'interesados' (leads-workqueue 2026-06 — la plata
    caliente primero; antes era 'leads').

    Comportamiento por rol:
        - admin: ve los 3 tabs + dropdown "Asignar a…" + badge counters.
        - agent (Plan 111-04, ROLE-09): "Mis Asignados" único view.
          tab y ?tab= se ignoran — siempre se fuerza tab='asignados' +
          agent_filter=user.id. tab_counts y assignable_users vacíos
          (template oculta tabs y dropdown vía user.role == 'admin').
        - user:  bloqueado por require_agent_or_admin (403).
    """
    # Normalizar tab → default 'interesados' si inválido (= default sin ?tab=).
    if tab not in VALID_LEAD_TABS:
        tab = "interesados"

    # LEADS-03 — sanitizar búsqueda/filtros.
    q, source, agent_id_val = _sanitize_lead_filters(q, source, agent_id)

    per_page = 25
    agent_filter: int | None = None

    if user.role == "agent":
        # ROLE-09: agent SOLO ve sus contacts asignados. Cualquier ?tab= se
        # ignora — el template renderiza la vista "Mis Asignados" (sin tabs,
        # sin dropdown Asignar, empty-state propio). El filtro de asesor es
        # solo-admin: un agent no puede mirar la cola de otro.
        tab = "asignados"
        agent_filter = user.id
        agent_id_val = None

    logger.info(
        "Leads listed: user=%s role=%s tab=%s page=%s agent_filter=%s q=%s source=%s agent_id=%s",
        user.email, user.role, tab, page, agent_filter, q, source, agent_id_val,
    )

    try:
        leads, total = await lead_service.list_leads_by_tab(
            db, tab, agent_filter=agent_filter, page=page, per_page=per_page,
            q=q, source=source, agent_id=agent_id_val,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    total_pages = calculate_total_pages(total, per_page)

    # Badge counters (admin only — agent solo ve un tab).
    if user.role == "admin":
        tab_counts = await lead_service.count_leads_per_tab(db)
        # Dropdown "Asignar a…" requires the list of assignable users.
        assignable_users = await user_repo.list_active_assignable(db)
        # Phase 116 — UAT request. lead_item.html renders the assigned agent
        # display_name as a sub-line under the lead's name. We include
        # inactive users in the lookup so old assignments still resolve.
        all_users = await user_repo.get_all(db, active=None)
        agents_display_map = {u.id: (u.display_name or "") for u in all_users}
    else:
        tab_counts = {}
        assignable_users = []
        agents_display_map = {}

    context = {
        "request": request,
        "user": user,
        "leads": leads,
        "tab": tab,
        "tab_counts": tab_counts,
        "assignable_users": assignable_users,
        "agents_display_map": agents_display_map,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        # LEADS-03 — filtros activos + querystring para preservarlos en
        # tabs/paginación/export.
        "q": q,
        "source": source,
        "agent_id": agent_id_val,
        "filter_qs": _filter_qs(q, source, agent_id_val),
    }

    if request.headers.get("HX-Request"):
        # LEADS-04 — el partial HTMX incluye tabla (desktop) + cards (mobile)
        # para que el refresh SSE nunca desincronice las dos vistas.
        return templates.TemplateResponse("partials/leads_views.html", context)

    return templates.TemplateResponse("leads.html", context)


@router.get("/leads/export")
async def export_leads_xlsx(
    request: Request,
    tab: str = Query("leads"),
    q: str | None = Query(None),
    source: str | None = Query(None),
    agent_id: str | None = Query(None),
    user: User = Depends(require_agent_or_admin),
    db: AsyncSession = Depends(get_db),
):
    """Export the currently-filtered leads to an .xlsx file.

    Chunk 2 LEADS-03 — habla el vocabulario M6.1 de la página
    (tab=leads|interesados|asignados|sin_respuesta) + q/source/agent_id,
    por el MISMO WHERE-building que GET /leads (lead_service.
    list_leads_for_export → lead_repo.get_by_tab). El vocabulario legacy
    (tab=all|interested|agent_replied + status libre) fue eliminado:
    'agent_replied' era branch muerto desde mig 018 y la página ya no
    genera esos links; tab desconocido → default 'leads'.

    Mismo enforcement de rol que la página: agent exporta SOLO su bucket
    de asignados. No pagination — full filtered set (cap EXPORT_MAX_ROWS).
    """
    if tab not in VALID_LEAD_TABS:
        tab = "leads"
    q, source, agent_id_val = _sanitize_lead_filters(q, source, agent_id)

    agent_filter: int | None = None
    if user.role == "agent":
        tab = "asignados"
        agent_filter = user.id
        agent_id_val = None

    leads = await lead_service.list_leads_for_export(
        db, tab=tab, agent_filter=agent_filter,
        q=q, source=source, agent_id=agent_id_val,
    )

    logger.info(
        "Leads exported: user=%s tab=%s q=%s source=%s agent_id=%s rows=%d",
        user.email, tab, q, source, agent_id_val, len(leads),
    )

    xlsx_bytes = build_leads_xlsx(leads)
    filename = export_filename(tab, source, None)
    return Response(
        content=xlsx_bytes,
        media_type=XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/leads/badges")
async def leads_badges(
    request: Request,
    user: User = Depends(require_agent_or_admin),
    db: AsyncSession = Depends(get_db),
):
    """Return badge counts for the M6.1 3 tabs as JSON.

    Plan 111-03 — SSE-driven badge refresher in leads.html consumes these
    counts to update the header badges without a full reload.
    Solo admin necesita los 3 counts; agent ve solo "Mis Asignados" (Plan 111-04).
    """
    if user.role == "admin":
        counts = await lead_service.count_leads_per_tab(db)
    else:
        # Agent: solo conoce su propio bucket de asignados.
        from app.repositories.lead_repo import lead_repo as _lr
        counts = {
            "leads": 0,
            "interesados": 0,
            "asignados": await _lr.count_by_tab(db, "asignados", agent_filter=user.id),
            "sin_respuesta": 0,
        }
    return JSONResponse(counts)


@router.post("/leads/{contact_id}/status", response_class=HTMLResponse)
async def update_lead_status(
    contact_id: int,
    request: Request,
    status: str = Form(...),
    user: User = Depends(require_agent_or_admin),
    db: AsyncSession = Depends(get_db),
):
    # Validate status first (no DB hit) to preserve 400 before 403/404 checks.
    from app.constants import VALID_STATUSES as _VALID_STATUSES
    if status not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Estado invalido")
    # feat(authz): agent ownership check (ROLE-agent-write)
    await ensure_contact_access(db, user, contact_id)
    contact, error = await lead_service.change_status(db, contact_id, status, user.id)
    # STAB-01 (TD-115-01): make the write path self-contained. On any error
    # branch roll back explicitly so the transaction is never left open
    # (closes the idle-in-transaction window deterministically instead of
    # relying solely on get_db's commit-on-yield-exit / rollback-on-exception).
    if error == "invalid_status":
        await db.rollback()
        raise HTTPException(status_code=400, detail="Estado invalido")
    if error == "not_found":
        await db.rollback()
        raise HTTPException(status_code=404, detail="Lead no encontrado")
    if error == "baja":
        await db.rollback()
        raise HTTPException(status_code=400, detail="Contacto con baja: el opt-out es irreversible")
    # STAB-01: persist the status change here, not only via get_db yield-exit
    # (mirrors agent_assign's explicit commit).
    await db.commit()
    logger.info("Lead status changed: id=%d new_status=%s user=%s", contact_id, status, user.email)

    try:
        from app.services.event_bus import event_bus as _event_bus
        await _event_bus.publish("lead.status_changed", {
            "contact_id": contact_id,
            "old_status": None,
            "new_status": status,
            "agent_user_id": None,
            "user_id": user.id,
            "user_name": user.name or user.email,
        })
    except Exception:
        pass

    # Fetch lead dict with property/IC joins via repo
    lead = await lead_service.get_lead_with_property(db, contact_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead no encontrado")

    all_users = await user_repo.get_all(db, active=None)
    agents_display_map = {u.id: (u.display_name or "") for u in all_users}
    assignable_users = await user_repo.list_active_assignable(db)
    return templates.TemplateResponse("partials/lead_item.html", {
        "request": request, "user": user, "lead": lead,
        "assignable_users": assignable_users, "tab": "leads",
        "agents_display_map": agents_display_map,
    })


@router.post("/leads/{contact_id}/agent-assign", response_class=HTMLResponse)
async def agent_assign(
    contact_id: int,
    request: Request,
    target_user_id: int | None = Form(None),
    user: User = Depends(require_agent_or_admin),
    db: AsyncSession = Depends(get_db),
):
    """Plan 111-03 §6.3 — asignar contact a un user (admin|agent activo).

    Body: target_user_id (Form). Si el cliente legacy no lo envía
    (WA button en lead_item.html), se asume self-assign del caller.

    Permission matrix:
        admin → puede asignar a cualquier user activo con role admin|agent.
        agent → solo puede self-asignarse; target_user_id != self.id → 403.

    Side effects:
        contact.agent_user_id    = target_user_id
        contact.agent_assigned_at = NOW()  (ROLE-15 spec §10.1)
        Promueve status si está en {new, no_response, bot_replied} → agent_replied.
        Desactiva bot para todas las conversations del contact.
        Crea lead_event (event_type='agent_assigned') para auditoría.
    """
    from sqlalchemy import text as sa_text, select, func
    from app.repositories.lead_event_repo import lead_event_repo

    # ── Resolve target_user_id (default: self) ──
    if target_user_id is None:
        target_user_id = user.id

    # ── Permission check (agent only self) ──
    if user.role == "agent" and target_user_id != user.id:
        raise HTTPException(
            status_code=403, detail="Solo podés auto-asignarte",
        )

    # ── Validate target user exists + is active + has assignable role ──
    target_user = await user_repo.get_by_id(db, target_user_id)
    if (
        target_user is None
        or not target_user.is_active
        or target_user.role not in ("admin", "agent")
    ):
        raise HTTPException(status_code=400, detail="Target user inválido")

    # ── Load contact ──
    result = await db.execute(select(Contact).where(Contact.id == contact_id))
    contact = result.scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Lead no encontrado")

    # ── Mutate ──
    old_status = contact.status
    # 'contacted' removido (deuda anotada): estado muerto post-mig-018, el
    # CHECK de contacts ya no lo permite — era branch inalcanzable.
    _PROMOTABLE = ("new", "no_response", "bot_replied")
    if old_status in _PROMOTABLE:
        contact.status = "agent_replied"

    contact.agent_user_id = target_user_id
    contact.agent_assigned_at = func.now()  # ROLE-15
    await db.flush()

    # Deactivate bot for all conversations of this contact
    await db.execute(
        sa_text(
            "UPDATE conversations SET is_bot_active = false "
            "WHERE contact_id = :id"
        ),
        {"id": contact_id},
    )

    # ── Audit ──
    await lead_event_repo.create(
        db=db,
        contact_id=contact_id,
        event_type="agent_assigned",
        old_status=old_status,
        new_status=contact.status,
        triggered_by=f"user:{user.id}",
        metadata={
            "agent_user_id": target_user_id,
            "assigned_by_user_id": user.id,
        },
    )
    await db.commit()

    # ── Event bus (best-effort) ──
    try:
        from app.services.event_bus import event_bus as _event_bus
        await _event_bus.publish("lead.agent_assigned", {
            "contact_id": contact_id,
            "agent_user_id": target_user_id,
            "agent_name": target_user.name or target_user.email,
            "user_id": user.id,
            "user_name": user.name or user.email,
        })
        await _event_bus.publish("lead.status_changed", {
            "contact_id": contact_id,
            "old_status": old_status,
            "new_status": contact.status,
            "agent_user_id": target_user_id,
            "user_id": user.id,
            "user_name": user.name or user.email,
        })
    except Exception:
        pass

    # ── Return updated row (HTMX swap) ──
    lead = await lead_service.get_lead_with_property(db, contact_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead no encontrado")

    all_users = await user_repo.get_all(db, active=None)
    agents_display_map = {u.id: (u.display_name or "") for u in all_users}
    assignable_users = await user_repo.list_active_assignable(db)
    return templates.TemplateResponse(
        "partials/lead_item.html",
        {"request": request, "user": user, "lead": lead,
         "assignable_users": assignable_users, "tab": "asignados",
         "agents_display_map": agents_display_map},
    )
