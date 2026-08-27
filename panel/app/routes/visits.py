"""Routes for the `visits` resource (M6.2 — Plan 114-01 §3, Plan 115-03).

5 endpoints — 4 POST (create / cancel / complete / reschedule) and 1 GET
(partial). All require role 'admin' or 'agent' (require_agent_or_admin).
POST endpoints return HTML partial + HX-Trigger header carrying
{refreshVisits, refreshEvents, showToast} JSON; GET returns the partial
with no HX-Trigger (lazy-load by 115-04 UI).

agent_user_id for create_visit (UAT-fix-forward in Phase 116):
- role='agent' → ALWAYS forced to session user (form value ignored).
- role='admin' → form value honored if it resolves to an active user with
  role in (admin, agent); empty form → defaults to session user.
- Default pre-selection in the create modal: `contact.agent_user_id` if
  set, else session user.
"""
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_agent_or_admin
from app.models.contact import Contact
from app.models.user import User
from app.repositories.contact_repo import contact_repo
from app.repositories.user_repo import user_repo
from app.repositories.visit_repo import visit_repo
from app.services.authz_service import ensure_contact_access, ensure_visit_access
from app.services.contact_service import contact_service
from app.services.visit_service import VisitService
from app.tz import get_templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["visits"])
templates = get_templates()

_ASUNCION = ZoneInfo("America/Asuncion")


def _parse_local_dt(raw: str) -> datetime | None:
    """Parse 'YYYY-MM-DDTHH:MM' from a datetime-local input as America/Asuncion.

    Returns None if the string is not parseable so callers can render the
    inline error partial (no HX-Trigger on validation failures).
    """
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M").replace(tzinfo=_ASUNCION)
    except (ValueError, TypeError):
        return None


def _hx_trigger_payload(toast: str) -> str:
    """Build the HX-Trigger JSON value used by every successful write endpoint.

    Plan 114 §3.1 + RESEARCH §1.3/§1.4 — keys MUST be camelCase. Alpine
    listener uses '@show-toast.window' (kebab-case event), HTMX dispatches
    both forms automatically when JSON payload uses camelCase keys.
    """
    return json.dumps({
        "refreshVisits": True,
        "refreshEvents": True,
        "refreshStatusBlock": True,
        "showToast": {"type": "success", "message": toast},
    })


def _build_property_options(*_args, **_kwargs) -> list[dict]:
    """Sin catálogo no hay propiedades que ofrecer al agendar una visita.

    Devolvía las propiedades candidatas del contacto (la vinculada, la de
    InfoCasas, y las que había visto) para el selector del modal. Se fue con el
    vertical inmobiliario. La visita sigue existiendo: es una reunión con un
    contacto, y `property_id` quedó opcional y siempre en None.
    """
    return []

async def _build_users_map(db: AsyncSession) -> dict[int, str]:
    """Map of {user.id: display_name_or_name} for "Reg. por: …" labels."""
    users = await user_repo.get_all(db, active=None)
    out: dict[int, str] = {}
    for u in users:
        out[u.id] = u.display_name or u.name or u.email or f"user:{u.id}"
    return out


async def _build_agent_options(
    db: AsyncSession, contact: Contact, current_user: User
) -> tuple[list[dict], int]:
    """Options + default for the "Asesor que va" select in the create modal.

    Phase 116 UAT fix-forward (replaces OQ-6 session-only attribution).
    Defaults precedence: `contact.agent_user_id` → `current_user.id`.
    Options visible:
      - admin: all active users with role in (admin, agent).
      - agent: only the current user (field renders disabled).
    """
    default_id = contact.agent_user_id or current_user.id

    if current_user.role == "agent":
        label = (current_user.display_name
                 or current_user.name
                 or current_user.email
                 or f"user:{current_user.id}")
        return [{"id": current_user.id, "label": label}], current_user.id

    users = await user_repo.get_all(db, active=True)
    options = [
        {
            "id": u.id,
            "label": u.display_name or u.name or u.email or f"user:{u.id}",
        }
        for u in users
        if u.role in ("admin", "agent")
    ]
    return options, default_id


async def _render_visits_block(
    request: Request,
    db: AsyncSession,
    user: User,
    contact_id: int,
    toast: str | None = None,
) -> HTMLResponse:
    """Render `partials/visits_block.html` with full context.

    Used by every write endpoint (with toast) and the GET partial (no toast).
    Sets the HX-Trigger header when `toast` is provided.
    """
    contact = await contact_repo.get_by_id(db, contact_id)
    if contact is None:
        return HTMLResponse(
            '<p class="text-red-500 text-sm">Contacto no encontrado</p>',
            status_code=404,
        )

    bucketed = await VisitService.list_visits_for_contact(db, contact_id=contact_id)
    property_options = await _build_property_options(db, contact_id)
    agent_options, default_agent_id = await _build_agent_options(db, contact, user)
    users_map = await _build_users_map(db)
    now_iso_local = datetime.now(_ASUNCION).strftime("%Y-%m-%dT%H:%M")

    response = templates.TemplateResponse(
        "partials/visits_block.html",
        {
            "request": request,
            "user": user,
            "contact": contact,
            "proximas": bucketed["proximas"],
            "historico": bucketed["historico"],
            "property_options": property_options,
            "agent_options": agent_options,
            "default_agent_id": default_agent_id,
            "users_map": users_map,
            "now_iso_local": now_iso_local,
        },
    )
    if toast:
        response.headers["HX-Trigger"] = _hx_trigger_payload(toast)
    return response


# ---------------------------------------------------------------------------
# POST /contacts/{id}/visits — create
# ---------------------------------------------------------------------------


@router.post("/contacts/{contact_id}/visits", response_class=HTMLResponse)
async def create_visit(
    contact_id: int,
    request: Request,
    scheduled_at: str = Form(...),
    property_id: str = Form(""),
    notes: str = Form(""),
    agent_user_id: str = Form(""),
    user: User = Depends(require_agent_or_admin),
    db: AsyncSession = Depends(get_db),
):
    """Plan 114 §3.2 — create a visit for a contact.

    Source is ALWAYS 'panel'. agent_user_id attribution (Phase 116):
    - role='agent' → forced to session user (form ignored).
    - role='admin' → honors form value if it resolves to an active user
      with role in (admin, agent); empty form → defaults to session.
    """
    # feat(authz): agent ownership check (ROLE-agent-write)
    await ensure_contact_access(db, user, contact_id)
    parsed = _parse_local_dt(scheduled_at)
    if parsed is None:
        return HTMLResponse(
            '<p class="text-red-500 text-sm">Fecha inválida</p>',
            status_code=400,
        )

    pid: int | None = None
    if property_id:
        try:
            pid = int(property_id)
        except (ValueError, TypeError):
            return HTMLResponse(
                '<p class="text-red-500 text-sm">Propiedad inválida</p>',
                status_code=400,
            )

    if user.role == "agent":
        attributed_agent_id = user.id
    elif agent_user_id:
        try:
            requested_id = int(agent_user_id)
        except (ValueError, TypeError):
            return HTMLResponse(
                '<p class="text-red-500 text-sm">Asesor inválido</p>',
                status_code=400,
            )
        requested_user = await user_repo.get_by_id(db, requested_id)
        if (
            requested_user is None
            or not requested_user.is_active
            or requested_user.role not in ("admin", "agent")
        ):
            return HTMLResponse(
                '<p class="text-red-500 text-sm">Asesor inválido</p>',
                status_code=400,
            )
        attributed_agent_id = requested_id
    else:
        attributed_agent_id = user.id

    visit, err = await VisitService.create_visit(
        db,
        contact_id=contact_id,
        scheduled_at=parsed,
        agent_user_id=attributed_agent_id,
        property_id=pid,
        notes=(notes or None),
        source="panel",
    )
    if err:
        return HTMLResponse(
            f'<p class="text-red-500 text-sm">{err}</p>',
            status_code=400,
        )

    return await _render_visits_block(
        request, db, user, contact_id, toast="Visita agendada",
    )


# ---------------------------------------------------------------------------
# POST /visits/{id}/cancel
# ---------------------------------------------------------------------------


@router.post("/visits/{visit_id}/cancel", response_class=HTMLResponse)
async def cancel_visit(
    visit_id: int,
    request: Request,
    user: User = Depends(require_agent_or_admin),
    db: AsyncSession = Depends(get_db),
):
    """Plan 114 §3.3 — cancel a scheduled visit."""
    # feat(authz): agent ownership check resolves visit→contact (ROLE-agent-write)
    visit_pre = await ensure_visit_access(db, user, visit_id)

    _, err = await VisitService.cancel_visit(db, visit_id=visit_id, user_id=user.id)
    if err:
        return HTMLResponse(
            f'<p class="text-red-500 text-sm">{err}</p>',
            status_code=400,
        )

    return await _render_visits_block(
        request, db, user, visit_pre.contact_id, toast="Visita cancelada",
    )


# ---------------------------------------------------------------------------
# POST /visits/{id}/complete?result=done|no_show
# ---------------------------------------------------------------------------


@router.post("/visits/{visit_id}/complete", response_class=HTMLResponse)
async def complete_visit(
    visit_id: int,
    request: Request,
    result: str = Query(...),
    user: User = Depends(require_agent_or_admin),
    db: AsyncSession = Depends(get_db),
):
    """Plan 114 §3.4 — mark scheduled visit as 'done' or 'no_show'."""
    if result not in ("done", "no_show"):
        return HTMLResponse(
            '<p class="text-red-500 text-sm">Result inválido</p>',
            status_code=400,
        )

    # feat(authz): agent ownership check resolves visit→contact (ROLE-agent-write)
    visit_pre = await ensure_visit_access(db, user, visit_id)

    _, err = await VisitService.complete_visit(
        db, visit_id=visit_id, result=result, user_id=user.id,
    )
    if err:
        return HTMLResponse(
            f'<p class="text-red-500 text-sm">{err}</p>',
            status_code=400,
        )

    toast = "Visita marcada realizada" if result == "done" else "Visita marcada como no-show"
    return await _render_visits_block(
        request, db, user, visit_pre.contact_id, toast=toast,
    )


# ---------------------------------------------------------------------------
# POST /visits/{id}/reschedule
# ---------------------------------------------------------------------------


@router.post("/visits/{visit_id}/reschedule", response_class=HTMLResponse)
async def reschedule_visit(
    visit_id: int,
    request: Request,
    scheduled_at: str = Form(...),
    notes: str = Form(""),
    user: User = Depends(require_agent_or_admin),
    db: AsyncSession = Depends(get_db),
):
    """Plan 114 §3.5 — cancel+insert atomically with same contact/property/agent."""
    parsed = _parse_local_dt(scheduled_at)
    if parsed is None:
        return HTMLResponse(
            '<p class="text-red-500 text-sm">Fecha inválida</p>',
            status_code=400,
        )

    # feat(authz): agent ownership check resolves visit→contact (ROLE-agent-write)
    await ensure_visit_access(db, user, visit_id)

    new_visit, err = await VisitService.reschedule_visit(
        db,
        visit_id=visit_id,
        scheduled_at=parsed,
        user_id=user.id,
        notes=(notes or None),
    )
    if err:
        return HTMLResponse(
            f'<p class="text-red-500 text-sm">{err}</p>',
            status_code=400,
        )

    return await _render_visits_block(
        request, db, user, new_visit.contact_id, toast="Visita reagendada",
    )


# ---------------------------------------------------------------------------
# GET /contacts/{id}/visits — partial (lazy-load by 115-04 UI)
# ---------------------------------------------------------------------------


@router.get("/contacts/{contact_id}/visits", response_class=HTMLResponse)
async def list_visits(
    contact_id: int,
    request: Request,
    user: User = Depends(require_agent_or_admin),
    db: AsyncSession = Depends(get_db),
):
    """Plan 114 §3.6 — return the visits_block partial with full context.
    No HX-Trigger header (lazy-load reads only)."""
    # feat(authz): agent ownership check (ROLE-agent-read)
    await ensure_contact_access(db, user, contact_id)
    return await _render_visits_block(request, db, user, contact_id, toast=None)
