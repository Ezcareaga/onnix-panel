"""Gemini fallback wrapper (M4 Task 3.10).

Called by the orchestrator when Claude raises an API error and the circuit
breaker decides to fall back. Gemini doesn't support tool use here — it's
used as a "best effort text response" to avoid leaving the user in silence.

Extracted from ``Orchestrator._call_gemini``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.bot.ai.prompts import build_fecha_actual_line
from app.bot.ai.types import AIResponse
from app.bot.core.types import HistoryMessage

if TYPE_CHECKING:
    from app.bot.ai.gemini_client import GeminiClient


async def call_gemini(
    gemini: "GeminiClient",
    system_prompt: str,
    history: list[HistoryMessage],
    current_text: str,
) -> AIResponse:
    """Call Gemini with concatenated history as a single user_content block.

    Appends the current-date line (America/Asuncion) per request so the
    fallback reasons with the real date, same as the Claude path.
    """
    system_prompt = system_prompt + "\n\n" + build_fecha_actual_line()
    history_lines = [msg.format() for msg in history]
    user_content = ""
    if history_lines:
        user_content = "Historial:\n" + "\n".join(history_lines) + "\n\n"
    user_content += "Mensaje actual: " + current_text

    return await gemini.send_message(
        system=system_prompt,
        user_content=user_content,
    )
