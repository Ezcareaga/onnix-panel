"""Telegram webhook endpoint.

Receives Telegram Update JSON via POST /webhook/telegram, parses it
into a BotRequest, verifies the secret token header, and returns 200
immediately while processing the message in a background task.

Plan 66-01: Tasks 1-4.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request

from app.bot.config import bot_settings
from app.bot.core.types import BotRequest
from app.config import settings as _settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhook"])


# ---------------------------------------------------------------------------
# Task 1: Parse Telegram Update -> BotRequest | None
# ---------------------------------------------------------------------------


def parse_telegram_update(body: dict[str, Any]) -> BotRequest | None:
    """Parse a raw Telegram Update dict into a BotRequest.

    Returns None for:
    - Group chats (chat.type != "private")
    - Messages with no text/caption and no callback_query
    - edited_message updates (no ``message`` key, only ``edited_message``)
    - Unrecognised update shapes
    """
    # --- callback_query ---
    callback = body.get("callback_query")
    if callback:
        message = callback.get("message") or {}
        chat = message.get("chat") or {}

        if chat.get("type") != "private":
            return None

        from_user = callback.get("from") or {}

        return BotRequest(
            platform="telegram",
            chat_id=str(chat.get("id", "")),
            user_id=str(from_user.get("id", "")),
            user_name=_build_user_name(from_user),
            text=None,
            external_id=str(callback.get("id", "")),
            callback_data=callback.get("data"),
        )

    # --- regular message ---
    msg = body.get("message")
    if msg is None:
        # edited_message or other unsupported update types
        return None

    chat = msg.get("chat") or {}
    if chat.get("type") != "private":
        return None

    from_user = msg.get("from") or {}
    text = msg.get("text") or msg.get("caption")

    if not text:
        return None

    return BotRequest(
        platform="telegram",
        chat_id=str(chat.get("id", "")),
        user_id=str(from_user.get("id", "")),
        user_name=_build_user_name(from_user),
        text=text,
        external_id=str(msg.get("message_id", "")),
        callback_data=None,
    )


def _build_user_name(from_user: dict[str, Any]) -> str:
    """Build a display name from the Telegram ``from`` object."""
    first = from_user.get("first_name") or ""
    last = from_user.get("last_name") or ""
    name = f"{first} {last}".strip()
    return name or from_user.get("username") or "Unknown"


# ---------------------------------------------------------------------------
# Task 2: Secret token verification dependency
# ---------------------------------------------------------------------------


async def verify_telegram_secret(
    x_telegram_bot_api_secret_token: str | None = Header(None),
) -> None:
    """Verify the X-Telegram-Bot-Api-Secret-Token header.

    If TELEGRAM_WEBHOOK_SECRET is empty, validation is skipped (dev mode).
    """
    expected = bot_settings.TELEGRAM_WEBHOOK_SECRET
    if not expected:
        if _settings.is_production:
            raise HTTPException(
                status_code=403,
                detail="Signature verification unavailable",
            )
        # Dev mode — no secret configured, skip validation
        return

    if x_telegram_bot_api_secret_token != expected:
        raise HTTPException(status_code=403, detail="Invalid secret token")


# ---------------------------------------------------------------------------
# Task 4: Background processing placeholder
# ---------------------------------------------------------------------------


async def _process_telegram(request: BotRequest) -> None:
    """Process a Telegram message through the full bot pipeline.

    Creates its own DB session (background tasks run outside the
    request lifecycle) and delegates to the wired MessageHandler.
    """
    from app.bot.core.conversation import persist_inbound
    from app.bot.observability.context import set_request_context, clear_request_context
    from app.bot.webhooks.dependencies import get_bot_dependencies
    from app.database import async_session_factory

    logger.info(
        'Telegram processing start — {"chat_id": "%s", "user": "%s", "text": "%.50s", "callback": %s}',
        request.chat_id,
        request.user_name,
        request.text or "",
        '"%s"' % request.callback_data if request.callback_data else "null",
    )

    # Set request-scoped context vars so all logs in this task are enriched.
    set_request_context(
        request_id=uuid.uuid4().hex,
        external_id=request.external_id or "",
        channel="telegram",
        phone_e164=request.user_id or "unknown",
    )

    start = time.monotonic()
    # La sesion primero, el grafo adentro del try. Armar el grafo construye los
    # clientes de Claude y Gemini, el buscador y los senders, asi que una
    # credencial ausente revienta ahi mismo — genai.Client(api_key="") tira
    # ValueError al instanciar. Afuera del try eso salia como error no manejado
    # de la background task, sin pasar por BotErrorService y sin llegar al
    # contador que apaga el bot. async_session_factory() no hace I/O, asi que
    # adelantarla es gratis y deja a session siempre disponible para el rollback
    # y el close.
    session = async_session_factory()
    try:
        # El entrante se guarda ANTES de todo lo que puede fallar: el grafo, las
        # compuertas y el LLM. Que el bot no conteste es una decision; que el
        # mensaje no exista es perdida de datos. Ver `persist_inbound`.
        # El commit es propio a proposito: el rollback del `except` de abajo no
        # tiene que poder deshacer el guardado.
        await persist_inbound(session, request)
        await session.commit()

        deps = get_bot_dependencies()
        await deps.tg_handler.handle(request, session)
        await session.commit()
        # Post-commit SSE: data is now visible to other sessions
        try:
            from sqlalchemy import text as _text
            from app.services.event_bus import event_bus as _event_bus
            _res = await session.execute(
                _text(
                    "SELECT c.id, co.id AS contact_id, co.name, co.phone, co.status, "
                    "  co.agent_user_id, "
                    "  (NOW() - co.created_at) < INTERVAL '30 seconds' AS is_new "
                    "FROM conversations c "
                    "JOIN contacts co ON co.id = c.contact_id "
                    "WHERE co.source = 'telegram' AND co.source_id = :source_id "
                    "ORDER BY c.updated_at DESC LIMIT 1"
                ),
                {"source_id": request.user_id},
            )
            _row = _res.first()
            if _row:
                _cid = _row.id
                await _event_bus.publish("conversation_update", {"conversation_id": _cid})
                await _event_bus.publish(f"message_update_{_cid}", {"conversation_id": _cid})
                if _row.is_new:
                    await _event_bus.publish("lead.created", {
                        "contact_id": _row.contact_id,
                        "name": _row.name or "",
                        "source": "telegram",
                        "phone": _row.phone or "",
                        "status": _row.status or "new",
                        "agent_user_id": _row.agent_user_id,
                    })
        except Exception:
            pass  # SSE is best-effort
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "Telegram processing complete (%.0fms) — chat_id=%s",
            elapsed_ms, request.chat_id,
        )
    except Exception as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.exception(
            "Telegram processing error (%.0fms) — chat_id=%s",
            elapsed_ms, request.chat_id,
        )
        await session.rollback()

        # Record error in a separate session (main session is rolled back)
        try:
            from app.bot.services.error_service import BotErrorService

            err_session = async_session_factory()
            try:
                svc = BotErrorService(workflow="telegram")
                await svc.record_error(
                    err_session,
                    str(exc),
                    node="webhook_process",
                    chat_id=request.chat_id,
                )
                await svc.check_and_disable(err_session)
            finally:
                await err_session.close()
        except Exception:
            logger.warning("Failed to record bot error (non-fatal)", exc_info=True)
    finally:
        await session.close()
        clear_request_context()


# ---------------------------------------------------------------------------
# Task 4: Route
# ---------------------------------------------------------------------------


@router.post("/webhook/telegram", status_code=200)
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    _secret: None = Depends(verify_telegram_secret),
) -> dict[str, str]:
    """Receive Telegram webhook updates.

    Parses the Update, returns 200 immediately, and processes
    the message in a background task.
    """
    body = await request.json()

    update_type = "callback_query" if "callback_query" in body else (
        "message" if "message" in body else "other"
    )
    logger.info(
        'Webhook received — {"method": "POST", "path": "/webhook/telegram", "update_type": "%s", "update_id": %s}',
        update_type, body.get("update_id", "null"),
    )

    bot_request = parse_telegram_update(body)

    if bot_request is None:
        logger.debug("Telegram update ignored (non-private or unsupported type)")
        return {"status": "ignored"}

    background_tasks.add_task(_process_telegram, bot_request)

    return {"status": "ok"}
