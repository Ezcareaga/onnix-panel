"""WhatsApp/Twilio webhook endpoint.

Receives Twilio form-encoded POST data, verifies the X-Twilio-Signature
HMAC header, filters out StatusCallback events, parses the payload into
a BotRequest, returns TwiML immediately, and queues a background task
for message processing.

Plan 66-02: WhatsApp/Twilio Webhook Endpoint.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import time
import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import Response

from app.bot.core.types import BotRequest
from app.config import settings as _settings

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)
# Log status callbacks under bot.sender (app.bot.channels → bot.sender alias)
_sender_logger = logging.getLogger("app.bot.channels")

# Dedup: track last TG notification per ErrorCode (max 1 per hour)
_twilio_error_notified: dict[str, float] = {}
_TWILIO_ERROR_DEDUP_SECONDS = 3600

router = APIRouter()

# StatusCallback SmsStatus values that indicate delivery updates, not messages
_STATUS_CALLBACK_VALUES = frozenset({
    "sent", "delivered", "read", "failed", "undelivered",
})

# Twilio error codes that should NOT trigger an admin alert (already imported
# from twilio_retry to keep both paths in sync).
from app.bot.channels.twilio_retry import NO_ALERT_TWILIO_CODES  # noqa: E402

# Empty TwiML to acknowledge the webhook immediately
_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


# ---------------------------------------------------------------------------
# Twilio signature verification
# ---------------------------------------------------------------------------

def _get_twilio_auth_token() -> str:
    """Read TWILIO_AUTH_TOKEN from environment (via bot_settings)."""
    from app.bot.config import bot_settings
    return bot_settings.TWILIO_AUTH_TOKEN


def _get_webhook_base_url() -> str:
    """Read the webhook base URL override, if any.

    In production behind a reverse proxy (Cloudflare/Nginx), the
    URL that Twilio signed may differ from what FastAPI sees.
    TWILIO_WEBHOOK_URL_BASE overrides the scheme+host portion.
    """
    return os.environ.get("TWILIO_WEBHOOK_URL_BASE", "")


def verify_twilio_signature(
    url: str,
    params: dict[str, str],
    signature: str,
    auth_token: str,
) -> bool:
    """Verify Twilio X-Twilio-Signature using HMAC-SHA1.

    Implements the same algorithm as twilio.request_validator:
    1. Take the full URL
    2. Append each POST param key+value sorted by key
    3. HMAC-SHA1 with auth_token as key
    4. Base64-encode the digest
    5. Compare with the provided signature
    """
    data_str = url
    for key in sorted(params.keys()):
        data_str += key + params[key]

    computed = base64.b64encode(
        hmac.new(
            auth_token.encode("utf-8"),
            data_str.encode("utf-8"),
            hashlib.sha1,
        ).digest()
    ).decode("utf-8")

    return hmac.compare_digest(computed, signature)


# ---------------------------------------------------------------------------
# Parse Twilio form data into BotRequest
# ---------------------------------------------------------------------------

def parse_twilio_webhook(form_data: dict[str, str]) -> BotRequest | None:
    """Parse Twilio webhook form data into a BotRequest.

    Returns ``None`` for StatusCallback events (delivery receipts)
    and messages with no usable content.

    StatusCallback detection: SmsStatus is present with a delivery
    status AND there is no Body field.
    """
    sms_status = form_data.get("SmsStatus", "")
    body = form_data.get("Body", "")

    # Filter StatusCallback — delivery receipts without a message body
    if sms_status.lower() in _STATUS_CALLBACK_VALUES and not body.strip():
        logger.debug(
            "Filtered StatusCallback: SmsStatus=%s MessageSid=%s",
            sms_status,
            form_data.get("MessageSid", ""),
        )
        return None

    # Extract sender info
    raw_from = form_data.get("From", "")
    # Strip whatsapp: prefix to get clean E.164 phone
    chat_id = raw_from.replace("whatsapp:", "").strip()
    if not chat_id:
        logger.warning("WhatsApp webhook missing From field")
        return None

    user_name = form_data.get("ProfileName", "").strip() or chat_id
    message_sid = form_data.get("MessageSid", "")

    # Determine text and callback_data
    button_payload = form_data.get("ButtonPayload", "")
    button_text = form_data.get("ButtonText", "")

    text: str | None = None
    callback_data: str | None = None

    if button_payload:
        callback_data = button_payload
        text = button_text or button_payload
    elif body.strip():
        text = body.strip()
        callback_data = None
    else:
        # No usable content (media-only, etc.)
        logger.debug(
            "WhatsApp webhook with no text/button: MessageSid=%s",
            message_sid,
        )
        return None

    return BotRequest(
        platform="whatsapp",
        chat_id=chat_id,
        user_id=chat_id,
        user_name=user_name,
        text=text,
        external_id=message_sid or None,
        callback_data=callback_data,
    )


# ---------------------------------------------------------------------------
# Background task placeholder (wired in 66-03)
# ---------------------------------------------------------------------------

async def _process_whatsapp(request: BotRequest) -> None:
    """Process an inbound WhatsApp message through the full bot pipeline.

    Creates its own DB session (background tasks run outside the
    request lifecycle) and delegates to the wired MessageHandler.
    """
    from app.bot.core.conversation import persist_inbound
    from app.bot.observability.context import set_request_context, clear_request_context
    from app.database import async_session_factory

    logger.info(
        'WhatsApp processing start — {"chat_id": "%s", "text": "%.50s", "callback": %s}',
        request.chat_id,
        request.text or "",
        '"%s"' % request.callback_data if request.callback_data else "null",
    )

    # Set request-scoped context vars so all logs in this task are enriched.
    set_request_context(
        request_id=uuid.uuid4().hex,
        external_id=request.external_id or "",
        channel="whatsapp",
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

        # Acá arrancaba el bot: `deps.wa_handler.handle(...)` armaba el grafo de
        # IA, resolvía el turno y contestaba solo. Onnix no quiere eso — quiere
        # la bandeja, y que conteste una persona desde el panel.
        #
        # El entrante ya quedó guardado arriba, así que sacar la respuesta
        # automática no le saca nada a la conversación: el mensaje entra, el SSE
        # de abajo lo empuja al panel, y el agente responde con `reply_service`
        # o con una plantilla desde el hilo.
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
                    "WHERE co.phone = :phone "
                    "ORDER BY c.updated_at DESC LIMIT 1"
                ),
                {"phone": request.chat_id},
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
                        "source": "whatsapp",
                        "phone": _row.phone or request.chat_id,
                        "status": _row.status or "new",
                        "agent_user_id": _row.agent_user_id,
                    })
        except Exception:
            pass  # SSE is best-effort
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "WhatsApp processing complete (%.0fms) — chat_id=%s",
            elapsed_ms, request.chat_id,
        )
    except Exception as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.exception(
            "WhatsApp processing error (%.0fms) — chat_id=%s",
            elapsed_ms, request.chat_id,
        )
        await session.rollback()

        # Record error in a separate session (main session is rolled back)
        try:
            from app.bot.services.error_service import BotErrorService

            err_session = async_session_factory()
            try:
                svc = BotErrorService(workflow="whatsapp")
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
# Route
# ---------------------------------------------------------------------------

@router.post("/webhook/whatsapp")
async def whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """Receive Twilio WhatsApp webhook.

    1. Read form data
    2. Verify X-Twilio-Signature (skip in dev mode if no auth token)
    3. Parse into BotRequest
    4. If StatusCallback or empty -> return empty TwiML
    5. Queue background task for processing
    6. Return empty TwiML immediately
    """
    # Read form data once
    form = await request.form()
    form_data: dict[str, str] = {k: str(v) for k, v in form.items()}

    logger.info(
        'Webhook received — {"method": "POST", "path": "/webhook/whatsapp", "from": "%s", "body_preview": "%.80s"}',
        form_data.get("From", "unknown"),
        form_data.get("Body", "")[:80],
    )

    # Twilio signature verification
    auth_token = _get_twilio_auth_token()
    if auth_token:
        signature = request.headers.get("X-Twilio-Signature", "")
        if not signature:
            logger.warning("WhatsApp webhook missing X-Twilio-Signature")
            raise HTTPException(status_code=403, detail="Missing signature")

        # Build the URL Twilio signed against
        webhook_base = _get_webhook_base_url()
        if webhook_base:
            # Use override base URL + path (Twilio signs the public URL)
            url = webhook_base.rstrip("/") + str(request.url.path)
        else:
            url = str(request.url)

        if not verify_twilio_signature(url, form_data, signature, auth_token):
            logger.warning("WhatsApp webhook invalid X-Twilio-Signature")
            raise HTTPException(status_code=403, detail="Invalid signature")
    else:
        if _settings.is_production:
            raise HTTPException(
                status_code=403,
                detail="Signature verification unavailable",
            )
        logger.debug("TWILIO_AUTH_TOKEN empty — skipping signature verification (dev mode)")

    # Parse the webhook data
    bot_request = parse_twilio_webhook(form_data)

    if bot_request is None:
        # StatusCallback or empty message — acknowledge silently
        return Response(content=_EMPTY_TWIML, media_type="application/xml")

    # Queue background processing
    background_tasks.add_task(_process_whatsapp, bot_request)

    # Return empty TwiML immediately
    return Response(content=_EMPTY_TWIML, media_type="application/xml")


# ---------------------------------------------------------------------------
# Twilio delivery status callback
# ---------------------------------------------------------------------------

@router.post("/webhook/whatsapp/status")
async def whatsapp_status_callback(request: Request) -> Response:
    """Receive Twilio delivery status callbacks.

    Twilio POSTs: MessageSid, MessageStatus (queued/sent/delivered/
    read/failed/undelivered), ErrorCode (optional).
    We verify the Twilio HMAC-SHA1 signature, log, and acknowledge.
    """
    form = await request.form()
    form_data: dict[str, str] = {k: str(v) for k, v in form.items()}

    # Twilio signature verification (same as main webhook)
    auth_token = _get_twilio_auth_token()
    if auth_token:
        signature = request.headers.get("X-Twilio-Signature", "")
        if not signature:
            logger.warning("WhatsApp status callback missing X-Twilio-Signature")
            raise HTTPException(status_code=403, detail="Missing signature")
        webhook_base = _get_webhook_base_url()
        if webhook_base:
            url = webhook_base.rstrip("/") + str(request.url.path)
        else:
            url = str(request.url)
        if not verify_twilio_signature(url, form_data, signature, auth_token):
            logger.warning("WhatsApp status callback invalid X-Twilio-Signature")
            raise HTTPException(status_code=403, detail="Invalid signature")
    else:
        if _settings.is_production:
            raise HTTPException(
                status_code=403,
                detail="Signature verification unavailable",
            )
        logger.debug(
            "TWILIO_AUTH_TOKEN empty — skipping status callback signature verification (dev mode)"
        )

    sid = form_data.get("MessageSid", "")
    status = form_data.get("MessageStatus", "")
    error_code = form_data.get("ErrorCode", "")
    to = form_data.get("To", "")

    # ----------------------------------------------------------------
    # Persist delivery status and publish SSE (best-effort)
    # ----------------------------------------------------------------
    _PERSIST_STATUSES = frozenset({"sent", "delivered", "read", "failed", "undelivered"})
    if status in _PERSIST_STATUSES:
        try:
            from app.database import async_session_factory
            from app.services.message_status_service import message_status_service

            _db = async_session_factory()
            try:
                await message_status_service.handle_status_callback(
                    db=_db,
                    message_sid=sid,
                    new_status=status,
                    error_code=error_code,
                )
                await _db.commit()
            except Exception:
                await _db.rollback()
                _sender_logger.warning(
                    "[STATUS] DB persist failed for SID=%s status=%s (non-fatal)",
                    sid, status, exc_info=True,
                )
            finally:
                await _db.close()
        except Exception:
            _sender_logger.warning(
                "[STATUS] Session setup failed for SID=%s (non-fatal)",
                sid, exc_info=True,
            )

    if error_code:
        _sender_logger.warning(
            '[STATUS] SID=%s FAILED status=%s error=%s to=%s',
            sid, status, error_code, to,
        )
        # Suppress admin alert for known silent error codes (e.g. 63016 —
        # recipient not on WhatsApp).  Still logged above for audit trail.
        if str(error_code) in NO_ALERT_TWILIO_CODES:
            _sender_logger.warning(
                "[STATUS] Suppressed notification for silent error %s (to=%s)",
                error_code, to,
            )
        else:
            # Notify admin via TG (dedup: max 1 per error_code per hour)
            now = time.time()
            last_notified = _twilio_error_notified.get(error_code, 0.0)
            if now - last_notified >= _TWILIO_ERROR_DEDUP_SECONDS:
                _twilio_error_notified[error_code] = now
                try:
                    from app.bot.services.admin_notifier import get_admin_notifier
                    notifier = get_admin_notifier()
                    await notifier.notify_twilio_error(
                        error_code=error_code,
                        error_message=f"Status={status} SID={sid}",
                        to_number=to.replace("whatsapp:", ""),
                    )
                except Exception:
                    _sender_logger.warning(
                        "Failed to notify admin about Twilio error (non-fatal)",
                        exc_info=True,
                    )
    else:
        _sender_logger.info(
            '[STATUS] SID=%s status=%s to=%s',
            sid, status, to,
        )

    return Response(content=_EMPTY_TWIML, media_type="application/xml")
