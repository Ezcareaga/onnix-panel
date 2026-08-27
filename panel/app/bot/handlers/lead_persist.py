"""Post-AI lead persistence (M4 Fase 6.B).

Runs all the side-effects that follow a successful ``register_lead``
tool call during the Claude loop:

- Advance ``contacts.status`` to ``interested`` (guarded by old status).
- Link ``contacts.property_id`` to the last viewed property if set.
- Write a ``lead_registered`` event to ``lead_events``.
- Kick off the lead profiler (Claude summarizer → ``contacts.preferences``).
- Best-effort admin notification via ``notify_new_lead``.

All branches swallow their own errors so the lead flow never breaks —
the registration must land even if the profiler or the notifier fails.

Extracted from ``Orchestrator.handle_message`` (was an inline 90-line
block between the opt-out and events-recording sections).
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from sqlalchemy import text as sa_text

from app.bot.ai.prompts import SUMMARIZER_PROMPT

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.bot.ai.claude_client import ClaudeClient
    from app.bot.core.types import (
        BotRequest,
        ContactInfo,
        ConversationState,
        HistoryMessage,
    )

logger = logging.getLogger(__name__)

# M6.3 BOT-09 (resistente path): markers that a derivation captured only
# partial info. The recepcionista prompt's "Cliente resistente (path
# defensivo)" instructs the bot to derive ANYWAY (register_lead) when the
# client says "pasame al asesor ya" or stops answering after 2 cordial
# attempts — and "El sistema marca la captura parcial para el asesor".
# That marking is THIS code's job. A derivation is partial when the contact
# has no captured name at derivation, OR the motivo signals a partial /
# resistente capture.
_PARTIAL_MOTIVO_MARKERS = (
    "captura parcial",
    "parcial",
    "resistente",
    "pasame al asesor",
    "sin nombre",
    "no dio nombre",
)


# M6.3 BOT-14 (123-09): valid reasons for an automatic recepcionista→búsqueda
# switch. Mirrors the dataset's switch_directo reasons (raw/switch_criteria.json).
# A switch with no cleanly derivable reason falls back to "unspecified".
_SWITCH_REASONS = ("zona_distinta", "tipo_distinto", "precio_fuera_rango")

# Filter keys that signal each switch reason. Priority order (zona > tipo >
# precio) when a search carries more than one distinct dimension.
_ZONA_FILTER_KEYS = ("ciudad", "barrio", "zona", "zonas_cercanas_a")
_TIPO_FILTER_KEYS = ("tipo",)
_PRECIO_FILTER_KEYS = ("precio_min", "precio_max", "presupuesto_max", "presupuesto_min")


def derive_switch_reason(filters: dict | None) -> str:
    """Derive the switch reason enum from the search filters Claude passed.

    The recepcionista→búsqueda switch fires when Claude calls
    ``search_properties`` with concrete DISTINCT criteria. The criterion the
    client changed maps to a reason in ``_SWITCH_REASONS``:

      - zona keys (ciudad/barrio/zona) → ``zona_distinta``
      - tipo key                       → ``tipo_distinto``
      - precio keys                    → ``precio_fuera_rango``

    Priority is zona > tipo > precio when several dimensions are present (zona
    is the strongest distinct-search signal in the dataset). Returns
    ``"unspecified"`` when no recognizable criterion is present — never raises.
    """
    f = filters or {}
    if any((f.get(k) not in (None, "", [])) for k in _ZONA_FILTER_KEYS):
        return "zona_distinta"
    if any((f.get(k) not in (None, "", [])) for k in _TIPO_FILTER_KEYS):
        return "tipo_distinto"
    if any((f.get(k) not in (None, "", [])) for k in _PRECIO_FILTER_KEYS):
        return "precio_fuera_rango"
    return "unspecified"


async def persist_mode_switch(
    session: "AsyncSession",
    *,
    contact_id: int,
    conversation_id: int | None,
    reason: str,
) -> None:
    """Log a ``mode_switch`` lead_event (BOT-14) for an automatic switch.

    Records the recepcionista→búsqueda flip in ``lead_events.metadata`` so the
    panel/asesor can see the bot switched the client into search mode and why.
    No schema change — ``lead_events.metadata`` is free JSONB. Defensive: never
    raises (delegates to ``record_event``, which swallows its own errors). The
    raw trigger text is intentionally NOT stored (``trigger_text_redacted``).
    """
    from app.services.lead_event_service import record_event

    safe_reason = reason if reason in _SWITCH_REASONS else "unspecified"
    await record_event(
        session,
        contact_id=contact_id,
        conversation_id=conversation_id,
        event_type="mode_switch",
        trigger="switch_directo",
        metadata={
            "from": "recepcionista",
            "to": "busqueda",
            "reason": safe_reason,
            "trigger_text_redacted": True,
        },
    )


def _is_partial_capture(contact: "ContactInfo", lead_motivo: str) -> bool:
    """Return True when a ``register_lead`` derivation captured partial info.

    Resistente (defensive) derivations land here with no usable name and/or a
    motivo that flags the partial capture. Normal (complete) derivations — the
    client gave name + interest — return False and carry no flag.
    """
    if not (contact.name or "").strip():
        return True
    motivo_l = (lead_motivo or "").lower()
    return any(marker in motivo_l for marker in _PARTIAL_MOTIVO_MARKERS)


async def persist_lead_outcome(
    session: "AsyncSession",
    contact: "ContactInfo",
    request: "BotRequest",
    history: "list[HistoryMessage]",
    search_context: "ConversationState",
    lead_motivo: str,
    *,
    claude_client: "ClaudeClient",
) -> None:
    """Persist all side-effects of a ``register_lead`` tool call.

    Idempotent at the SQL level (UPDATEs are guarded by status / NULL
    checks). Safe to call multiple times for the same contact in the
    rare event that two loops register a lead concurrently.
    """
    # Advance contact to 'interested' — keeps legacy statuses too so
    # migration 018_v17_estados stragglers don't get stuck.
    await session.execute(sa_text(
        "UPDATE contacts SET status = 'interested' "
        "WHERE id = :id AND status IN ('new', 'contacted', 'bot_replied', 'agent_replied')"
    ), {"id": contact.id})

    property_id = search_context.last_detalle_id
    if property_id:
        await session.execute(sa_text(
            "UPDATE contacts SET property_id = :prop_id, updated_at = NOW() "
            "WHERE id = :id AND property_id IS NULL"
        ), {"id": contact.id, "prop_id": property_id})

    lead_metadata_dict: dict = {
        "motivo": lead_motivo,
        "property_id": property_id,
    }
    # BOT-09: flag resistente / partial captures so the asesor knows the lead
    # was derived defensively with incomplete info. greenfield JSONB — no
    # migration. Complete derivations omit the flag entirely.
    if _is_partial_capture(contact, lead_motivo):
        lead_metadata_dict["partial_capture"] = True
    lead_metadata = json.dumps(lead_metadata_dict, ensure_ascii=False)
    await session.execute(sa_text(
        "INSERT INTO lead_events "
        "(contact_id, event_type, old_status, new_status, triggered_by, metadata, created_at) "
        "VALUES (:id, 'lead_registered', :old_status, 'interested', 'bot', :metadata, NOW())"
    ), {"id": contact.id, "old_status": contact.status, "metadata": lead_metadata})

    await _run_lead_profiler(session, contact, history, claude_client)
    await _notify_admin_of_new_lead(
        contact, request, property_id, lead_motivo,
    )


async def _run_lead_profiler(
    session: "AsyncSession",
    contact: "ContactInfo",
    history: "list[HistoryMessage]",
    claude_client: "ClaudeClient",
) -> None:
    """Summarize the conversation into a preferences dict and persist it.

    Runs a dedicated Claude call with ``SUMMARIZER_PROMPT``. Failures are
    logged and recorded as a ``lead_profile_failed`` event — never raised.
    """
    profile_text = ""
    try:
        history_text = "\n".join(msg.format() for msg in history)
        profile_response = await claude_client.send_message(
            system=SUMMARIZER_PROMPT,
            messages=[{"role": "user", "content": history_text}],
            max_tokens=512,
            temperature=0.1,
            _tracking_source="bot.lead_profiler",
        )
        profile_text = (profile_response.text or "").strip()
        if profile_text.startswith("```"):
            profile_text = profile_text.split("\n", 1)[-1]
        if profile_text.endswith("```"):
            profile_text = profile_text.rsplit("```", 1)[0].strip()
        preferences = json.loads(profile_text)
        if not isinstance(preferences, dict):
            raise ValueError(
                f"profile not a dict: got {type(preferences).__name__}"
            )
        await session.execute(sa_text(
            "UPDATE contacts SET preferences = :prefs WHERE id = :id"
        ), {"id": contact.id, "prefs": json.dumps(preferences, ensure_ascii=False)})
        logger.info("Lead profile saved for contact=%d", contact.id)
    except Exception as exc:
        logger.warning(
            "Lead profiling failed for contact=%d — non-fatal, continuing",
            contact.id, exc_info=True,
        )
        try:
            await session.execute(sa_text(
                "INSERT INTO lead_events "
                "(contact_id, event_type, old_status, new_status, triggered_by, metadata, created_at) "
                "VALUES (:id, 'lead_profile_failed', NULL, NULL, 'bot', :meta, NOW())"
            ), {
                "id": contact.id,
                "meta": json.dumps({
                    "reason": str(exc)[:200],
                    "profile_text_snippet": profile_text[:200],
                }, ensure_ascii=False),
            })
        except Exception:
            logger.warning(
                "Failed to record lead_profile_failed event for contact=%d",
                contact.id,
            )


async def _notify_admin_of_new_lead(
    contact: "ContactInfo",
    request: "BotRequest",
    property_id: int | None,
    lead_motivo: str,
) -> None:
    """Best-effort Telegram notification to admin. Never raises."""
    try:
        from app.bot.services.admin_notifier import get_admin_notifier
        notifier = get_admin_notifier()
        await notifier.notify_new_lead(
            name=contact.name or "",
            phone=contact.phone or "",
            property_id=property_id,
            source=request.platform,
            motivo=lead_motivo,
        )
    except Exception:
        logger.warning(
            "Lead notification failed for contact=%d — non-fatal",
            contact.id, exc_info=True,
        )
