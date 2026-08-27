"""handler: ver_detalles for InfoCasas leads + IC filtros loader (M4 Task 3.11).

Provides two IC-specific helpers extracted from Orchestrator:

- ``handle_ver_detalles_ic`` — renders the detail view for an IC lead by
  fetching directly from ``infocasas_properties``. Handles the ~40% of IC
  leads that have no cross-reference to the canonical ``properties``
  table. Falls back to lookup by ``contact.infocasas_ref`` when the ID
  lookup misses, and caches the resolved ID back into ``search_context``
  for subsequent calls.

- ``load_ic_filtros_for_contact`` — extracts search filtros (tipo,
  ciudad, barrio, operacion, precio_max, moneda) from the IC lead row
  reachable via the contact's ``infocasas_ref``. Used by the reenviado
  shortcut when ``search_context.filtros`` is empty.

Extracted from:
- ``Orchestrator._handle_ver_detalles_ic`` → ``handle_ver_detalles_ic``
- ``Orchestrator._load_ic_filtros_for_contact`` → ``load_ic_filtros_for_contact``
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.bot.core.types import BotResponse
from app.bot.handlers._types import HandlerResult
from app.repositories.contact_repo import ContactRepository
from app.repositories.property_repo import PropertyRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.bot.core.conversation import ConversationManager
    from app.bot.core.types import ConversationState

logger = logging.getLogger(__name__)


async def handle_ver_detalles_ic(
    ic_prop_id: int | None,
    session: "AsyncSession",
    contact,
    conversation,
    search_context: "ConversationState",
    *,
    conversation_manager: "ConversationManager",
) -> HandlerResult:
    """Handle VER_DETALLES for IC leads by fetching from infocasas_properties.

    Fetches the IC property by primary key when ic_prop_id is set.
    Falls back to lookup by contact.infocasas_ref when the ID lookup misses
    or when ic_prop_id is None (e.g. stale context or failed preload).
    On a successful ref-based lookup, caches the found ID in search_context
    so subsequent calls use the faster ID path.

    Returns a conversacion fallback when neither lookup finds the record.

    Args:
        ic_prop_id: Primary key from infocasas_properties, or None.
        session: Active async DB session.
        contact: ContactInfo for the requesting user.
        conversation: ConversationInfo for the current conversation.
        search_context: Current ConversationState.
        conversation_manager: Used to persist search_context and outbound message.

    Returns:
        HandlerResult with a BotResponse (intent='detalle' on success,
        intent='conversacion' on fallback) and the updated search_context.
    """
    from app.bot.core.response_builder import ic_prop_to_detail_dict

    ic_prop = None
    if ic_prop_id is not None:
        ic_prop = await PropertyRepository.get_ic_by_id(session, ic_prop_id)

    # Fallback: lookup by infocasas_ref when ID lookup missed or ID was None
    if ic_prop is None and getattr(contact, "infocasas_ref", None):
        ic_prop = await PropertyRepository.get_ic_by_ref(session, contact.infocasas_ref)
        if ic_prop is not None:
            # Cache the found ID so future VER_DETALLES calls use the faster ID path
            search_context.last_ic_prop_id = ic_prop.id
            await conversation_manager.update_search_context(
                session, conversation.id, search_context
            )

    if ic_prop is None:
        bot_response = BotResponse(
            text="No pude encontrar esa propiedad. ¿Querés que busque opciones similares?",
            intent="conversacion",
        )
        await conversation_manager.save_outbound_message(
            session, conversation.id, contact.id,
            bot_response.text, bot_response.intent,
        )
        return HandlerResult(response=bot_response, search_context=search_context)

    prop_dict = ic_prop_to_detail_dict(ic_prop)

    # Cross-ref enrichment: when the IC property has a match in the
    # canonical `properties` table (~60% of IC leads), merge fields
    # that don't exist in infocasas_properties (description, address,
    # coordinates, photos) for a richer detail view.
    if getattr(ic_prop, "property_id", None) is not None:
        matched = await PropertyRepository.get_by_id(session, ic_prop.property_id)
        if matched is not None:
            for field in (
                "description", "address",
                "latitude", "longitude",
                "main_image_url", "image_urls", "local_image_count",
            ):
                val = getattr(matched, field, None)
                if val not in (None, "", []):
                    prop_dict[field] = val

    shown_ids = [ic_prop_id]

    bot_response = BotResponse(
        text="",
        intent="detalle",
        properties=[prop_dict],
        shown_ids=shown_ids,
    )

    await conversation_manager.save_outbound_message(
        session, conversation.id, contact.id,
        bot_response.text, bot_response.intent,
        properties_shown=shown_ids,
    )

    logger.info(
        "Decision — {\"intent\": \"detalle\", \"model\": \"ic_shortcut\", \"properties\": 1, \"is_lead\": true}",
    )

    return HandlerResult(response=bot_response, search_context=search_context)


async def load_ic_filtros_for_contact(
    contact_id: int,
    session: "AsyncSession",
) -> dict:
    """Fetch IC lead filtros when search_context.filtros is empty.

    Queries infocasas_properties via the contact's infocasas_ref.
    Returns filtros dict or empty dict if no IC data found.
    """
    try:
        contact_db = await ContactRepository.get_by_id(session, contact_id)
        ref = getattr(contact_db, "infocasas_ref", None) if contact_db else None
        if not ref:
            return {}

        ic_prop = await PropertyRepository.get_ic_by_ref(session, ref)
        if not ic_prop:
            return {}

        operation = getattr(ic_prop, "operation", None) or ""
        if "alquiler" in operation or "rent" in operation:
            price = getattr(ic_prop, "price_rent", None)
            moneda = getattr(ic_prop, "currency_rent", None) or "gs"
        else:
            price = getattr(ic_prop, "price_sale", None)
            moneda = getattr(ic_prop, "currency_sale", None) or "gs"

        precio_max = None
        if price:
            try:
                precio_max = int(float(price) * 1.3)
            except (ValueError, TypeError):
                pass

        return {
            "tipo": getattr(ic_prop, "property_type", None),
            "ciudad": getattr(ic_prop, "city", None),
            "barrio": getattr(ic_prop, "neighborhood", None),
            "operacion": operation,
            "precio_max": precio_max,
            "moneda": moneda,
        }
    except Exception:
        logger.debug(
            "IC filtros lookup failed for contact_id=%d — using empty filtros",
            contact_id,
        )
        return {}
