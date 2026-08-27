"""AI dispatch with Claude → Gemini fallback (M4 Fase 6.D).

Wraps the full AI-provider decision tree that used to live inline in
``Orchestrator.handle_message``:

1. If the circuit breaker is open, skip Claude entirely and call Gemini.
2. Otherwise, run the Claude tool-use loop.
3. If Claude raises an Anthropic SDK error, trip the breaker and fall
   back to Gemini. Non-SDK exceptions propagate to the outer handler
   (``safe_handle``) — they should not trip the breaker.
4. If Gemini also fails, alert the admin and return a graceful
   ``ai_dual_fail`` ``BotResponse`` to the user after persisting the
   outbound message.

Return value: ``AIOutcome`` for normal continuation (orchestrator
proceeds with post-processing), or a ``BotResponse`` for the dual-fail
terminal case (orchestrator returns it directly to the channel).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.bot.ai.claude_client import is_anthropic_api_error
from app.bot.ai.gemini_fallback import call_gemini
from app.bot.ai.prompts import get_ai_dual_fail_text
from app.bot.ai.tool_use_loop import run_tool_use_loop
from app.bot.ai.types import AIResponse
from app.bot.core.types import BotResponse

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.bot.ai.circuit_breaker import CircuitBreaker
    from app.bot.ai.claude_client import ClaudeClient
    from app.bot.ai.gemini_client import GeminiClient
    from app.bot.core.conversation import ConversationManager
    from app.bot.core.tool_executor import ToolExecutor
    from app.bot.core.types import (
        ContactInfo,
        ConversationInfo,
        ConversationState,
        HistoryMessage,
    )

logger = logging.getLogger(__name__)


@dataclass
class AIOutcome:
    """Normal-case result of ``run_ai_with_fallback``.

    Mirrors the 9-tuple that ``run_tool_use_loop`` used to return to
    ``handle_message``, plus a ``fallback_used`` flag indicating whether
    Gemini took over. Fields default to empty so the Gemini-only branch
    (circuit breaker open OR Claude error recovered) can build a partial
    outcome without having to fill tool-loop-specific values.
    """
    ai_response: AIResponse
    properties_collected: list[dict] = field(default_factory=list)
    all_ids_collected: list[int] = field(default_factory=list)
    is_lead: bool = False
    is_detail: bool = False
    is_opt_out: bool = False
    lead_motivo: str = ""
    events_to_record: list[dict] = field(default_factory=list)
    tool_iterations: int = 0
    fallback_used: bool = False


async def run_ai_with_fallback(
    *,
    claude_client: "ClaudeClient",
    gemini_client: "GeminiClient",
    circuit_breaker: "CircuitBreaker",
    tool_executor: "ToolExecutor",
    conversation_manager: "ConversationManager",
    session: "AsyncSession",
    messages: list[dict],
    history: "list[HistoryMessage]",
    search_context: "ConversationState",
    user_text: str,
    system_prompt: str,
    gemini_system_prompt: str,
    tools: list,
    url_context: str,
    contact: "ContactInfo",
    conversation: "ConversationInfo",
) -> "AIOutcome | BotResponse":
    """Drive the Claude tool-use loop, falling back to Gemini when needed."""
    if circuit_breaker.is_open:
        logger.info(
            "Circuit breaker OPEN — using Gemini fallback (state=%s)",
            circuit_breaker.state.value,
        )
        ai_response = await call_gemini(
            gemini_client, gemini_system_prompt, history, user_text,
        )
        return AIOutcome(ai_response=ai_response, fallback_used=True)

    try:
        result = await run_tool_use_loop(
            claude_client,
            tool_executor,
            circuit_breaker,
            messages,
            session,
            search_context,
            system_prompt,
            tools,
            url_context=url_context,
        )
    except Exception as exc:
        if not is_anthropic_api_error(exc):
            logger.exception(
                "Tool execution failed (not Claude API) — propagating "
                "without tripping circuit breaker"
            )
            raise
        logger.exception("Claude failed, falling back to Gemini")
        circuit_breaker.record_failure()
        try:
            ai_response = await call_gemini(
                gemini_client, gemini_system_prompt, history, user_text,
            )
        except Exception:
            return await _handle_dual_fail(
                session, contact, conversation, conversation_manager,
            )
        return AIOutcome(ai_response=ai_response, fallback_used=True)

    return AIOutcome(
        ai_response=result.response,
        properties_collected=list(result.properties),
        all_ids_collected=list(result.all_ids),
        is_lead=result.is_lead,
        is_detail=result.is_detail,
        is_opt_out=result.is_opt_out,
        lead_motivo=result.lead_motivo,
        events_to_record=list(result.events),
        tool_iterations=result.iterations,
        fallback_used=False,
    )


async def _handle_dual_fail(
    session: "AsyncSession",
    contact: "ContactInfo",
    conversation: "ConversationInfo",
    conversation_manager: "ConversationManager",
) -> BotResponse:
    """Both providers failed — alert admin + persist graceful fallback."""
    logger.exception("Both Claude and Gemini failed")
    try:
        from app.bot.services.admin_notifier import get_admin_notifier
        notifier = get_admin_notifier()
        await notifier.notify(
            "<b>ALERTA: AMBOS providers AI fallaron</b>\n"
            "Claude y Gemini fallaron en el mismo request.\n"
            "Bot no puede responder busquedas."
        )
    except Exception:
        pass

    fallback_text = await get_ai_dual_fail_text(session)
    fallback_response = BotResponse(text=fallback_text, intent="ai_dual_fail")
    try:
        await conversation_manager.save_outbound_message(
            session, conversation.id, contact.id,
            fallback_response.text, fallback_response.intent,
        )
    except Exception:
        logger.warning(
            "Failed to save ai_dual_fail outbound message for contact=%d",
            contact.id, exc_info=True,
        )
    return fallback_response
