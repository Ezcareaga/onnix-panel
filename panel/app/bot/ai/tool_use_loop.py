"""Claude tool-use loop driver (M4 Task 3.14).

Runs the Claude messages-API conversation loop: initial call → inspect
tool_calls → execute each tool via ``ToolExecutor`` → feed the results
back to Claude → repeat until ``stop_reason != "tool_use"`` or
``max_iterations`` is reached.

Mutates ``search_context`` (filtros / total_found / last_search_at /
busquedas_historicas / lead_registrado) as side-effects of the search,
register_lead, and opt-out tools. Accumulates properties, lead events,
and flags (is_lead, is_detail, is_opt_out) across iterations.

Extracted from ``Orchestrator._call_claude_with_tools``.

The return value is a ``NamedTuple`` so existing callers that unpack the
9 positional fields keep working while new callers can use attribute
access.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, NamedTuple

from app.bot.ai.prompt_builder import build_dynamic_prompt
from app.bot.ai.types import AIResponse

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.bot.ai.circuit_breaker import CircuitBreaker
    from app.bot.ai.claude_client import ClaudeClient
    from app.bot.core.tool_executor import ToolExecutor
    from app.bot.core.types import ConversationState

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 5


class ToolUseLoopResult(NamedTuple):
    """Outcome of a full Claude tool-use loop."""

    response: AIResponse
    properties: list[dict]
    all_ids: list[int]
    is_lead: bool
    is_detail: bool
    is_opt_out: bool
    lead_motivo: str
    events: list[dict]
    iterations: int


async def run_tool_use_loop(
    claude_client: "ClaudeClient",
    tool_executor: "ToolExecutor",
    circuit_breaker: "CircuitBreaker",
    messages: list[dict],
    session: "AsyncSession",
    search_context: "ConversationState",
    base_system_prompt: str,
    tools: list,
    url_context: str = "",
    max_iterations: int = MAX_TOOL_ITERATIONS,
) -> ToolUseLoopResult:
    """Drive the Claude tool-use loop.

    Mutates ``messages`` (appends assistant + user turns each iteration)
    and ``search_context`` (filtros, total_found, last_search_at,
    busquedas_historicas, lead_registrado) — matches legacy behaviour.

    Returns a ``ToolUseLoopResult`` with the final AIResponse, collected
    properties/ids, flags, events, and iteration count.
    """
    properties_collected: list[dict] = []
    all_ids_collected: list[int] = []
    is_lead = False
    is_detail = False
    is_opt_out = False
    lead_motivo: str = ""
    events_to_record: list[dict] = []

    # Build dynamic system prompt with search context and optional URL context
    system_prompt = build_dynamic_prompt(
        base_system_prompt, search_context, url_context=url_context,
    )

    # Initial Claude call
    response = await claude_client.send_message(
        system=system_prompt,
        messages=messages,
        tools=tools,
    )
    circuit_breaker.record_success()

    # Tool-use loop
    iterations = 0
    while response.stop_reason == "tool_use" and iterations < max_iterations:
        iterations += 1

        tool_results = []
        for tc in response.tool_calls:
            logger.info(
                "Tool call — {\"tool\": \"%s\", \"iteration\": %d, \"input_keys\": %s}",
                tc.name, iterations, list(tc.input.keys()),
            )
            result = await tool_executor.execute(
                tc, session, search_context,
            )
            tool_results.append(
                tool_executor.build_tool_result_message(tc, result),
            )

            # Collect properties from search results
            if "properties" in result:
                properties_collected.extend(result["properties"])
            if "all_ids" in result:
                all_ids_collected.extend(result["all_ids"])
                search_context.filtros = {**search_context.filtros, **tc.input}
                search_context.total_found = result.get("total_found", 0)
                search_context.last_search_at = datetime.now(timezone.utc).isoformat()
                # Surface relaxed_filters as transient attribute (same pattern
                # as _contact_id / _conversation_id) so the orchestrator can
                # propagate them to BotResponse.metadata for ResponseBuilder.
                # The intro carrying the relaxation explanation must bypass
                # the 150-char truncation; see response_builder.py.
                relaxed = result.get("relaxed_filters") or []
                search_context._last_relaxed_filters = list(relaxed) if relaxed else None
                # Append to search history for panel visibility
                search_context.busquedas_historicas.append({
                    "fecha": search_context.last_search_at,
                    "operacion": tc.input.get("operacion", ""),
                    "tipo": tc.input.get("tipo", ""),
                    "ciudad": tc.input.get("ciudad", ""),
                    "barrio": tc.input.get("barrio", ""),
                    "presupuesto_max": tc.input.get("precio_max"),
                    "moneda": tc.input.get("moneda", ""),
                    "resultados_encontrados": result.get("total_found", 0),
                })
                if len(search_context.busquedas_historicas) > 20:
                    search_context.busquedas_historicas = search_context.busquedas_historicas[-20:]
                events_to_record.append({
                    "event_type": "search",
                    "metadata": {
                        "filters": tc.input,
                        "total_found": result.get("total_found", 0),
                        "shown_ids": [p["id"] for p in result.get("properties", [])],
                    },
                })

            # Collect single property from detail
            if tc.name == "get_property_detail" and "error" not in result and "id" in result:
                properties_collected.append(result)
                is_detail = True
                events_to_record.append({
                    "event_type": "detail_view",
                    "metadata": {"property_id": result.get("id")},
                })

            # Detect lead
            if tc.name == "register_lead" and result.get("success"):
                is_lead = True
                lead_motivo = result.get("motivo", "")
                search_context.lead_registrado = True

            # Detect opt-out
            if tc.name == "process_opt_out" and result.get("success"):
                is_opt_out = True

        # Append assistant message (raw_content) + tool results
        messages.append({
            "role": "assistant",
            "content": response.raw_content,
        })
        messages.append({
            "role": "user",
            "content": tool_results,
        })

        # Next Claude call
        response = await claude_client.send_message(
            system=system_prompt,
            messages=messages,
            tools=tools,
        )
        circuit_breaker.record_success()

    return ToolUseLoopResult(
        response=response,
        properties=properties_collected,
        all_ids=all_ids_collected,
        is_lead=is_lead,
        is_detail=is_detail,
        is_opt_out=is_opt_out,
        lead_motivo=lead_motivo,
        events=events_to_record,
        iterations=iterations,
    )
