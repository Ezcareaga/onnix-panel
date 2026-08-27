import logging
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.conversation_repo import conversation_repo
from app.repositories.message_repo import message_repo
from app.repositories.contact_repo import contact_repo
from app.repositories.property_repo import property_repo

logger = logging.getLogger(__name__)


class ConversationService:

    @staticmethod
    def _enrich_with_needs_reply(items: list[dict]) -> list[dict]:
        """Marca las conversaciones donde la pelota está del lado del asesor.

        Se llamaba ``has_unread`` y el título en pantalla decía «Mensajes
        nuevos». Las dos cosas eran mentira: esto no mira si alguien leyó nada,
        mira si el ÚLTIMO mensaje es entrante — o sea si está **sin
        responder**. Por eso tampoco se apagaba al abrir la conversación, y
        parecía un bug: con el nombre correcto es el comportamiento correcto,
        porque abrir no es contestar.
        """
        for item in items:
            item["needs_reply"] = item.get("last_message_direction") == "inbound"
        return items

    @staticmethod
    async def get_conversations(
        db: AsyncSession,
        limit: int = 50,
        offset: int = 0,
        agent_filter: int | None = None,
        channel: str | None = None,
        stuck: bool = False,
    ) -> list:
        """Return conversations, optionally scoped to an agent's assigned contacts.

        agent_filter: user.id to restrict to contacts.agent_user_id == user.id.
        Pass None (default) for admin/unrestricted access.
        channel: 'whatsapp' or 'telegram' to filter by channel, None for all.
        stuck: solo las trabadas, el mismo predicado que cuenta el KPI.
        offset: number of rows to skip for load-more pagination.
        """
        result = await conversation_repo.get_with_contacts(
            db, limit, offset=offset, agent_filter=agent_filter,
            channel=channel, stuck=stuck,
        )
        ConversationService._enrich_with_needs_reply(result)
        logger.info(
            "Conversations listed: count=%s, limit=%s, offset=%s, agent_filter=%s, channel=%s, stuck=%s",
            len(result), limit, offset, agent_filter, channel, stuck,
        )
        return result

    @staticmethod
    async def search_conversations(
        db: AsyncSession,
        query: str,
        limit: int = 50,
        offset: int = 0,
        agent_filter: int | None = None,
        channel: str | None = None,
        stuck: bool = False,
    ) -> list:
        """Search conversations by contact name or message body content.

        agent_filter: restrict results to the agent's assigned contacts when set.
        channel: 'whatsapp' or 'telegram' to filter by channel, None for all.
        stuck: solo las trabadas, el mismo predicado que cuenta el KPI.
        offset: number of rows to skip for load-more pagination.
        """
        result = await conversation_repo.search_with_contacts(
            db, query, limit, offset=offset, agent_filter=agent_filter,
            channel=channel, stuck=stuck
        )
        ConversationService._enrich_with_needs_reply(result)
        logger.info(
            "Conversations searched: query=%r, count=%s, limit=%s, offset=%s, agent_filter=%s, channel=%s",
            query, len(result), limit, offset, agent_filter, channel,
        )
        return result

    @staticmethod
    async def toggle_bot_active(db: AsyncSession, conv_id: int) -> tuple[bool, int] | None:
        """Toggle is_bot_active for a conversation.

        Returns (new_is_bot_active, contact_id), or None if conversation not found.
        """
        from sqlalchemy import select
        from app.models.conversation import Conversation

        result = await db.execute(
            select(Conversation).where(Conversation.id == conv_id)
        )
        conv = result.scalar_one_or_none()
        if conv is None:
            return None
        new_val = not bool(conv.is_bot_active)  # bool() guards against NULL
        conv.is_bot_active = new_val
        if new_val:
            # Reactivation: clear the human-reply cooldown so the bot responds
            # immediately.  The orchestrator gates on last_human_reply_at for 30 min
            # after a manual reply; keeping that timestamp would silence the bot until
            # the window expires even though the operator explicitly turned it back on.
            conv.last_human_reply_at = None
        await db.flush()
        return new_val, conv.contact_id

    @staticmethod
    async def get_thread(db: AsyncSession, conversation_id: int) -> dict | None:
        conv = await conversation_repo.get_by_id(db, conversation_id)
        if not conv:
            logger.warning("Thread not found: conversation_id=%s", conversation_id)
            return None
        messages = await message_repo.get_by_conversation(db, conversation_id)
        logger.info("Thread loaded: conversation_id=%s, messages=%s", conversation_id, len(messages))
        contact = await contact_repo.get_by_id(db, conv.contact_id) if conv.contact_id else None

        # Calculate 24h window expiry for WhatsApp
        window_expired = False
        if contact and hasattr(contact, 'last_user_message_at'):
            # Always query messages table — last_user_message_at may be stale (N8N era)
            last_inbound_ts = await message_repo.get_last_inbound_at(db, contact.id)
            # Use whichever is more recent: cached field or actual messages
            effective_ts = contact.last_user_message_at
            if last_inbound_ts and (effective_ts is None or last_inbound_ts > effective_ts):
                effective_ts = last_inbound_ts
            if effective_ts is not None:
                hours_since = (datetime.now(timezone.utc) - effective_ts).total_seconds() / 3600
                window_expired = hours_since > 24
            else:
                # No inbound messages at all — expire only for WhatsApp
                conv_channel = str(getattr(conv, 'channel', None) or getattr(conv, 'platform', None) or 'whatsapp')
                window_expired = (conv_channel == 'whatsapp')

        # Collect all unique property IDs referenced across messages
        all_property_ids: set[int] = set()
        for msg in messages:
            if msg.properties_shown:
                all_property_ids.update(msg.properties_shown)

        # Fetch property summaries in one bulk query and index by id
        id_to_property: dict[int, dict] = {}
        if all_property_ids:
            summaries = await property_repo.get_summary_by_ids(db, list(all_property_ids))
            id_to_property = {p["id"]: p for p in summaries}
            logger.info(
                "Property summaries loaded: conversation_id=%s, ids=%s, found=%s",
                conversation_id,
                len(all_property_ids),
                len(id_to_property),
            )

        # Build properties_map: message_id → [property_dict, ...]
        # Only populated for messages that have properties_shown
        properties_map: dict[int, list[dict]] = {}
        for msg in messages:
            if msg.properties_shown:
                properties_map[msg.id] = [
                    id_to_property[pid]
                    for pid in msg.properties_shown
                    if pid in id_to_property
                ]

        return {
            "conversation": conv,
            "messages": messages,
            "contact_name": contact.name if contact else "Desconocido",
            "contact_phone": contact.phone or "" if contact else "",
            "contact": contact,
            "window_expired": window_expired,
            "properties_map": properties_map,
        }

    @staticmethod
    async def get_activity(db: AsyncSession, conv_id: int) -> list[dict]:
        """Return enriched activity items for the conversation activity panel.

        Resolves the contact_id from the conversation, then fetches the 30 most
        recent lead_events for that contact. Each event is returned as a dict
        with a human-readable 'description' field in Spanish and the raw event
        for timestamp rendering.
        """
        from app.models.conversation import Conversation as _Conv
        from sqlalchemy import select as _select
        from app.repositories.lead_event_repo import lead_event_repo as _le_repo

        _LABELS: dict[str, str] = {
            "bot_toggle":                   "Bot alternado",
            "auto_status_change":           "Estado actualizado automáticamente",
            "status_change":                "Estado cambiado",
            "client_responded_to_agent":    "Cliente respondió al asesor",
            "new_contact":                  "Contacto creado",
            "created":                      "Contacto creado",
            "new_lead":                     "Lead creado",
            "updated":                      "Datos actualizados",
            "agent_assigned":               "Lead asignado a agente",
            "lead_registered":              "Registrado como interesado",
            "note_created":                 "Nota agregada",
            "note_edited":                  "Nota editada",
            "note_deleted":                 "Nota eliminada",
            "visit_created":                "Visita agendada",
            "visit_cancelled":              "Visita cancelada",
            "visit_completed":              "Visita realizada",
            "visit_rescheduled":            "Visita reagendada",
            "followup_sent":                "Seguimiento automático enviado",
            "callback_asesor":              "Solicitud de asesor",
            "first_contact":                "Primer contacto",
            "wa_send_failed":               "Mensaje WhatsApp no entregado",
            "client_declined_now":          "Cliente pidió no recibir opciones",
            "detail_view":                  "Propiedad vista",
            "search":                       "Búsqueda realizada",
            "bot_interaction":              "Conversación bot",
            "mode_switch":                  "Bot cambió a modo búsqueda",
            "zero_results_offered":         "Bot ofreció alternativas",
            "zero_results_accepted":        "Cliente aceptó alternativas",
            "zero_results_abandoned":       "Búsqueda sin resultados abandonada",
            "notified_ez":                  "Notificación enviada",
        }

        res = await db.execute(_select(_Conv).where(_Conv.id == conv_id))
        conv = res.scalar_one_or_none()
        if conv is None or conv.contact_id is None:
            return []

        events = await _le_repo.get_recent_for_activity(db, conv.contact_id, limit=30)

        items = []
        for ev in events:
            meta = ev.event_metadata or {}
            # Build contextual description
            base = _LABELS.get(ev.event_type, ev.event_type.replace("_", " "))
            if ev.event_type == "bot_toggle":
                active = meta.get("is_bot_active", True)
                base = "Bot activado" if active else "Bot desactivado"
                triggered = ev.triggered_by or ""
                if triggered.startswith("user:"):
                    base += " por asesor"
                elif "system" in triggered:
                    base += " por sistema"
            elif ev.event_type == "status_change" and ev.old_status and ev.new_status:
                _status_labels = {
                    "new": "Nuevo", "bot_replied": "Bot respondió",
                    "agent_replied": "Asesor respondió", "interested": "Interesado",
                    "closed": "Cerrado", "no_response": "Sin respuesta",
                    "discarded": "Descartado", "deleted": "Eliminado",
                }
                old_lbl = _status_labels.get(ev.old_status, ev.old_status)
                new_lbl = _status_labels.get(ev.new_status, ev.new_status)
                base = f"Estado: {old_lbl} → {new_lbl}"
            elif ev.event_type == "auto_status_change" and ev.old_status and ev.new_status:
                _status_labels = {
                    "new": "Nuevo", "bot_replied": "Bot respondió",
                    "agent_replied": "Asesor respondió", "interested": "Interesado",
                    "closed": "Cerrado", "no_response": "Sin respuesta",
                }
                old_lbl = _status_labels.get(ev.old_status, ev.old_status)
                new_lbl = _status_labels.get(ev.new_status, ev.new_status)
                base = f"Estado automático: {old_lbl} → {new_lbl}"
            elif ev.event_type == "agent_assigned" and meta.get("agent_name"):
                base = f"Asignado a {meta['agent_name']}"
            items.append({
                "event": ev,
                "description": base,
                "event_type": ev.event_type,
                "created_at": ev.created_at,
                "triggered_by": ev.triggered_by or "",
                "metadata": meta,
            })
        return items


conversation_service = ConversationService()
