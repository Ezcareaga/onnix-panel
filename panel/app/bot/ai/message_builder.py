"""Conversation history → Claude messages array + sanitization.

Extracted from orchestrator.py in M4 Task 3.4. Two functions:

- ``build_messages(history, current_text)`` — converts the stored
  ``HistoryMessage`` list into the ``[{"role": ..., "content": ...}]``
  format the Anthropic SDK expects. Merges consecutive same-role
  messages (Anthropic requires strict alternation).

- ``sanitize_bot_response(msg)`` — replaces raw property listing text
  with a short summary so Claude doesn't mimic listings instead of
  calling the ``search_properties`` tool.

See PLAN_M4_REFACTOR.md §Fase 3 Task 3.4.
"""
from __future__ import annotations

from app.bot.core.types import HistoryMessage


# Patterns in bot responses that indicate property listings which Claude
# should NOT see verbatim (it copies them instead of calling tools).
_LISTING_MARKERS = ("**1.", "**1 ", "📍", "dorm,", "dorm |", "m²", "USD/mes", "/mes")


def sanitize_bot_response(msg: HistoryMessage) -> str:
    """Replace property listing text with a short summary.

    When Claude sees full property listings in the history it learns to
    mimic them in plain text instead of calling tools. Replacing the
    listing with a terse note forces it to always invoke
    ``search_properties`` / ``get_property_detail``.
    """
    body = msg.body or ""

    # Explicit: properties_shown was recorded by the orchestrator
    if msg.properties_shown:
        n = len(msg.properties_shown)
        return f"[Usé la herramienta de búsqueda y mostré {n} propiedades con fotos al usuario]"

    # Heuristic: detect listing patterns even when properties_shown was
    # not set (e.g. hallucinated responses from earlier bug)
    if any(marker in body for marker in _LISTING_MARKERS):
        return "[Usé la herramienta de búsqueda y mostré propiedades con fotos al usuario]"

    return body


def build_messages(
    history: list[HistoryMessage],
    current_text: str,
) -> list[dict]:
    """Convert history + current message into Claude messages array.

    Merges consecutive same-role messages (Anthropic requires alternating
    roles). Bot responses containing property listings are replaced with
    a short summary so Claude is forced to use tools instead of copying
    previous results.
    """
    raw: list[dict] = []
    for msg in history:
        if msg.direction == "inbound" or msg.sender_type == "contact":
            raw.append({"role": "user", "content": msg.body})
        else:
            body = sanitize_bot_response(msg)
            raw.append({"role": "assistant", "content": body})

    # Append current user message
    raw.append({"role": "user", "content": current_text})

    # Merge consecutive same-role messages
    merged: list[dict] = []
    for msg_dict in raw:
        if merged and merged[-1]["role"] == msg_dict["role"]:
            merged[-1]["content"] += "\n" + msg_dict["content"]
        else:
            merged.append(dict(msg_dict))

    return merged
