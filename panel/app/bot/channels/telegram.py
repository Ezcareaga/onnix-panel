"""Telegram channel sender — delivers messages via Bot API.

Uses httpx to call api.telegram.org endpoints: sendMessage,
sendPhoto, and sendMediaGroup. All errors are caught and logged;
``send()`` returns ``False`` on any failure.

Plan 63-01: CHAN-03 Telegram sender.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import httpx

from app.bot.channels.base import BaseSender

if TYPE_CHECKING:
    from app.bot.core.types import ChannelPayload, PayloadMessage

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
SEND_TIMEOUT = 10.0  # seconds per request


def telegram_send_disabled() -> bool:
    """True cuando ``TELEGRAM_NOTIFICATIONS_ENABLED`` esta explicitamente en off.

    Mismo contrato que ``wa_send_disabled`` de ``twilio_retry``, y por el mismo
    motivo: staging hereda el ``.env`` de produccion, asi que sale con el token
    real de Telegram. ``false`` / ``0`` / vacio cuentan como apagado; el default
    es ``"true"``, o sea que produccion no cambia.

    Hasta el 2026-08-23 esta variable **no la leia nadie**: estaba en
    ``docker-compose.dev.yml`` y en el ``CLAUDE.md`` como uno de los seis guards
    obligatorios de aislamiento, y era la unica de las seis sin implementacion.
    """
    return os.getenv("TELEGRAM_NOTIFICATIONS_ENABLED", "true").strip().lower() in (
        "false", "0", "",
    )


class TelegramSender(BaseSender):
    """Sends messages to Telegram via the Bot API.

    Parameters
    ----------
    bot_token:
        Telegram bot token (``123456:ABC-DEF...``).
    client:
        Optional pre-configured ``httpx.AsyncClient`` (for testing).
    """

    def __init__(
        self,
        bot_token: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = bot_token
        self._client = client
        self._base_url = f"{API_BASE}/bot{bot_token}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send(self, payload: "ChannelPayload", chat_id: str) -> bool:
        """Deliver every message in *payload* to *chat_id*.

        Returns ``True`` only if **all** messages were sent successfully.
        """
        if not payload.messages:
            return True

        client = self._client or httpx.AsyncClient(timeout=SEND_TIMEOUT)
        own_client = self._client is None
        try:
            for msg in payload.messages:
                ok = await self._send_one(client, chat_id, msg)
                if not ok:
                    return False
            return True
        except Exception:
            logger.exception("TelegramSender.send failed for chat_id=%s", chat_id)
            return False
        finally:
            if own_client:
                await client.aclose()

    # ------------------------------------------------------------------
    # Private: dispatch per message type
    # ------------------------------------------------------------------

    async def _send_one(
        self,
        client: httpx.AsyncClient,
        chat_id: str,
        msg: "PayloadMessage",
    ) -> bool:
        """Send a single PayloadMessage, choosing the right API method."""
        if msg.photo_url:
            return await self._send_photo(client, chat_id, msg)
        return await self._send_text(client, chat_id, msg)

    # ------------------------------------------------------------------
    # Private: sendMessage
    # ------------------------------------------------------------------

    async def _send_text(
        self,
        client: httpx.AsyncClient,
        chat_id: str,
        msg: "PayloadMessage",
    ) -> bool:
        """Send a text message with optional inline keyboard."""
        data: dict[str, Any] = {
            "chat_id": chat_id,
            "text": msg.text or "(sin texto)",
            "parse_mode": "HTML",
        }
        keyboard = self._build_keyboard(msg.buttons)
        if keyboard:
            data["reply_markup"] = keyboard

        return await self._post("sendMessage", client, data)

    # ------------------------------------------------------------------
    # Private: sendPhoto
    # ------------------------------------------------------------------

    async def _send_photo(
        self,
        client: httpx.AsyncClient,
        chat_id: str,
        msg: "PayloadMessage",
    ) -> bool:
        """Send a photo with caption and optional inline keyboard."""
        data: dict[str, Any] = {
            "chat_id": chat_id,
            "photo": msg.photo_url,
            "caption": msg.text or "",
            "parse_mode": "HTML",
        }
        keyboard = self._build_keyboard(msg.buttons)
        if keyboard:
            data["reply_markup"] = keyboard

        return await self._post("sendPhoto", client, data)

    # ------------------------------------------------------------------
    # Private: HTTP call
    # ------------------------------------------------------------------

    async def _post(
        self,
        method: str,
        client: httpx.AsyncClient,
        data: dict,
    ) -> bool:
        """POST to the Telegram Bot API and check the result."""
        # El guard va ACA y no en `send()`: este es el unico lugar del modulo
        # donde se abre el socket, y por el mismo motivo que WA_SEND_ENABLED se
        # consulta en `twilio_post_with_retry` — siete caminos llegaban a Twilio
        # y seis no tenian guard.
        if telegram_send_disabled():
            logger.info(
                'Telegram send skipped — {"motivo": "TELEGRAM_NOTIFICATIONS_ENABLED=off", "metodo": "%s"}',
                method,
            )
            return False
        # Un chat_id vacio es la otra forma del mismo problema: staging lo deja
        # en blanco a proposito, y sin esto el POST sale igual —con el token
        # real— para que Telegram conteste el error. Es una llamada saliente a
        # una API externa desde staging, que es lo que la regla 10 prohibe.
        # `or ""` y no un default en el `get`: la clave EXISTE con valor
        # None cuando el chat_id no se configuro, y `str(None)` es "None",
        # que es truthy. El test lo agarro.
        if not str(data.get("chat_id") or "").strip():
            logger.info(
                'Telegram send skipped — {"motivo": "chat_id vacio", "metodo": "%s"}',
                method,
            )
            return False
        url = f"{self._base_url}/{method}"
        msg_type = "photo" if method == "sendPhoto" else "text"
        has_buttons = "reply_markup" in data
        try:
            # reply_markup must be JSON-encoded string for Telegram
            if "reply_markup" in data:
                import json
                data["reply_markup"] = json.dumps(data["reply_markup"])

            resp = await client.post(url, data=data)
            if resp.status_code == 200:
                body = resp.json()
                if body.get("ok"):
                    result = body.get("result", {})
                    logger.info(
                        'Message sent — {"channel": "telegram", "chat_id": "%s", "type": "%s", "buttons": %s, "message_id": %s}',
                        data.get("chat_id", ""), msg_type,
                        "true" if has_buttons else "false",
                        result.get("message_id", "null"),
                    )
                    return True
                logger.warning(
                    'Send failed — {"channel": "telegram", "chat_id": "%s", "type": "%s", "error": "%s"}',
                    data.get("chat_id", ""), msg_type, body.get("description", ""),
                )
                return False
            logger.warning(
                'Send failed — {"channel": "telegram", "chat_id": "%s", "type": "%s", "http_status": %d, "error": "%.200s"}',
                data.get("chat_id", ""), msg_type, resp.status_code, resp.text,
            )
            return False
        except httpx.TimeoutException:
            logger.warning(
                'Send timeout — {"channel": "telegram", "chat_id": "%s", "type": "%s"}',
                data.get("chat_id", ""), msg_type,
            )
            return False
        except Exception:
            logger.exception(
                'Send error — {"channel": "telegram", "chat_id": "%s", "type": "%s"}',
                data.get("chat_id", ""), msg_type,
            )
            return False

    # ------------------------------------------------------------------
    # Private: inline keyboard builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_keyboard(buttons: list[dict]) -> dict | None:
        """Build a Telegram InlineKeyboardMarkup from button dicts.

        Each button dict has ``text`` and ``callback_data`` keys.
        Returns None if no buttons.
        """
        if not buttons:
            return None
        rows = []
        for btn in buttons:
            rows.append([{
                "text": btn.get("text", ""),
                "callback_data": btn.get("callback_data", ""),
            }])
        return {"inline_keyboard": rows}
