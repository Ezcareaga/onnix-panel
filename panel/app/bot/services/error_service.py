"""BotErrorService — records errors and auto-disables the bot.

Inserts errors into the ``bot_errors`` table and checks if the error
rate exceeds a threshold (default: 3 errors in 15 minutes).  When
triggered, the bot is auto-disabled via ``bot_settings.bot_enabled``
and an admin notification is sent.

Plan 71-03: Task 3 (P1-05).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bot_error import BotError
from app.models.bot_setting import BotSetting

logger = logging.getLogger(__name__)

# Maximum length for error_message to avoid bloating the DB
_MAX_ERROR_MESSAGE_LENGTH = 2000


class BotErrorService:
    """Record bot errors and auto-disable when threshold exceeded.

    Parameters
    ----------
    workflow:
        Identifier for the workflow/pipeline that errored (e.g. "telegram", "whatsapp").
    """

    def __init__(self, workflow: str) -> None:
        self.workflow = workflow

    # ------------------------------------------------------------------
    # Record an error
    # ------------------------------------------------------------------

    async def record_error(
        self,
        session: AsyncSession,
        error_message: str,
        *,
        node: str | None = None,
        chat_id: str | None = None,
        execution_id: str | None = None,
    ) -> None:
        """Insert a row into ``bot_errors``. Never raises.

        Long error messages are truncated to avoid bloating the table.
        """
        try:
            truncated = (
                error_message[:_MAX_ERROR_MESSAGE_LENGTH]
                if error_message
                else None
            )
            error = BotError(
                workflow=self.workflow,
                node=node,
                error_message=truncated,
                execution_id=execution_id,
                chat_id=chat_id,
            )
            session.add(error)
            await session.commit()
            logger.info(
                "BotErrorService: recorded error for workflow=%s node=%s",
                self.workflow,
                node or "N/A",
            )
        except Exception:
            logger.warning(
                "BotErrorService: failed to record error (non-fatal)",
                exc_info=True,
            )
            try:
                await session.rollback()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Count recent errors
    # ------------------------------------------------------------------

    async def count_recent(
        self,
        session: AsyncSession,
        window_minutes: int = 15,
    ) -> int:
        """Count errors in the last *window_minutes* for this workflow."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
        stmt = (
            select(func.count())
            .select_from(BotError)
            .where(
                BotError.workflow == self.workflow,
                BotError.created_at >= cutoff,
            )
        )
        result = await session.execute(stmt)
        return result.scalar_one()

    # ------------------------------------------------------------------
    # Check threshold and auto-disable
    # ------------------------------------------------------------------

    async def check_and_disable(
        self,
        session: AsyncSession,
        *,
        threshold: int = 3,
        window_minutes: int = 15,
    ) -> bool:
        """Disable the bot if recent errors exceed *threshold*.

        Returns True if the bot was disabled, False otherwise.
        """
        try:
            count = await self.count_recent(session, window_minutes=window_minutes)
            if count < threshold:
                return False

            # Disable the bot
            stmt = (
                update(BotSetting)
                .where(BotSetting.key == "bot_enabled")
                .values(value="false")
            )
            await session.execute(stmt)
            await session.commit()

            reason = (
                f"{count} errores en {window_minutes}min "
                f"(workflow={self.workflow}, threshold={threshold})"
            )
            logger.critical(
                "BotErrorService: AUTO-DISABLED bot — %s", reason,
            )

            # El apagado automatico tiene que dejar rastro DONDE SE MIRA.
            #
            # El unico aviso era `notify_bot_disabled`, que sale por Telegram, y
            # Telegram esta caido (404) igual que el SMTP (535). Sin esto el bot
            # se apaga solo y en la tabla de settings queda `bot_enabled=false`
            # sin una palabra de por que: identico a que alguien lo hubiera
            # apagado a mano desde el panel.
            #
            # `bot_settings` es key/value y el formulario de settings renderiza
            # TODAS las filas no sensibles (`settings_service.get_all_settings`
            # -> `partials/settings_form.html`), asi que una fila nueva aparece
            # sola en el panel, con su `updated_at`. Cero UI, cero migracion.
            try:
                from app.repositories.bot_setting_repo import bot_setting_repo

                await bot_setting_repo.upsert(
                    session,
                    "bot_disabled_reason",
                    f"AUTO: {reason}. Ver tabla bot_errors.",
                    description=(
                        "Por que se apago el bot la ultima vez. Lo escribe el "
                        "apagado automatico por errores; queda como historial "
                        "hasta el proximo apagado."
                    ),
                )
                await session.commit()
            except Exception:
                logger.warning(
                    "BotErrorService: no se pudo dejar bot_disabled_reason "
                    "(non-fatal)",
                    exc_info=True,
                )

            # Best-effort admin notification
            try:
                from app.bot.services.admin_notifier import get_admin_notifier

                notifier = get_admin_notifier()
                await notifier.notify_bot_disabled(reason)
            except Exception:
                logger.warning(
                    "BotErrorService: failed to notify admin about disable (non-fatal)",
                    exc_info=True,
                )

            return True
        except Exception:
            logger.warning(
                "BotErrorService: check_and_disable failed (non-fatal)",
                exc_info=True,
            )
            try:
                await session.rollback()
            except Exception:
                pass
            return False
