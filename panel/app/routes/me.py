"""Self-service account routes — POST /me/password and POST /me/profile.

Any authenticated user (admin, agent, user) can change their own password or
update their own phone/display_name. No admin privilege required.
"""
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.services.user_management_service import user_management_service
from app.utils.phone_utils import normalize_phone

logger = logging.getLogger(__name__)

router = APIRouter()

_ERR_TEMPLATE = (
    '<div class="text-red-600 bg-red-50 border border-red-200 rounded px-4 py-2 text-sm">'
    '{msg}'
    '</div>'
)

_MAX_PASSWORD_LEN = 1024
_MAX_DISPLAY_NAME_LEN = 200


def _error(msg: str, status_code: int = 400) -> HTMLResponse:
    return HTMLResponse(_ERR_TEMPLATE.format(msg=msg), status_code=status_code)


@router.post("/me/password", response_class=HTMLResponse)
async def change_own_password(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Defensive: strip but guard against oversized inputs before any processing
    try:
        current_password = current_password.strip()
        new_password = new_password.strip()
        confirm_password = confirm_password.strip()
    except Exception:
        return _error("Entrada inválida")

    if len(new_password) > _MAX_PASSWORD_LEN:
        return _error("Contraseña demasiado larga")

    if len(new_password) < 12:
        return _error("Contraseña debe tener al menos 12 caracteres")

    if new_password != confirm_password:
        return _error("Las contraseñas no coinciden")

    try:
        await user_management_service.change_own_password(
            db, user, current_password, new_password
        )
    except ValueError as exc:
        return _error(str(exc))
    except Exception:
        logger.exception("change_own_password unexpected error: user_id=%s", user.id)
        return _error("Error al cambiar la contraseña")

    # Refresh session so the acting user's current session is NOT kicked out by
    # their own password change. issued_at is set to now (>= the new pw_changed_at),
    # so the invalidation check in get_current_user will pass on the next request.
    now_ts = int(datetime.now(timezone.utc).timestamp())
    request.session["issued_at"] = now_ts
    request.session["last_activity"] = now_ts

    logger.info("Self-service password change: user_id=%s", user.id)
    trigger = json.dumps({
        "showToast": {"type": "success", "message": "Contraseña cambiada exitosamente"}
    })
    response = HTMLResponse("<div></div>")
    response.headers["HX-Trigger"] = trigger
    return response


@router.post("/me/profile", response_class=HTMLResponse)
async def update_own_profile(
    request: Request,
    phone: str = Form(""),
    display_name: str = Form(""),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Self-service profile update: phone (E.164 or PY national) and display_name.

    Empty phone → clears to NULL (allowed).
    Empty display_name → clears to NULL (allowed).
    Invalid phone → 400 with inline error fragment.
    display_name > 200 chars → 400 with inline error fragment.
    Success → 200 with HX-Trigger showToast header (same pattern as /me/password).
    """
    phone_raw = phone.strip()
    display_name_clean = display_name.strip()

    # Validate display_name length (model column limit is 200)
    if len(display_name_clean) > _MAX_DISPLAY_NAME_LEN:
        return _error(f"Nombre para mostrar debe tener máximo {_MAX_DISPLAY_NAME_LEN} caracteres")

    # Validate and normalize phone
    phone_e164, phone_err = normalize_phone(phone_raw)
    if phone_err:
        return _error(phone_err)

    # Normalize empty strings to NULL
    phone_to_store: str | None = phone_e164  # already None if empty
    display_name_to_store: str | None = display_name_clean or None

    try:
        await user_management_service.update_own_profile(
            db, user, phone=phone_to_store, display_name=display_name_to_store
        )
    except Exception:
        logger.exception("update_own_profile unexpected error: user_id=%s", user.id)
        return _error("Error al actualizar el perfil")

    logger.info("Self-service profile update: user_id=%s", user.id)
    trigger = json.dumps({
        "showToast": {"type": "success", "message": "Perfil actualizado exitosamente"}
    })
    response = HTMLResponse("<div></div>")
    response.headers["HX-Trigger"] = trigger
    return response
