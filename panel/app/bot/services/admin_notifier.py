"""AdminNotifier — sends Telegram alerts to admin chat.

Centralises all admin notification logic that was previously duplicated
across cold_lead_check and heartbeat tasks.  Every method is best-effort:
failures are logged but never raised.

Plan 71-03: Task 2 (P1-06).
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx

if TYPE_CHECKING:
    from app.schemas.metrics import BotHealthSnapshot

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
NOTIFY_TIMEOUT = 10.0  # seconds


class AdminNotifier:
    """Send Telegram notifications to the admin chat.

    Parameters
    ----------
    chat_id:
        Telegram chat ID for the admin.
    bot_token:
        Telegram Bot API token.
    """

    def __init__(self, chat_id: str, bot_token: str) -> None:
        self.chat_id = chat_id
        self.bot_token = bot_token

    # ------------------------------------------------------------------
    # Core: send message
    # ------------------------------------------------------------------

    async def notify(self, message: str, *, parse_mode: str = "HTML") -> bool:
        """Send a Telegram message to the admin chat.

        Returns True on success, False on any failure.
        Never raises — all errors are swallowed and logged.
        """
        if not self.chat_id or not self.bot_token:
            logger.debug("AdminNotifier: no chat_id or bot_token configured, skipping")
            return False

        url = f"{TELEGRAM_API_BASE}/bot{self.bot_token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=NOTIFY_TIMEOUT) as client:
                resp = await client.post(
                    url,
                    data={
                        "chat_id": self.chat_id,
                        "text": message,
                        "parse_mode": parse_mode,
                    },
                )
                if resp.status_code != 200:
                    logger.warning(
                        "AdminNotifier: Telegram API returned HTTP %d",
                        resp.status_code,
                    )
                    return False
                return True
        except Exception:
            logger.warning("AdminNotifier: send failed (non-fatal)", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    async def notify_error(
        self,
        workflow: str,
        error_message: str,
        *,
        node: str | None = None,
        chat_id: str | None = None,
    ) -> bool:
        """Notify about a bot processing error."""
        parts = [
            f"<b>Bot Error</b>",
            f"Workflow: {workflow}",
        ]
        if node:
            parts.append(f"Node: {node}")
        if chat_id:
            parts.append(f"Chat: {chat_id}")
        # Truncate long error messages for Telegram (4096 char limit)
        msg_preview = error_message[:500] if error_message else "unknown"
        parts.append(f"Error: {msg_preview}")
        return await self.notify("\n".join(parts))

    async def notify_bot_disabled(self, reason: str) -> bool:
        """Notify that the bot has been auto-disabled."""
        text = (
            f"<b>ALERTA: Bot Auto-Desactivado</b>\n"
            f"Motivo: {reason}\n"
            f"El bot ha sido desactivado automaticamente. "
            f"Reactivar manualmente desde el panel."
        )
        return await self.notify(text)

    async def notify_cold_leads(
        self,
        updated: int,
        contact_ids: list[int],
    ) -> bool:
        """Notify about cold leads transitioned to no_response."""
        text = (
            f"Cold Lead Check\n"
            f"Contactos marcados no_response: {updated}\n"
            f"IDs: {', '.join(str(cid) for cid in contact_ids[:20])}"
        )
        if len(contact_ids) > 20:
            text += f"\n... y {len(contact_ids) - 20} mas"
        return await self.notify(text)

    async def notify_heartbeat_failure(self, timestamp: str) -> bool:
        """Notify about a heartbeat DB health check failure."""
        text = (
            f"ALERTA: DB Health Check Failed\n"
            f"Timestamp: {timestamp}\n"
            f"SELECT 1 failed — base de datos no responde."
        )
        return await self.notify(text)

    async def notify_heartbeat_snapshot(self, snapshot: "BotHealthSnapshot") -> bool:
        """Send a compact Telegram HTML summary of the bot health snapshot.

        Returns True on success, False on any failure (silent — never raises).
        Respects staging guardrail: if chat_id or bot_token is empty, returns False.
        """
        if not self.chat_id or not self.bot_token:
            return False
        costs = snapshot.costs
        msg = (
            f"<b>Heartbeat — {snapshot.generated_at.strftime('%Y-%m-%d %H:%M UTC')}</b>\n"
            f"Mensajes 24h: {snapshot.message_volume.total} "
            f"(in {snapshot.message_volume.inbound}, "
            f"bot {snapshot.message_volume.bot_out}, "
            f"agent {snapshot.message_volume.agent_out})\n"
            f"Latencia p95: {snapshot.latency.p95_ms}ms (avg {snapshot.latency.avg_ms}ms, n={snapshot.latency.n})\n"
            f"Fallback Gemini: {snapshot.provider_mix.pct_fallback:.1f}% "
            f"(Claude {snapshot.provider_mix.claude}, Gemini {snapshot.provider_mix.gemini})\n"
            f"Tool iters promedio: {snapshot.tool_iterations.avg:.1f} (max {snapshot.tool_iterations.max})\n"
            f"Conv. trabadas: {snapshot.stuck_conversations.count}\n"
            f"Errores 24h: {snapshot.errors.total}\n"
            f"Costo hoy: ${costs.total_today_usd:.2f} "
            f"(IA ${costs.ai_today.total_usd:.2f} + Twilio ${costs.twilio_today.total_usd:.2f})\n"
            f"Costo mes: ${costs.total_month_usd:.2f}"
        )
        return await self.notify(msg)

    async def notify_new_lead(
        self,
        name: str,
        phone: str,
        *,
        property_id: int | None = None,
        source: str = "",
        motivo: str = "",
    ) -> bool:
        """Notify about a new lead registered by the bot."""
        parts = [
            "<b>Nuevo Lead</b>",
            f"Nombre: {name or 'Sin nombre'}",
            f"Tel: {phone or 'Sin tel'}",
        ]
        if source:
            parts.append(f"Fuente: {source}")
        if property_id:
            parts.append(f"Propiedad: #{property_id}")
        if motivo:
            parts.append(f"Motivo: {motivo}")
        return await self.notify("\n".join(parts))

    async def notify_circuit_breaker_open(self, failures: int) -> bool:
        """Notify that the circuit breaker has opened (Claude failing)."""
        text = (
            f"<b>ALERTA: Circuit Breaker ABIERTO</b>\n"
            f"Claude falló {failures} veces consecutivas.\n"
            f"Usando Gemini como fallback automático."
        )
        return await self.notify(text)

    async def notify_login_locked(
        self,
        email: str,
        ip: str | None,
        user_agent: str | None,
        fail_count: int,
        lock_until_iso: str,
    ) -> bool:
        """Send a Telegram alert for a panel login lockout (M6.1, Phase 111-02).

        Fired by lockout_service.maybe_trigger_lockout_alert when an email
        crosses the failure threshold within the 15 min window. Best-effort:
        returns False on any error, never raises — the lockout itself is
        already persisted in auth_audit, so a Telegram failure does not
        compromise security.
        """
        ua_trimmed = (user_agent or "unknown")[:200]
        msg = (
            f"<b>Login lockout</b>\n\n"
            f"<b>Email:</b> {html.escape(email)}\n"
            f"<b>IP:</b> {html.escape(ip or 'unknown')}\n"
            f"<b>User-Agent:</b> {html.escape(ua_trimmed)}\n"
            f"<b>Fallos en ventana 15min:</b> {fail_count}\n"
            f"<b>Locked hasta:</b> ~{html.escape(lock_until_iso)}\n"
            f"<b>Timestamp:</b> {datetime.now(timezone.utc).isoformat()}\n\n"
            f"Usá <code>/admin/auth-audit?email={quote(email)}</code> "
            f"para ver el historial."
        )
        return await self.notify(msg, parse_mode="HTML")

    async def notify_twilio_error(
        self,
        error_code: str,
        error_message: str,
        *,
        to_number: str = "",
    ) -> bool:
        """Notify about a Twilio send failure."""
        parts = [
            "<b>Twilio Error</b>",
            f"Código: {error_code}",
        ]
        if to_number:
            parts.append(f"Destino: {to_number}")
        msg_preview = error_message[:300] if error_message else "unknown"
        parts.append(f"Error: {msg_preview}")
        return await self.notify("\n".join(parts))


# ------------------------------------------------------------------
# Factory
# ------------------------------------------------------------------


def get_admin_notifier() -> AdminNotifier:
    """Create an AdminNotifier from bot_settings config."""
    from app.bot.config import bot_settings

    return AdminNotifier(
        chat_id=bot_settings.TELEGRAM_EZ_CHAT_ID,
        bot_token=bot_settings.TELEGRAM_BOT_TOKEN,
    )
