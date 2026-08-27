"""feat(authz): central ownership-check helpers for agent authorization.

Role model (approved — Phase 119 / feat/agent-authz):
  - role='admin' : full access, no ownership restriction.
  - role='user'  : legacy, full access (no change).
  - role='agent' : can only read/write contacts where
                   contacts.agent_user_id == user.id.

Usage pattern:
    await ensure_contact_access(db, user, contact_id)
    await ensure_conversation_access(db, user, conv_id)
    await ensure_visit_access(db, user, visit_id)

Raises HTTPException(403) for agents that do not own the resource.
Raises HTTPException(404) when the resource does not exist (consistent
with the behaviour already present in GET /contacts/{id}).

Reference: routes/contacts.py:86-90 (M6.1 ROLE-13).
"""
from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.user import User
from app.models.visit import Visit

logger = logging.getLogger(__name__)


async def ensure_contact_access(
    db: AsyncSession,
    user: User,
    contact_id: int,
) -> Contact:
    """Fetch `contact_id` and enforce agent ownership.

    Returns the Contact ORM object so callers that already need the
    object can reuse it without a second DB round-trip.

    Raises:
        HTTPException(404) if the contact does not exist.
        HTTPException(403) if user.role == 'agent' and
                           contact.agent_user_id != user.id.
    """
    result = await db.execute(select(Contact).where(Contact.id == contact_id))
    contact = result.scalar_one_or_none()
    if contact is None:
        raise HTTPException(status_code=404, detail="Contacto no encontrado")
    if user.role == "agent" and contact.agent_user_id != user.id:
        logger.debug(
            "authz: agent %d denied contact %d (owner=%s)",
            user.id, contact_id, contact.agent_user_id,
        )
        raise HTTPException(status_code=403, detail="No tenés acceso a este contacto")
    return contact


async def ensure_conversation_access(
    db: AsyncSession,
    user: User,
    conv_id: int,
) -> Conversation:
    """Fetch `conv_id`, resolve contact_id, and enforce agent ownership.

    Returns the Conversation ORM object.

    Raises:
        HTTPException(404) if the conversation does not exist.
        HTTPException(403) if user.role == 'agent' and the conversation's
                           contact.agent_user_id != user.id.
    """
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    if user.role == "agent":
        # Resolve contact to check ownership
        await ensure_contact_access(db, user, conv.contact_id)
    return conv


async def ensure_visit_access(
    db: AsyncSession,
    user: User,
    visit_id: int,
) -> Visit:
    """Fetch `visit_id`, resolve contact_id, and enforce agent ownership.

    Returns the Visit ORM object.

    Raises:
        HTTPException(404) if the visit does not exist.
        HTTPException(403) if user.role == 'agent' and the visit's
                           contact.agent_user_id != user.id.
    """
    result = await db.execute(select(Visit).where(Visit.id == visit_id))
    visit = result.scalar_one_or_none()
    if visit is None:
        raise HTTPException(status_code=404, detail="Visita no encontrada")
    if user.role == "agent":
        await ensure_contact_access(db, user, visit.contact_id)
    return visit
