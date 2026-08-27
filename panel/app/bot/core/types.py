"""Core types for bot engine.

Shared dataclasses used across the bot orchestration layer.
These define the contracts between orchestrator, conversation manager,
response builder, and downstream channel adapters.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields as dataclass_fields
from datetime import datetime, timezone


@dataclass
class BotRequest:
    """Inbound message from any channel.

    Captures all data from a Telegram or WhatsApp incoming message
    needed by the orchestrator.
    """

    platform: str  # "telegram" or "whatsapp"
    chat_id: str   # TG chat_id or WA phone
    user_id: str   # TG user_id or WA phone
    user_name: str
    text: str | None = None
    external_id: str | None = None  # TG message_id or WA MessageSid
    callback_data: str | None = None  # Inline button callback


@dataclass
class BotResponse:
    """Outbound response to send via channel.

    Carries the full outbound payload: text, properties, intent,
    buttons, AI metadata, and error flag.
    """

    text: str
    intent: str
    properties: list[dict] = field(default_factory=list)
    shown_ids: list[int] = field(default_factory=list)
    pending_ids: list[int] = field(default_factory=list)
    buttons: list[dict] = field(default_factory=list)
    ai_model: str = ""
    ai_tokens_in: int = 0
    ai_tokens_out: int = 0
    is_error: bool = False
    is_lead: bool = False
    metadata: dict = field(default_factory=dict)


@dataclass
class ConversationState:
    """Typed wrapper around the search_context JSONB column.

    Mirrors the structure stored in conversations.search_context,
    providing type-safe access and serialization methods.
    """

    etapa: str = "inicio"
    filtros: dict = field(default_factory=dict)
    button_mapping: dict = field(default_factory=dict)
    current_page_ids: list[int] = field(default_factory=list)
    shown_properties: list[int] = field(default_factory=list)
    resultados_pendientes: list[int] = field(default_factory=list)
    ultima_actualizacion: str | None = None
    last_detalle_id: int | None = None
    last_ic_prop_id: int | None = None
    multi_barrio: bool = False
    barrios_pendientes: list = field(default_factory=list)
    filtros_originales: dict = field(default_factory=dict)
    alternatives_shown: bool = False
    busquedas_historicas: list = field(default_factory=list)
    search_shown_count: int = 0
    total_found: int = 0
    last_search_at: str | None = None
    lead_registrado: bool = False
    # Fase D — alternatives offered to client pending selection.
    # Each dict has shape: {id, label, count, filters, reason, callback_payload}
    pending_alternatives: list = field(default_factory=list)
    pending_alternatives_age: int = 0  # turns since alternatives were offered

    # M6.3 Plan 123-02 (BOT-03/BOT-04): per-chat mode override.
    # None = no explicit override (mode resolved by auto-detect / default).
    # 'recepcionista' | 'busqueda' = sticky override read/written via the
    # get_mode_override()/set_mode_override() helpers. Persisted to JSONB so the
    # override survives across turns. NOT stored in conversations.metadata
    # (that column does not exist).
    mode: str | None = None

    # Fase I (M5) — transient context for metric emission.
    # NOT serialized to/from JSONB — set by orchestrator each turn.
    _contact_id: int | None = field(default=None, repr=False, compare=False)
    _conversation_id: int | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_jsonb(cls, data: dict | None) -> ConversationState:
        """Build a ConversationState from a search_context JSONB dict.

        Handles None, empty dict, and unknown keys gracefully.
        """
        if not data:
            return cls()
        # Excluir campos transitorios (prefijo _) — nunca vienen del JSONB de DB,
        # pero defensive guard por si algún ambiente legacy los tiene.
        known = {f.name for f in dataclass_fields(cls) if not f.name.startswith("_")}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    def to_jsonb(self) -> dict:
        """Serialize to a dict suitable for JSONB storage.

        Caps shown_properties to the last 50 entries and sets
        ultima_actualizacion to the current ISO timestamp.
        """
        # Cap shown_properties at last 50
        capped = self.shown_properties[-50:] if len(self.shown_properties) > 50 else list(self.shown_properties)

        now = datetime.now(timezone.utc).isoformat()

        return {
            "etapa": self.etapa,
            "filtros": dict(self.filtros),
            "button_mapping": dict(self.button_mapping),
            "current_page_ids": list(self.current_page_ids),
            "shown_properties": capped,
            "resultados_pendientes": list(self.resultados_pendientes),
            "ultima_actualizacion": now,
            "last_detalle_id": self.last_detalle_id,
            "last_ic_prop_id": self.last_ic_prop_id,
            "multi_barrio": self.multi_barrio,
            "barrios_pendientes": list(self.barrios_pendientes),
            "filtros_originales": dict(self.filtros_originales),
            "alternatives_shown": self.alternatives_shown,
            "busquedas_historicas": list(self.busquedas_historicas),
            "search_shown_count": self.search_shown_count,
            "total_found": self.total_found,
            "last_search_at": self.last_search_at,
            "lead_registrado": self.lead_registrado,
            "pending_alternatives": list(self.pending_alternatives),
            "pending_alternatives_age": self.pending_alternatives_age,
            "mode": self.mode,
        }

    def merge_filters(self, new_filters: dict) -> None:
        """Merge new filters into existing filtros.

        Updates self.filtros with new_filters, removing keys with
        None values. The ``zonas_cercanas_a`` key is consumed
        (not persisted) since it triggers zone selection flow.
        """
        for key, value in new_filters.items():
            if value is None:
                self.filtros.pop(key, None)
            else:
                self.filtros[key] = value
        # Consume zonas_cercanas_a — don't persist
        self.filtros.pop("zonas_cercanas_a", None)

    # M6.3 Plan 123-02: per-chat mode override helpers (read/write the JSONB
    # 'mode' key). The override lives in search_context — NOT in a
    # conversations.metadata column (that column does not exist).
    def get_mode_override(self) -> str | None:
        """Return the sticky per-chat mode override, or None if unset."""
        return self.mode

    def set_mode_override(self, value: str | None) -> None:
        """Set (or clear with None) the sticky per-chat mode override."""
        self.mode = value


@dataclass
class HistoryMessage:
    """A single message from conversation history.

    Used to build the message array sent to the LLM for context.
    """

    direction: str  # "inbound" or "outbound"
    sender_type: str  # "contact", "bot", "agent"
    body: str
    properties_shown: list[int] | None = None

    def format(self) -> str:
        """Format for LLM history context.

        Returns 'Usuario: ...' for inbound/contact, 'Bot: ...' for
        outbound/bot, 'Agente: ...' for agent messages.
        """
        if self.sender_type == "agent":
            return f"Agente: {self.body}"
        if self.direction == "inbound":
            return f"Usuario: {self.body}"
        return f"Bot: {self.body}"


@dataclass
class ContactInfo:
    """Typed result from contact resolution.

    Returned by ConversationManager.resolve_contact() with all
    data needed for orchestration decisions (baja check, status).
    """

    id: int
    name: str
    status: str
    is_baja: bool = False
    platform: str = ""
    phone: str | None = None
    source_id: str | None = None
    # M6.3 Plan 123-02: contacts.source (e.g. 'vista_publica', 'infocasas',
    # 'whatsapp', 'telegram') — used by _resolve_mode check 2b auto-detect.
    source: str | None = None
    infocasas_ref: str | None = None
    agent_user_id: int | None = None  # Panel user who took this lead (FK → users.id)


@dataclass
class ConversationInfo:
    """Conversation-level metadata.

    Returned by ConversationManager.get_or_create_conversation()
    with the data needed by the orchestrator.
    """

    id: int
    contact_id: int
    platform: str
    chat_id: str
    is_bot_active: bool = True
    is_open: bool = True
    search_context: dict = field(default_factory=dict)
    message_count: int = 0
    last_human_reply_at: datetime | None = None


@dataclass
class PayloadMessage:
    """A single message in a channel payload.

    Represents one discrete message the channel adapter will send.
    May contain text, buttons, and/or a photo URL.
    """

    text: str
    photo_url: str | None = None
    buttons: list[dict] = field(default_factory=list)
    template_id: str | None = None


@dataclass
class ChannelPayload:
    """Channel-agnostic output from response builder.

    Contains a list of messages to send and the target channel.
    """

    messages: list[PayloadMessage] = field(default_factory=list)
    channel: str = ""
