"""MessageHandler — main entry point for the bot pipeline.

Receives webhook data, applies middleware (idempotency, rate limit),
calls the orchestrator, builds the channel payload, and sends via
the appropriate channel sender.

Plan 65-01: HAND-01..08 — all implicit via Orchestrator tool-use loop.
"""
from __future__ import annotations

import dataclasses
import logging
import os
from typing import TYPE_CHECKING

from app.bot.core.types import BotRequest, BotResponse, ChannelPayload, PayloadMessage
from app.bot.middleware.error_handler import safe_handle, SAFE_ERROR_TEXT
from app.bot.observability.outcome import RequestOutcome
from app.repositories.bot_setting_repo import BotSettingRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.bot.channels.base import BaseSender
    from app.bot.core.orchestrator import Orchestrator
    from app.bot.core.response_builder import ResponseBuilder
    from app.bot.middleware.rate_limiter import RateLimiter
    from app.bot.middleware.idempotency import IdempotencyGuard
    from app.bot.middleware.injection_guard import InjectionGuard

logger = logging.getLogger(__name__)


def bot_disabled_by_env() -> bool:
    """True cuando ``BOT_ENABLED`` esta explicitamente en off.

    Mismo contrato que ``wa_send_disabled`` y ``telegram_send_disabled``:
    ``false`` / ``0`` / vacio cuentan como apagado, el default es ``"true"``, y
    produccion —que no declara la variable— no cambia.
    """
    return os.getenv("BOT_ENABLED", "true").strip().lower() in ("false", "0", "")


class MessageHandler:
    """Pipeline: validate → dedup → rate limit → kill switches → orchestrate → send.

    Ties together all middleware, the orchestrator, the response
    builder, and the channel sender into a single ``handle()`` call.

    Parameters
    ----------
    orchestrator:
        The bot orchestrator that drives the AI loop.
    response_builder:
        Builds ChannelPayload from BotResponse.
    sender:
        Channel-specific sender (TelegramSender or WhatsAppSender).
    rate_limiter:
        Sliding-window rate limiter.
    idempotency_guard:
        Deduplication guard by external_id.
    """

    def __init__(
        self,
        orchestrator: "Orchestrator",
        response_builder: "ResponseBuilder",
        sender: "BaseSender",
        rate_limiter: "RateLimiter",
        idempotency_guard: "IdempotencyGuard",
        injection_guard: "InjectionGuard | None" = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._response_builder = response_builder
        self._sender = sender
        self._rate_limiter = rate_limiter
        self._idempotency_guard = idempotency_guard
        self._injection_guard = injection_guard

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def handle(
        self,
        request: BotRequest,
        session: "AsyncSession",
    ) -> bool:
        """Process an inbound message through the full pipeline.

        Returns ``True`` if the message was handled (sent or silenced),
        ``False`` if sending failed. Wrapped in error handler so it
        never raises.

        Steps:
        1. Idempotency check (skip duplicates)
        2. Rate limit check
        3. Bot enabled check (global kill switch)
        4. WhatsApp mode check (manual mode silences WA)
        5. Injection guard (sanitize input)
        6. Orchestrate (AI loop)
        7. Build channel payload
        8. Send via channel
        """
        # Wrap the entire pipeline in safe_handle
        outcome = await safe_handle(
            self._handle_inner(request, session)
        )

        if outcome is None:
            # safe_handle swallowed an unhandled error (shouldn't happen with current impl,
            # but guard defensively).
            return True

        if outcome.status == "error":
            # Error occurred — send plain Body text (no template/ContentSid)
            payload = ChannelPayload(
                messages=[PayloadMessage(text=SAFE_ERROR_TEXT)],
                channel=request.platform,
            )
            await self._sender.send(payload, request.chat_id)
            return False

        if outcome.status == "send_failed":
            # Primary send already failed — do NOT retry (sender is broken).
            # The failure was logged and admin notified in _handle_inner.
            return False

        return True

    # ------------------------------------------------------------------
    # Private: inner pipeline
    # ------------------------------------------------------------------

    async def _handle_inner(
        self,
        request: BotRequest,
        session: "AsyncSession",
    ) -> RequestOutcome:
        """The actual pipeline logic, separated for error wrapping.

        Returns a RequestOutcome describing what happened.  safe_handle
        catches unhandled exceptions and fills in status="error".
        """

        # Step 0: Aislamiento de ambiente — BOT_ENABLED
        #
        # NO es el mismo switch que el `bot_enabled` de la DB del Step 3, y la
        # diferencia es la que justifica que existan los dos:
        #
        #   bot_settings.bot_enabled (DB)  producto. Lo prende y apaga
        #                                  la administradora, y CONTESTA avisando que
        #                                  el asistente no esta disponible.
        #   BOT_ENABLED (entorno)          aislamiento. Lo pone el compose de
        #                                  staging, y NO contesta nada.
        #
        # Que no conteste es el punto. Staging hereda el `.env` de produccion:
        # cualquier respuesta suya sale con las credenciales reales a un
        # telefono real. El Step 3 manda `bot_off_message`; este no manda nada.
        #
        # Hasta el 2026-08-23 esta variable estaba en `docker-compose.dev.yml` y
        # en el `CLAUDE.md` como uno de los seis guards obligatorios, y no la
        # leia ni una linea de codigo.
        if bot_disabled_by_env():
            logger.info(
                'Bot skipped — {"motivo": "BOT_ENABLED=off", "platform": "%s"}',
                request.platform,
            )
            return RequestOutcome(status="skipped", skip_reason="bot_disabled_env")

        # Step 1: Idempotency — skip already-processed messages
        if self._idempotency_guard.is_duplicate(request.external_id):
            logger.info(
                "Skipping duplicate message: external_id=%s",
                request.external_id,
            )
            return RequestOutcome(status="skipped", skip_reason="duplicate")

        # Step 2: Rate limit — check before expensive AI call
        if self._rate_limiter.is_rate_limited(request.user_id):
            logger.warning(
                "Rate limited: user_id=%s platform=%s",
                request.user_id, request.platform,
            )
            # Send a brief rate-limit message — plain Body, no template
            payload = ChannelPayload(
                messages=[PayloadMessage(
                    text="Estas enviando mensajes muy rapido. Espera un momento por favor.",
                )],
                channel=request.platform,
            )
            await self._sender.send(payload, request.chat_id)
            return RequestOutcome(status="skipped", skip_reason="rate_limited")

        # Step 3: Global kill switch — bot_enabled
        bot_enabled_raw = await BotSettingRepository.get_value(session, "bot_enabled")
        if bot_enabled_raw != "true":
            logger.info(
                "Bot disabled globally — sending bot_off_message to user_id=%s",
                request.user_id,
            )
            bot_off_message = await BotSettingRepository.get_value(
                session, "bot_off_message",
            )
            if not bot_off_message:
                bot_off_message = "En este momento nuestro asistente no esta disponible."
            payload = ChannelPayload(
                messages=[PayloadMessage(text=bot_off_message)],
                channel=request.platform,
            )
            await self._sender.send(payload, request.chat_id)
            return RequestOutcome(status="skipped", skip_reason="bot_disabled")

        # Step 4: WhatsApp manual mode — skip silently
        if request.platform == "whatsapp":
            wa_mode = await BotSettingRepository.get_value(session, "whatsapp_mode")
            if wa_mode == "manual":
                logger.info(
                    "WhatsApp manual mode — skipping AI for user_id=%s",
                    request.user_id,
                )
                return RequestOutcome(status="skipped", skip_reason="manual_mode")

        # Step 5: Sanitize input (injection guard)
        if self._injection_guard is not None and request.text:
            sanitized = self._injection_guard.sanitize(request.text)
            request = dataclasses.replace(request, text=sanitized.text)
            if sanitized.is_suspicious:
                self._injection_guard.record_suspicious(request.user_id)

        # Step 6: Orchestrate — call AI pipeline
        bot_response = await self._orchestrator.handle_message(request, session)

        # Step 6b: If None, bot should stay silent (cooldown, bot_inactive, etc.)
        if bot_response is None:
            logger.info(
                "Bot silent for user_id=%s (cooldown or inactive)",
                request.user_id,
            )
            return RequestOutcome(status="ok")

        # Extract observability metadata from bot_response (set by orchestrator)
        _meta = bot_response.metadata or {}
        outcome = RequestOutcome(
            status="ok",
            intent=bot_response.intent,
            llm_provider=_meta.get("llm_provider"),
            ai_model=bot_response.ai_model or None,
            tool_iterations=_meta.get("tool_iterations"),
            tokens_in=bot_response.ai_tokens_in or None,
            tokens_out=bot_response.ai_tokens_out or None,
            fallback_used=bool(_meta.get("fallback_used", False)),
            contact_id=_meta.get("contact_id"),
            ai_latency_ms=_meta.get("ai_latency_ms"),
        )

        # Step 7: Build channel payload
        payload = self._response_builder.build_payload(
            text=bot_response.text,
            intent=bot_response.intent,
            properties=bot_response.properties,
            channel=request.platform,
            has_pending=bool(bot_response.pending_ids),
            metadata=bot_response.metadata,
        )

        # Step 7b: Resolve WA template keys → real Twilio ContentSids
        if request.platform == "whatsapp":
            for msg in payload.messages:
                if msg.template_id and not msg.template_id.startswith("HX"):
                    real_sid = await BotSettingRepository.get_value(
                        session, msg.template_id,
                    )
                    if real_sid:
                        msg.template_id = real_sid
                    else:
                        logger.warning(
                            "No ContentSid found for key=%s, sending as Body",
                            msg.template_id,
                        )
                        msg.template_id = None

        # Step 8: Send via channel
        sent = await self._sender.send(payload, request.chat_id)
        if not sent:
            logger.error(
                "Failed to send response for user_id=%s chat_id=%s",
                request.user_id, request.chat_id,
            )
            # Best-effort admin notification for WhatsApp (Twilio) failures
            if request.platform == "whatsapp":
                try:
                    from app.bot.services.admin_notifier import get_admin_notifier
                    _notifier = get_admin_notifier()
                    await _notifier.notify_twilio_error(
                        error_code="SEND_FAIL",
                        error_message=f"No se pudo enviar respuesta a {request.chat_id}",
                        to_number=request.chat_id,
                    )
                except Exception:
                    pass  # notification is best-effort
            outcome.status = "send_failed"

        return outcome
