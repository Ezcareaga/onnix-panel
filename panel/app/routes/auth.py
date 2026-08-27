import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.services import lockout_service
from app.services.auth_service import auth_service
from app.tz import get_templates
from app.utils.log_utils import mask_email

logger = logging.getLogger(__name__)

router = APIRouter()
templates = get_templates()


def _real_ip_from_headers(request: Request) -> str | None:
    """Extract the real visitor IP from trusted proxy headers.

    Priority order (deployment is Cloudflare -> nginx -> app):
      1. CF-Connecting-IP  — Cloudflare sets exactly one value; nginx only
         forwards it from CF ranges, so it is trustworthy when present.
      2. X-Forwarded-For leftmost entry — set by nginx when CF-Connecting-IP
         is absent (e.g. direct nginx hits in staging).
      3. request.client.host — last resort fallback.

    This helper is intentionally pure (no settings read) so it is unit-testable
    without environment side-effects.
    """
    cf = request.headers.get("CF-Connecting-IP")
    if cf:
        return cf.strip()

    xff = request.headers.get("X-Forwarded-For")
    if xff:
        # XFF may be a comma-separated list; leftmost is the original client.
        return xff.split(",")[0].strip()

    return request.client.host if request.client else None


def _extract_ip(request: Request, trust_proxy: bool | None = None) -> str | None:
    """Extract caller IP from the request.

    When `trust_proxy` is True (or when Settings.TRUST_PROXY_HEADERS is True
    and `trust_proxy` is not passed), reads the real visitor IP from proxy
    headers via `_real_ip_from_headers`.  This is safe only when the app sits
    behind Cloudflare + nginx that we control.

    When trust is disabled (default), returns `request.client.host` — the
    pre-existing behaviour, preserving all existing tests.

    Production/staging: set TRUST_PROXY_HEADERS=true in .env.
    No docker-compose.dev.yml override is needed because the flag does not
    touch any external API.
    """
    if trust_proxy is None:
        from app.config import settings
        trust_proxy = settings.TRUST_PROXY_HEADERS

    if trust_proxy:
        return _real_ip_from_headers(request)

    return request.client.host if request.client else None


def _inicio_para(role: str | None) -> str:
    """Adonde cae cada rol al entrar.

    El asesor NO va al dashboard: desde el 2026-08-23 `/dashboard` y `/stats`
    piden `require_admin`, asi que mandarlo ahi seria un 403 en la cara justo
    despues de escribir su contraseña. Su pantalla es la cola de trabajo.
    """
    return "/dashboard" if role == "admin" else "/leads"


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(
            _inicio_para(request.session.get("user_role")), status_code=303,
        )
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    # §10.4: normalize email inline (strip + lower) before any audit/auth.
    email = email.strip().lower()
    ip = _extract_ip(request)
    user_agent = request.headers.get("user-agent")

    # 1. Lockout pre-check (D-2 email-only).
    if await lockout_service.is_locked(db, email):
        await lockout_service.record_attempt(
            db, email, ip, user_agent, result="locked"
        )
        logger.warning(
            "Login blocked (locked): email=%s ip=%s", mask_email(email), ip
        )
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Cuenta bloqueada por seguridad. Reintentá en unos minutos.",
            },
            status_code=401,
        )

    # 2. Authenticate.
    user, auth_result = await auth_service.authenticate(db, email, password)
    await lockout_service.record_attempt(
        db, email, ip, user_agent, result=auth_result
    )

    if user is None:
        # Failure path: maybe we just crossed the threshold → alert + lock row.
        await lockout_service.maybe_trigger_lockout_alert(
            db, email, ip, user_agent
        )
        logger.warning(
            "Login failed: email=%s reason=%s", mask_email(email), auth_result
        )
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Credenciales incorrectas"},
            status_code=401,
        )

    # 3. Success — AP-9 fix: clear session BEFORE setting user_id to prevent
    # session fixation (OWASP ASVS §3.3.1). Any pre-login session id is
    # discarded; a new id is issued on the next response.
    now_ts = int(datetime.now(timezone.utc).timestamp())
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["user_role"] = user.role
    request.session["user_name"] = user.display_name or user.name or user.email
    request.session["issued_at"] = now_ts
    request.session["last_activity"] = now_ts
    logger.info(
        "Login successful: user=%s role=%s", mask_email(user.email), user.role
    )
    return RedirectResponse(_inicio_para(user.role), status_code=303)


@router.get("/logout")
async def logout(request: Request):
    user_email = request.session.get("user_name", "unknown")
    logger.info("Logout: user=%s", user_email)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
