from datetime import datetime, timezone
from fastapi import Request, Depends, HTTPException
from starlette.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.repositories.user_repo import user_repo
from app.models.user import User


def _session_expired_due_to_inactivity(session: dict, now: int, inactivity_seconds: int) -> bool:
    """Return True if last_activity is present and stale; False otherwise (backward-compat).

    If last_activity is ABSENT (legacy session), returns False — do NOT invalidate.
    """
    last_activity = session.get("last_activity")
    if last_activity is None:
        return False
    return (now - last_activity) > inactivity_seconds


def _session_expired_due_to_pw_change(session: dict, user: User) -> bool:
    """Return True if pw_changed_at > issued_at (password changed since session was issued).

    Backward-compat: if pw_changed_at is None OR issued_at is absent, returns False.
    """
    pw_changed_at = user.pw_changed_at
    if pw_changed_at is None:
        return False
    issued_at = session.get("issued_at")
    if issued_at is None:
        return False
    # Ensure pw_changed_at is timezone-aware
    if pw_changed_at.tzinfo is None:
        pw_changed_at = pw_changed_at.replace(tzinfo=timezone.utc)
    return pw_changed_at.timestamp() > issued_at


def _redirect_to_login(request: Request):
    """Raise the same login-redirect the existing code uses (HX-Request aware)."""
    if request.headers.get("HX-Request"):
        raise HTTPException(status_code=200, headers={"HX-Redirect": "/login"})
    raise HTTPException(status_code=303, headers={"Location": "/login"})


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    from app.config import settings

    user_id = request.session.get("user_id")
    if not user_id:
        if request.headers.get("HX-Request"):
            raise HTTPException(status_code=200, headers={"HX-Redirect": "/login"})
        raise HTTPException(status_code=303, headers={"Location": "/login"})

    user = await user_repo.get_by_id(db, user_id)
    if not user or not user.is_active:
        request.session.clear()
        if request.headers.get("HX-Request"):
            raise HTTPException(status_code=200, headers={"HX-Redirect": "/login"})
        raise HTTPException(status_code=303, headers={"Location": "/login"})

    now = int(datetime.now(timezone.utc).timestamp())
    inactivity_seconds = settings.SESSION_INACTIVITY_MINUTES * 60

    # Inactivity check (backward-compat: if last_activity absent, skip invalidation)
    if _session_expired_due_to_inactivity(request.session, now, inactivity_seconds):
        request.session.clear()
        _redirect_to_login(request)

    # Password-change invalidation (backward-compat: if issued_at or pw_changed_at absent, skip)
    if _session_expired_due_to_pw_change(request.session, user):
        request.session.clear()
        _redirect_to_login(request)

    # Update sliding window — always set last_activity (bootstraps legacy sessions too)
    request.session["last_activity"] = now

    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Acceso solo para administradores")
    return user


async def require_agent_or_admin(
    user: User = Depends(get_current_user),
) -> User:
    """M6.1 (ROLE-07) — admite role 'admin' o 'agent'; bloquea 'user' con 403.

    Plan 111-03: usar en /leads y endpoints relacionados que deben quedar
    accesibles tanto al admin (la administradora) como a los asesores (agents),
    pero NO a usuarios regulares (role='user'). El check de role 'user'
    devuelve 403 con mensaje en español.
    """
    if user.role not in ("admin", "agent"):
        raise HTTPException(
            status_code=403,
            detail="Acceso solo para administradores o asesores",
        )
    return user
