"""Bot gate state management (M4 Task 4.1).

Encapsulates the two pieces of ``is_bot_active`` handling that used to
live module-level in ``orchestrator.py``:

- ``check_bot_active`` — gate function that decides whether the bot
  should process the incoming message. Reads ``conversations.is_bot_active``
  from the DB. Task 4.2 will switch this to ``SELECT FOR UPDATE`` to close
  the race with panel-driven writes from reply_service / conversation_service.

- ``reactivate_from_agent_replied`` — flips a conversation back to
  bot-active when a client responds after an agent's manual reply. Task
  4.3 will harden this against concurrent opt-out writes.

Both must run in the same session/transaction as the rest of the
webhook so the check and any subsequent update are consistent.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select, text as sa_text

from app.bot.services.admin_notifier import get_admin_notifier
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.repositories.lead_event_repo import lead_event_repo

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.bot.core.types import ContactInfo, ConversationInfo

logger = logging.getLogger(__name__)


def check_bot_active(conversation: "ConversationInfo") -> bool:
    """Return True when the bot should process messages for this conversation.

    In-memory read of ``conversation.is_bot_active``. Use for read-only
    callers (UI, background jobs) where a stale value is acceptable.

    The orchestrator webhook path uses ``check_bot_active_locked``
    instead, which also takes a row lock to serialize against panel
    writes.
    """
    return conversation.is_bot_active


async def check_bot_active_locked(
    session: "AsyncSession",
    conversation_id: int,
) -> bool:
    """Return True when the bot should process messages, taking a row lock.

    Issues ``SELECT is_bot_active FROM conversations WHERE id = :id
    FOR UPDATE`` inside the caller's transaction. The lock persists
    until the transaction commits or rolls back, which:

    - Forces panel writes (reply_service / conversation_service) that
      touch the same row to wait until the webhook finishes — closes
      races #1 and #2 from AUDIT_M4_FASE0 §3.
    - Makes the gate read reflect the authoritative DB state at the
      moment the lock was acquired, not a stale in-memory snapshot.

    Call ONCE near the top of ``handle_message``, after the conversation
    is resolved but before any bot-visible work (save_inbound_message,
    Claude call, etc.). The lock releases automatically on commit.
    """
    stmt = (
        select(Conversation.is_bot_active)
        .where(Conversation.id == conversation_id)
        .with_for_update()
    )
    result = await session.execute(stmt)
    return bool(result.scalar())


async def reactivate_from_agent_replied(
    session: "AsyncSession",
    contact: "ContactInfo",
    conversation: "ConversationInfo",
) -> None:
    """Reactivate the bot when a client responds after an agent reply.

    Called when ``contact.status == 'agent_replied'`` and a new inbound
    message arrives. Must run BEFORE the ``is_bot_active`` gate so that
    Fase 3 agent assignment (``is_bot_active=False``) does not silently
    block the reactivation path.

    Side effects:
    - Sets ``contacts.status = 'bot_replied'`` (DB + in-memory ContactInfo)
    - Sets ``conversations.is_bot_active = true`` (DB + in-memory)
    - Sets ``conversations.last_human_reply_at = NULL`` (DB + in-memory) so
      the orchestrator's human-cooldown check (30 min) does not silence the
      bot immediately after the reactivation announcement.
    - Creates a ``client_responded_to_agent`` lead_event
    - Best-effort admin notification (errors are swallowed)

    Race #3 (opt-out vs reactivation) is closed by re-reading
    ``contact.baja_at`` with ``SELECT FOR UPDATE`` before mutating: if
    another transaction already committed an opt-out, this function
    exits early and leaves the contact in the irreversible baja state.
    """
    # Race #3 guard: lock the contact row and re-read baja_at. If an
    # opt-out already landed (possibly in a concurrent transaction that
    # committed moments ago), do NOT flip the bot back on — opt-out is
    # irreversible by design.
    baja_stmt = (
        select(Contact.baja_at)
        .where(Contact.id == contact.id)
        .with_for_update()
    )
    baja_at = (await session.execute(baja_stmt)).scalar()
    if baja_at is not None:
        logger.info(
            "Reactivation skipped: contact=%d is in opt-out state (baja_at=%s)",
            contact.id, baja_at,
        )
        return

    agent_user_id = contact.agent_user_id

    await session.execute(
        sa_text(
            "UPDATE contacts SET status = 'bot_replied', updated_at = NOW() "
            "WHERE id = :id AND status = 'agent_replied'"
        ),
        {"id": contact.id},
    )
    await session.execute(
        sa_text(
            "UPDATE conversations "
            "SET is_bot_active = true, last_human_reply_at = NULL "
            "WHERE id = :conv_id"
        ),
        {"conv_id": conversation.id},
    )

    await lead_event_repo.create(
        db=session,
        contact_id=contact.id,
        event_type="client_responded_to_agent",
        old_status="agent_replied",
        new_status="bot_replied",
        triggered_by="bot",
        metadata={"agent_user_id": agent_user_id, "conversation_id": conversation.id},
    )

    # Reflect the status change in-memory so downstream logic sees bot_replied
    contact.status = "bot_replied"
    # Reflect is_bot_active=True so the gate below lets the message through
    conversation.is_bot_active = True
    # Clear the human cooldown in-memory — orchestrator reads this field on the
    # same object right after reactivation (orchestrator.py:204-205).  Without
    # this, check_human_cooldown would still see the old timestamp and silence
    # the bot even though we just announced "Bot reactivado automáticamente".
    conversation.last_human_reply_at = None

    try:
        _notifier = get_admin_notifier()
        await _notifier.notify(
            f"<b>Cliente respondió al agente</b>\n"
            f"Contacto id={contact.id} ({contact.name or contact.phone}) "
            f"respondió. Bot reactivado automáticamente."
        )
    except Exception:
        logger.warning(
            "Reactivation notifier failed for contact=%d — non-fatal",
            contact.id, exc_info=True,
        )

    logger.info(
        "Bot reactivated after agent_replied — contact=%d conversation=%d",
        contact.id, conversation.id,
    )
