import asyncio
import json
import logging

import httpx
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.dependencies import get_current_user
from app.services.authz_service import ensure_contact_access, ensure_conversation_access
from app.services.conversation_service import conversation_service
from app.services.reply_service import reply_service
from app.services.settings_service import settings_service
from app.services.template_service import template_service
from app.schemas.template import SendTemplateRequest
from app.models.user import User
from app.tz import get_templates
from app.utils.phone_utils import PREFIXES
from app.repositories.lead_event_repo import lead_event_repo

logger = logging.getLogger(__name__)

router = APIRouter()
templates = get_templates()

@router.get("/conversations", response_class=HTMLResponse)
async def conversations_page(request: Request, stuck: str | None = None,
                              user: User = Depends(get_current_user),
                              db: AsyncSession = Depends(get_db)):
    # El KPI «Conversaciones trabadas» de /stats/health linkea acá con ?stuck=1.
    # Decía que había cuatro clientes esperando y no dejaba ir a ninguno.
    solo_trabadas = stuck == "1"
    # ROLE-04: agents only see their assigned contacts' conversations
    agent_filter = user.id if user.role == "agent" else None
    conversations = await conversation_service.get_conversations(
        db, agent_filter=agent_filter, stuck=solo_trabadas,
    )
    settings = await settings_service.get_all_settings(db)
    whatsapp_mode = settings["whatsapp_mode"]
    context = {
        "request": request,
        "user": user,
        "conversations": conversations,
        "selected_id": None,
        "thread": None,
        "whatsapp_mode": whatsapp_mode,
        "phone_prefixes": PREFIXES,
        "channel": "",
        "stuck": solo_trabadas,
        "q": "",
        "offset": 0,
        "has_more": len(conversations) == 50,
        "limit": 50,
    }
    return templates.TemplateResponse("conversations.html", context)

@router.get("/conversations/list", response_class=HTMLResponse)
async def conversation_list(
    request: Request,
    selected_id: str | None = None,
    q: str | None = None,
    channel: str | None = None,
    stuck: str | None = None,
    offset: int = 0,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    selected_id_int: int | None = int(selected_id) if selected_id else None
    query = q.strip() if q else ""
    # Normalise channel: only accept known values, ignore anything else
    _valid_channels = {"whatsapp", "telegram"}
    channel_filter = channel if channel in _valid_channels else None
    solo_trabadas = stuck == "1"
    # ROLE-04: agents only see their assigned contacts' conversations
    agent_filter = user.id if user.role == "agent" else None
    limit = 50
    if query:
        conversations = await conversation_service.search_conversations(
            db, query, limit=limit, offset=offset, agent_filter=agent_filter,
            channel=channel_filter, stuck=solo_trabadas,
        )
    else:
        conversations = await conversation_service.get_conversations(
            db, limit=limit, offset=offset, agent_filter=agent_filter,
            channel=channel_filter, stuck=solo_trabadas,
        )
    has_more = len(conversations) == limit
    return templates.TemplateResponse("partials/conversation_list.html", {
        "request": request,
        "user": user,
        "conversations": conversations,
        "selected_id": selected_id_int,
        "channel": channel_filter or "",
        "stuck": solo_trabadas,
        "q": query,
        "offset": offset,
        "has_more": has_more,
        "limit": limit,
    })

@router.get("/conversations/sse")
async def conversations_sse(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """SSE endpoint for real-time conversation updates.

    Sends keepalive comment every 30s to prevent Cloudflare timeout.

    feat(authz) — agent SSE filtering (TODO resolved from ROLE-04):
    When user.role == 'agent', only forward events whose conversation_id
    belongs to a contact assigned to the agent. A simple per-event SELECT
    is used (volume is low). Admin/user roles receive all events unchanged.
    """
    from app.services.event_bus import event_bus
    from app.routes.events import should_forward_event
    from sqlalchemy import text as _sa_text

    is_agent = user.role == "agent"
    agent_id = user.id if is_agent else None

    async def _agent_owns_conversation(conv_id: int) -> bool:
        """Return True iff the conversation's contact is assigned to this agent."""
        res = await db.execute(
            _sa_text(
                "SELECT c.agent_user_id "
                "FROM conversations v "
                "JOIN contacts c ON c.id = v.contact_id "
                "WHERE v.id = :conv_id"
            ).bindparams(conv_id=conv_id)
        )
        row = res.first()
        if row is None:
            return False
        return row[0] == agent_id

    queue = event_bus.subscribe()

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    # feat(authz): filter lead.created by ownership (A1 — same helper as /events)
                    if not should_forward_event(event, is_agent=is_agent, user_id=agent_id):
                        continue
                    # feat(authz): filter conversation_update and message_update
                    # events for agents — only forward events for owned contacts.
                    if is_agent:
                        event_type: str = event.get("type", "")
                        event_data: dict = event.get("data", {})
                        conv_id = event_data.get("conversation_id")
                        if conv_id is not None and (
                            event_type == "conversation_update"
                            or event_type.startswith("message_update_")
                        ):
                            if not await _agent_owns_conversation(conv_id):
                                continue
                    payload = json.dumps(event["data"])
                    yield f"event: {event['type']}\ndata: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/conversations/{conv_id}", response_class=HTMLResponse)
async def conversation_detail(conv_id: int, request: Request,
                               user: User = Depends(get_current_user),
                               db: AsyncSession = Depends(get_db)):
    from fastapi import HTTPException
    from app.repositories.contact_repo import contact_repo as _contact_repo
    from sqlalchemy import select as _select
    from app.models.conversation import Conversation as _Conv

    # ROLE-04: verify agent has access to this conversation's contact
    if user.role == "agent":
        _res = await db.execute(_select(_Conv).where(_Conv.id == conv_id))
        _conv_obj = _res.scalar_one_or_none()
        if _conv_obj is None:
            raise HTTPException(status_code=404)
        _contact = await _contact_repo.get_by_id(db, _conv_obj.contact_id) if _conv_obj.contact_id else None
        if _contact is None or _contact.agent_user_id != user.id:
            raise HTTPException(status_code=403, detail="Acceso denegado")

    # ROLE-04: list uses agent_filter for consistent sidebar
    agent_filter = user.id if user.role == "agent" else None
    conversations = await conversation_service.get_conversations(db, agent_filter=agent_filter)
    thread = await conversation_service.get_thread(db, conv_id)
    settings = await settings_service.get_all_settings(db)
    whatsapp_mode = settings["whatsapp_mode"]

    context = {
        "request": request,
        "user": user,
        "conversations": conversations,
        "selected_id": conv_id,
        "thread": thread,
        "whatsapp_mode": whatsapp_mode,
        "phone_prefixes": PREFIXES,
        "channel": "",
        "q": "",
        "offset": 0,
        "has_more": len(conversations) == 50,
        "limit": 50,
    }

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("partials/conversation_thread.html", {
            "request": request, "user": user, "thread": thread,
        })

    return templates.TemplateResponse("conversations.html", context)


@router.post("/conversations/wa-mode-toggle", response_class=HTMLResponse)
async def conversations_wa_mode_toggle(request: Request,
                                        user: User = Depends(get_current_user),
                                        db: AsyncSession = Depends(get_db)):
    new_mode = await settings_service.toggle_whatsapp_mode(db, user.id)
    is_auto = new_mode == "auto"
    return templates.TemplateResponse("partials/wa_mode_toggle.html", {
        "request": request, "is_auto": is_auto,
    })

@router.post("/conversations/{conv_id}/bot-toggle", response_class=HTMLResponse)
async def conversation_bot_toggle(
    conv_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Toggle is_bot_active for a conversation and log an audit event."""
    # feat(authz): agent ownership check (ROLE-agent-write)
    await ensure_conversation_access(db, user, conv_id)
    result = await conversation_service.toggle_bot_active(db, conv_id)
    if result is None:
        return HTMLResponse(status_code=404)
    new_val, contact_id = result
    await lead_event_repo.create(
        db=db,
        contact_id=contact_id,
        event_type="bot_toggle",
        old_status=None,
        new_status=None,
        triggered_by=f"user:{user.id}",
        metadata={"conversation_id": conv_id, "is_bot_active": new_val},
    )
    await db.commit()
    try:
        from app.services.event_bus import event_bus as _event_bus
        await _event_bus.publish("conversation.bot_toggled", {
            "conversation_id": conv_id,
            "is_bot_active": new_val,
            "user_id": user.id,
            "user_name": user.name or user.email,
        })
        await _event_bus.publish("conversation_update", {"conversation_id": conv_id})
    except Exception:
        pass  # SSE is best-effort
    return templates.TemplateResponse(
        "partials/conversation_bot_toggle.html",
        {"request": request, "conv_id": conv_id, "is_bot_active": new_val},
    )


@router.get("/conversations/{conv_id}/messages", response_class=HTMLResponse)
async def conversation_messages(conv_id: int, request: Request,
                                 user: User = Depends(get_current_user),
                                 db: AsyncSession = Depends(get_db)):
    # feat(authz): agent ownership check (ROLE-agent-read)
    await ensure_conversation_access(db, user, conv_id)
    thread = await conversation_service.get_thread(db, conv_id)
    messages = thread["messages"] if thread else []
    properties_map = thread["properties_map"] if thread else {}
    return templates.TemplateResponse("partials/message_list_items.html", {
        "request": request,
        "messages": messages,
        "properties_map": properties_map,
    })

@router.post("/conversations/{conv_id}/reply", response_class=HTMLResponse)
async def send_reply(
    conv_id: int,
    request: Request,
    message: str = Form(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send a manual reply (WhatsApp or Telegram) from the panel."""
    # feat(authz): agent ownership check (ROLE-agent-write)
    await ensure_conversation_access(db, user, conv_id)
    text = message.strip()
    if not text or len(text) > 4096:
        return templates.TemplateResponse("partials/error_message.html", {
            "request": request, "message": "Mensaje invalido (1-4096 caracteres)",
        })
    try:
        result = await reply_service.send_reply(db, conv_id, text, user.id)
        # Commit before SSE so the new message is visible to other sessions
        await db.commit()
        try:
            from app.services.event_bus import event_bus as _event_bus
            await _event_bus.publish("conversation_update", {"conversation_id": conv_id})
            await _event_bus.publish(f"message_update_{conv_id}", {"conversation_id": conv_id})
        except Exception:
            pass  # SSE is best-effort
        msg = result["message"]
        warning = result["warning"]
        return templates.TemplateResponse("partials/reply_response.html", {
            "request": request,
            "msg": msg,
            "conv_id": conv_id,
            "warning": warning,
        })
    except ValueError as e:
        return templates.TemplateResponse("partials/error_message.html", {
            "request": request, "message": str(e),
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Twilio HTTP error on reply conv_id=%d status=%d",
            conv_id,
            e.response.status_code,
        )
        return templates.TemplateResponse("partials/error_message.html", {
            "request": request, "message": "Error al enviar el mensaje. Intente nuevamente.",
        })
    except Exception:
        logger.exception("Unexpected error on reply conv_id=%d", conv_id)
        return templates.TemplateResponse("partials/error_message.html", {
            "request": request, "message": "Error al enviar mensaje",
        })


@router.get("/conversations/contacts/search", response_class=HTMLResponse)
async def search_contacts_for_template(
    request: Request,
    q: str = "",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return contact search results as an HTML list for the send-template drawer.

    feat(authz): agents only see contacts assigned to them.
    """
    from app.repositories.contact_repo import contact_repo as _contact_repo
    contacts = []
    if q and len(q) >= 2:
        contacts = await _contact_repo.get_all(
            db, status=None, source=None, search=q, limit=8, offset=0,
            agent_user_id=user.id if user.role == "agent" else None,
        )
    return templates.TemplateResponse("partials/contact_search_results.html", {
        "request": request,
        "contacts": contacts,
        "q": q,
    })


@router.post("/conversations/send_template_new")
async def send_template_to_new_contact(
    request: Request,
    name: str = Form(...),
    phone: str = Form(...),
    template_key: str = Form(...),
    property_id: int | None = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new contact then send a template to them."""
    from app.repositories.contact_repo import contact_repo as _contact_repo
    from app.repositories.lead_event_repo import lead_event_repo as _lead_event_repo
    from app.schemas.template import ALLOWED_TEMPLATE_KEYS
    from fastapi.responses import RedirectResponse

    if template_key not in ALLOWED_TEMPLATE_KEYS:
        return templates.TemplateResponse("partials/error_message.html", {
            "request": request,
            "message": f"Template no valido: {template_key}",
        })

    cleaned_phone = phone.strip()
    if not cleaned_phone.startswith("+"):
        cleaned_phone = "+" + cleaned_phone.lstrip("+")

    # `property_id` quedó como parámetro muerto: el catálogo se fue con el
    # vertical inmobiliario. Se ignora en vez de rechazarlo para no romper un
    # formulario viejo que todavía lo mande.
    property_id = None

    try:
        contact = await _contact_repo.get_by_phone(db, cleaned_phone)
        is_new_contact = contact is None
        if is_new_contact:
            contact = await _contact_repo.create(
                db,
                name=name.strip(),
                phone=cleaned_phone,
                source="manual",
                status="new",
                property_id=property_id,
            )
            await _lead_event_repo.create(
                db,
                contact_id=contact.id,
                event_type="new_contact",
                old_status=None,
                new_status="new",
                triggered_by=f"user:{user.id}",
                metadata={"source": "manual", "template": template_key, "role": user.role},
            )
    except Exception:
        logger.exception("Failed to get/create contact for send_template_new")
        return templates.TemplateResponse("partials/error_message.html", {
            "request": request,
            "message": "Error al crear el contacto",
        })

    try:
        result = await template_service.send_template(db, contact.id, template_key)
        return RedirectResponse(url=f"/conversations/{result['conversation_id']}", status_code=303)
    except ValueError as e:
        return templates.TemplateResponse("partials/error_message.html", {
            "request": request,
            "message": str(e),
        })
    except Exception:
        logger.exception("send_template_new failed")
        return templates.TemplateResponse("partials/error_message.html", {
            "request": request,
            "message": "Error al enviar el template",
        })


@router.get("/conversations/{conv_id}/activity", response_class=HTMLResponse)
async def conversation_activity(
    conv_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the activity timeline partial for a conversation.

    Loads the 30 most recent lead_events for the conversation's contact,
    rendered as a human-readable Spanish timeline.
    Access-controlled: agents must own the conversation's contact.
    """
    # feat(authz): agent ownership check (ROLE-agent-read)
    await ensure_conversation_access(db, user, conv_id)
    items = await conversation_service.get_activity(db, conv_id)
    return templates.TemplateResponse(
        "partials/conversation_activity.html",
        {"request": request, "items": items},
    )


@router.post("/conversations/send_template", response_class=HTMLResponse)
async def send_template(
    request: Request,
    contact_id: int = Form(...),
    template_key: str = Form(...),
    property_id: int | None = Form(None),
    pref_zona: str | None = Form(None),
    pref_tipo: str | None = Form(None),
    pref_operacion: str | None = Form(None),
    pref_presupuesto: str | None = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send a pre-approved WhatsApp template to a contact."""
    # feat(authz): agent ownership check (ROLE-agent-write)
    await ensure_contact_access(db, user, contact_id)
    try:
        # Validate via Pydantic schema
        SendTemplateRequest(contact_id=contact_id, template_key=template_key)

        result = await template_service.send_template(
            db=db,
            contact_id=contact_id,
            template_key=template_key,
            property_id=property_id,
            pref_zona=pref_zona,
            pref_tipo=pref_tipo,
            pref_operacion=pref_operacion,
            pref_presupuesto=pref_presupuesto,
        )
        from fastapi.responses import RedirectResponse
        return RedirectResponse(
            url=f"/conversations/{result['conversation_id']}",
            status_code=303,
        )
    except ValueError as e:
        return templates.TemplateResponse("partials/error_message.html", {
            "request": request, "message": str(e),
        })
    except httpx.HTTPStatusError as e:
        logger.error(
            "Twilio HTTP error sending template contact_id=%d status=%d",
            contact_id,
            e.response.status_code,
        )
        return templates.TemplateResponse("partials/error_message.html", {
            "request": request, "message": "Error al enviar template. Intente nuevamente.",
        })
    except Exception:
        logger.exception("Unexpected error sending template contact_id=%d", contact_id)
        return templates.TemplateResponse("partials/error_message.html", {
            "request": request, "message": "Error al enviar template",
        })
