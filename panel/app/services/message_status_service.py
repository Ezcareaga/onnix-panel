"""Service for processing Twilio delivery status callbacks.

Handles the message status progression logic: applies valid forward
transitions (sent→delivered→read), always applies error states
(failed/undelivered), and ignores out-of-order regressions.

After a successful update, publishes SSE events so the panel thread
refreshes the delivery checkmark in real time.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.message_repo import message_repo
from app.services.event_bus import event_bus

if TYPE_CHECKING:
    from app.models.message import Message

logger = logging.getLogger(__name__)

# Statuses we will ever persist from a callback.
_PERSIST_STATUSES: frozenset[str] = frozenset(
    {"sent", "delivered", "read", "failed", "undelivered"}
)

# Error statuses: always written, regardless of current status.
_ERROR_STATUSES: frozenset[str] = frozenset({"failed", "undelivered"})

# Ordinal rank for forward-progression guard.
# Higher rank = further along the happy path.
_STATUS_RANK: dict[str, int] = {
    "queued": 0,
    "sent": 1,
    "delivered": 2,
    "read": 3,
    # Error states are handled separately — not in the rank chain.
}


def _is_progression(current: str | None, new: str) -> bool:
    """Return True iff transitioning from current to new is a valid forward step.

    Rules:
    - Error states (failed/undelivered) are always valid (handled by caller).
    - None / unknown current status: treat as rank 0 (any forward move valid).
    - New rank must be strictly greater than current rank.
    """
    current_rank = _STATUS_RANK.get(current or "queued", 0)
    new_rank = _STATUS_RANK.get(new, -1)
    if new_rank < 0:
        # Unknown new status in rank table — not a happy-path state.
        return False
    return new_rank > current_rank


class MessageStatusService:
    """Business logic for Twilio delivery status callbacks."""

    @staticmethod
    async def handle_status_callback(
        db: AsyncSession,
        message_sid: str,
        new_status: str,
        error_code: str = "",
    ) -> "Message | None":
        """Process a Twilio StatusCallback for a specific MessageSid.

        Looks up the message by external_id, applies the status if it's a
        valid progression (or an error state), commits, and fires SSE events.

        Args:
            db: The async SQLAlchemy session (caller owns the transaction).
            message_sid: The Twilio MessageSid from the callback.
            new_status: The MessageStatus value from the callback.
            error_code: Optional ErrorCode from the callback (for failed states).

        Returns:
            The updated Message object, or None if the SID was unknown or the
            update was skipped (out-of-order regression).
        """
        if new_status not in _PERSIST_STATUSES:
            logger.debug(
                "[STATUS] Ignoring non-persistent status=%s for SID=%s",
                new_status,
                message_sid,
            )
            return None

        msg = await message_repo.get_by_external_id(db, message_sid)
        if msg is None:
            logger.info(
                "[STATUS] Unknown SID=%s status=%s — no message record, skipping",
                message_sid,
                new_status,
            )
            return None

        current_status = msg.status

        # Decide whether to apply the update.
        if new_status in _ERROR_STATUSES:
            # Error states are always written.
            apply = True
        else:
            apply = _is_progression(current_status, new_status)

        if not apply:
            logger.debug(
                "[STATUS] Out-of-order SID=%s: current=%s new=%s — skipped",
                message_sid,
                current_status,
                new_status,
            )
            return None

        # Build error fields if this is a failure callback.
        err_code: str | None = error_code if error_code else None
        err_msg: str | None = (
            f"Twilio error {error_code}" if error_code else None
        )

        await message_repo.update_status(
            db, msg, new_status, error_code=err_code, error_message=err_msg
        )

        logger.info(
            "[STATUS] SID=%s updated %s→%s (msg_id=%d conv_id=%s)",
            message_sid,
            current_status,
            new_status,
            msg.id,
            msg.conversation_id,
        )

        # Publish SSE events so the panel thread refreshes checkmarks.
        conv_id = msg.conversation_id
        if conv_id is not None:
            try:
                await event_bus.publish(
                    f"message_update_{conv_id}",
                    {"conversation_id": conv_id, "message_id": msg.id},
                )
                await event_bus.publish(
                    "conversation_update",
                    {"conversation_id": conv_id},
                )
            except Exception:
                logger.warning(
                    "[STATUS] SSE publish failed for msg_id=%d (non-fatal)",
                    msg.id,
                    exc_info=True,
                )

        return msg


message_status_service = MessageStatusService()
