import json
import logging

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.dependencies import require_admin, get_current_user
from app.services.user_management_service import user_management_service
from app.models.user import User
from app.tz import get_templates
from app.utils.phone_utils import normalize_phone as _normalize_phone

logger = logging.getLogger(__name__)

router = APIRouter()
templates = get_templates()


# ---------------------------------------------------------------------------
# Phase 111-06 — Create Agent (admin-only)
#
# Quedo solo el redirect: el alta vive en POST /users, desde el modal de
# /settings?tab=usuarios. El POST /users/create-agent que habia aca no lo
# llamaba ninguna plantilla y se borro en el carril D.
#
# IMPORTANT: se declara ANTES de /users/{user_id}/... para que el literal
# "/users/create-agent" gane sobre el patron dinamico "/users/{id}/edit".
# FastAPI/Starlette matchean en orden de declaracion.
# ---------------------------------------------------------------------------


@router.get("/users/create-agent")
async def create_agent_form(
    user: User = Depends(require_admin),
):
    return RedirectResponse(url="/settings?tab=usuarios", status_code=301)


@router.get("/users")
async def users_page(user: User = Depends(require_admin)):
    return RedirectResponse(url="/settings?tab=usuarios", status_code=301)


@router.post("/users", response_class=HTMLResponse)
async def create_user(request: Request,
                      email: str = Form(""),
                      name: str = Form(""),
                      password: str = Form(""),
                      role: str = Form("agent"),
                      display_name: str = Form(""),
                      phone: str = Form(""),
                      user: User = Depends(require_admin),
                      db: AsyncSession = Depends(get_db)):
    email = email.strip().lower()
    name = name.strip()
    password = password.strip()
    role = role.strip()
    display_name_clean: str | None = display_name.strip() or None
    phone_raw = phone.strip()

    # Un mensaje por campo, no una frase que los tapa a todos: el patron que
    # rescatamos de users_create_agent.html cuando ese archivo se borro.
    errors: dict[str, str] = {}
    if not name:
        errors["name"] = "Nombre requerido"
    if not email:
        errors["email"] = "Email requerido"
    if not password:
        errors["password"] = "Contraseña requerida"
    elif len(password) < 12:
        errors["password"] = "Contraseña debe tener al menos 12 caracteres"

    phone_e164, phone_err = _normalize_phone(phone_raw)
    if phone_err:
        errors["phone"] = phone_err

    created_email: str | None = None
    if not errors:
        try:
            await user_management_service.create_user(
                db, email=email, password=password, name=name, role=role,
                display_name=display_name_clean, phone=phone_e164,
            )
            created_email = email
            logger.info("User created: email=%s role=%s by=%s", email, role, user.email)
        except Exception:
            logger.exception("Failed to create user: email=%s", email)
            await db.rollback()
            errors["email"] = "El email ya existe o los datos son inválidos"

    if errors:
        # El error tiene que aparecer DENTRO del <dialog> abierto. Sin el
        # retarget la respuesta va a #settings-users-table, que el modal
        # inertiza y tapa: el usuario aprieta Guardar y no ve pasar nada.
        response = templates.TemplateResponse(
            "partials/user_create_form.html",
            {
                "request": request,
                "user": user,
                "errors": errors,
                "form": {
                    "name": name,
                    "display_name": display_name_clean or "",
                    "email": email,
                    "phone": phone_raw,
                    "role": role,
                },
            },
            status_code=422,
        )
        response.headers["HX-Retarget"] = "#create-user-form"
        response.headers["HX-Reswap"] = "outerHTML"
        return response

    users = await user_management_service.get_all(db)
    response = templates.TemplateResponse(
        "partials/users_table.html",
        {"request": request, "user": user, "users": users},
        status_code=200,
    )
    response.headers["HX-Trigger"] = (
        f'{{"userCreated":{{"email":"{created_email}"}}}}'
    )
    return response


@router.get("/users/{user_id}/edit", response_class=HTMLResponse)
async def edit_form(user_id: int, request: Request,
                    user: User = Depends(require_admin),
                    db: AsyncSession = Depends(get_db)):
    target = await user_management_service.get_user_by_id(db, user_id)
    if not target:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse("partials/user_edit_row.html", {
        "request": request, "user": user, "target": target,
    })


@router.get("/users/{user_id}/row", response_class=HTMLResponse)
async def user_row(user_id: int, request: Request,
                   user: User = Depends(require_admin),
                   db: AsyncSession = Depends(get_db)):
    target = await user_management_service.get_user_by_id(db, user_id)
    if not target:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse("partials/user_row.html", {
        "request": request, "user": user, "target": target,
    })


@router.post("/users/{user_id}/edit", response_class=HTMLResponse)
async def update_user(user_id: int, request: Request,
                      name: str = Form(""),
                      email: str = Form(""),
                      role: str = Form("user"),
                      user: User = Depends(require_admin),
                      db: AsyncSession = Depends(get_db)):
    name = name.strip()
    email = email.strip()
    role = role.strip()
    target = await user_management_service.update_user(
        db, user_id, name=name, email=email, role=role,
    )
    if not target:
        raise HTTPException(status_code=404)
    logger.info("User edited: target_id=%d email=%s role=%s by=%s", user_id, email, role, user.email)
    resp = templates.TemplateResponse("partials/user_row.html", {
        "request": request, "user": user, "target": target,
    })
    resp.headers["HX-Trigger"] = json.dumps({
        "showToast": {"type": "success", "message": "Usuario actualizado"}
    })
    return resp


@router.post("/users/{user_id}/toggle", response_class=HTMLResponse)
async def toggle_active(user_id: int, request: Request,
                        user: User = Depends(require_admin),
                        db: AsyncSession = Depends(get_db)):
    if user_id == user.id:
        raise HTTPException(status_code=400, detail="No puedes desactivarte a ti mismo")
    target = await user_management_service.toggle_active(db, user_id)
    if not target:
        raise HTTPException(status_code=404)
    logger.info("User toggled: target_id=%d is_active=%s by=%s", user_id, target.is_active, user.email)
    state_label = "activado" if target.is_active else "desactivado"
    resp = templates.TemplateResponse("partials/user_row.html", {
        "request": request, "user": user, "target": target,
    })
    resp.headers["HX-Trigger"] = json.dumps({
        "showToast": {"type": "success", "message": f"Usuario {state_label}"}
    })
    return resp


@router.post("/users/{user_id}/password", response_class=HTMLResponse)
async def change_password(user_id: int, request: Request,
                          new_password: str = Form("", alias="password"),
                          user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    if user.role != "admin" and user.id != user_id:
        raise HTTPException(status_code=403)
    new_password = new_password.strip()
    if len(new_password) < 12:
        # Se re-renderiza la fila con el error adentro, no se levanta un 400.
        #
        # HTMX **no hace swap en una respuesta 4xx**, asi que el `raise` dejaba
        # a la duena apretando «Cambiar contrasena» sin que pasara nada: ni el
        # cambio, ni un mensaje, ni una pista. Es el mismo patron que el alta de
        # usuario ya usa mas arriba —un error por campo, renderizado adentro del
        # formulario— y esta ruta era la unica que no lo seguia.
        #
        # El `minlength` del input frena el caso comun del lado del navegador;
        # esto cubre el resto: pegar desde el portapapeles, un navegador viejo,
        # o un `minlength` que vuelva a desincronizarse.
        target = await user_management_service.get_user_by_id(db, user_id)
        if not target:
            raise HTTPException(status_code=404)
        return templates.TemplateResponse("partials/user_edit_row.html", {
            "request": request,
            "target": target,
            "user": user,
            "error_password": "Contraseña debe tener al menos 12 caracteres",
        }, status_code=200)
    target = await user_management_service.change_password(db, user_id, new_password)
    if not target:
        raise HTTPException(status_code=404)
    logger.info("User password changed: target_id=%d by=%s", user_id, user.email)
    # If the admin changed their OWN password, refresh the session so they are not
    # kicked out on the next request (pw_changed_at > issued_at would invalidate it).
    # Mirrors the same refresh done in routes/me.py after /me/password.
    # Use int(pw_changed_at.timestamp()) + 1 to guarantee issued_at strictly
    # exceeds pw_changed_at even with sub-second fractional precision.
    if user_id == user.id and target.pw_changed_at is not None:
        now_ts = int(target.pw_changed_at.timestamp()) + 1
        request.session["issued_at"] = now_ts
        request.session["last_activity"] = now_ts
    resp = templates.TemplateResponse("partials/user_row.html", {
        "request": request, "user": user, "target": target,
    })
    resp.headers["HX-Trigger"] = json.dumps({
        "showToast": {"type": "success", "message": "Contraseña actualizada"}
    })
    return resp
