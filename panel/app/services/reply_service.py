"""Service for sending manual WhatsApp replies from the admin panel."""
import httpx
import logging
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.channels.twilio_retry import wa_send_disabled
from app.bot.config import bot_settings
from app.repositories.conversation_repo import conversation_repo
from app.repositories.contact_repo import contact_repo
from app.repositories.message_repo import message_repo
from app.repositories.lead_event_repo import lead_event_repo

logger = logging.getLogger(__name__)

# Shared HTTP client with connection pooling for Twilio and Telegram APIs
_http_client = httpx.AsyncClient(
    timeout=httpx.Timeout(10.0),
    limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
)


class ReplyService:
    @staticmethod
    async def _send_twilio_whatsapp(to_phone: str, body: str) -> dict:
        """Send a WhatsApp message via Twilio REST API.

        Returns the Twilio response JSON (contains 'sid', 'status', etc.).
        Raises httpx.HTTPStatusError on Twilio errors.
        """
        account_sid = bot_settings.TWILIO_ACCOUNT_SID
        auth_token = bot_settings.TWILIO_AUTH_TOKEN
        from_number = bot_settings.TWILIO_WHATSAPP_FROM

        url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"

        # Ensure To has whatsapp: prefix
        to_wa = to_phone if to_phone.startswith("whatsapp:") else f"whatsapp:{to_phone}"

        data: dict[str, str] = {"From": from_number, "To": to_wa, "Body": body}

        # Staging isolation guard (incidente 2026-04-04 class) — this service
        # posts on its own _http_client, so it never passes through
        # twilio_post_with_retry.  Empty dict => external_sid "" downstream.
        if wa_send_disabled():
            logger.warning(
                "WA send disabled via WA_SEND_ENABLED=false — manual reply NOT sent "
                "(to=%s, body_len=%d)",
                to_wa, len(body),
            )
            return {}

        # Include StatusCallback so manual replies also get delivery receipts.
        status_callback_url = bot_settings.TWILIO_STATUS_CALLBACK_URL
        if status_callback_url:
            data["StatusCallback"] = status_callback_url

        resp = await _http_client.post(
            url,
            auth=(account_sid, auth_token),
            data=data,
        )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    async def _send_telegram(chat_id: str, body: str) -> dict:
        """Send a Telegram message via Bot API.

        Returns dict with 'message_id' on success.
        Raises httpx.HTTPStatusError on Telegram API errors.
        """
        bot_token = bot_settings.TELEGRAM_BOT_TOKEN
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

        resp = await _http_client.post(
            url,
            json={"chat_id": chat_id, "text": body},
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise ValueError(f"Telegram API error: {data.get('description', 'unknown')}")
        return data.get("result", {})

    @staticmethod
    async def send_reply(
        db: AsyncSession,
        conversation_id: int,
        message_text: str,
        user_id: int,
    ) -> dict:
        """Send a manual WhatsApp reply from the panel.

        Returns dict with 'message' (Message object) and 'warning' (str or None).
        Raises ValueError for validation errors, httpx.HTTPStatusError for Twilio errors.
        """
        # 1. Load conversation
        conv = await conversation_repo.get_by_id(db, conversation_id)
        if not conv:
            raise ValueError("Conversacion no encontrada")

        # 2. Load contact and validate
        contact = await contact_repo.get_by_id(db, conv.contact_id)
        if not contact or not contact.phone:
            raise ValueError("Contacto sin telefono")
        if contact.status == "discarded" or contact.baja_at is not None:
            raise ValueError("Contacto opt-out / descartado - no se puede contactar")

        # 3. Determine channel first (needed for window check)
        channel = conv.channel or conv.platform or "whatsapp"

        # 4. Check 24h WhatsApp session window — BLOCK if expired (WhatsApp only)
        warning = None
        if channel == "whatsapp":
            # Always use max of cached field and actual messages — field may be stale (N8N era)
            last_inbound_ts = await message_repo.get_last_inbound_at(db, conv.contact_id)
            effective_ts = contact.last_user_message_at
            if last_inbound_ts and (effective_ts is None or last_inbound_ts > effective_ts):
                effective_ts = last_inbound_ts
            if not effective_ts:
                raise ValueError(
                    "Fuera de ventana 24h: sin registro de último mensaje del cliente. "
                    "Usá una plantilla aprobada."
                )
            hours_since = (datetime.now(timezone.utc) - effective_ts).total_seconds() / 3600
            if hours_since > 24:
                raise ValueError(
                    f"Fuera de ventana 24h: último mensaje del cliente hace {int(hours_since)}h. "
                    "Usá una plantilla aprobada."
                )
        logger.info(
            "Sending manual reply to %s via %s (conv=%d, user=%d)",
            contact.phone,
            channel,
            conversation_id,
            user_id,
        )

        external_sid = ""
        if channel == "telegram":
            chat_id = conv.platform_chat_id
            if not chat_id:
                raise ValueError("Conversacion Telegram sin chat_id")
            tg_result = await ReplyService._send_telegram(chat_id, message_text)
            external_sid = str(tg_result.get("message_id", ""))
            logger.info("Telegram message_id: %s", external_sid)
        else:
            twilio_response = await ReplyService._send_twilio_whatsapp(contact.phone, message_text)
            external_sid = twilio_response.get("sid", "")
            logger.info("Twilio response SID: %s", external_sid)

        # 5. Insert message
        msg = await message_repo.create(
            db=db,
            conversation_id=conversation_id,
            contact_id=conv.contact_id,
            direction="outbound",
            sender_type="agent",
            body=message_text,
            content=message_text,
            external_id=external_sid,
            status="sent",
        )

        # 6. Update conversation timestamps
        now = datetime.now(timezone.utc)
        conv.last_message_at = now
        conv.message_count = (conv.message_count or 0) + 1
        conv.last_human_reply_at = now
        await db.flush()

        # 7. Auto-update status: new/no_response -> agent_replied + lead_event
        if contact.status in ("new", "no_response"):
            old_status = contact.status
            contact.status = "agent_replied"
            contact.updated_at = now
            await lead_event_repo.create(
                db=db,
                contact_id=conv.contact_id,
                event_type="auto_status_change",
                old_status=old_status,
                new_status="agent_replied",
                triggered_by=f"manual_reply:user:{user_id}",
                metadata={
                    "conversation_id": conversation_id,
                    "trigger": "manual_panel_reply",
                },
            )
            await db.flush()
            logger.info(
                "Auto status update: contact %d %s -> agent_replied (manual reply by user %d)",
                conv.contact_id,
                old_status,
                user_id,
            )

        # 8. Disable bot for this conversation — a human is handling it now.
        # Without this the orchestrator gate (is_bot_active) stays True and
        # the bot may reply on top of the agent, creating contradictory
        # messages. Runs unconditionally: any manual reply means "human is
        # driving" regardless of prior contact status. M2.F8 fix.
        conv.is_bot_active = False
        await db.flush()

        logger.info(
            "Manual reply saved: msg_id=%d, conv=%d, external_id=%s, channel=%s",
            msg.id,
            conversation_id,
            external_sid,
            channel,
        )

        return {"message": msg, "warning": warning}


reply_service = ReplyService()
