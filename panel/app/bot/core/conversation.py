"""ConversationManager — stateful backbone for bot conversations.

Handles contact resolution (upsert), conversation management,
message history, search_context persistence, and human cooldown.
All DB operations use sqlalchemy.text() with async sessions.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import text

from app.bot.core.types import (
    BotRequest,
    ContactInfo,
    ConversationInfo,
    ConversationState,
    HistoryMessage,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

HUMAN_COOLDOWN_MINUTES = 30


# ---------------------------------------------------------------------------
# M6.3 Plan 123-10 (BOT-16/BOT-17): vista_publica handshake detection.
#
# The public site (onnix.com.py) "consultar por WhatsApp" CTA pre-fills:
#   "Hola! Me interesa la propiedad {CODIGO_PROP} que vi en onnix.com.py"
# (ROADMAP §2.4). When a NEW contact arrives with that CTA we tag the contact
# source='vista_publica' and store the consulted prop code so the recepcionista
# DIRECTO flow greets with the property. _is_vista_publica_handshake is the
# predicate referenced by Orchestrator._resolve_mode check 2a (123-02).
# ---------------------------------------------------------------------------

# Domain core, tolerant of www./https:// prefixes, trailing slash and a minor
# typo on the TLD (\.co\w matches .com / .con / .co). Matched case-insensitively
# against a whitespace-collapsed copy of the text.
_DOMAIN_RE = re.compile(r"onnix\.co\w", re.IGNORECASE)

# Prop-code token: real corpus codes are 6-char uppercase-alphanumeric tokens
# that contain at least one digit (e.g. EC1754, A99D31, GAEAE3, B651EC). The
# digit requirement keeps ordinary Spanish words (PROPIEDAD, ONNIX) from matching.
_CODE_TOKEN_RE = re.compile(r"\b(?=[A-Z0-9]*\d)([A-Z0-9]{5,8})\b")

# Anchored extractor for the canonical CTA wording: "propiedad <CODE> que vi".
# Most reliable signal — tried first, falls back to the token scan.
_ANCHORED_CODE_RE = re.compile(
    r"propiedad\s+([A-Za-z0-9]{4,12})\s+que\s+vi", re.IGNORECASE
)

# Tokens that look code-shaped but are noise (the domain core, common words).
_CODE_STOPWORDS = frozenset({"Onnix", "ONNIX", "PROPIEDAD", "WHATSAPP"})


def _is_vista_publica_handshake(text: str | None) -> bool:
    """Return True when *text* is the public-site CTA handshake.

    Requires BOTH the onnix.com.py domain (tolerant of www./https:// prefixes,
    trailing slash, surrounding/extra whitespace, lowercase, and a minor TLD
    typo) AND a prop-code token. Order-independent (code-then-domain works).
    """
    if not text:
        return False
    collapsed = re.sub(r"\s+", " ", text).strip()
    if not _DOMAIN_RE.search(collapsed):
        return False
    return _extract_prop_code(collapsed) is not None


def _extract_prop_code(text: str | None) -> str | None:
    """Extract the consulted prop code from a CTA handshake, or None.

    Tries the anchored CTA wording first ("propiedad <CODE> que vi"), then a
    general code-token scan (6-ish char uppercase-alphanumeric with a digit).
    Returns the code uppercased so it matches infocasas_ref storage.
    """
    if not text:
        return None
    collapsed = re.sub(r"\s+", " ", text).strip()

    anchored = _ANCHORED_CODE_RE.search(collapsed)
    if anchored:
        candidate = anchored.group(1).upper()
        if candidate not in _CODE_STOPWORDS:
            return candidate

    for match in _CODE_TOKEN_RE.finditer(collapsed.upper()):
        token = match.group(1)
        if token in _CODE_STOPWORDS:
            continue
        return token
    return None


class ConversationManager:
    """Manages contacts, conversations, messages, and search_context.

    Every method receives an AsyncSession — the caller controls the
    transaction boundary.
    """

    # ------------------------------------------------------------------
    # Contact resolution
    # ------------------------------------------------------------------

    async def resolve_contact(
        self,
        session: "AsyncSession",
        platform: str,
        user_id: str,
        user_name: str,
        text_msg: str | None = None,
    ) -> ContactInfo:
        """Upsert a contact by platform-specific identifier.

        Telegram: lookup by (source='telegram', source_id=user_id).
        WhatsApp: lookup by phone (E.164).

        Returns ContactInfo with is_baja=True for discarded contacts.

        M6.3 Plan 123-10 (BOT-16/BOT-17): when the FIRST inbound message is a
        public-site CTA handshake (onnix.com.py + prop code), a NEW contact is
        created with source='vista_publica' and the extracted prop code stored
        on infocasas_ref (semantic reuse — infocasas_ref doubles as the
        prop-code carrier so the recepcionista DIRECTO flow greets with the
        prop). Existing contacts are NEVER relabeled: the ON CONFLICT DO UPDATE
        clause only touches last_activity_at + name, so source/infocasas_ref
        seeded in VALUES apply ONLY to freshly-inserted rows.
        """
        # Detect the vista_publica handshake on the inbound text. WhatsApp-only
        # in practice (the CTA lives on the web → WA deep link), but kept
        # platform-agnostic here; _resolve_mode (check 0) gates non-WA modes.
        if platform == "whatsapp" and _is_vista_publica_handshake(text_msg):
            new_source = "vista_publica"
            new_ref = _extract_prop_code(text_msg)
        else:
            new_source = "whatsapp" if platform != "telegram" else "telegram"
            new_ref = None

        if platform == "telegram":
            sql = text(
                "INSERT INTO contacts "
                "(name, source, source_id, status, first_message, "
                " last_activity_at, created_at) "
                "VALUES (:name, 'telegram', :user_id, 'new', :text, "
                " NOW(), NOW()) "
                "ON CONFLICT (source, source_id) DO UPDATE SET "
                " last_activity_at = NOW(), "
                " name = COALESCE(NULLIF(contacts.name, ''), EXCLUDED.name) "
                "RETURNING id, name, phone, status, source, source_id, baja_at, infocasas_ref, agent_user_id"
            )
            params = {
                "name": user_name,
                "user_id": user_id,
                "text": text_msg,
            }
        else:
            # WhatsApp: lookup / upsert by phone. source + infocasas_ref are
            # parameterized so a NEW handshake row lands as vista_publica with
            # the prop code; the DO UPDATE clause leaves an EXISTING row's
            # source/infocasas_ref untouched.
            sql = text(
                "INSERT INTO contacts "
                "(name, phone, phone_normalized, source, status, "
                " first_message, infocasas_ref, last_activity_at, created_at) "
                "VALUES (:name, :phone, :phone, :source, 'new', :text, "
                " :infocasas_ref, NOW(), NOW()) "
                "ON CONFLICT (phone) WHERE phone IS NOT NULL "
                "DO UPDATE SET "
                " last_activity_at = NOW(), "
                " name = COALESCE(NULLIF(contacts.name, ''), EXCLUDED.name) "
                "RETURNING id, name, phone, status, source, source_id, baja_at, infocasas_ref, agent_user_id"
            )
            params = {
                "name": user_name,
                "phone": user_id,
                "text": text_msg,
                "source": new_source,
                "infocasas_ref": new_ref,
            }

        result = await session.execute(sql, params)
        row = result.first()

        is_baja = (
            row.status == "discarded" or row.baja_at is not None
        )

        return ContactInfo(
            id=row.id,
            name=row.name or "",
            status=row.status,
            is_baja=is_baja,
            platform=platform,
            phone=row.phone,
            source_id=row.source_id,
            source=row.source,
            infocasas_ref=row.infocasas_ref,
            agent_user_id=row.agent_user_id,
        )

    # ------------------------------------------------------------------
    # Origin context (M6.3 Plan 123-05 — BOT-06 directo-IC greeting)
    # ------------------------------------------------------------------

    # `build_origin_context` y `_build_indirecto_note` se fueron con el bot:
    # armaban el contexto con el que el bot saludaba nombrando la propiedad que
    # el cliente había consultado. Sin bot no hay saludo que armar, y sin
    # catálogo no hay propiedad que nombrar.

    async def get_or_create_conversation(
        self,
        session: "AsyncSession",
        contact_id: int,
        platform: str,
        chat_id: str,
    ) -> ConversationInfo:
        """Upsert a conversation with UNIQUE(contact_id, platform, platform_chat_id).

        Creates a new conversation if none exists, otherwise updates
        the updated_at timestamp and returns the existing one.
        """
        sql = text(
            "INSERT INTO conversations "
            "(contact_id, platform, platform_chat_id, channel, status, "
            " is_bot_active, is_open, message_count, search_context, "
            " created_at, updated_at) "
            "VALUES (:contact_id, :platform, :chat_id, :platform, 'active', "
            " true, true, 0, '{}', NOW(), NOW()) "
            "ON CONFLICT (contact_id, platform, platform_chat_id) "
            "DO UPDATE SET updated_at = NOW() "
            "RETURNING id, contact_id, platform, platform_chat_id, "
            " is_bot_active, is_open, search_context, message_count, "
            " last_human_reply_at"
        )
        params = {
            "contact_id": contact_id,
            "platform": platform,
            "chat_id": chat_id,
        }

        result = await session.execute(sql, params)
        row = result.first()

        return ConversationInfo(
            id=row.id,
            contact_id=row.contact_id,
            platform=row.platform,
            chat_id=row.platform_chat_id,
            is_bot_active=bool(row.is_bot_active),
            is_open=bool(row.is_open),
            search_context=row.search_context or {},
            message_count=row.message_count or 0,
            last_human_reply_at=row.last_human_reply_at,
        )

    # ------------------------------------------------------------------
    # Message history
    # ------------------------------------------------------------------

    # Patterns that indicate contaminated bot responses (Gemini tool
    # leakage or hallucinated search results from non-tool-use turns).
    _CONTAMINATION_PATTERNS = (
        "Herramienta a usar",
        "search_properties",
        "get_property_detail",
        "register_lead",
        "`search",
        "tool_use",
    )

    async def get_history(
        self,
        session: "AsyncSession",
        conversation_id: int,
        limit: int = 12,
    ) -> list[HistoryMessage]:
        """Return last N messages in chronological order (oldest first).

        Queries in DESC order and reverses to achieve chronological output.
        Filters out contaminated bot messages that leak tool names or
        describe tools in plaintext (legacy Gemini fallback artefacts).
        """
        # Fetch extra rows to compensate for filtered-out messages
        fetch_limit = limit + 10
        sql = text(
            "SELECT direction, sender_type, body, properties_shown "
            "FROM messages "
            "WHERE conversation_id = :conv_id "
            "ORDER BY created_at DESC "
            "LIMIT :limit"
        )
        params = {"conv_id": conversation_id, "limit": fetch_limit}

        result = await session.execute(sql, params)
        rows = result.fetchall()

        # Reverse to chronological order (oldest first)
        messages: list[HistoryMessage] = []
        for row in reversed(rows):
            # Skip contaminated bot messages
            if row.sender_type == "bot" and row.body and self._is_contaminated(row.body):
                logger.debug(
                    "Filtered contaminated bot message from history (conv=%d)",
                    conversation_id,
                )
                continue
            messages.append(HistoryMessage(
                direction=row.direction,
                sender_type=row.sender_type,
                body=row.body,
                properties_shown=row.properties_shown,
            ))

        # Cap at requested limit after filtering
        if len(messages) > limit:
            messages = messages[-limit:]

        return messages

    @classmethod
    def _is_contaminated(cls, body: str) -> bool:
        """Check if a bot message contains tool-name leakage."""
        return any(pat in body for pat in cls._CONTAMINATION_PATTERNS)

    # ------------------------------------------------------------------
    # Search context
    # ------------------------------------------------------------------

    async def get_search_context(
        self,
        session: "AsyncSession",
        conversation_id: int,
    ) -> ConversationState:
        """Load the search_context JSONB as a typed ConversationState."""
        sql = text(
            "SELECT search_context FROM conversations WHERE id = :conv_id"
        )
        result = await session.execute(sql, {"conv_id": conversation_id})
        row = result.first()

        data = row.search_context if row else None
        return ConversationState.from_jsonb(data)

    async def update_search_context(
        self,
        session: "AsyncSession",
        conversation_id: int,
        state: ConversationState,
    ) -> None:
        """Persist a ConversationState back to the JSONB column.

        Caps shown_properties to the last 20 via to_jsonb().
        """
        context_dict = state.to_jsonb()

        sql = text(
            "UPDATE conversations "
            "SET search_context = CAST(:context AS jsonb), updated_at = NOW() "
            "WHERE id = :conv_id"
        )
        await session.execute(sql, {
            "context": json.dumps(context_dict),
            "conv_id": conversation_id,
        })

    # ------------------------------------------------------------------
    # Message recording
    # ------------------------------------------------------------------

    async def save_inbound_message(
        self,
        session: "AsyncSession",
        conversation_id: int,
        contact_id: int,
        body: str,
        external_id: str | None = None,
    ) -> int:
        """Insert an inbound message, idempotent on external_id.

        Uses ON CONFLICT (external_id) DO NOTHING. If the row already
        exists (duplicate), returns the existing message ID.
        Also increments conversation.message_count and updates
        last_message_at.
        """
        if external_id:
            insert_sql = text(
                "INSERT INTO messages "
                "(conversation_id, contact_id, direction, sender_type, "
                " body, external_id, created_at) "
                "VALUES (:conv_id, :contact_id, 'inbound', 'contact', "
                " :body, :ext_id, NOW()) "
                "ON CONFLICT (external_id) "
                "WHERE external_id IS NOT NULL AND external_id != '' "
                "DO NOTHING "
                "RETURNING id"
            )
        else:
            insert_sql = text(
                "INSERT INTO messages "
                "(conversation_id, contact_id, direction, sender_type, "
                " body, external_id, created_at) "
                "VALUES (:conv_id, :contact_id, 'inbound', 'contact', "
                " :body, :ext_id, NOW()) "
                "RETURNING id"
            )

        params = {
            "conv_id": conversation_id,
            "contact_id": contact_id,
            "body": body,
            "ext_id": external_id,
        }

        result = await session.execute(insert_sql, params)
        row = result.first()
        inserted = row is not None

        if row is None and external_id:
            # Conflict: fetch existing message ID
            select_sql = text(
                "SELECT id FROM messages WHERE external_id = :ext_id"
            )
            result = await session.execute(select_sql, {"ext_id": external_id})
            row = result.first()

        if row is None:
            logger.error(
                "save_inbound_message: INSERT and fallback SELECT both returned None "
                "(conv_id=%s, ext_id=%s)",
                conversation_id,
                external_id,
            )
            raise ValueError(
                f"Failed to persist inbound message for conversation {conversation_id}"
            )

        msg_id = row.id

        # Los contadores se mueven SOLO cuando la fila entro de verdad. Antes
        # esto corria tambien en el camino del ON CONFLICT, y no molestaba
        # porque el guard de idempotencia en memoria cortaba primero. Ahora el
        # guardado es lo PRIMERO del webhook, asi que un reintento de Twilio con
        # el mismo MessageSid llega hasta aca: sin este `if`, un redelivery
        # inflaba `message_count` y pisaba `last_user_message_at`.
        if not inserted:
            return msg_id

        # Update conversation counters
        update_sql = text(
            "UPDATE conversations "
            "SET message_count = message_count + 1, "
            "    last_message_at = NOW() "
            "WHERE id = :conv_id"
        )
        await session.execute(update_sql, {"conv_id": conversation_id})

        # Update last_user_message_at on contact
        contact_update_sql = text(
            "UPDATE contacts SET last_user_message_at = NOW() WHERE id = :contact_id"
        )
        await session.execute(contact_update_sql, {"contact_id": contact_id})

        return msg_id

    async def save_outbound_message(
        self,
        session: "AsyncSession",
        conversation_id: int,
        contact_id: int,
        body: str,
        intent: str,
        ai_model: str = "",
        ai_tokens_in: int = 0,
        ai_tokens_out: int = 0,
        ai_latency_ms: int = 0,
        properties_shown: list[int] | None = None,
        tool_iterations: int | None = None,
    ) -> int:
        """Insert an outbound (bot) message.

        Records intent, AI model, token usage, latency, properties shown,
        and tool_iterations (number of Claude tool-use cycles for this turn).
        """
        sql = text(
            "INSERT INTO messages "
            "(conversation_id, contact_id, direction, sender_type, "
            " body, intent, ai_model, ai_tokens_in, ai_tokens_out, "
            " ai_latency_ms, properties_shown, tool_iterations, created_at) "
            "VALUES (:conv_id, :contact_id, 'outbound', 'bot', "
            " :body, :intent, :ai_model, :ai_tokens_in, :ai_tokens_out, "
            " :ai_latency_ms, :properties_shown, :tool_iterations, NOW()) "
            "RETURNING id"
        )
        params = {
            "conv_id": conversation_id,
            "contact_id": contact_id,
            "body": body,
            "intent": intent,
            "ai_model": ai_model,
            "ai_tokens_in": ai_tokens_in,
            "ai_tokens_out": ai_tokens_out,
            "ai_latency_ms": ai_latency_ms,
            "properties_shown": properties_shown,
            "tool_iterations": tool_iterations,
        }

        result = await session.execute(sql, params)
        row = result.first()

        if row is None:
            logger.error(
                "save_outbound_message: INSERT RETURNING returned None "
                "(conv_id=%s, intent=%s)",
                conversation_id,
                intent,
            )
            raise ValueError(
                f"Failed to persist outbound message for conversation {conversation_id}"
            )

        return row.id

    # ------------------------------------------------------------------
    # Human cooldown
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Pending alternatives TTL helpers (Fase D)
    # ------------------------------------------------------------------

    # Las "alternativas pendientes" eran estado del tool-loop: el bot ofrecía
    # opciones y esperaba que el cliente eligiera una. Se fueron con él.

    def check_human_cooldown(
        self,
        last_human_reply_at: datetime | None,
        cooldown_minutes: int = HUMAN_COOLDOWN_MINUTES,
    ) -> bool:
        """Return True if a human replied within cooldown_minutes.

        When a human agent takes over a conversation, the bot should
        stay silent for cooldown_minutes (default 30) to avoid
        interfering.
        """
        if last_human_reply_at is None:
            return False
        elapsed = datetime.now(timezone.utc) - last_human_reply_at
        return elapsed < timedelta(minutes=cooldown_minutes)


# ----------------------------------------------------------------------
# El entrante se guarda ANTES de procesarlo
# ----------------------------------------------------------------------

async def persist_inbound(
    session: "AsyncSession",
    request: BotRequest,
) -> int:
    """Resolver contacto + conversacion y guardar el mensaje entrante.

    Es el PRIMER paso del webhook: antes de armar el grafo de dependencias y
    antes de cualquier compuerta. El invariante que fija es uno solo:

        un mensaje entrante valido se persiste pase lo que pase despues.

    Que el bot no conteste es una decision del producto —`bot_enabled`,
    `whatsapp_mode='manual'`, `is_bot_active`, el cooldown humano—; que el
    mensaje no exista es perdida de datos. Hasta el 2026-08-24 el guardado
    estaba en el paso 4 del orquestador, DEBAJO de las cuatro compuertas y
    debajo del armado del grafo: una `GEMINI_API_KEY` vacia se llevaba puesta
    la consulta del cliente sin dejar rastro, y `reply_service` apaga
    `is_bot_active` en cada respuesta manual del panel, asi que despues de la
    primera respuesta de un asesor todo mensaje siguiente de ese cliente
    desaparecia de la base.

    Devuelve el id del mensaje. Idempotente por `external_id`.
    """
    manager = ConversationManager()
    contact = await manager.resolve_contact(
        session,
        request.platform,
        request.user_id,
        request.user_name,
        request.text,
    )
    conversation = await manager.get_or_create_conversation(
        session, contact.id, request.platform, request.chat_id,
    )
    return await manager.save_inbound_message(
        session,
        conversation.id,
        contact.id,
        request.text or "",
        request.external_id,
    )
