"""Daily report — sends a summary email at 08:00 PYT.

Queries leads, messages, errors, InfoCasas contacts, and property
counts for the last 24 hours and sends an HTML email via Gmail SMTP.
Also sends a Telegram notification with the summary.
"""
from __future__ import annotations

import logging
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy import func, select, text

from app.bot.config import bot_settings
from app.bot.services.admin_notifier import AdminNotifier
from app.database import async_session_factory
from app.tz import PYT
from app.models.bot_error import BotError
from app.models.contact import Contact
from app.models.message import Message
from app.models.property import Property

logger = logging.getLogger(__name__)

# El huso sale de app.tz, que ya lo tiene como ZoneInfo. Acá vivía un
# `timezone(timedelta(hours=-4))` escrito a mano: mal por dos motivos. Uno,
# Paraguay abolió el horario de verano y quedó en UTC-3, así que el reporte
# rotulaba con la fecha corrida una hora. Dos, un offset fijo no sobrevive a
# un cambio de reglas; el nombre de la zona sí.

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


class DailyReportGenerator:
    """Gathers metrics and sends the daily summary email + Telegram.

    Parameters
    ----------
    smtp_email:
        Gmail address to send from.
    smtp_password:
        Gmail App Password.
    report_to:
        Recipient email address.
    session_factory:
        Optional async session factory override (for testing).
    notifier:
        Optional AdminNotifier override (for testing).
    """

    def __init__(
        self,
        smtp_email: str,
        smtp_password: str,
        report_to: str,
        *,
        session_factory=None,
        notifier: AdminNotifier | None = None,
    ) -> None:
        self._smtp_email = smtp_email
        self._smtp_password = smtp_password
        self._report_to = report_to
        self._session_factory = session_factory or async_session_factory
        self._notifier = notifier or AdminNotifier(
            chat_id=bot_settings.TELEGRAM_EZ_CHAT_ID,
            bot_token=bot_settings.TELEGRAM_BOT_TOKEN,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> dict:
        """Generate and send the daily report. Returns metrics dict."""
        start = time.monotonic()
        metrics = await self._gather_metrics()

        # Send email (sync SMTP — fast enough for a daily task)
        email_sent = self._send_email(metrics)

        # Send Telegram summary
        tg_sent = await self._send_telegram(metrics)

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            'Job executed — {"task": "daily_report", "duration_ms": %.0f, '
            '"email_sent": %s, "tg_sent": %s}',
            elapsed_ms, email_sent, tg_sent,
        )
        return {**metrics, "email_sent": email_sent, "tg_sent": tg_sent}

    # ------------------------------------------------------------------
    # Private: gather metrics
    # ------------------------------------------------------------------

    async def _gather_metrics(self) -> dict:
        """Query the DB for last-24h metrics."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

        async with self._session_factory() as session:
            # New leads (contacts created in last 24h with source in bot sources)
            leads_result = await session.execute(
                select(func.count()).select_from(Contact).where(
                    Contact.created_at >= cutoff,
                    Contact.source.in_(("whatsapp", "telegram", "infocasas")),
                )
            )
            leads_count = leads_result.scalar_one()

            # Lead details (name, phone, source)
            leads_detail_result = await session.execute(
                select(Contact.name, Contact.phone, Contact.source).where(
                    Contact.created_at >= cutoff,
                    Contact.source.in_(("whatsapp", "telegram", "infocasas")),
                ).order_by(Contact.created_at.desc()).limit(20)
            )
            leads_list = [
                {"name": r.name or "Sin nombre", "phone": r.phone or "?", "source": r.source}
                for r in leads_detail_result.fetchall()
            ]

            # Messages processed
            messages_result = await session.execute(
                select(func.count()).select_from(Message).where(
                    Message.created_at >= cutoff,
                )
            )
            messages_count = messages_result.scalar_one()

            # Errors
            errors_result = await session.execute(
                select(func.count()).select_from(BotError).where(
                    BotError.created_at >= cutoff,
                )
            )
            errors_count = errors_result.scalar_one()

            # Error summary (top 5 by workflow)
            error_summary_result = await session.execute(
                select(
                    BotError.workflow,
                    func.count().label("cnt"),
                ).where(
                    BotError.created_at >= cutoff,
                ).group_by(BotError.workflow).order_by(
                    func.count().desc()
                ).limit(5)
            )
            error_summary = [
                {"workflow": r.workflow, "count": r.cnt}
                for r in error_summary_result.fetchall()
            ]

            # InfoCasas leads (source='infocasas')
            ic_result = await session.execute(
                select(func.count()).select_from(Contact).where(
                    Contact.created_at >= cutoff,
                    Contact.source == "infocasas",
                )
            )
            infocasas_count = ic_result.scalar_one()

            # Bot uptime — count heartbeat errors in 24h
            hb_errors = await session.execute(
                select(func.count()).select_from(BotError).where(
                    BotError.created_at >= cutoff,
                    BotError.workflow == "heartbeat",
                )
            )
            heartbeat_fails = hb_errors.scalar_one()

            # Active properties
            props_result = await session.execute(
                select(func.count()).select_from(Property).where(
                    Property.is_active == True,  # noqa: E712
                )
            )
            active_properties = props_result.scalar_one()

        return {
            "date": datetime.now(PYT).strftime("%Y-%m-%d"),
            "leads_count": leads_count,
            "leads_list": leads_list,
            "messages_count": messages_count,
            "errors_count": errors_count,
            "error_summary": error_summary,
            "infocasas_count": infocasas_count,
            "heartbeat_fails": heartbeat_fails,
            "heartbeat_status": "OK" if heartbeat_fails == 0 else f"FAIL ({heartbeat_fails})",
            "active_properties": active_properties,
        }

    # ------------------------------------------------------------------
    # Private: send email
    # ------------------------------------------------------------------

    def _send_email(self, metrics: dict) -> bool:
        """Send the HTML email via Gmail SMTP. Returns True on success."""
        if not self._smtp_email or not self._smtp_password or not self._report_to:
            logger.info("Daily report: SMTP not configured, skipping email")
            return False

        html = self._build_html(metrics)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Onnix — Reporte Diario {metrics['date']}"
        msg["From"] = self._smtp_email
        msg["To"] = self._report_to
        msg.attach(MIMEText(html, "html"))

        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(self._smtp_email, self._smtp_password)
                server.sendmail(self._smtp_email, [self._report_to], msg.as_string())
            logger.info("Daily report email sent to %s", self._report_to)
            return True
        except Exception:
            logger.warning("Daily report: email send failed", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Private: send Telegram summary
    # ------------------------------------------------------------------

    async def _send_telegram(self, metrics: dict) -> bool:
        """Send a concise Telegram summary."""
        lines = [
            f"<b>Reporte Diario — {metrics['date']}</b>",
            f"Leads nuevos: {metrics['leads_count']}",
            f"  InfoCasas: {metrics['infocasas_count']}",
            f"Mensajes: {metrics['messages_count']}",
            f"Errores: {metrics['errors_count']}",
            f"Heartbeat: {metrics['heartbeat_status']}",
            f"Props activas: {metrics['active_properties']}",
        ]
        return await self._notifier.notify("\n".join(lines))

    # ------------------------------------------------------------------
    # Private: build HTML
    # ------------------------------------------------------------------

    def _build_html(self, m: dict) -> str:
        """Build the HTML email body."""
        leads_rows = ""
        for lead in m["leads_list"]:
            leads_rows += (
                f"<tr><td>{lead['name']}</td>"
                f"<td>{lead['phone']}</td>"
                f"<td>{lead['source']}</td></tr>"
            )

        error_rows = ""
        for err in m["error_summary"]:
            error_rows += (
                f"<tr><td>{err['workflow']}</td>"
                f"<td>{err['count']}</td></tr>"
            )

        return f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #B8860B;">Onnix SA — Reporte Diario</h2>
            <p style="color: #666;">{m['date']}</p>

            <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
                <tr style="background: #f5f5f5;">
                    <td style="padding: 8px; border: 1px solid #ddd;"><b>Leads nuevos</b></td>
                    <td style="padding: 8px; border: 1px solid #ddd;">{m['leads_count']}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; border: 1px solid #ddd;">InfoCasas leads</td>
                    <td style="padding: 8px; border: 1px solid #ddd;">{m['infocasas_count']}</td>
                </tr>
                <tr style="background: #f5f5f5;">
                    <td style="padding: 8px; border: 1px solid #ddd;"><b>Mensajes procesados</b></td>
                    <td style="padding: 8px; border: 1px solid #ddd;">{m['messages_count']}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; border: 1px solid #ddd;"><b>Errores</b></td>
                    <td style="padding: 8px; border: 1px solid #ddd;">{m['errors_count']}</td>
                </tr>
                <tr style="background: #f5f5f5;">
                    <td style="padding: 8px; border: 1px solid #ddd;">Heartbeat</td>
                    <td style="padding: 8px; border: 1px solid #ddd;">{m['heartbeat_status']}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; border: 1px solid #ddd;"><b>Props activas</b></td>
                    <td style="padding: 8px; border: 1px solid #ddd;">{m['active_properties']}</td>
                </tr>
            </table>

            {"<h3>Leads (ultimas 24h)</h3><table style='width:100%;border-collapse:collapse;'><tr style='background:#B8860B;color:white;'><th style='padding:6px;text-align:left;'>Nombre</th><th style='padding:6px;text-align:left;'>Tel</th><th style='padding:6px;text-align:left;'>Fuente</th></tr>" + leads_rows + "</table>" if leads_rows else ""}

            {"<h3>Errores por workflow</h3><table style='width:100%;border-collapse:collapse;'><tr style='background:#cc0000;color:white;'><th style='padding:6px;text-align:left;'>Workflow</th><th style='padding:6px;text-align:left;'>Count</th></tr>" + error_rows + "</table>" if error_rows else ""}

            <p style="color: #999; font-size: 12px; margin-top: 24px;">
                Generado automaticamente por Onnix Bot.
            </p>
        </body>
        </html>
        """


# ------------------------------------------------------------------
# Module-level factory
# ------------------------------------------------------------------


async def run_daily_report() -> dict:
    """Factory function invoked by the scheduler."""
    generator = DailyReportGenerator(
        smtp_email=bot_settings.SMTP_EMAIL,
        smtp_password=bot_settings.SMTP_PASSWORD,
        report_to=bot_settings.REPORT_EMAIL_TO,
    )
    return await generator.run()
