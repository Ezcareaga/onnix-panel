"""InfoCasas orchestrator service — complete lead capture pipeline.

Wires together SessionManager, NotificationFetcher, LeadParser, and existing
repositories/notifiers into a single polling cycle.

Pipeline (run_poll):
  1. Get valid token (SessionManager)
  2. Fetch notifications (NotificationFetcher)
  3. Filter notifications (LeadParser.should_process_notification)
  4. Extract consulta_ids (LeadParser.extract_consulta_id)
  5. Batch dedup check (NotificationFetcher.check_existing_ids)
  6. For each new lead: fetch details -> parse -> upsert contact ->
     match property -> log lead_event -> notify
  7. Mark ALL notifications as seen (even on error)
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.bot.core.conversation import ConversationManager
from app.utils.money import miles
from app.bot.core.types import ConversationState
from app.bot.services.admin_notifier import AdminNotifier
from app.bot.services.infocasas.lead_parser import (
    ParsedLead,
    extract_consulta_id,
    parse_lead,
    select_zone,
    should_process_notification,
)
from app.bot.services.infocasas.notification_fetcher import NotificationFetcher
from app.bot.services.infocasas.session_manager import SessionManager
from app.database import async_session_factory
from app.models.contact import Contact
from app.models.infocasas_property import InfocasasProperty
from app.repositories.bot_setting_repo import BotSettingRepository
from app.repositories.contact_repo import ContactRepository
from app.repositories.inquiry_history_repo import InquiryHistoryRepository
from app.repositories.lead_event_repo import LeadEventRepository
from app.repositories.message_repo import message_repo
from app.repositories.property_repo import PropertyRepository
from app.bot.channels.twilio_retry import TwilioPostResult, twilio_post_with_retry
from app.bot.channels.wa_failure_marker import write_wa_send_failed_marker

logger = logging.getLogger(__name__)

# Status sets for recurring IC lead processing — keep in sync with each other.
_IC_RESETABLE_STATUSES = {"no_response", "bot_replied", "discarded"}
_IC_SKIP_TEMPLATE_STATUSES = {"interested", "agent_replied", "closed"}


class InfocasasService:
    """Orchestrate the InfoCasas lead capture pipeline.

    Parameters
    ----------
    session_manager:
        Handles JWT token lifecycle for the InfoCasas GraphQL API.
    notification_fetcher:
        Handles polling, detail fetching, mark-seen, and dedup checks.
    notifier:
        Optional AdminNotifier for Telegram alerts to Ez.  Pass None to
        disable all Telegram notifications (useful in tests).
    session_factory:
        Optional async session factory override.  Defaults to the
        production ``async_session_factory`` when not supplied.
    """

    def __init__(
        self,
        session_manager: SessionManager,
        notification_fetcher: NotificationFetcher,
        *,
        notifier: AdminNotifier | None = None,
        session_factory: Any = None,
    ) -> None:
        self._session_manager = session_manager
        self._fetcher = notification_fetcher
        self._notifier = notifier
        self._session_factory = session_factory or async_session_factory

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_poll(self) -> dict[str, Any]:
        """Execute one InfoCasas polling cycle.

        Returns
        -------
        dict
            Metrics dict with keys: status, processed, new, skipped, errors.
            ``status`` is one of: ``"ok"``, ``"auth_failed"``,
            ``"fetch_failed"``.
        """
        # Step 1: Authenticate
        token = await self._session_manager.get_valid_token()
        if not token:
            logger.warning("InfocasasService.run_poll: auth failed")
            if self._notifier:
                await self._notifier.notify_error(
                    "infocasas_poll",
                    "Auth failed — no valid token obtained",
                )
            return {
                "status": "auth_failed",
                "processed": 0,
                "new": 0,
                "skipped": 0,
                "errors": 0,
            }

        # Step 2: Fetch notifications
        notifications = await self._fetcher.fetch_notifications(token)
        if notifications is None:
            logger.warning("InfocasasService.run_poll: fetch_notifications returned None")
            return {
                "status": "fetch_failed",
                "processed": 0,
                "new": 0,
                "skipped": 0,
                "errors": 0,
            }
        if not notifications:
            logger.debug("InfocasasService.run_poll: no notifications")
            return {
                "status": "ok",
                "processed": 0,
                "new": 0,
                "skipped": 0,
                "errors": 0,
            }

        # Step 3 & 4: Filter and extract consulta IDs
        candidates: list[tuple[dict, str]] = []
        for notif in notifications:
            if should_process_notification(notif):
                cid = extract_consulta_id(notif.get("url"))
                if cid:
                    candidates.append((notif, cid))

        # Step 5: Batch dedup check
        if candidates:
            async with self._session_factory() as session:
                consulta_ids = [cid for _, cid in candidates]
                existing = await self._fetcher.check_existing_ids(session, consulta_ids)
            new_candidates = [(n, cid) for n, cid in candidates if cid not in existing]
        else:
            existing = set()
            new_candidates = []

        # Step 6: Process each new lead
        new_count = 0
        error_count = 0
        for notif, consulta_id in new_candidates:
            try:
                is_new = await self._process_lead(token, consulta_id)
                if is_new:
                    new_count += 1
            except Exception:
                logger.exception(
                    "InfocasasService: unhandled error processing consulta_id=%s",
                    consulta_id,
                )
                error_count += 1

        # Step 7: Mark ALL notifications as seen (even errors and duplicates)
        phpsessid = await self._get_phpsessid()
        for notif in notifications:
            nid = notif.get("id")
            if nid:
                await self._fetcher.mark_seen(token, str(nid), phpsessid)

        metrics: dict[str, Any] = {
            "status": "ok",
            "processed": len(notifications),
            "new": new_count,
            "skipped": len(candidates) - len(new_candidates),
            "errors": error_count,
        }
        logger.info("InfocasasService.run_poll: %s", metrics)
        return metrics

    # ------------------------------------------------------------------
    # Private: lead processing
    # ------------------------------------------------------------------

    async def _process_lead(self, token: str, consulta_id: str) -> bool:
        """Process a single lead from raw consulta_id to stored contact.

        Parameters
        ----------
        token:
            Valid InfoCasas JWT Bearer token.
        consulta_id:
            InfoCasas lead identifier to process.

        Returns
        -------
        bool
            True when a new contact was created; False when the lead was
            a duplicate or could not be parsed.
        """
        # Fetch lead details
        lead_data = await self._fetcher.fetch_lead_details(token, consulta_id)
        if not lead_data:
            logger.info(
                "InfocasasService._process_lead: fetch_lead_details returned None "
                "(consulta_id=%s)",
                consulta_id,
            )
            return False

        # Parse
        parsed = parse_lead(lead_data)
        if not parsed:
            logger.info(
                "InfocasasService._process_lead: parse_lead returned None "
                "(consulta_id=%s) — dead-letter",
                consulta_id,
            )
            await self._log_discarded_lead(consulta_id, lead_data)
            return False

        # DB operations in a single transaction
        async with self._session_factory() as session:
            is_new, is_new_property, contact = await self._upsert_contact(session, parsed)

            # Fase 5: phone+property dedup window. If the contact already has an IC
            # event for this property within 24h, skip the template; otherwise promote
            # to recurrente so the user gets a response instead of a silent drop.
            # Note: promoting is_new_property=True also causes the downstream block
            # (around L280) to fire _notify_new_lead(is_recurring=True) and the
            # recurrente template path. Both effects are intentional.
            skipped_reason: str | None = None
            if contact and (not is_new) and (not is_new_property) and parsed.property_code:
                recent = await self._has_recent_ic_event(
                    session, contact.id, parsed.property_code, within_hours=24
                )
                if recent:
                    skipped_reason = "ic_dedup_within_24h"
                else:
                    # Promote to recurrente — the downstream branch will send
                    # wa_tpl_ic_recurrente_directo (or reenviado).
                    is_new_property = True

            matched_property: dict | None = None
            if contact:
                matched_property = await self._match_property(session, parsed, contact.id)

                if is_new:
                    event_type = "created"
                elif is_new_property:
                    event_type = "new_inquiry"
                else:
                    event_type = "linked_existing"
                await self._log_lead_event(
                    session, contact.id, event_type, parsed, matched_property,
                    skipped_reason=skipped_reason,
                )

            await session.commit()

        # SSE event for new IC lead — best-effort, outside the DB session
        if is_new and contact:
            try:
                from app.services.event_bus import event_bus as _event_bus
                await _event_bus.publish("lead.created", {
                    "contact_id": contact.id,
                    "name": contact.name,
                    "source": "infocasas",
                    "phone": contact.phone or "",
                    "status": contact.status,
                    "agent_user_id": contact.agent_user_id,
                })
            except Exception:
                pass

        # Notifications are best-effort, outside the DB session
        if (is_new or is_new_property) and contact:
            await self._notify_new_lead(
                parsed,
                matched_property,
                is_recurring=not is_new and is_new_property,
                contact_status=contact.status,
                is_optout=contact.baja_at is not None,
            )
        if is_new and contact and parsed.phone:
            if parsed.is_reassigned:
                async with self._session_factory() as _s:
                    reenviado_enabled = await self._get_bool_setting(
                        _s, "ic_autoreply_reenviados_enabled"
                    )
                if reenviado_enabled:
                    logger.info(
                        "InfocasasService: lead reenviado, ic_autoreply_reenviados_enabled=ON "
                        "— sending wa_tpl_ic_reenviado_welcome (consulta_id=%s)",
                        parsed.consulta_id,
                    )
                    await self._send_whatsapp_reenviado_welcome(contact, parsed, None)
                else:
                    logger.info(
                        "InfocasasService: lead reenviado, ic_autoreply_reenviados_enabled=OFF "
                        "— skip (consulta_id=%s)",
                        parsed.consulta_id,
                    )
            else:
                if matched_property is not None:
                    await self._send_whatsapp_welcome(
                        parsed, matched_property, contact.id
                    )
                else:
                    async with self._session_factory() as _s:
                        reenviado_enabled = await self._get_bool_setting(
                            _s, "ic_autoreply_reenviados_enabled"
                        )
                    if reenviado_enabled:
                        logger.info(
                            "InfocasasService: lead directo sin match, fallback reenviado, "
                            "ic_autoreply_reenviados_enabled=ON — sending wa_tpl_ic_reenviado_welcome (consulta_id=%s)",
                            parsed.consulta_id,
                        )
                        await self._send_whatsapp_reenviado_welcome(
                            contact, parsed, None
                        )
                    else:
                        logger.info(
                            "InfocasasService: lead directo sin match, fallback reenviado, "
                            "ic_autoreply_reenviados_enabled=OFF — skip (consulta_id=%s)",
                            parsed.consulta_id,
                        )

        elif is_new_property and not is_new and contact and parsed.phone:
            # Skip recurrente template if contact is in active state or opted out
            should_send_template = (
                contact.status not in _IC_SKIP_TEMPLATE_STATUSES
                and contact.baja_at is None
            )

            if should_send_template:
                if parsed.is_reassigned:
                    async with self._session_factory() as _s:
                        reenviado_enabled = await self._get_bool_setting(
                            _s, "ic_autoreply_reenviados_enabled"
                        )
                    if reenviado_enabled:
                        await self._send_whatsapp_recurrente_reenviado(contact, parsed, matched_property)
                    else:
                        logger.info(
                            "InfocasasService: recurrente reenviado, ic_autoreply_reenviados_enabled=OFF "
                            "-- skip (consulta_id=%s)", parsed.consulta_id,
                        )
                else:
                    async with self._session_factory() as _s:
                        autoreply_enabled = await self._get_bool_setting(
                            _s, "ic_autoreply_enabled"
                        )
                    if autoreply_enabled:
                        await self._send_whatsapp_recurrente_directo(contact, parsed, matched_property)
                    else:
                        logger.info(
                            "InfocasasService: recurrente directo, ic_autoreply_enabled=OFF "
                            "-- skip (consulta_id=%s)", parsed.consulta_id,
                        )
            else:
                logger.info(
                    "InfocasasService: recurrente lead, skip template — contact status=%s baja_at=%s (consulta_id=%s)",
                    contact.status, "set" if contact.baja_at else "null", parsed.consulta_id,
                )

        return is_new

    # ------------------------------------------------------------------
    # Private: DB operations
    # ------------------------------------------------------------------

    async def _upsert_contact(
        self,
        session: Any,
        parsed: ParsedLead,
    ) -> tuple[bool, bool, Contact | None]:
        """Find or create a Contact for this lead.

        Lookup order:
        1. Existing contact by ``phone`` (E.164 match).
        2. Existing contact by ``source='infocasas'`` + ``source_id``.
        3. Create new contact.

        Parameters
        ----------
        session:
            Active async SQLAlchemy session owned by the caller.
        parsed:
            Parsed lead data.

        Returns
        -------
        tuple[bool, bool, Contact | None]
            ``(is_new, is_new_property, contact)`` where:
            - ``is_new=True`` when a new contact row was inserted.
            - ``is_new_property=True`` when the property consulted differs from
              the contact's previous ``infocasas_ref`` (new inquiry from same
              person) OR when the contact is brand new.
            - ``is_new_property=False`` when the same property was already
              recorded (true duplicate).
        """
        from sqlalchemy import select as sa_select

        now = datetime.now(timezone.utc)

        # Check by phone first
        if parsed.phone:
            existing = await ContactRepository.get_by_phone(session, parsed.phone)
            if existing:
                is_new_property = existing.infocasas_ref != parsed.property_code

                # Save history before overwriting infocasas_ref
                if is_new_property and existing.infocasas_ref:
                    ic_prop_result = await session.execute(
                        sa_select(InfocasasProperty.title).where(
                            InfocasasProperty.infocasas_ref == existing.infocasas_ref
                        ).limit(1)
                    )
                    ic_title = ic_prop_result.scalar_one_or_none()

                    await InquiryHistoryRepository.create(
                        session,
                        contact_id=existing.id,
                        infocasas_ref=existing.infocasas_ref,
                        consulta_id=existing.source_id,
                        consulta_date=existing.consulta_date,
                        property_title=ic_title,
                        archived_at=now,
                    )

                # Reset status if contact was dormant and no opt-out
                if is_new_property and existing.baja_at is None:
                    if existing.status in _IC_RESETABLE_STATUSES:
                        existing.status = "new"

                existing.source_id = parsed.consulta_id
                existing.infocasas_ref = parsed.property_code
                existing.updated_at = now
                existing.last_activity_at = now
                # Update IC type if it changes (idempotent)
                if existing.preferences is None:
                    existing.preferences = {}
                if "ic_type" not in existing.preferences:
                    existing.preferences["ic_type"] = "reenviada" if parsed.is_reassigned else "directa"
                await session.flush()
                return False, is_new_property, existing

        # Check by source + source_id (idempotency guard)
        result = await session.execute(
            sa_select(Contact).where(
                Contact.source == "infocasas",
                Contact.source_id == parsed.consulta_id,
            )
        )
        existing_by_sid = result.scalar_one_or_none()
        if existing_by_sid:
            existing_by_sid.updated_at = now
            existing_by_sid.last_activity_at = now
            await session.flush()
            return False, False, existing_by_sid

        # Create new contact
        contact = Contact(
            name=parsed.name,
            phone=parsed.phone,
            phone_normalized=parsed.phone,
            email=parsed.email,
            source="infocasas",
            source_id=parsed.consulta_id,
            infocasas_ref=parsed.property_code,
            first_message=parsed.message,
            status="new",
            consulta_date=parsed.consulta_date,
            created_at=now,
            updated_at=now,
            last_activity_at=now,
        )
        session.add(contact)
        # Tag IC type based on reassignment detection
        contact.preferences = {"ic_type": "reenviada" if parsed.is_reassigned else "directa"}
        await session.flush()
        return True, True, contact

    async def _match_property(
        self,
        session: Any,
        parsed: ParsedLead,
        contact_id: int,
    ) -> dict | None:
        """Return IC property info for logging/notifications. Does NOT touch contacts.property_id.

        IC leads are identified via infocasas_ref. The panel uses the
        infocasas_properties JOIN already — setting contacts.property_id
        from IC cross-ref data caused wrong property associations (71% Remax).

        Parameters
        ----------
        session:
            Active async SQLAlchemy session owned by the caller.
        parsed:
            Parsed lead data.
        contact_id:
            ID of the contact (unused, kept for signature compatibility).

        Returns
        -------
        dict | None
            IC property info with keys city, title, matched_by; or None.
        """
        if not parsed.property_code:
            return None

        ic_prop = await PropertyRepository.get_ic_by_ref(session, parsed.property_code)
        if ic_prop:
            return {
                "city": ic_prop.city,
                "title": ic_prop.title,
                "matched_by": "infocasas_ref",
            }

        return None

    async def _has_recent_ic_event(
        self,
        session: Any,
        contact_id: int,
        property_code: str,
        within_hours: int = 24,
    ) -> bool:
        """Return True if this contact has a lead_event for this property in the last N hours.

        Queries event_type IN ('created', 'new_inquiry') filtered by
        metadata->>'property_code' = :property_code AND created_at > now - interval.
        Rows without ``property_code`` in metadata do not match.

        Parameters
        ----------
        session:
            Active async SQLAlchemy session.
        contact_id:
            Contact to check.
        property_code:
            IC property reference code (e.g. ``"OF23CE"``).
        within_hours:
            Look-back window in hours (default 24).

        Returns
        -------
        bool
            True if a qualifying event exists within the window.
        """
        from sqlalchemy import text as sa_text

        cutoff = datetime.now(timezone.utc) - timedelta(hours=within_hours)
        sql = sa_text(
            """
            SELECT 1
            FROM lead_events
            WHERE contact_id = :contact_id
              AND event_type IN ('created', 'new_inquiry')
              AND metadata->>'property_code' = :property_code
              AND created_at > :cutoff
            LIMIT 1
            """
        )
        result = await session.execute(
            sql,
            {"contact_id": contact_id, "property_code": property_code, "cutoff": cutoff},
        )
        row = result.scalar()
        return bool(row)

    async def _log_discarded_lead(
        self, consulta_id: str, lead_data: dict
    ) -> None:
        """Dead-letter de una consulta que el parser no pudo convertir en lead.

        `parse_lead` devuelve None cuando la consulta no trae ni teléfono ni
        email — sin ninguno de los dos no se puede crear un contacto ruteable.
        Hasta el 2026-08-24 el descarte no dejaba rastro en ninguna tabla, y
        como el dedup (`check_existing_ids`) busca el `consulta_id` en
        `contacts.source_id` y en `lead_events.metadata->>'consulta_id'`, la
        consulta volvía a entrar como candidata en el ciclo siguiente. Los
        mismos 6 `consulta_id` cada 5 minutos, para siempre.

        Este evento cierra el circuito: el dedup lo encuentra por metadata y
        deja de re-pedir el `leadById`. El `from` crudo se guarda entero a
        propósito — de los 6 medidos el 2026-08-24, **dos tenían el apellido
        de la persona metido en el campo `phone`** ("Elisa"/"Gill",
        "Lorena"/"Pereira") y uno traía un número de 18 dígitos. Son clientes
        reales, no basura: guardar el payload deja la puerta abierta a
        recuperarlos sin volver a pedirle nada a InfoCasas.

        `contact_id` va NULL (mig 046): la consulta descartada no tiene
        contacto, y las lecturas de `lead_events` filtran todas por un
        `contact_id` concreto, así que la fila no ensucia ninguna ficha.

        Best-effort: si la escritura falla, se loguea y el poll sigue. Un
        dead-letter roto no puede tumbar la captura de los leads buenos.
        """
        listing = lead_data.get("listing") or {}
        metadata: dict[str, Any] = {
            "source": "infocasas",
            "consulta_id": consulta_id,
            "discard_reason": "no_phone_no_email",
            "raw_from": lead_data.get("from"),
            "original_message": lead_data.get("message"),
            "property_title": listing.get("title"),
            "property_code": listing.get("code"),
            "fechaCreacion": str(lead_data.get("created_at") or ""),
        }
        try:
            async with self._session_factory() as session:
                await LeadEventRepository.create(
                    session,
                    contact_id=None,
                    event_type="discarded_no_contact",
                    old_status=None,
                    new_status=None,
                    triggered_by="infocasas_poll",
                    metadata=metadata,
                )
                await session.commit()
        except Exception:
            logger.warning(
                "InfocasasService._log_discarded_lead: no se pudo escribir el "
                "dead-letter (consulta_id=%s) — se va a reprocesar",
                consulta_id,
                exc_info=True,
            )

    async def _log_lead_event(
        self,
        session: Any,
        contact_id: int,
        event_type: str,
        parsed: ParsedLead,
        matched_property: dict | None,
        skipped_reason: str | None = None,
    ) -> None:
        """Create a lead_event record for audit trail.

        Parameters
        ----------
        session:
            Active async SQLAlchemy session owned by the caller.
        contact_id:
            ID of the contact this event belongs to.
        event_type:
            ``"created"`` for new contacts, ``"linked_existing"`` for dupes.
        parsed:
            Parsed lead data for metadata.
        matched_property:
            Optional matched property dict.
        skipped_reason:
            Optional reason the template was suppressed (e.g.
            ``"ic_dedup_within_24h"``).  Stored in ``metadata`` so the event
            remains queryable.  None for the normal (non-skipped) path.
        """
        metadata: dict[str, Any] = {
            "source": "infocasas",
            "lead_name": parsed.name,
            "lead_email": parsed.email,
            "lead_phone": parsed.phone,
            "consulta_id": parsed.consulta_id,
            "fechaCreacion": str(parsed.consulta_date),
            "property_title": parsed.property_title,
            "property_code": parsed.property_code,
            "original_message": parsed.message,
        }
        if matched_property:
            prop_id = matched_property.get("id")
            if prop_id is not None:
                metadata["matched_property_id"] = prop_id
            metadata["matched_by"] = matched_property.get("matched_by")
        if skipped_reason is not None:
            metadata["skipped_reason"] = skipped_reason
        # M6.0 / CLEAN-03 — persist 7 parsed listing fields for reenviado
        # leads under a stable "reenviado" sub-key so the original
        # InfoCasas characteristics block stays queryable
        # (e.g. ``WHERE metadata ? 'reenviado'``). Sub-key (not flat) per
        # Phase 106 blueprint Q1 decision.
        if parsed.is_reassigned:
            metadata["reenviado"] = {
                "listing_type": parsed.listing_type,
                "listing_operation": parsed.listing_operation,
                "listing_bedrooms": parsed.listing_bedrooms,
                "listing_area_m2": parsed.listing_area_m2,
                "listing_price": parsed.listing_price,
                "listing_currency": parsed.listing_currency,
                "listing_zone_from_message": parsed.listing_zone_from_message,
            }

        await LeadEventRepository.create(
            session,
            contact_id=contact_id,
            event_type=event_type,
            old_status=None,
            new_status="new" if event_type == "created" else None,
            triggered_by="infocasas_poll",
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Private: notifications
    # ------------------------------------------------------------------

    async def _notify_new_lead(
        self,
        parsed: ParsedLead,
        matched_property: dict | None,
        *,
        is_recurring: bool = False,
        contact_status: str = "new",
        is_optout: bool = False,
    ) -> None:
        """Send a Telegram notification to Ez about a new InfoCasas lead.

        Best-effort — exceptions are swallowed and logged.

        Parameters
        ----------
        parsed:
            Parsed lead data.
        matched_property:
            Optional matched property dict.
        is_recurring:
            True when the lead is from an existing contact (new property).
        contact_status:
            Current contact status (used in header when recurring).
        is_optout:
            True when the contact has baja_at set (opted out).
        """
        if not self._notifier:
            return

        zone = select_zone(matched_property, parsed.listing_city, parsed.property_title)

        if not is_recurring:
            header = "\U0001f514 <b>NUEVO LEAD INFOCASAS</b>"
        elif is_optout:
            header = "\U0001f6ab <b>LEAD RECURRENTE IC (opt-out)</b>"
        elif contact_status == "new":
            header = "\U0001f504 <b>LEAD RECURRENTE IC (reactivado)</b>"
        else:
            header = f"\U0001f504 <b>LEAD RECURRENTE IC (activo: {contact_status})</b>"

        parts = [
            header,
            "",
            f"\U0001f464 Contacto: {parsed.name}",
        ]
        if parsed.phone:
            parts.append(f"\U0001f4f1 Tel: {parsed.phone}")
        if parsed.email:
            parts.append(f"\U0001f4e7 Email: {parsed.email}")
        parts.append("")
        if parsed.property_title:
            parts.append(f"\U0001f3e0 Propiedad: {parsed.property_title[:80]}")
        parts.append(f"\U0001f4cd Zona: {zone}")
        if parsed.property_code:
            parts.append(f"\U0001f517 Ref: {parsed.property_code}")
        parts.append("")
        parts.append("\u26a1 Nuevo lead de InfoCasas!")

        try:
            await self._notifier.notify("\n".join(parts))
        except Exception:
            logger.warning(
                "InfocasasService: failed to send Telegram notification",
                exc_info=True,
            )

    async def _send_whatsapp_welcome(
        self,
        parsed: ParsedLead,
        matched_property: dict | None,
        contact_id: int,
    ) -> None:
        """Send a WhatsApp welcome template to the new lead via Twilio.

        Template SID and delay bounds are read from ``bot_settings`` DB keys:
        - ``wa_tpl_ic_welcome`` — Twilio ContentSid v1 (required fallback, 2 vars).
        - ``wa_tpl_ic_welcome_v3`` — Twilio ContentSid v3 (Onnix identity, 4 vars).
          When present and ``matched_property`` is not None, v3 is used with
          richer variables (name, property title, city, type/price).
        - ``infocasas_wa_delay_min`` — minimum delay in seconds (default 1).
        - ``infocasas_wa_delay_max`` — maximum delay in seconds (default 5).

        After a successful send, creates a conversation, persists the outbound
        message in the ``messages`` table, and (when ``matched_property`` is
        not None) pre-loads the conversation search_context with
        ``last_detalle_id`` and ``filtros`` so button handlers have context.

        Skips silently when the phone is missing or the template is not
        configured.  All network / DB errors are logged but not re-raised.

        Parameters
        ----------
        parsed:
            Parsed lead data.  ``phone`` must be non-None before calling.
        matched_property:
            Optional matched property dict used for zone selection and v2
            template variables.  When None the v1 template (2 vars) is used
            and no context pre-loading occurs.
        contact_id:
            ID of the contact row that was upserted in ``_process_lead``.
        """
        if not parsed.phone:
            return

        # Read config from bot_settings
        async with self._session_factory() as session:
            autoreply_enabled = await BotSettingRepository.get_value(
                session, "ic_autoreply_enabled"
            )
            template_sid_v1 = await BotSettingRepository.get_value(
                session, "wa_tpl_ic_welcome"
            )
            template_sid_v3 = await BotSettingRepository.get_value(
                session, "wa_tpl_ic_welcome_v3"
            )
            delay_min_str = await BotSettingRepository.get_value(
                session, "infocasas_wa_delay_min"
            )
            delay_max_str = await BotSettingRepository.get_value(
                session, "infocasas_wa_delay_max"
            )

        if autoreply_enabled == "false":
            logger.info(
                "InfocasasService: ic_autoreply_enabled=OFF — skip WA welcome (consulta_id=%s)",
                parsed.consulta_id,
            )
            return

        if not template_sid_v1:
            logger.info(
                "InfocasasService: wa_tpl_ic_welcome not configured — skipping WA welcome"
            )
            return

        delay_min = int(delay_min_str or "1")
        delay_max = int(delay_max_str or "5")

        zone = select_zone(matched_property, parsed.listing_city, parsed.property_title)

        # Fetch full IC property data for v2 template and context pre-loading
        ic_prop_full = None
        if matched_property is not None and parsed.property_code:
            async with self._session_factory() as session:
                ic_prop_full = await PropertyRepository.get_ic_by_ref(
                    session, parsed.property_code
                )

        # Select template: prefer v3 when available and we have a matched property
        using_v3 = bool(template_sid_v3) and matched_property is not None
        template_sid = template_sid_v3 if using_v3 else template_sid_v1

        # Build ContentVariables
        if using_v3 and ic_prop_full is not None:
            content_vars = self._build_v2_content_vars(parsed, ic_prop_full)
        else:
            content_vars = {"1": parsed.name, "2": zone}

        logger.info(
            "InfocasasService: ic_autoreply_enabled=ON — sending wa_tpl_ic_welcome (consulta_id=%s)",
            parsed.consulta_id,
        )

        delay = random.uniform(delay_min, delay_max)
        await asyncio.sleep(delay)

        # Capture send timestamp BEFORE the Twilio HTTP call so the DB record
        # reflects when the message was dispatched, not when the INSERT ran.
        sent_at = datetime.now(timezone.utc)

        # Send via Twilio REST API directly
        from app.bot.config import bot_settings

        twilio_url = (
            f"https://api.twilio.com/2010-04-01/Accounts/"
            f"{bot_settings.TWILIO_ACCOUNT_SID}/Messages.json"
        )
        data = {
            "From": bot_settings.TWILIO_WHATSAPP_FROM,
            "To": f"whatsapp:{parsed.phone}",
            "ContentSid": template_sid,
            "ContentVariables": json.dumps(content_vars),
        }

        try:
            sent_ok = await self._twilio_post_with_marker(
                twilio_url,
                data,
                contact_id=contact_id,
                message_id=None,  # message row created only on success below
            )
            if sent_ok:
                logger.info(
                    "InfocasasService: WA welcome sent to %s", parsed.phone
                )
                # Persist conversation + outbound message (best-effort)
                await self._save_welcome_message(
                    contact_id=contact_id,
                    phone=parsed.phone,
                    name=parsed.name,
                    zone=zone,
                    sent_at=sent_at,
                )
                # Pre-load search_context for matched properties (best-effort)
                if matched_property is not None:
                    await self._preload_search_context(
                        contact_id=contact_id,
                        phone=parsed.phone,
                        ic_prop_full=ic_prop_full,
                    )
        except Exception:
            logger.warning(
                "InfocasasService: WA welcome send error", exc_info=True
            )

    async def _twilio_post(
        self,
        url: str,
        data: dict,
    ) -> bool:
        """POST to Twilio with retry/backoff, wired to the project's admin notifier.

        Shared helper used by all four IC send-WA methods to avoid duplicating
        the ``httpx.AsyncClient`` + auth + error-handling boilerplate.

        Returns True on HTTP 200/201, False otherwise.  Never raises.

        .. deprecated::
            Use ``_twilio_post_with_marker`` for new calls.  This method is
            kept for backwards-compatibility with callsites that do not yet have
            a ``contact_id`` available.
        """
        return await self._twilio_post_with_marker(url, data, contact_id=None, message_id=None)

    async def _twilio_post_with_marker(
        self,
        url: str,
        data: dict,
        *,
        contact_id: int | None,
        message_id: int | None,
    ) -> bool:
        """POST to Twilio with retry/backoff and write a failure marker on exhaustion.

        When ``contact_id`` is provided, a ``wa_send_failed`` lead_event is
        written if all retries fail.  When ``message_id`` is also provided, the
        ``messages`` row is updated to ``status='failed'``.

        Returns True on HTTP 200/201, False otherwise.  Never raises.

        Parameters
        ----------
        url:
            Full Twilio Messages URL.
        data:
            Form-encoded payload dict.
        contact_id:
            DB contact id for the failure marker.  When ``None`` no marker is
            written (backwards-compatible with legacy callsites).
        message_id:
            DB message id to mark as failed.  Ignored when ``contact_id`` is
            ``None``.
        """
        from app.bot.config import bot_settings
        from app.bot.services.admin_notifier import get_admin_notifier

        auth = (bot_settings.TWILIO_ACCOUNT_SID, bot_settings.TWILIO_AUTH_TOKEN)
        msg_type = "template" if "ContentSid" in data else "text"
        to_number = data.get("To", "")

        on_permanent_failure = None
        if contact_id is not None:
            _session_factory = self._session_factory
            _contact_id = contact_id
            _message_id = message_id

            async def _on_fail(result: TwilioPostResult) -> None:
                await write_wa_send_failed_marker(
                    result=result,
                    contact_id=_contact_id,
                    message_id=_message_id,
                    session_factory=_session_factory,
                )

            on_permanent_failure = _on_fail

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                result = await twilio_post_with_retry(
                    client=client,
                    url=url,
                    data=data,
                    auth=auth,
                    admin_notifier=get_admin_notifier(),
                    to_number=to_number,
                    message_type=msg_type,
                    on_permanent_failure=on_permanent_failure,
                )
            return result.success
        except Exception:
            logger.warning(
                "InfocasasService._twilio_post_with_marker: unexpected error (to=%s)",
                to_number,
                exc_info=True,
            )
            return False

    @staticmethod
    def _build_v2_content_vars(parsed: ParsedLead, ic_prop: Any) -> dict[str, str]:
        """Build 4-variable ContentVariables dict for the v2 IC welcome template.

        Variables:
        - "1": lead name
        - "2": property title (truncated to 80 chars)
        - "3": property city
        - "4": property type with price (e.g. "Casa · USD 120.000")

        Parameters
        ----------
        parsed:
            Parsed lead data (provides the lead name).
        ic_prop:
            InfocasasProperty ORM object with title, city, property_type,
            price_sale, price_rent fields.
        """
        title = (ic_prop.title or "")[:80]
        city = ic_prop.city or ""

        # Build a human-readable type+price string
        prop_type = ic_prop.property_type or ""
        price_usd = ic_prop.price_sale or ic_prop.price_rent
        if price_usd:
            try:
                price_int = int(price_usd)
                price_str = f"USD {miles(price_int)}"
            except (ValueError, TypeError):
                price_str = ""
        else:
            price_str = ""

        if prop_type and price_str:
            type_price = f"{prop_type} · {price_str}"
        elif prop_type:
            type_price = prop_type
        elif price_str:
            type_price = price_str
        else:
            type_price = ""

        return {
            "1": parsed.name,
            "2": title,
            "3": city,
            "4": type_price,
        }

    @staticmethod
    def _build_reenviado_content_vars(
        parsed: ParsedLead,
        ic_prop: Any | None,
    ) -> dict[str, str]:
        """Build 4-variable ContentVariables dict for the reenviado welcome template.

        Variables:
        - "1": lead name (from parsed.name)
        - "2": property title — ic_prop.title[:60] or parsed.property_title[:60] (default "propiedad similar")
        - "3": city — ic_prop.city, ic_prop.neighborhood (default "tu zona")
        - "4": formatted price — "Gs. X.XXX" for PYG, "USD X.XXX" for USD (default "consultar precio")

        When ic_prop is None, falls back to parsed.listing_* fields.

        Parameters
        ----------
        parsed:
            Parsed lead data (provides name and fallback listing fields).
        ic_prop:
            InfocasasProperty ORM object or None.  When provided, property
            fields override parsed.listing_* for variables 2–4.
        """
        if ic_prop is not None:
            titulo_display = (
                ic_prop.title[:60] if ic_prop.title
                else (parsed.property_title[:60] if parsed.property_title else "propiedad similar")
            )
            ciudad_display = (
                ic_prop.city
                or ic_prop.neighborhood
                or "tu zona"
            )

            # Choose price based on operation
            op_lower = (ic_prop.operation or "").lower()
            if op_lower == "alquiler":
                raw_price = ic_prop.price_rent
                currency_field = ic_prop.currency_rent
            else:
                raw_price = ic_prop.price_sale
                currency_field = ic_prop.currency_sale

            price_display = ""
            if raw_price:
                try:
                    currency = (currency_field or "usd").upper()
                    if currency in ("GS", "PYG", "GUARANIES", "GUARANÍES"):
                        price_fmt = miles(raw_price)
                        price_display = f"Gs. {price_fmt}"
                    else:
                        price_fmt = miles(raw_price)
                        price_display = f"USD {price_fmt}"
                except (ValueError, TypeError):
                    price_display = ""
            if not price_display:
                price_display = "consultar precio"
        else:
            # Fallback to parsed.listing_* when no IC property record available
            titulo_display = (
                parsed.property_title[:60] if parsed.property_title
                else "propiedad similar"
            )
            ciudad_display = parsed.listing_city or "tu zona"

            price_display = ""
            if parsed.listing_price:
                try:
                    currency = (parsed.listing_currency or "gs").upper()
                    if currency == "GS":
                        price_fmt = miles(parsed.listing_price)
                        price_display = f"Gs. {price_fmt}"
                    else:
                        price_fmt = miles(parsed.listing_price)
                        price_display = f"USD {price_fmt}"
                except (ValueError, TypeError):
                    price_display = ""
            if not price_display:
                price_display = "consultar precio"

        return {
            "1": parsed.name,
            "2": titulo_display,
            "3": ciudad_display,
            "4": price_display,
        }

    async def _preload_search_context(
        self,
        contact_id: int,
        phone: str,
        ic_prop_full: Any | None,
    ) -> None:
        """Pre-load the conversation search_context after a successful IC welcome.

        Sets ``last_detalle_id``, ``etapa='viendo_detalle'``, and ``filtros``
        derived from the matched IC property so that subsequent button callbacks
        (ver detalles, agendar visita, etc.) have context.

        Best-effort: logs a warning on failure but never raises.

        Parameters
        ----------
        contact_id:
            ID of the contact whose conversation will be updated.
        phone:
            E.164 phone number used to look up the conversation.
        ic_prop_full:
            InfocasasProperty ORM object.  When None no context is written.
        """
        if ic_prop_full is None:
            return

        try:
            conv_mgr = ConversationManager()
            async with self._session_factory() as session:
                conv = await conv_mgr.get_or_create_conversation(
                    session,
                    contact_id=contact_id,
                    platform="whatsapp",
                    chat_id=phone,
                )

                # Determine price and currency from ic_prop_full
                if ic_prop_full.price_sale:
                    raw_price = ic_prop_full.price_sale
                    currency = (ic_prop_full.currency_sale or "USD").upper()
                else:
                    raw_price = ic_prop_full.price_rent
                    currency = (ic_prop_full.currency_rent or "USD").upper()

                moneda = "gs" if currency == "PYG" else "usd"
                precio_max = None
                if raw_price:
                    try:
                        precio_max = int(float(raw_price) * 1.3)
                    except (ValueError, TypeError):
                        precio_max = None

                if ic_prop_full.property_id is None:
                    # No match in properties table — populate filtros from IC data so
                    # the user's search context is useful even without a cross-reference.
                    # last_detalle_id remains None (no matched property record to fetch).
                    # last_ic_prop_id is set so VER_DETALLES can fetch IC data directly.
                    logger.info(
                        "InfocasasService: preloading IC filtros without property match "
                        "(contact_id=%d, ic_prop_id=%d)",
                        contact_id,
                        ic_prop_full.id,
                    )
                    state = ConversationState(
                        last_detalle_id=None,
                        last_ic_prop_id=ic_prop_full.id,
                        etapa="esperando_confirmacion_busqueda",
                        filtros={
                            "tipo": ic_prop_full.property_type,
                            "ciudad": ic_prop_full.city,
                            "barrio": ic_prop_full.neighborhood or "",
                            "operacion": ic_prop_full.operation,
                            "precio_max": precio_max,
                            "moneda": moneda,
                        },
                    )
                else:
                    state = ConversationState(
                        last_detalle_id=ic_prop_full.property_id,
                        last_ic_prop_id=ic_prop_full.id,
                        etapa="viendo_detalle",
                        filtros={
                            "tipo": ic_prop_full.property_type,
                            "ciudad": ic_prop_full.city,
                            "barrio": ic_prop_full.neighborhood or "",
                            "operacion": ic_prop_full.operation,
                            "precio_max": precio_max,
                            "moneda": moneda,
                        },
                    )

                await conv_mgr.update_search_context(session, conv.id, state)
                await session.commit()

            if ic_prop_full.property_id is not None:
                logger.info(
                    "InfocasasService: search_context pre-loaded "
                    "(contact_id=%d, ic_prop_id=%d, property_id=%d)",
                    contact_id,
                    ic_prop_full.id,
                    ic_prop_full.property_id,
                )
        except Exception:
            logger.warning(
                "InfocasasService: failed to pre-load search_context",
                exc_info=True,
            )

    async def _save_welcome_message(
        self,
        contact_id: int,
        phone: str,
        name: str,
        zone: str,
        sent_at: datetime,
    ) -> None:
        """Create a conversation and persist the welcome template message.

        Uses ``message_repo.create()`` directly so that ``created_at`` is set
        to the real send timestamp captured before the Twilio HTTP call, rather
        than defaulting to the DB ``NOW()`` at INSERT time.

        Best-effort: logs a warning on failure but never raises.

        Parameters
        ----------
        contact_id:
            ID of the contact who received the template.
        phone:
            E.164 phone number (used as ``chat_id``).
        name:
            Contact name used in the template body.
        zone:
            Zone string used in the template body.
        sent_at:
            Timezone-aware UTC timestamp captured immediately before the
            Twilio API call — stored as ``created_at`` on the message row.
        """
        try:
            conv_mgr = ConversationManager()
            body = (
                f"Hola {name}, vimos tu consulta sobre una propiedad "
                f"en {zone}. \u00bfC\u00f3mo podemos ayudarte?"
            )
            async with self._session_factory() as session:
                conv = await conv_mgr.get_or_create_conversation(
                    session,
                    contact_id=contact_id,
                    platform="whatsapp",
                    chat_id=phone,
                )
                await message_repo.create(
                    db=session,
                    conversation_id=conv.id,
                    contact_id=contact_id,
                    direction="outbound",
                    sender_type="bot",
                    body=body,
                    content=body,
                    external_id="",
                    status="sent",
                    intent="ic_welcome",
                    created_at=sent_at,
                )
                await session.commit()
            logger.info(
                "InfocasasService: welcome message saved (contact_id=%d, conv_id=%d)",
                contact_id,
                conv.id,
            )
        except Exception:
            logger.warning(
                "InfocasasService: failed to save welcome message to DB",
                exc_info=True,
            )

    async def _send_whatsapp_reenviado_welcome(
        self,
        contact: Any,
        parsed: ParsedLead,
        session: Any,
    ) -> None:
        """Send a WhatsApp welcome template to a reenviado or unmatched lead.

        Template SID is read from ``wa_tpl_ic_reenviado_welcome_v3`` bot_settings key.
        Variables: {"1": name, "2": tipo, "3": zona, "4": precio, "5": operacion}.

        Skips silently when the template is not configured.  All network / DB
        errors are logged but not re-raised.

        Parameters
        ----------
        contact:
            Contact ORM object (provides contact.id and contact.phone).
        parsed:
            Parsed lead data.
        session:
            Active async SQLAlchemy session (used for template SID lookup).
        """
        if not parsed.phone:
            return

        # Read config from bot_settings
        async with self._session_factory() as _session:
            template_sid = await BotSettingRepository.get_value(
                _session, "wa_tpl_ic_reenviado_welcome_v3"
            )
            delay_min_str = await BotSettingRepository.get_value(
                _session, "infocasas_wa_delay_min"
            )
            delay_max_str = await BotSettingRepository.get_value(
                _session, "infocasas_wa_delay_max"
            )

        if not template_sid:
            logger.warning(
                "InfocasasService: wa_tpl_ic_reenviado_welcome_v3 not configured — "
                "skipping reenviado WA welcome (consulta_id=%s)",
                parsed.consulta_id,
            )
            return

        delay_min = int(delay_min_str or "1")
        delay_max = int(delay_max_str or "5")

        # Look up the IC property record for the assigned property so template
        # variables reflect the actual listing, not the client's search preferences.
        ic_prop_full: Any | None = None
        ic_ref = getattr(contact, "infocasas_ref", None)
        if ic_ref:
            async with self._session_factory() as _session:
                ic_prop_full = await PropertyRepository.get_ic_by_ref(_session, ic_ref)

        content_vars = self._build_reenviado_content_vars(parsed, ic_prop_full)

        delay = random.uniform(delay_min, delay_max)
        await asyncio.sleep(delay)

        sent_at = datetime.now(timezone.utc)

        from app.bot.config import bot_settings

        twilio_url = (
            f"https://api.twilio.com/2010-04-01/Accounts/"
            f"{bot_settings.TWILIO_ACCOUNT_SID}/Messages.json"
        )
        data = {
            "From": bot_settings.TWILIO_WHATSAPP_FROM,
            "To": f"whatsapp:{parsed.phone}",
            "ContentSid": template_sid,
            "ContentVariables": json.dumps(content_vars),
        }

        try:
            sent_ok = await self._twilio_post_with_marker(
                twilio_url,
                data,
                contact_id=contact.id,
                message_id=None,
            )
            if sent_ok:
                logger.info(
                    "InfocasasService: WA reenviado welcome sent to %s "
                    "(consulta_id=%s)",
                    parsed.phone,
                    parsed.consulta_id,
                )
                await self._save_reenviado_message(
                    contact_id=contact.id,
                    phone=parsed.phone,
                    content_vars=content_vars,
                    sent_at=sent_at,
                    template_sid=template_sid,
                )
                await self._preload_search_context(
                    contact_id=contact.id,
                    phone=parsed.phone,
                    ic_prop_full=ic_prop_full,
                )
        except Exception:
            logger.warning(
                "InfocasasService: WA reenviado welcome send error", exc_info=True
            )

    async def _send_whatsapp_recurrente_directo(
        self,
        contact: Any,
        parsed: ParsedLead,
        matched_property: dict | None,
    ) -> None:
        """Send WA template 3 (recurrente directo) to an existing contact with a new inquiry.

        Template SID is read from ``wa_tpl_ic_recurrente_directo_v2`` bot_settings key.
        Variables: {"1": name, "2": title, "3": city, "4": type+price}.

        The autoreply toggle is checked by the caller (_process_lead), not here.
        Skips silently when the template SID is not configured.
        All network / DB errors are logged but not re-raised.

        Parameters
        ----------
        contact:
            Contact ORM object (provides contact.id and contact.phone).
        parsed:
            Parsed lead data.
        matched_property:
            Optional matched property dict used for IC property lookup.
        """
        if not parsed.phone:
            return

        # Read template SID and delay config
        async with self._session_factory() as _session:
            template_sid = await BotSettingRepository.get_value(
                _session, "wa_tpl_ic_recurrente_directo_v2"
            )
            delay_min_str = await BotSettingRepository.get_value(
                _session, "infocasas_wa_delay_min"
            )
            delay_max_str = await BotSettingRepository.get_value(
                _session, "infocasas_wa_delay_max"
            )

        if not template_sid:
            logger.info(
                "InfocasasService: wa_tpl_ic_recurrente_directo_v2 not configured — "
                "skipping recurrente directo WA (consulta_id=%s)",
                parsed.consulta_id,
            )
            return

        delay_min = int(delay_min_str or "1")
        delay_max = int(delay_max_str or "5")

        # Fetch full IC property data for richer variables
        ic_prop_full = None
        if parsed.property_code:
            async with self._session_factory() as _session:
                ic_prop_full = await PropertyRepository.get_ic_by_ref(
                    _session, parsed.property_code
                )

        content_vars = self._build_recurrente_directo_content_vars(parsed, ic_prop_full)

        logger.info(
            "InfocasasService: sending wa_tpl_ic_recurrente_directo to %s "
            "(consulta_id=%s)",
            parsed.phone,
            parsed.consulta_id,
        )

        delay = random.uniform(delay_min, delay_max)
        await asyncio.sleep(delay)

        sent_at = datetime.now(timezone.utc)

        from app.bot.config import bot_settings

        twilio_url = (
            f"https://api.twilio.com/2010-04-01/Accounts/"
            f"{bot_settings.TWILIO_ACCOUNT_SID}/Messages.json"
        )
        data = {
            "From": bot_settings.TWILIO_WHATSAPP_FROM,
            "To": f"whatsapp:{contact.phone}",
            "ContentSid": template_sid,
            "ContentVariables": json.dumps(content_vars),
        }

        try:
            sent_ok = await self._twilio_post_with_marker(
                twilio_url,
                data,
                contact_id=contact.id,
                message_id=None,
            )
            if sent_ok:
                logger.info(
                    "InfocasasService: WA recurrente directo sent to %s "
                    "(consulta_id=%s)",
                    contact.phone,
                    parsed.consulta_id,
                )
                await self._save_recurrente_directo_message(
                    contact_id=contact.id,
                    phone=contact.phone,
                    content_vars=content_vars,
                    sent_at=sent_at,
                )
                await self._preload_search_context(
                    contact_id=contact.id,
                    phone=contact.phone,
                    ic_prop_full=ic_prop_full,
                )
        except Exception:
            logger.warning(
                "InfocasasService: WA recurrente directo send error", exc_info=True
            )

    @staticmethod
    def _build_recurrente_directo_content_vars(
        parsed: ParsedLead,
        ic_prop: Any | None,
    ) -> dict[str, str]:
        """Build 4-variable ContentVariables dict for the recurrente directo template (template 3).

        Variables:
        - "1": contact name (from parsed.name)
        - "2": property title (truncated to 80 chars; from ic_prop.title or parsed.property_title)
        - "3": property city (from ic_prop.city or parsed.listing_city)
        - "4": type+price string (e.g. "Casa · USD 120.000"; empty when unavailable)

        Parameters
        ----------
        parsed:
            Parsed lead data (provides name and fallback title/city).
        ic_prop:
            InfocasasProperty ORM object or None.  When None, parsed fallback
            values are used for variables 2 and 3; variable 4 is empty.
        """
        if ic_prop is not None:
            title = (ic_prop.title or "")[:80]
            city = ic_prop.city or ""
            prop_type = ic_prop.property_type or ""
            price_usd = ic_prop.price_sale or ic_prop.price_rent
            if price_usd:
                try:
                    price_int = int(price_usd)
                    price_str = f"USD {miles(price_int)}"
                except (ValueError, TypeError):
                    price_str = ""
            else:
                price_str = ""

            if prop_type and price_str:
                type_price = f"{prop_type} · {price_str}"
            elif prop_type:
                type_price = prop_type
            elif price_str:
                type_price = price_str
            else:
                type_price = ""
        else:
            # Fallback to parsed data when no IC property record is available
            title = (parsed.property_title or "")[:80]
            city = parsed.listing_city or ""
            type_price = ""

        return {
            "1": parsed.name,
            "2": title,
            "3": city,
            "4": type_price,
        }

    async def _send_whatsapp_recurrente_reenviado(
        self,
        contact: Any,
        parsed: ParsedLead,
        matched_property: dict | None,
    ) -> None:
        """Send WA template 4 (recurrente reenviado) to an existing contact with a new reenviado inquiry.

        Template SID is read from ``wa_tpl_ic_recurrente_reenviado_v2`` bot_settings key.
        Variables: {"1": name, "2": zona_original, "3": title, "4": city, "5": precio}.

        The toggle is checked by the caller (_process_lead), not here.
        Skips silently when the template SID is not configured.
        All network / DB errors are logged but not re-raised.

        Parameters
        ----------
        contact:
            Contact ORM object (provides contact.id, contact.phone, contact.name).
        parsed:
            Parsed lead data.
        matched_property:
            Optional matched property dict used for richer template variables.
        """
        if not parsed.phone:
            return

        # Read template SID and delay config
        async with self._session_factory() as _session:
            template_sid = await BotSettingRepository.get_value(
                _session, "wa_tpl_ic_recurrente_reenviado_v2"
            )
            delay_min_str = await BotSettingRepository.get_value(
                _session, "infocasas_wa_delay_min"
            )
            delay_max_str = await BotSettingRepository.get_value(
                _session, "infocasas_wa_delay_max"
            )

        if not template_sid:
            logger.info(
                "InfocasasService: wa_tpl_ic_recurrente_reenviado_v2 not configured — "
                "skipping recurrente reenviado WA (consulta_id=%s)",
                parsed.consulta_id,
            )
            return

        delay_min = int(delay_min_str or "1")
        delay_max = int(delay_max_str or "5")

        content_vars = self._build_recurrente_reenviado_content_vars(contact, parsed, matched_property)

        logger.info(
            "InfocasasService: sending wa_tpl_ic_recurrente_reenviado to %s "
            "(consulta_id=%s)",
            parsed.phone,
            parsed.consulta_id,
        )

        delay = random.uniform(delay_min, delay_max)
        await asyncio.sleep(delay)

        sent_at = datetime.now(timezone.utc)

        from app.bot.config import bot_settings

        twilio_url = (
            f"https://api.twilio.com/2010-04-01/Accounts/"
            f"{bot_settings.TWILIO_ACCOUNT_SID}/Messages.json"
        )
        data = {
            "From": bot_settings.TWILIO_WHATSAPP_FROM,
            "To": f"whatsapp:{parsed.phone}",
            "ContentSid": template_sid,
            "ContentVariables": json.dumps(content_vars),
        }

        try:
            sent_ok = await self._twilio_post_with_marker(
                twilio_url,
                data,
                contact_id=contact.id,
                message_id=None,
            )
            if sent_ok:
                logger.info(
                    "InfocasasService: WA recurrente reenviado sent to %s "
                    "(consulta_id=%s)",
                    parsed.phone,
                    parsed.consulta_id,
                )
                await self._save_recurrente_reenviado_message(
                    contact_id=contact.id,
                    phone=parsed.phone,
                    content_vars=content_vars,
                    sent_at=sent_at,
                )
                await self._preload_reenviado_context(
                    contact_id=contact.id,
                    phone=parsed.phone,
                    parsed=parsed,
                )
        except Exception:
            logger.warning(
                "InfocasasService: WA recurrente reenviado send error", exc_info=True
            )

    @staticmethod
    def _build_recurrente_reenviado_content_vars(
        contact: Any,
        parsed: ParsedLead,
        matched_property: dict | None,
    ) -> dict[str, str]:
        """Build 5-variable ContentVariables dict for the recurrente reenviado template (template 4).

        Variables:
        - "1": contact name (contact.name or parsed.name as fallback)
        - "2": original zone searched (parsed.listing_city or listing_zone_from_message)
        - "3": title of the similar property found (matched_property['title'], max 80 chars)
        - "4": city of the similar property (matched_property['city'])
        - "5": formatted price of the similar property

        When matched_property is None, variables 3-5 fall back to parsed data.

        Parameters
        ----------
        contact:
            Contact ORM object (provides contact.name).
        parsed:
            Parsed lead data (provides listing_city, property_title, listing_price, etc.)
        matched_property:
            Optional dict with keys title, city, price, currency.
        """
        name = (contact.name if hasattr(contact, "name") and contact.name else parsed.name) or ""
        zona_original = parsed.listing_city or parsed.property_title or ""

        if matched_property is not None:
            title = (matched_property.get("title") or "")[:80]
            city = matched_property.get("city") or parsed.listing_city or ""
            # Try to format price from matched_property
            raw_price = matched_property.get("price")
            currency = (matched_property.get("currency") or "USD").upper()
            if raw_price:
                try:
                    price_int = int(raw_price)
                    price_str = f"USD {miles(price_int)}"
                except (ValueError, TypeError):
                    price_str = "consultar precio"
            else:
                price_str = "consultar precio"
        else:
            # Fallback to parsed data
            title = (parsed.property_title or "")[:80]
            city = parsed.listing_city or ""
            if parsed.listing_price:
                try:
                    currency = (parsed.listing_currency or "USD").upper()
                    price_int = int(parsed.listing_price)
                    if currency == "GS":
                        price_str = f"Gs. {miles(price_int)}"
                    else:
                        price_str = f"USD {miles(price_int)}"
                except (ValueError, TypeError):
                    price_str = "consultar precio"
            else:
                price_str = "consultar precio"

        return {
            "1": name,
            "2": zona_original,
            "3": title,
            "4": city,
            "5": price_str,
        }

    async def _save_reenviado_message(
        self,
        contact_id: int,
        phone: str,
        content_vars: dict[str, str],
        sent_at: datetime,
        template_sid: str,
    ) -> None:
        """Create a conversation and persist the reenviado welcome template message.

        Best-effort: logs a warning on failure but never raises.

        Parameters
        ----------
        contact_id:
            ID of the contact who received the template.
        phone:
            E.164 phone number (used as ``chat_id``).
        content_vars:
            Template content variables dict with keys "1"–"5":
            name, tipo, zona, precio, operacion.
        sent_at:
            Timezone-aware UTC timestamp captured before the Twilio API call.
        template_sid:
            The ContentSid that was sent (stored for audit).
        """
        try:
            conv_mgr = ConversationManager()
            name = content_vars.get("1", "")
            tipo = content_vars.get("2", "propiedad")
            zona = content_vars.get("3", "")
            precio = content_vars.get("4", "consultar precio")
            operacion = content_vars.get("5", "compra")
            body = f"[IC Reenviado] {name}: {tipo} en {zona} - {precio} ({operacion})"
            async with self._session_factory() as session:
                conv = await conv_mgr.get_or_create_conversation(
                    session,
                    contact_id=contact_id,
                    platform="whatsapp",
                    chat_id=phone,
                )
                await message_repo.create(
                    db=session,
                    conversation_id=conv.id,
                    contact_id=contact_id,
                    direction="outbound",
                    sender_type="bot",
                    body=body,
                    content=body,
                    external_id="",
                    status="sent",
                    intent="ic_reenviado_welcome",
                    created_at=sent_at,
                )
                await session.commit()
            logger.info(
                "InfocasasService: reenviado welcome message saved "
                "(contact_id=%d, conv_id=%d)",
                contact_id,
                conv.id,
            )
        except Exception:
            logger.warning(
                "InfocasasService: failed to save reenviado welcome message to DB",
                exc_info=True,
            )

    async def _save_recurrente_directo_message(
        self,
        contact_id: int,
        phone: str,
        content_vars: dict[str, str],
        sent_at: datetime,
    ) -> None:
        """Persist the recurrente directo template message after successful Twilio send.

        Best-effort: logs a warning on failure but never raises.
        """
        try:
            conv_mgr = ConversationManager()
            name = content_vars.get("1", "")
            title = content_vars.get("2", "")
            city = content_vars.get("3", "")
            type_price = content_vars.get("4", "")
            parts = [p for p in [title, f"en {city}" if city else "", type_price] if p]
            body = f"[IC] Hola {name}, consultaste nuevamente por: {' '.join(parts)}"
            async with self._session_factory() as session:
                conv = await conv_mgr.get_or_create_conversation(
                    session,
                    contact_id=contact_id,
                    platform="whatsapp",
                    chat_id=phone,
                )
                await message_repo.create(
                    db=session,
                    conversation_id=conv.id,
                    contact_id=contact_id,
                    direction="outbound",
                    sender_type="bot",
                    body=body,
                    content=body,
                    external_id="",
                    status="sent",
                    intent="ic_recurrente_directo",
                    created_at=sent_at,
                )
                await session.commit()
            logger.info(
                "InfocasasService: recurrente directo message saved (contact_id=%d, conv_id=%d)",
                contact_id,
                conv.id,
            )
        except Exception:
            logger.warning(
                "InfocasasService: failed to save recurrente directo message to DB",
                exc_info=True,
            )

    async def _save_recurrente_reenviado_message(
        self,
        contact_id: int,
        phone: str,
        content_vars: dict[str, str],
        sent_at: datetime,
    ) -> None:
        """Persist the recurrente reenviado template message after successful Twilio send.

        Best-effort: logs a warning on failure but never raises.
        """
        try:
            conv_mgr = ConversationManager()
            name = content_vars.get("1", "")
            zona_original = content_vars.get("2", "")
            title = content_vars.get("3", "")
            city = content_vars.get("4", "")
            precio = content_vars.get("5", "")
            body = (
                f"[IC] Hola {name}, buscabas en {zona_original}. "
                f"Te mandamos: {title} en {city}. {precio}"
            )
            async with self._session_factory() as session:
                conv = await conv_mgr.get_or_create_conversation(
                    session,
                    contact_id=contact_id,
                    platform="whatsapp",
                    chat_id=phone,
                )
                await message_repo.create(
                    db=session,
                    conversation_id=conv.id,
                    contact_id=contact_id,
                    direction="outbound",
                    sender_type="bot",
                    body=body,
                    content=body,
                    external_id="",
                    status="sent",
                    intent="ic_recurrente_reenviado",
                    created_at=sent_at,
                )
                await session.commit()
            logger.info(
                "InfocasasService: recurrente reenviado message saved (contact_id=%d, conv_id=%d)",
                contact_id,
                conv.id,
            )
        except Exception:
            logger.warning(
                "InfocasasService: failed to save recurrente reenviado message to DB",
                exc_info=True,
            )

    async def _preload_reenviado_context(
        self,
        contact_id: int,
        phone: str,
        parsed: ParsedLead,
    ) -> None:
        """Pre-load the conversation search_context after a reenviado welcome.

        Sets ``etapa='esperando_confirmacion_busqueda'`` and ``filtros``
        derived from the parsed lead characteristics so that subsequent
        button callbacks (SI_MOSTRAME_REENVIADO, etc.) have filter context.

        Best-effort: logs a warning on failure but never raises.

        Parameters
        ----------
        contact_id:
            ID of the contact whose conversation will be updated.
        phone:
            E.164 phone number used to look up the conversation.
        parsed:
            Parsed lead data with listing_* characteristic fields.
        """
        try:
            conv_mgr = ConversationManager()
            async with self._session_factory() as session:
                conv = await conv_mgr.get_or_create_conversation(
                    session,
                    contact_id=contact_id,
                    platform="whatsapp",
                    chat_id=phone,
                )

                precio_max = None
                if parsed.listing_price:
                    try:
                        precio_max = int(parsed.listing_price * 1.3)
                    except (ValueError, TypeError):
                        precio_max = None

                # Determine if zone is a city or a barrio so the search
                # resolves correctly.  IC reenviado zones are often barrio-level
                # (e.g. "Recoleta") not city-level.
                from app.bot.search.geo_resolver import GeoResolver as _GR
                _geo = _GR()
                zone = parsed.listing_zone_from_message or parsed.listing_city
                if zone and _geo.is_known_city(zone):
                    filtro_ciudad = zone
                    filtro_barrio = None
                else:
                    filtro_ciudad = None
                    filtro_barrio = zone

                state = ConversationState(
                    etapa="esperando_confirmacion_busqueda",
                    filtros={
                        "tipo": parsed.listing_type,
                        "ciudad": filtro_ciudad,
                        "barrio": filtro_barrio,
                        "operacion": parsed.listing_operation,
                        "precio_max": precio_max,
                        "moneda": parsed.listing_currency or "gs",
                    },
                )
                await conv_mgr.update_search_context(session, conv.id, state)
                await session.commit()

            logger.info(
                "InfocasasService: reenviado search_context pre-loaded "
                "(contact_id=%d, filtros=%s)",
                contact_id,
                {k: v for k, v in state.filtros.items() if v is not None},
            )
        except Exception:
            logger.warning(
                "InfocasasService: failed to pre-load reenviado search_context",
                exc_info=True,
            )

    async def _get_bool_setting(self, session: Any, key: str) -> bool:
        """Read a boolean bot_setting by key.

        Returns True when the stored value equals ``"true"`` (case-sensitive),
        False for any other value including ``None`` or ``"false"``.

        Parameters
        ----------
        session:
            Active async SQLAlchemy session.
        key:
            The bot_settings key to read.
        """
        value = await BotSettingRepository.get_value(session, key)
        return value == "true"

    # ------------------------------------------------------------------
    # Private: helpers
    # ------------------------------------------------------------------

    async def _get_phpsessid(self) -> str | None:
        """Read PHPSESSID from bot_settings for mark_seen requests.

        Returns
        -------
        str | None
            The stored value, or None when absent or on DB error.
        """
        try:
            async with self._session_factory() as session:
                return await BotSettingRepository.get_value(
                    session, "infocasas_phpsessid"
                )
        except Exception:
            logger.warning(
                "InfocasasService: failed to read infocasas_phpsessid", exc_info=True
            )
            return None


# ---------------------------------------------------------------------------
# Module-level factory
# ---------------------------------------------------------------------------


def get_infocasas_service() -> InfocasasService:
    """Create a production-ready InfocasasService.

    Reads credentials from environment variables via the respective
    factories.

    Returns
    -------
    InfocasasService
        Fully configured instance ready for use.
    """
    from app.bot.services.admin_notifier import get_admin_notifier
    from app.bot.services.infocasas.session_manager import get_session_manager

    return InfocasasService(
        session_manager=get_session_manager(),
        notification_fetcher=NotificationFetcher(),
        notifier=get_admin_notifier(),
    )
