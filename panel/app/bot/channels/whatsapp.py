"""WhatsApp channel sender — delivers messages via Twilio REST API.

Uses httpx to POST to api.twilio.com. Supports plain text (Body),
ContentSid templates, and MediaUrl for photos. All errors are caught
and logged; ``send()`` returns ``False`` on any failure.

Plan 63-01: CHAN-02 WhatsApp sender.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

import httpx

from app.bot.channels.base import BaseSender
from app.bot.channels.twilio_retry import twilio_post_with_retry, wa_send_disabled

if TYPE_CHECKING:
    from app.bot.core.types import ChannelPayload, PayloadMessage

logger = logging.getLogger(__name__)

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"
SEND_TIMEOUT = 15.0  # seconds per request
# Delays between Twilio messages
INTER_MESSAGE_DELAY = 0.8   # 800ms between text messages
INTER_MEDIA_DELAY = 3.0     # 3s between media messages (avoids WA 63021 rate limit)
PRE_BUTTONS_DELAY = 5.0     # 5s before button/template messages


class WhatsAppSender(BaseSender):
    """Sends messages to WhatsApp via the Twilio REST API.

    Parameters
    ----------
    account_sid:
        Twilio Account SID.
    auth_token:
        Twilio Auth Token.
    from_number:
        The ``whatsapp:+NNNNN`` sender number.
    status_callback_url:
        URL where Twilio will POST delivery status updates.
    client:
        Optional pre-configured ``httpx.AsyncClient`` (for testing).
    """

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        status_callback_url: str = "",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._from_number = from_number
        self._status_callback_url = status_callback_url
        self._client = client
        self._messages_url = (
            f"{TWILIO_API_BASE}/Accounts/{account_sid}/Messages.json"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send(self, payload: "ChannelPayload", chat_id: str) -> bool:
        """Deliver every message in *payload* to *chat_id*.

        ``chat_id`` should be an E.164 phone number (e.g. +595981...).
        Automatically prepends ``whatsapp:`` if not present.

        Returns ``True`` only if **all** messages were sent successfully.
        """
        if not payload.messages:
            return True

        # Staging isolation guard (incidente 2026-04-04 class): when
        # WA_SEND_ENABLED is explicitly false/0/empty, short-circuit BEFORE any
        # httpx POST to api.twilio.com so staging — which inherits prod Twilio
        # creds via env_file: .env — can NEVER send a real WhatsApp message.
        # Default is "true": production behavior is byte-unchanged.
        if wa_send_disabled():
            n = len(payload.messages)
            kinds = [
                "template" if m.template_id else ("photo" if m.photo_url else "text")
                for m in payload.messages
            ]
            lens = [len(m.text or "") for m in payload.messages]
            logger.info(
                "WA send disabled via WA_SEND_ENABLED=false — payload NOT sent "
                "(chat_id=%s, messages=%d, kinds=%s, text_lens=%s)",
                chat_id, n, kinds, lens,
            )
            return True

        to_number = chat_id if chat_id.startswith("whatsapp:") else f"whatsapp:{chat_id}"

        client = self._client or httpx.AsyncClient(timeout=SEND_TIMEOUT)
        own_client = self._client is None
        try:
            total = len(payload.messages)
            for idx, msg in enumerate(payload.messages):
                # Delay between messages so Twilio processes media delivery
                if idx > 0:
                    is_last = idx == total - 1
                    has_template = bool(msg.template_id)
                    has_preceding_photos = any(
                        m.photo_url for m in payload.messages[:idx]
                    )
                    if is_last and has_template and has_preceding_photos:
                        # Long delay after photos for Twilio media processing
                        await asyncio.sleep(PRE_BUTTONS_DELAY)
                    elif is_last and has_template:
                        # Short delay for text-only → template transition
                        await asyncio.sleep(INTER_MESSAGE_DELAY)
                    elif msg.photo_url:
                        # Media messages need longer delay to avoid 63021 rate limit
                        await asyncio.sleep(INTER_MEDIA_DELAY)
                    else:
                        await asyncio.sleep(INTER_MESSAGE_DELAY)

                msg_kind = "template" if msg.template_id else ("photo" if msg.photo_url else "text")
                logger.info(
                    "WA send %d/%d [%s] to %s — media=%s",
                    idx + 1, total, msg_kind, to_number,
                    msg.photo_url.split("/")[-1] if msg.photo_url else "none",
                )
                ok = await self._send_one(client, to_number, msg)
                if not ok:
                    logger.warning(
                        "WA send %d/%d FAILED — stopping delivery for %s",
                        idx + 1, total, to_number,
                    )
                    return False
            return True
        except Exception:
            logger.exception("WhatsAppSender.send failed for chat_id=%s", chat_id)
            return False
        finally:
            if own_client:
                await client.aclose()

    # ------------------------------------------------------------------
    # Private: send a single message
    # ------------------------------------------------------------------

    async def _send_one(
        self,
        client: httpx.AsyncClient,
        to_number: str,
        msg: "PayloadMessage",
    ) -> bool:
        """Send a single PayloadMessage via Twilio."""
        data: dict[str, Any] = {
            "From": self._from_number,
            "To": to_number,
        }

        # ContentSid template takes priority
        if msg.template_id:
            data["ContentSid"] = msg.template_id
            if msg.text and msg.text.strip():
                data["ContentVariables"] = json.dumps({"1": msg.text})
        elif msg.text:
            data["Body"] = msg.text

        # Photo via MediaUrl
        if msg.photo_url:
            data["MediaUrl"] = msg.photo_url

        # If neither Body nor ContentSid, skip empty message
        if "Body" not in data and "ContentSid" not in data and "MediaUrl" not in data:
            return True

        if self._status_callback_url:
            data["StatusCallback"] = self._status_callback_url

        return await self._post(client, data)

    # ------------------------------------------------------------------
    # Private: HTTP call
    # ------------------------------------------------------------------

    async def _post(
        self,
        client: httpx.AsyncClient,
        data: dict,
    ) -> bool:
        """POST to the Twilio Messages API with retry and admin alerting."""
        from app.bot.services.admin_notifier import get_admin_notifier

        msg_type = "template" if "ContentSid" in data else (
            "photo" if "MediaUrl" in data else "text"
        )
        result = await twilio_post_with_retry(
            client=client,
            url=self._messages_url,
            data=data,
            auth=(self._account_sid, self._auth_token),
            timeout=SEND_TIMEOUT,
            admin_notifier=get_admin_notifier(),
            to_number=data.get("To", ""),
            message_type=msg_type,
        )
        return result.success
