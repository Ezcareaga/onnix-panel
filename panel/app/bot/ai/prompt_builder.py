"""Dynamic system prompt builder.

Extracted from orchestrator.py in M4 Task 3.4. Originally was the
``Orchestrator._build_dynamic_prompt`` method — now a module function
that receives ``base_system_prompt`` explicitly instead of reading from
``self._system_prompt``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.bot.ai.prompts import build_fecha_actual_line, build_search_context_section

if TYPE_CHECKING:
    from app.bot.core.types import ConversationState


def build_dynamic_prompt(
    base_system_prompt: str,
    search_context: "ConversationState",
    url_context: str = "",
) -> list[dict]:
    """Build a two-block system prompt list for the Anthropic SDK with prompt caching.

    The base system prompt (~5 200 tokens, static) is placed in block 0 with
    ``cache_control: ephemeral`` so Anthropic caches ``tools + system[0]``
    across turns.  The dynamic section (current date + search context +
    optional URL note) goes in block 1 without cache_control — it changes
    every turn and must never be cached.

    Block 1 always exists: it starts with the current-date line
    (America/Asuncion), computed per request so the LLM never reasons with
    hallucinated dates when scheduling visits ("este sábado").

    Parameters
    ----------
    base_system_prompt:
        The root system prompt (from ``get_system_prompt()``).
    search_context:
        Current conversation state.
    url_context:
        Optional system note about a property URL shared by the user.
        Appended after the context section so Claude sees it near the end.
    """
    blocks: list[dict] = [
        {"type": "text", "text": base_system_prompt, "cache_control": {"type": "ephemeral"}},
    ]
    section = build_search_context_section(search_context)
    dynamic_text = build_fecha_actual_line() + section
    if url_context:
        dynamic_text += "\n\n" + url_context.rstrip()
    blocks.append({"type": "text", "text": dynamic_text})
    return blocks
