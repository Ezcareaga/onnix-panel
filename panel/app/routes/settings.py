import logging
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Depends, Form, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.dependencies import require_admin, get_current_user
from app.services.settings_service import settings_service
from app.services.user_management_service import user_management_service
from app.services import lockout_service
from app.models.user import User
from app.tz import get_templates

logger = logging.getLogger(__name__)

router = APIRouter()
templates = get_templates()

_AUDIT_PER_PAGE = 50


async def _get_audit_context(
    db: AsyncSession,
    email: str | None,
    ip: str | None,
    date_from_str: str | None,
    date_to_str: str | None,
    page: int,
) -> dict:
    """Build auth-audit query context for the accesos tab."""
    date_from_parsed: date | None = None
    if date_from_str and date_from_str.strip():
        try:
            date_from_parsed = date.fromisoformat(date_from_str.strip())
        except ValueError:
            pass

    date_to_parsed: date | None = None
    if date_to_str and date_to_str.strip():
        try:
            date_to_parsed = date.fromisoformat(date_to_str.strip())
        except ValueError:
            pass

    clauses: list[str] = ["1=1"]
    params: dict = {}
    if email:
        clauses.append("email = :email")
        params["email"] = email.strip().lower()
    if ip:
        clauses.append("ip = :ip")
        params["ip"] = ip.strip()
    if date_from_parsed:
        clauses.append("created_at >= :date_from_ts")
        params["date_from_ts"] = datetime.combine(date_from_parsed, time.min, tzinfo=timezone.utc)
    if date_to_parsed:
        clauses.append("created_at < :date_to_exclusive_ts")
        params["date_to_exclusive_ts"] = datetime.combine(
            date_to_parsed + timedelta(days=1), time.min, tzinfo=timezone.utc
        )
    where_sql = " AND ".join(clauses)
    offset = (page - 1) * _AUDIT_PER_PAGE

    rows = (
        await db.execute(
            text(f"""
                SELECT id, email, ip, user_agent, result, created_at
                FROM auth_audit
                WHERE {where_sql}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            {**params, "limit": _AUDIT_PER_PAGE, "offset": offset},
        )
    ).mappings().all()

    total = (
        await db.execute(
            text(f"SELECT COUNT(*) FROM auth_audit WHERE {where_sql}"),
            params,
        )
    ).scalar_one()

    qs_pairs: list[tuple[str, str]] = []
    if email:
        qs_pairs.append(("email", email))
    if ip:
        qs_pairs.append(("ip", ip))
    if date_from_parsed:
        qs_pairs.append(("date_from", date_from_parsed.isoformat()))
    if date_to_parsed:
        qs_pairs.append(("date_to", date_to_parsed.isoformat()))
    # Preserve active tab in all filter links
    qs_pairs.append(("tab", "accesos"))
    filters_querystring = urlencode(qs_pairs)

    # Check if the email is currently locked. Use lockout_service.is_locked()
    # so the UI matches the actual lockout decision (5 fails in 15-min window
    # + last fail within 30-min lock duration). Calling the service directly
    # avoids re-implementing the rule and keeps UI semantics aligned with auth.
    is_currently_locked = False
    if email:
        is_currently_locked = await lockout_service.is_locked(
            db, email.strip().lower()
        )

    # Build a set of emails visible in the current page that are actively locked.
    # Used by the inline unlock icon per locked row (Fix 1 UX).
    candidate_emails: set[str] = {
        row["email"] for row in rows if row["result"] == "locked"
    }
    locked_emails_in_view: set[str] = set()
    for candidate in candidate_emails:
        if await lockout_service.is_locked(db, candidate):
            locked_emails_in_view.add(candidate)

    return {
        "rows": rows,
        "total": total,
        "page": page,
        "per_page": _AUDIT_PER_PAGE,
        "filters": {
            "email": email,
            "ip": ip,
            "date_from": date_from_parsed,
            "date_to": date_to_parsed,
        },
        "filters_querystring": filters_querystring,
        "is_currently_locked": is_currently_locked,
        "locked_emails_in_view": locked_emails_in_view,
    }


async def _get_users(
    db: AsyncSession,
    search: str | None = None,
    role: str | None = None,
    active: bool | None = True,
) -> list:
    """Fetch users for the usuarios tab, with optional filters."""
    return await user_management_service.get_all(db, search=search, role=role, active=active)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    # Audit tab filters (passed when tab=accesos)
    email: str | None = Query(None),
    ip: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    page: int = Query(1, ge=1),
    # Usuarios tab filters
    user_search: str | None = Query(None),
    user_role: str | None = Query(None),
    user_active: str | None = Query(None),
):
    # Normalize empty strings to None so the filter form does not re-populate
    # stale values when the user submits with a blank input (browser sends
    # ?email= which FastAPI delivers as ""). Without this, filters.email would
    # be "" and the template would render value="" — correct — but an adjacent
    # Alpine/browser autofill could re-surface the empty string. Normalizing
    # here also guards _get_audit_context against accidental empty-string matches.
    email = email or None
    ip = ip or None
    user_search = user_search or None
    user_role = user_role or None

    # user_active: param present with value "1" → True; absent or "0" → True (default)
    # We default to showing only active users unless explicitly "0".
    show_active: bool | None = True
    if user_active == "0":
        show_active = None  # show all (active + inactive)

    # Admin-only context: skip expensive queries for non-admin users. La
    # configuracion del bot tambien sale de aca: el template la muestra solo
    # para admin, y armarla igual la mandaba al HTML de cualquier rol.
    if user.role == "admin":
        data = await settings_service.get_all_settings(db)
        audit_ctx = await _get_audit_context(db, email, ip, date_from, date_to, page)
        users_list = await _get_users(db, search=user_search, role=user_role, active=show_active)
        user_filters = {
            "search": user_search or "",
            "role": user_role or "",
            "active": show_active is not None,
        }
    else:
        data = {}
        audit_ctx = {
            "rows": [], "total": 0, "page": 1, "per_page": _AUDIT_PER_PAGE,
            "filters": {"email": None, "ip": None, "date_from": None, "date_to": None},
            "filters_querystring": "",
            "is_currently_locked": False,
            "locked_emails_in_view": set(),
        }
        users_list = []
        user_filters = {"search": "", "role": "", "active": True}

    context = {
        "request": request,
        "user": user,
        **data,
        **audit_ctx,
        # Pass users list for usuarios tab
        "users": users_list,
        # User filter state for template
        "user_filters": user_filters,
        # Provide audit_base_url for the partial
        "audit_base_url": "/settings",
    }
    return templates.TemplateResponse("settings.html", context)

@router.post("/settings/bot-toggle")
async def bot_toggle(request: Request, user: User = Depends(require_admin),
                     db: AsyncSession = Depends(get_db)):
    new_state = await settings_service.toggle_bot(db, user.id)
    logger.info("Setting updated: key=bot_enabled value=%s user=%s", new_state, user.email)
    try:
        from app.services.event_bus import event_bus as _event_bus
        await _event_bus.publish("settings.changed", {
            "key": "bot_enabled",
            "value": str(new_state).lower(),
            "user_id": user.id,
            "user_name": user.name or user.email,
        })
    except Exception:
        pass
    data = await settings_service.get_all_settings(db)
    data["bot_enabled"] = new_state  # use fresh toggle state
    return templates.TemplateResponse("partials/settings_form.html", {
        "request": request, "user": user, **data,
    })

@router.post("/settings/bot-default-mode")
async def set_bot_default_mode(request: Request, mode: str = Form(...),
                               user: User = Depends(require_admin),
                               db: AsyncSession = Depends(get_db)):
    try:
        new_mode = await settings_service.set_bot_default_mode(db, mode, user.id)
    except ValueError as exc:
        return HTMLResponse(
            f'<div class="text-red-600 text-sm">{exc}</div>',
            status_code=422,
        )
    logger.info("Setting updated: key=bot_default_mode value=%s user=%s", new_mode, user.email)
    try:
        from app.services.event_bus import event_bus as _event_bus
        await _event_bus.publish("settings.changed", {
            "key": "bot_default_mode",
            "value": new_mode,
            "user_id": user.id,
            "user_name": user.name or user.email,
        })
    except Exception:
        pass
    data = await settings_service.get_all_settings(db)
    return templates.TemplateResponse("partials/settings_form.html", {
        "request": request, "user": user, **data,
    })

@router.post("/settings/ic-autoreply-toggle")
async def ic_autoreply_toggle(request: Request, user: User = Depends(require_admin),
                              db: AsyncSession = Depends(get_db)):
    new_state = await settings_service.toggle_ic_autoreply(db, user.id)
    logger.info("Setting updated: key=ic_autoreply_enabled value=%s user=%s", new_state, user.email)
    try:
        from app.services.event_bus import event_bus as _event_bus
        await _event_bus.publish("settings.changed", {
            "key": "ic_autoreply_enabled",
            "value": str(new_state).lower(),
            "user_id": user.id,
            "user_name": user.name or user.email,
        })
    except Exception:
        pass
    data = await settings_service.get_all_settings(db)
    data["ic_autoreply_enabled"] = new_state
    return templates.TemplateResponse("partials/settings_form.html", {
        "request": request, "user": user, **data,
    })

@router.post("/settings/followup-toggle")
async def followup_toggle(request: Request, user: User = Depends(require_admin),
                          db: AsyncSession = Depends(get_db)):
    new_state = await settings_service.toggle_followup_sender(db, user.id)
    logger.info("Setting updated: key=scheduler_followup_sender_enabled value=%s user=%s", new_state, user.email)
    try:
        from app.services.event_bus import event_bus as _event_bus
        await _event_bus.publish("settings.changed", {
            "key": "followup_enabled",
            "value": str(new_state).lower(),
            "user_id": user.id,
            "user_name": user.name or user.email,
        })
    except Exception:
        pass
    data = await settings_service.get_all_settings(db)
    data["followup_enabled"] = new_state
    return templates.TemplateResponse("partials/settings_form.html", {
        "request": request, "user": user, **data,
    })

@router.post("/settings/ic-reenviados-toggle")
async def ic_reenviados_toggle(request: Request, user: User = Depends(require_admin),
                               db: AsyncSession = Depends(get_db)):
    new_state = await settings_service.toggle_ic_reenviados(db, user.id)
    logger.info("Setting updated: key=ic_autoreply_reenviados_enabled value=%s user=%s", new_state, user.email)
    try:
        from app.services.event_bus import event_bus as _event_bus
        await _event_bus.publish("settings.changed", {
            "key": "ic_reenviados_enabled",
            "value": str(new_state).lower(),
            "user_id": user.id,
            "user_name": user.name or user.email,
        })
    except Exception:
        pass
    data = await settings_service.get_all_settings(db)
    data["ic_reenviados_enabled"] = new_state
    return templates.TemplateResponse("partials/settings_form.html", {
        "request": request, "user": user, **data,
    })

@router.post("/settings/update")
async def update_setting(request: Request,
                         key: str = Form(""),
                         value: str = Form(""),
                         user: User = Depends(require_admin),
                         db: AsyncSession = Depends(get_db)):
    if key and value is not None:
        try:
            await settings_service.update_setting(db, key, value, user.id)
            logger.info("Setting updated: key=%s user=%s", key, user.email)
        except ValueError as exc:
            return HTMLResponse(
                f'<div class="text-red-600 text-sm">{exc}</div>',
                status_code=422,
            )
        try:
            from app.services.event_bus import event_bus as _event_bus
            await _event_bus.publish("settings.changed", {
                "key": key,
                "value": value,
                "user_id": user.id,
                "user_name": user.name or user.email,
            })
        except Exception:
            pass
    data = await settings_service.get_all_settings(db)
    return templates.TemplateResponse("partials/settings_form.html", {
        "request": request, "user": user, **data,
    })
