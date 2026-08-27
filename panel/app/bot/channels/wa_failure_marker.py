"""WhatsApp permanent-failure DB marker.

Writes a ``wa_send_failed`` lead_event and (when a message ID is provided)
updates the corresponding ``messages`` row to ``status='failed'``.

Called from ``on_permanent_failure`` callbacks wired by callers that have
access to a DB session factory and a contact_id.  The helper is intentionally
decoupled from ``twilio_retry.py`` (no DB imports there) and from the IC
service (no channel logic there).

Usage
-----
    from app.bot.channels.wa_failure_marker import write_wa_send_failed_marker

    async def _on_fail(result: TwilioPostResult) -> None:
        await write_wa_send_failed_marker(
            result=result,
            contact_id=contact_id,
            message_id=message_id,          # None when no message row was created
            session_factory=session_factory,
        )
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from sqlalchemy import text as sa_text

from app.bot.channels.twilio_retry import TwilioPostResult

logger = logging.getLogger(__name__)


async def write_wa_send_failed_marker(
    result: TwilioPostResult,
    contact_id: int,
    message_id: int | None,
    session_factory: Callable[[], Any],
) -> None:
    """Write a ``wa_send_failed`` lead_event and optionally mark a message failed.

    Best-effort: all exceptions are swallowed and logged.  Never raises.

    Parameters
    ----------
    result:
        The :class:`~app.bot.channels.twilio_retry.TwilioPostResult` from the
        exhausted retry — provides ``to_number``, ``twilio_error_code``,
        ``attempts``, ``status_code``, and ``message_type`` for the metadata.
    contact_id:
        DB primary key of the contact who was to receive the message.
    message_id:
        DB primary key of the ``messages`` row that was created for this
        outbound attempt.  When ``None`` (Twilio never returned a SID so no
        row was inserted), only the ``lead_event`` INSERT is executed.
    session_factory:
        Async session factory callable (``async_session_factory`` or a test
        override).  Called with no arguments; the result is used as an async
        context manager.
    """
    try:
        meta = json.dumps(
            {
                "to_number": result.to_number or "",
                "twilio_error_code": result.twilio_error_code or "",
                "attempts": result.attempts,
                "status_code": result.status_code,
                "message_type": result.message_type or "",
            },
            ensure_ascii=False,
        )
        async with session_factory() as session:
            try:
                await session.execute(
                    sa_text(
                        "INSERT INTO lead_events "
                        "(contact_id, event_type, old_status, new_status,"
                        " triggered_by, metadata, created_at) "
                        "VALUES (:id, 'wa_send_failed', NULL, NULL,"
                        " 'bot', CAST(:meta AS jsonb), NOW())"
                    ),
                    {"id": contact_id, "meta": meta},
                )

                if message_id is not None:
                    await session.execute(
                        sa_text(
                            "UPDATE messages "
                            "SET status = 'failed',"
                            "    error_code = :ec,"
                            "    error_message = :em "
                            "WHERE id = :mid"
                        ),
                        {
                            "mid": message_id,
                            "ec": result.twilio_error_code or str(result.status_code or ""),
                            "em": (
                                f"retries_exhausted after {result.attempts} attempts"
                            ),
                        },
                    )

                await session.commit()
                logger.info(
                    "wa_failure_marker: wa_send_failed recorded "
                    "(contact_id=%s, message_id=%s, attempts=%d)",
                    contact_id,
                    message_id,
                    result.attempts,
                )
            except Exception:
                logger.warning(
                    "wa_failure_marker: DB write failed for contact_id=%s",
                    contact_id,
                    exc_info=True,
                )
    except Exception:
        logger.warning(
            "wa_failure_marker: unexpected error (contact_id=%s)",
            contact_id,
            exc_info=True,
        )
