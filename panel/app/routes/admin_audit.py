"""Admin auth audit endpoint — M6.1 Phase 111-05.

Spec: .planning/phases/110-m6.1-plan-roles-auth/110-01-PLAN.md §7.1, §7.4.

Endpoints:
  GET  /admin/auth-audit                — admin-only HTML view with filters
                                          (email, ip, date_from, date_to) +
                                          pagination (50 rows/page).
  POST /admin/auth-audit/unlock-email   — admin-only; inserts an auth_audit row
                                          with result='success' ip='admin-unlock'
                                          which breaks the recent-failure window
                                          consulted by the lockout check.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin
from app.models.user import User
from app.tz import get_templates

logger = logging.getLogger(__name__)

router = APIRouter()
templates = get_templates()

PER_PAGE = 50


def _build_filters(
    email: str | None,
    ip: str | None,
    date_from: date | None,
    date_to: date | None,
) -> tuple[str, dict]:
    """Return (where_sql_fragment, params_dict) for the dynamic filter query."""
    clauses: list[str] = ["1=1"]
    params: dict = {}
    if email:
        clauses.append("email = :email")
        params["email"] = email.strip().lower()
    if ip:
        clauses.append("ip = :ip")
        params["ip"] = ip.strip()
    # NOTE: bind explicit UTC timestamps (not date objects). asyncpg's date
    # binding against timestamptz columns has off-by-one-day semantics that
    # break ?date_from=X&date_to=X queries (the row at day X is excluded and
    # the row at day X+1 is included). The auth_audit table is timestamptz
    # UTC, so we anchor the day window at UTC midnight explicitly.
    if date_from:
        clauses.append("created_at >= :date_from_ts")
        params["date_from_ts"] = datetime.combine(date_from, time.min, tzinfo=timezone.utc)
    if date_to:
        # End-of-day inclusive → < (date_to + 1 day) at UTC midnight
        clauses.append("created_at < :date_to_exclusive_ts")
        params["date_to_exclusive_ts"] = datetime.combine(
            date_to + timedelta(days=1), time.min, tzinfo=timezone.utc
        )
    return " AND ".join(clauses), params


@router.get("/admin/auth-audit")
async def list_auth_audit(
    request: Request,
    email: str | None = Query(None),
    ip: str | None = Query(None),
    date_from: str | None = Query(None, description="Filtra por fecha desde (YYYY-MM-DD). Empty string = no filter."),
    date_to: str | None = Query(None, description="Filtra por fecha hasta (YYYY-MM-DD). Empty string = no filter."),
    page: int = Query(1, ge=1),
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """301 redirect to /settings?tab=accesos (preserving any filter params)."""
    qs_pairs: list[tuple[str, str]] = [("tab", "accesos")]
    if email:
        qs_pairs.append(("email", email))
    if ip:
        qs_pairs.append(("ip", ip))
    if date_from:
        qs_pairs.append(("date_from", date_from))
    if date_to:
        qs_pairs.append(("date_to", date_to))
    if page > 1:
        qs_pairs.append(("page", str(page)))
    redirect_url = "/settings?" + urlencode(qs_pairs)
    return RedirectResponse(url=redirect_url, status_code=301)


@router.post("/admin/auth-audit/unlock-email")
async def unlock_email(
    request: Request,
    email: str = Form(...),
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    email_clean = (email or "").strip().lower()
    if not email_clean:
        raise HTTPException(status_code=400, detail="Email requerido")

    # Idempotency guard: if email is already unlocked, skip the INSERT.
    # Prevents duplicate success rows on rapid double-click / duplicate HTMX requests.
    from app.services import lockout_service as _ls

    if await _ls.is_locked(db, email_clean):
        # Spec §7.4: INSERT result='success' rompe la ventana del lockout.
        # Only insert when actually locked to stay idempotent.
        await db.execute(
            text(
                """
                INSERT INTO auth_audit (email, ip, user_agent, result, created_at)
                VALUES (:email, 'admin-unlock', 'manual-unlock', 'success', now())
                """
            ),
            {"email": email_clean},
        )
        await db.commit()
        logger.info(
            "Auth audit manual unlock: target=%s by_admin=%s",
            email_clean,
            user.email,
        )
    else:
        logger.info(
            "Auth audit unlock skipped (already unlocked): target=%s by_admin=%s",
            email_clean,
            user.email,
        )

    # HTMX request → return the re-queried partial so the table refreshes inline.
    is_hx = request.headers.get("HX-Request") == "true"
    if is_hx:
        from app.routes.settings import _get_audit_context

        # Re-derive filter params from the form post context. We only have email
        # at this point; ip/dates are not submitted. The partial will show all
        # rows for this email, freshly queried without any active lock.
        audit_ctx = await _get_audit_context(
            db=db,
            email=email_clean,
            ip=None,
            date_from_str=None,
            date_to_str=None,
            page=1,
        )
        resp = templates.TemplateResponse(
            "partials/auth_audit_table.html",
            {
                "request": request,
                "audit_base_url": "/settings",
                **audit_ctx,
            },
        )
        resp.headers["HX-Trigger"] = json.dumps({
            "showToast": {"type": "success", "message": f"Email desbloqueado: {email_clean}"}
        })
        return resp

    redirect_url = "/settings?" + urlencode({"tab": "accesos", "email": email_clean})
    return RedirectResponse(redirect_url, status_code=303)
