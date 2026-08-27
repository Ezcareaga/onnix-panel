"""Path-(b) deterministic name-ask state counter (POLISH-02 iter 2).

Pure, dependency-free module that computes — in CODE, NOT via the LLM — how
many prior BOT/outbound turns asked the contact for their name, and builds a
per-turn directive section injected into the recepcionista prompt.

Rationale (Phase 124.4): convs 168/206 are pure search-shoppers. The bot kept
re-asking the name; the narrative POLISH-02 path-(a) threshold did NOT fire
reliably, so register_lead never fired and status stayed bot_replied.
Path-(b) replaces the LLM-judged counting with a deterministic integer injected
per turn, paired with a HARD imperative at threshold >= 2.

Design contract:
- Both functions are pure: no DB, no I/O, no Claude. Idempotent on identical
  input → identical output on replay.
- count_name_ask_attempts counts prior BOT turns only (sender_type == "bot" OR
  direction == "outbound"), mirroring the conversation.py convention.
- Patterns are grounded in the REAL recepcionista name-ask phrasings from the
  prompts.py few-shots (Ej 1/2/3/5/7/8/9).
- This module is mode-agnostic: it does NOT reference SYSTEM_PROMPT_TEMPLATE
  and is NEVER invoked in busqueda mode (that guard lives in the orchestrator
  wiring, Plan 02). Import-cycle-safe: no orchestrator/conversation imports.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids import cycle
    from app.bot.core.types import HistoryMessage


# Accent fold: strip Spanish diacritics so phrasing matches regardless of
# whether the stored body carries accents ("cómo te llamás" == "como te llamas").
_ACCENT_MAP = str.maketrans("áéíóúüÁÉÍÓÚÜñÑ", "aeiouuAEIOUUnN")


def _normalize(text: str) -> str:
    """Lower-case and accent-fold a body for case/accent-insensitive matching."""
    return text.translate(_ACCENT_MAP).lower()


# Name-ask patterns, already normalized (lower-case, accent-folded) so they can
# be substring-tested against a _normalize()'d body. Grounded verbatim in the
# recepcionista few-shots:
#   "¿Con quién tengo el gusto?"            (Ej 1, 7, 9)
#   "¿Cómo te llamás …?"                    (Ej 2, 3)
#   "¿Tu nombre, para que te ubiquen?"      (Ej 5, 8)
#   "¿Tu nombre para que el asesor te ubique?" (Ej 7)
# Deliberately NOT a bare "quien" — ordinary prose ("¿Quién sabe?", "le paso
# tus datos a un asesor") must NOT match.
_NAME_ASK_PATTERNS = (
    "con quien tengo el gusto",
    "como te llamas",
    "con quien hablo",
    "tu nombre",
)


def count_name_ask_attempts(history: list[HistoryMessage] | None) -> int:
    """Count how many prior BOT turns asked the contact for their name.

    A turn is a BOT turn when ``sender_type == "bot"`` OR
    ``direction == "outbound"`` (mirrors the conversation.py convention:
    inbound/contact = user, everything else = bot/outbound). User turns are
    never counted, even if their body happens to contain a name-ask phrase.

    Each matching bot message counts exactly once, regardless of how many
    distinct patterns it matches. Empty/None history → 0; empty body → skipped.

    Pure and idempotent: identical input → identical output on replay.
    """
    if not history:
        return 0

    count = 0
    for msg in history:
        # Bot/outbound turn? (mirror conversation.py: inbound/contact = user)
        is_bot = msg.sender_type == "bot" or msg.direction == "outbound"
        if not is_bot:
            continue
        body = msg.body
        if not body:
            continue
        normalized = _normalize(body)
        if any(pattern in normalized for pattern in _NAME_ASK_PATTERNS):
            count += 1
    return count


def build_name_attempts_section(attempts: int) -> str:
    """Build the per-turn lead-state directive section for the prompt.

    - attempts <= 0  → "" (no injection; -1 defensive).
    - attempts == 1  → SOFT note: states the count, flags the name as DESEABLE /
      no obligatorio, does NOT force derivation (no register_lead / captura
      parcial / HARD imperative tokens).
    - attempts >= 2  → HARD imperative naming the exact count and ordering the
      model to STOP asking and call register_lead with partial capture.

    Pure and idempotent: identical input → identical output.
    """
    if attempts <= 0:
        return ""

    header = "## ESTADO DEL LEAD"

    if attempts == 1:
        return (
            f"{header}\n"
            f"ESTADO DETERMINISTICO: attempts_without_name={attempts}. "
            "Ya pediste el nombre 1 vez y el cliente aun no lo dio. "
            "El nombre es DESEABLE, NO obligatorio: podes volver a pedirlo una "
            "vez mas de forma natural, pero si el cliente prioriza ver opciones, "
            "segui ayudandolo sin forzar la identificacion."
        )

    # attempts >= 2 → HARD imperative.
    return (
        f"{header}\n"
        f"ESTADO DETERMINISTICO: attempts_without_name={attempts}. "
        f"YA pediste el nombre {attempts} veces y el cliente no lo dio. "
        "NO vuelvas a pedir el nombre. "
        "Llama register_lead AHORA con captura parcial "
        "(motivo = criterios + interes capturados, sin el nombre). "
        "El sistema marca la captura parcial para el asesor."
    )


# ---------------------------------------------------------------------------
# Iteración 3 — deterministic forced derivation (the guarantee moves to CODE)
#
# Phase 124.4 evidence: conv 168 received the HARD imperative on 4 consecutive
# turns and still re-asked the name (model non-compliance); conv 206 asks the
# name only once and loops on criteria-gathering, so the name-ask signal never
# reaches the threshold. The Gemini fallback runs no tools at all. The
# derivation guarantee therefore cannot live in the prompt: the orchestrator
# enforces it post-AI using the pure threshold below.
# ---------------------------------------------------------------------------

# >=2 explicit name-asks (conv-168 shape) OR >=3 full bot turns with the
# contact still unnamed (conv-206 criteria-loop shape).
_FORCED_NAME_ASK_THRESHOLD = 2
_FORCED_BOT_TURNS_THRESHOLD = 3

# Appended to the turn's reply when the code forces the derivation, so the
# user-visible text narrates what actually happened even if the model's prose
# was still gathering criteria. POLISH-05 appends the LEAD-{id} ref after it.
FORCED_DERIVATION_NOTE = (
    "Ya pasé tu consulta a un asesor de Onnix SA con los datos que "
    "me diste — te va a contactar por este medio."
)


def count_bot_turns(history: list[HistoryMessage] | None) -> int:
    """Count prior BOT/outbound turns with a non-empty body.

    Same bot-turn convention and purity contract as
    ``count_name_ask_attempts`` — but counts every bot message, not just
    name-asks. This is the engagement-without-progress signal that the
    name-ask counter structurally misses in the criteria-loop shape.
    """
    if not history:
        return 0
    return sum(
        1
        for msg in history
        if (msg.sender_type == "bot" or msg.direction == "outbound") and msg.body
    )


def forced_derivation_due(history: list[HistoryMessage] | None) -> bool:
    """Deterministic threshold for the code-level forced derivation.

    True when the bot has already asked the name >= 2 times, OR has had >= 3
    full turns while the contact is still unnamed (the unnamed guard lives in
    the orchestrator). Pure and idempotent on replay.
    """
    if not history:
        return False
    return (
        count_name_ask_attempts(history) >= _FORCED_NAME_ASK_THRESHOLD
        or count_bot_turns(history) >= _FORCED_BOT_TURNS_THRESHOLD
    )


def build_forced_lead_motivo(filtros: dict | None) -> str:
    """Build the deterministic register_lead motivo for a forced derivation.

    Summarizes whatever criteria the conversation captured (search_context
    filtros) so the asesor knows what the client wanted; never empty.
    """
    base = "Derivación automática (cliente sin nombre tras varios intentos)"
    if not filtros:
        return f"{base}. Sin criterios estructurados capturados."
    criteria = ", ".join(
        f"{key}={value}" for key, value in filtros.items() if value not in (None, "")
    )
    if not criteria:
        return f"{base}. Sin criterios estructurados capturados."
    return f"{base}. Criterios capturados: {criteria}."
