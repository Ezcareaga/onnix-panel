"""Service for sending WhatsApp templates from the admin panel."""
import json
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.channels.twilio_retry import wa_send_disabled
from app.bot.config import bot_settings
from app.bot.core.conversation import ConversationManager
from app.repositories.bot_setting_repo import BotSettingRepository
from app.repositories.contact_repo import contact_repo
from app.repositories.lead_event_repo import lead_event_repo
from app.repositories.message_repo import message_repo
from app.services.reply_service import _http_client

logger = logging.getLogger(__name__)


class TemplateService:
    """Send pre-approved WhatsApp templates via Twilio ContentSid."""

    @staticmethod
    async def send_template(
        db: AsyncSession,
        contact_id: int,
        template_key: str,
        property_id: int | None = None,
        pref_zona: str | None = None,
        pref_tipo: str | None = None,
        pref_operacion: str | None = None,
        pref_presupuesto: str | None = None,
    ) -> dict:
        """Send a WA template to a contact and persist the outbound message.

        Returns dict with 'conversation_id' on success.
        Raises ValueError for validation errors.
        Raises httpx.HTTPStatusError on Twilio failures.
        """
        # 1. Load and validate contact
        contact = await contact_repo.get_by_id(db, contact_id)
        if not contact:
            raise ValueError("Contacto no encontrado")
        if not contact.phone:
            raise ValueError("Contacto sin telefono")
        if contact.status == "discarded":
            raise ValueError("Contacto descartado - no se puede contactar")

        # 2. Resolve ContentSid from bot_settings
        content_sid = await BotSettingRepository.get_value(db, template_key)
        if not content_sid or content_sid == "PLACEHOLDER" or not content_sid.startswith("HX"):
            raise ValueError(f"Template {template_key} no configurada")

        # 3. Get or create conversation
        conv_mgr = ConversationManager()
        conv = await conv_mgr.get_or_create_conversation(
            db,
            contact_id=contact_id,
            platform="whatsapp",
            chat_id=contact.phone,
        )

        # 4. Capture sent_at BEFORE the Twilio call
        sent_at = datetime.now(timezone.utc)

        # 5a. Variables de la plantilla.
        #
        # Las que salían de una propiedad (título, ciudad, precio) se fueron con
        # el vertical inmobiliario. Quedan el nombre del contacto y las tres de
        # preferencias, que las escribe el agente en el drawer.
        #
        # ponytail: este servicio entero se reescribe cuando entren las
        # plantillas de Meta — allá no hay ContentSid de Twilio, hay nombre de
        # plantilla + idioma + components. Mientras tanto sigue siendo el
        # camino que usa el botón del hilo.
        content_variables: dict[str, str] = {"1": contact.name or ""}
        if template_key in ("wa_tpl_send_preferences", "wa_tpl_send_preferences_v4"):
            content_variables["2"] = pref_tipo or ""
            content_variables["3"] = pref_zona or ""
            content_variables["4"] = pref_operacion or ""

        # 5. Send via Twilio REST API — ContentSid only, NO Body field
        account_sid = bot_settings.TWILIO_ACCOUNT_SID
        auth_token = bot_settings.TWILIO_AUTH_TOKEN
        from_number = bot_settings.TWILIO_WHATSAPP_FROM

        url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
        to_wa = f"whatsapp:{contact.phone}" if not contact.phone.startswith("whatsapp:") else contact.phone

        data = {
            "From": from_number,
            "To": to_wa,
            "ContentSid": content_sid,
            "ContentVariables": json.dumps(content_variables),
        }

        # Staging isolation guard (incidente 2026-04-04 class) — this service
        # posts on its own _http_client, so it never passes through
        # twilio_post_with_retry.  Skip only the network call: the rest of the
        # method (message row, search_context, auto-status) still runs so the
        # panel keeps behaving like production, and external_sid stays "" —
        # the same marker a Twilio-less send already leaves.
        if wa_send_disabled():
            logger.warning(
                "WA send disabled via WA_SEND_ENABLED=false — template NOT sent "
                "(contact=%d, template=%s, to=%s)",
                contact_id, template_key, to_wa,
            )
            external_sid = ""
        else:
            resp = await _http_client.post(
                url,
                auth=(account_sid, auth_token),
                data=data,
            )
            resp.raise_for_status()
            twilio_response = resp.json()
            external_sid = twilio_response.get("sid", "")

            logger.info(
                "Template sent: contact=%d, template=%s, sid=%s",
                contact_id,
                template_key,
                external_sid,
            )

        # Update contact.property_id and search_context so "Ver similares" works
        if property_id is not None and prop is not None:
            contact.property_id = property_id
            contact.updated_at = sent_at
            state = await conv_mgr.get_search_context(db, conv.id)
            state.last_detalle_id = property_id
            state.etapa = "viendo_detalle"
            from decimal import Decimal as _Decimal
            state.filtros = {
                "tipo": prop.property_type,
                "ciudad": prop.city,
                "barrio": prop.neighborhood or "",
                "operacion": prop.operation,
                "precio_max": int(prop.price_usd * _Decimal("1.3")) if prop.price_usd else None,
                "moneda": "usd",
            }
            await conv_mgr.update_search_context(db, conv.id, state)
            await db.flush()

        # Save preferences to search_context so bot can search immediately on callback
        if template_key in ("wa_tpl_send_preferences", "wa_tpl_send_preferences_v4") and (pref_zona or pref_tipo or pref_operacion):
            try:
                from app.bot.core.types import ConversationState
                filtros: dict = {}
                if pref_operacion:
                    filtros["operacion"] = pref_operacion
                if pref_tipo:
                    filtros["tipo"] = pref_tipo
                if pref_zona:
                    filtros["ciudad"] = pref_zona
                if pref_presupuesto:
                    filtros["precio_max"] = pref_presupuesto
                state = ConversationState(filtros=filtros)
                await conv_mgr.update_search_context(db, conv.id, state)
            except Exception:
                logger.warning("Could not save search_context for template preferences", exc_info=True)

        # 6. Persist outbound message with intent=manual_template
        name = content_variables.get("1", "")
        if template_key in ("wa_tpl_send_property", "wa_tpl_send_property_v2", "wa_tpl_send_property_v4"):
            if prop:
                body = (
                    f"🏠 Hola {name}! Soy el asistente de Onnix SA. "
                    f"Encontramos una propiedad que podria interesarte: "
                    f"📍 {content_variables.get('2', '')} — {content_variables.get('3', '')} "
                    f"💰 {content_variables.get('4', '')}. ¿En que podemos ayudarte?"
                )
            else:
                body = f"🏠 Hola {name}! Soy el asistente de Onnix SA. Encontramos propiedades que pueden interesarte."
        elif template_key in ("wa_tpl_send_preferences", "wa_tpl_send_preferences_v4"):
            body = (
                f"🏠 Hola {name}! Soy el asistente de Onnix SA. "
                f"Tenemos opciones de {content_variables.get('2', '')} "
                f"en {content_variables.get('3', '')} para {content_variables.get('4', '')}. "
                f"¿Queres que te muestre las mejores opciones?"
            )
        elif template_key in ("wa_tpl_send_generic", "wa_tpl_send_generic_v3"):
            body = f"🏠 Hola {name}! Soy el asistente de Onnix SA. Estas buscando comprar, alquilar o vender una propiedad?"
        elif template_key == "wa_tpl_followup_v3":
            body = (
                f"🏠 Hola {name}! Te escribe el asistente de Onnix SA. "
                f"¿Seguís buscando propiedades? Podemos ayudarte."
            )
        elif template_key in ("wa_tpl_followup_72h", "wa_tpl_followup_72h_v3"):
            body = (
                f"🏠 Hola {name}! Soy el asistente de Onnix SA. "
                f"Ingresaron propiedades nuevas que podrían interesarte. ¿Seguís buscando?"
            )
        elif template_key in ("wa_tpl_agent_reply", "wa_tpl_agent_reply_v3"):
            body = (
                f"🏠 Hola {name}! Te escribe el equipo de Onnix SA. "
                f"¿Seguís interesado/a en propiedades?"
            )
        else:
            body = f"[Template: {template_key}] — {name}"
        await message_repo.create(
            db=db,
            conversation_id=conv.id,
            contact_id=contact_id,
            direction="outbound",
            sender_type="agent",
            body=body,
            content=body,
            external_id=external_sid,
            status="sent",
            intent="manual_template",
            created_at=sent_at,
        )

        # 7. Update conversation timestamps
        now = datetime.now(timezone.utc)
        from app.repositories.conversation_repo import conversation_repo
        conv_obj = await conversation_repo.get_by_id(db, conv.id)
        if conv_obj:
            conv_obj.last_message_at = now
            conv_obj.message_count = (conv_obj.message_count or 0) + 1
            await db.flush()

        # 8. Auto-status: new/no_response -> agent_replied + lead_event
        if contact.status in ("new", "no_response"):
            old_status = contact.status
            contact.status = "agent_replied"
            contact.updated_at = now
            await lead_event_repo.create(
                db=db,
                contact_id=contact_id,
                event_type="auto_status_change",
                old_status=old_status,
                new_status="agent_replied",
                triggered_by=f"manual_template:{template_key}",
                metadata={
                    "conversation_id": conv.id,
                    "trigger": "manual_template_send",
                    "template_key": template_key,
                },
            )
            await db.flush()
            logger.info(
                "Auto status update: contact %d %s -> agent_replied (template %s)",
                contact_id,
                old_status,
                template_key,
            )

        return {"conversation_id": conv.id}


template_service = TemplateService()
