"""BotErrorService — deja los errores del webhook en ``bot_errors``.

Tenia un segundo trabajo: pasado un umbral de errores apagaba el bot solo
—`bot_settings.bot_enabled = false`— y avisaba por Telegram. Sin bot no hay
nada que apagar y sin Telegram no hay a donde avisar, asi que ese camino se
fue el 2026-09-09. Queda el registro, que es lo que mira el reporte diario.

Plan 71-03: Task 3 (P1-05).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bot_error import BotError

logger = logging.getLogger(__name__)

# Maximum length for error_message to avoid bloating the DB
_MAX_ERROR_MESSAGE_LENGTH = 2000


class BotErrorService:
    """Record bot errors and auto-disable when threshold exceeded.

    Parameters
    ----------
    workflow:
        Identifier for the workflow/pipeline that errored (e.g. "whatsapp").
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
