"""Orchestrator — central message pipeline for the bot engine.

Receives a BotRequest, drives the Claude tool-use loop, falls back to
Gemini when the circuit breaker is open, manages conversation state, and
returns a BotResponse.

Plan 62-04: CORE-01 Orchestrator.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import text as sa_text

from app.bot.ai.ai_dispatch import run_ai_with_fallback
from app.bot.ai.message_builder import build_messages
from app.bot.ai.prompts import get_system_prompt, get_gemini_system_prompt, get_response_template, get_opt_out_text
from app.bot.ai.tools import get_tools
from app.bot.handlers.callback_resolver import translate_callback
from app.bot.handlers.dispatcher import try_shortcut_dispatch
from app.bot.handlers.event_persist import persist_opt_out, persist_turn_events
from app.bot.handlers.intent_detector import detect_intent_from_text
from app.bot.handlers.lead_persist import (
    persist_lead_outcome,
    persist_mode_switch,
    derive_switch_reason,
)
from app.bot.handlers.url_detection import extract_property_url_info, lookup_url_property
from app.bot.state.bot_gate import check_bot_active_locked, reactivate_from_agent_replied
from app.services.lead_event_service import record_event
from app.bot.core.conversation import _is_vista_publica_handshake
from app.bot.core.name_gate import (
    FORCED_DERIVATION_NOTE,
    build_forced_lead_motivo,
    build_name_attempts_section,
    count_bot_turns,
    count_name_ask_attempts,
    forced_derivation_due,
)
from app.bot.core.types import (
    BotRequest,
    BotResponse,
    ContactInfo,
    ConversationInfo,
    ConversationState,
    HistoryMessage,
)
from app.bot.observability.context import set_request_context
from app.repositories.bot_setting_repo import bot_setting_repo

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.bot.ai.claude_client import ClaudeClient
    from app.bot.ai.gemini_client import GeminiClient
    from app.bot.ai.circuit_breaker import CircuitBreaker
    from app.bot.core.conversation import ConversationManager
    from app.bot.core.response_builder import ResponseBuilder
    from app.bot.core.tool_executor import ToolExecutor

logger = logging.getLogger(__name__)


# M6.3 Plan 123-02 (BOT-03/BOT-04) + 123-10 (BOT-16): the vista_publica
# handshake detector (_is_vista_publica_handshake) lives in conversation.py
# next to the resolve_contact source branch + _extract_prop_code (shared
# regexes); imported above so _resolve_mode check 2a calls the live predicate.


class Orchestrator:
    """Central message pipeline: BotRequest -> AI loop -> BotResponse.

    Coordinates ConversationManager (state), ClaudeClient/GeminiClient (AI),
    CircuitBreaker (failover), ToolExecutor (tool dispatch), and
    ResponseBuilder (formatting).
    """

    def __init__(
        self,
        claude: "ClaudeClient" = None,
        gemini: "GeminiClient" = None,
        circuit_breaker: "CircuitBreaker" = None,
        search_service=None,
        conversation_manager: "ConversationManager" = None,
        response_builder: "ResponseBuilder" = None,
        tool_executor: "ToolExecutor" = None,
        geo_data_path: str | None = None,
    ) -> None:
        self._claude = claude
        self._gemini = gemini
        self._circuit_breaker = circuit_breaker
        self._search_service = search_service
        self._conversation_manager = conversation_manager
        self._response_builder = response_builder
        self._tool_executor = tool_executor
        # Buscador prompts (default mode). Kept as the per-turn fallback.
        self._system_prompt = get_system_prompt(geo_data_path)
        self._gemini_system_prompt = get_gemini_system_prompt(geo_data_path)
        # M6.3 Plan 123-04: pre-build the recepcionista prompts once — both
        # prompts are static, so we select per turn by the resolved mode
        # (see handle_message) instead of rebuilding each turn.
        self._recepcionista_system_prompt = get_system_prompt(
            geo_data_path, mode="recepcionista"
        )
        self._recepcionista_gemini_system_prompt = get_gemini_system_prompt(
            geo_data_path, mode="recepcionista"
        )
        self._tools = get_tools()

    # ------------------------------------------------------------------
    # Mode router (M6.3 Plan 123-02 — BOT-03/BOT-04 + D-2)
    # ------------------------------------------------------------------

    async def _resolve_mode(
        self,
        request: "BotRequest",
        contact: "ContactInfo",
        search_context: "ConversationState",
        session: "AsyncSession",
    ) -> str:
        """Resolve 'recepcionista' vs 'busqueda' for this turn.

        Exact priority chain (LOCKED):
          check 0 (D-2): platform != 'whatsapp' -> 'busqueda' (always).
                         This is the ONLY channel branch in the mode code.
          check 1:       search_context['mode'] explicit per-chat override.
          check 2:       auto-detect -> 'recepcionista' when any of:
                         2a vista_publica handshake text (stub until 123-10),
                         2b contact.source == 'vista_publica',
                         2c contact.infocasas_ref present.
          check 3:       bot_default_mode FRESH DB read each turn
                         (defensive default 'busqueda').
        """
        # check 0 (D-2): non-whatsapp channels NEVER enter recepcionista mode.
        if request.platform != "whatsapp":
            return "busqueda"

        # check 1: explicit per-chat override wins over auto-detect/default.
        override = search_context.get_mode_override()
        if override in ("recepcionista", "busqueda"):
            return override

        # check 2: auto-detect signals -> recepcionista.
        if (
            _is_vista_publica_handshake(request.text)        # 2a
            or contact.source == "vista_publica"             # 2b
            or contact.infocasas_ref                         # 2c
        ):
            return "recepcionista"

        # check 3: global default, read fresh from DB every turn (no cache).
        default = await bot_setting_repo.get_value(session, "bot_default_mode")
        return default if default in ("recepcionista", "busqueda") else "busqueda"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def handle_message(
        self,
        request: BotRequest,
        session: "AsyncSession",
    ) -> BotResponse | None:
        """Process an inbound message through the full pipeline.

        Returns BotResponse or None (when the bot should stay silent).
        """
        # Step 1: Resolve contact
        contact = await self._conversation_manager.resolve_contact(
            session, request.platform, request.user_id,
            request.user_name, request.text,
        )
        logger.info(
            "Contact resolved — {\"id\": %d, \"status\": \"%s\", \"platform\": \"%s\"}",
            contact.id, contact.status, contact.platform,
        )
        if contact.is_baja:
            logger.info("Contact id=%d is baja — returning opt_out", contact.id)
            return BotResponse(
                text=await get_opt_out_text(session),
                intent="opt_out",
            )

        # Step 2: Get or create conversation
        conversation = await self._conversation_manager.get_or_create_conversation(
            session, contact.id, request.platform, request.chat_id,
        )
        # Enrich request context with conversation_id so all logs in this turn are correlated.
        set_request_context(conversation_id=conversation.id)

        # Step 2b: Reactivate bot if client responds after agent interaction.
        # NOTE: reactivation must run BEFORE the gate — reads the fresh is_bot_active below
        if contact.status == "agent_replied":
            await reactivate_from_agent_replied(session, contact, conversation)

        if not await check_bot_active_locked(session, conversation.id):
            logger.info(
                "Bot inactive for conversation=%d contact=%d — silent",
                conversation.id, contact.id,
            )
            return None

        # Step 3: Check human cooldown
        if self._conversation_manager.check_human_cooldown(
            conversation.last_human_reply_at,
        ):
            logger.info(
                "Human cooldown active for conversation=%d — silent",
                conversation.id,
            )
            return None

        # Step 4: el entrante YA esta guardado.
        #
        # Hasta el 2026-08-24 `save_inbound_message` se llamaba aca, debajo de
        # la compuerta `is_bot_active` y del cooldown humano —y debajo del
        # armado del grafo, que corre antes de que el orquestador exista—, asi
        # que cualquiera de las tres hacia desaparecer la consulta del cliente.
        # Ahora lo guarda `persist_inbound` en el webhook, antes de todo. El
        # orquestador tiene un solo dueño del INSERT de entrantes, y ya no es el.

        # Step 5: Load history and search_context
        history = await self._conversation_manager.get_history(
            session, conversation.id,
        )
        search_context = await self._conversation_manager.get_search_context(
            session, conversation.id,
        )

        # Step 5 (M5 Fase I): Attach transient metric context so ToolExecutor
        # and ConversationManager helpers can emit lead_events without
        # changing method signatures. Never persisted to JSONB.
        search_context._contact_id = contact.id
        search_context._conversation_id = conversation.id

        # Step 5 (M5 Fase E): Tick pending alternatives TTL at the start of
        # each new turn. If the client has ignored the alternatives for 2 turns,
        # they are auto-cleared so stale alternatives don't bleed into future
        # searches. Must run exactly once per turn, before any processing.
        # Capture pre-tick state to detect expiry for abandoned metric (Fase I).
        _alts_before_tick = list(search_context.pending_alternatives)
        self._conversation_manager.tick_pending_alternatives_ttl(search_context)
        # --- Fase I: emit zero_results_abandoned if TTL expired this turn ---
        if (
            _alts_before_tick
            and not search_context.pending_alternatives  # cleared by tick
        ):
            await record_event(
                session,
                contact_id=contact.id,
                conversation_id=conversation.id,
                event_type="zero_results_abandoned",
                trigger="ttl_expired",
                metadata={
                    "alt_ids": [a.get("id") for a in _alts_before_tick],
                },
            )
        # --- end Fase I abandoned ---

        logger.debug(
            "Conversation loaded — {\"conv_id\": %d, \"history_len\": %d, \"search_context_etapa\": \"%s\"}",
            conversation.id, len(history), search_context.etapa or "none",
        )

        # Step 5b: Shortcut dispatcher — ver_mas / new_search / VER_DETALLES /
        # SI_MOSTRAME_REENVIADO / AHORA_NO_REENVIADO all resolve without a
        # Claude roundtrip. See app.bot.handlers.dispatcher for the matching
        # order and per-shortcut predicates.
        shortcut_response = await try_shortcut_dispatch(
            request, session, contact, conversation, search_context,
            search_service=self._search_service,
            conversation_manager=self._conversation_manager,
        )
        if shortcut_response is not None:
            return shortcut_response

        # Step 5c: Translate callback → natural language for AI processing
        if request.callback_data and request.callback_data != "ver_mas":
            translated = translate_callback(
                request.callback_data, search_context,
            )
            if translated:
                logger.info(
                    "Callback translated — {\"callback\": \"%s\", \"text\": \"%s\"}",
                    request.callback_data, translated,
                )
                request.text = translated
            else:
                logger.warning(
                    "Unknown callback '%s' — passing ButtonText to AI",
                    request.callback_data,
                )

        # Step 5d: URL detection — if the user shared a property link, look it
        # up in the DB and inject a context note so Claude never has to visit
        # the URL itself (which it cannot do).
        url_context = ""
        if request.text and not request.callback_data:
            url_info = extract_property_url_info(request.text)
            if url_info is not None:
                logger.info(
                    "Property URL detected — {\"source\": \"%s\", \"property_id\": \"%s\"}",
                    url_info["source"], url_info["property_id"],
                )
                url_context, url_property_id = await lookup_url_property(
                    url_info, session,
                )
                # Incidente remax: la prop resuelta por URL pisa el residuo de
                # last_detalle_id de búsquedas viejas (mismo mecanismo que el
                # preload del flujo directo IC) y se persiste de inmediato —
                # Step 9 solo escribe cuando hay properties/lead — para que un
                # register_lead de este turno o de turnos posteriores linkee
                # la propiedad correcta.
                if (
                    url_property_id is not None
                    and url_property_id != search_context.last_detalle_id
                ):
                    search_context.last_detalle_id = url_property_id
                    await self._conversation_manager.update_search_context(
                        session, conversation.id, search_context,
                    )
                    logger.info(
                        "URL property resolved — {\"db_property_id\": %d, \"last_detalle_id\": \"updated\"}",
                        url_property_id,
                    )

        # Step 5e (M6.3 Plan 123-02 — BOT-03/BOT-04 + D-2): resolve mode once
        # per turn. Drives which tools Claude sees (get_tools(mode) below).
        # D-2: telegram always resolves to 'busqueda' inside _resolve_mode.
        mode = await self._resolve_mode(request, contact, search_context, session)
        logger.info(
            "Mode resolved — {\"mode\": \"%s\", \"platform\": \"%s\"}",
            mode, request.platform,
        )
        # Step 5e (123-04): select the system prompt by the resolved mode.
        # Pre-built in __init__ (both prompts static) — pick per turn.
        if mode == "recepcionista":
            system_prompt = self._recepcionista_system_prompt
            gemini_system_prompt = self._recepcionista_gemini_system_prompt
        else:
            system_prompt = self._system_prompt
            gemini_system_prompt = self._gemini_system_prompt

        # Step 5e2 (M6.3 Plan 123-05 — BOT-06): surface the directo-IC origin
        # into the dynamic prompt section so Onnix greets with TÍTULO + CÓDIGO.
        # Data-only (no new tool): resolved from contact.infocasas_ref and
        # merged into url_context — the same dynamic-section channel the
        # buscador uses. Empty for non-directo turns (does not touch buscador).
        origin_context = await self._conversation_manager.build_origin_context(
            session, contact, mode,
        )
        # Guard: only merge a real, non-empty string. build_origin_context may
        # be a bare mock in unit tests that don't exercise the directo path.
        if isinstance(origin_context, str) and origin_context:
            url_context = (
                f"{url_context}\n\n{origin_context}".strip()
                if url_context else origin_context
            )

        # Step 5e3 (M6.3.1 path-b): deterministic name-ask counter. In recepcionista
        # mode for an unnamed contact, count how many prior bot turns already asked
        # the name (code-computed from history — idempotent on replay) and inject a
        # hard directive at threshold >=2 so Onnix derives the partial lead instead of
        # re-asking. Busqueda mode never computes this (zero buscador change).
        if mode == "recepcionista" and not (contact.name or "").strip():
            name_attempts = count_name_ask_attempts(history)
            attempts_section = build_name_attempts_section(name_attempts)
            if attempts_section:
                url_context = (
                    f"{url_context}\n\n{attempts_section}".strip()
                    if url_context else attempts_section
                )

        # Step 6: Build messages array
        messages = build_messages(history, request.text or "")

        # Step 7: Call AI — Claude tool-use loop with Gemini fallback.
        # Dual-fail returns a BotResponse directly (already persisted +
        # admin alerted). Otherwise we get an AIOutcome to post-process.
        ai_outcome = await run_ai_with_fallback(
            claude_client=self._claude,
            gemini_client=self._gemini,
            circuit_breaker=self._circuit_breaker,
            tool_executor=self._tool_executor,
            conversation_manager=self._conversation_manager,
            session=session,
            messages=messages,
            history=history,
            search_context=search_context,
            user_text=request.text or "",
            system_prompt=system_prompt,  # 123-04: per-turn mode drives the prompt
            gemini_system_prompt=gemini_system_prompt,
            tools=get_tools(mode),  # 123-02: per-turn mode drives the tool set
            url_context=url_context,
            contact=contact,
            conversation=conversation,
        )
        if isinstance(ai_outcome, BotResponse):
            return ai_outcome

        ai_response = ai_outcome.ai_response
        properties_collected = ai_outcome.properties_collected
        all_ids_collected = ai_outcome.all_ids_collected
        is_lead = ai_outcome.is_lead
        is_detail = ai_outcome.is_detail
        is_opt_out = ai_outcome.is_opt_out
        lead_motivo = ai_outcome.lead_motivo
        events_to_record = ai_outcome.events_to_record
        tool_iterations = ai_outcome.tool_iterations
        fallback_used = ai_outcome.fallback_used

        # Step 7b (M6.3.1 iter-3): deterministic forced derivation. The
        # recepcionista guarantee — derive an unnamed evasive contact within a
        # bounded number of turns — cannot be delegated to the LLM: the model
        # does not reliably honor the name_gate directive, and the Gemini
        # fallback runs no tools at all. When the turn ends without
        # register_lead and the pure threshold is met (>=2 name-asks OR >=3
        # bot turns with the contact still unnamed), the code registers the
        # lead itself; Step 8c + POLISH-05 persist status/events and append
        # the LEAD-{id} ref. lead_registrado persists in search_context JSONB
        # so the force fires at most once per conversation.
        forced_derivation = False
        if (
            mode == "recepcionista"
            and not is_lead
            and not is_opt_out
            and not (contact.name or "").strip()
            and not search_context.lead_registrado
            and forced_derivation_due(history)
        ):
            forced_derivation = True
            is_lead = True
            lead_motivo = build_forced_lead_motivo(search_context.filtros)
            search_context.lead_registrado = True
            logger.info(
                "Forced derivation — {\"contact_id\": %d, \"name_asks\": %d, \"bot_turns\": %d}",
                contact.id,
                count_name_ask_attempts(history),
                count_bot_turns(history),
            )

        intent = "conversacion"

        # Step 8: Determine intent from tool calls or response
        if is_opt_out:
            intent = "opt_out"
        elif is_lead:
            intent = "lead"
        elif is_detail and properties_collected:
            intent = "detalle"
        elif properties_collected:
            intent = "busqueda"
        elif ai_response.text:
            intent = detect_intent_from_text(ai_response.text)

        # Step 8b: Opt-out DB writes — see app.bot.handlers.event_persist.
        if is_opt_out:
            await persist_opt_out(session, contact)

        # Step 8c: Lead registration — status advance + event + profiler + notifier.
        # All side-effects live in app.bot.handlers.lead_persist.
        if is_lead:
            await persist_lead_outcome(
                session, contact, request, history, search_context, lead_motivo,
                claude_client=self._claude,
            )

        # Step 8d (M6.3 Plan 123-09 — BOT-14): automatic switch guard.
        # When the turn ran in recepcionista mode and Claude called
        # search_properties (decision A: concrete DISTINCT criteria), make the
        # switch STICKY by writing search_context['mode']='busqueda' so check 1
        # of _resolve_mode wins on every subsequent turn, and log a mode_switch
        # lead_event with a reason derived from the search filters. Decisions B
        # (preguntar) and C (no_switch) produce no search event → no flip, no log.
        if mode == "recepcionista":
            search_event = next(
                (e for e in events_to_record if e.get("event_type") == "search"),
                None,
            )
            if search_event is not None:
                search_context.set_mode_override("busqueda")
                # Persist the sticky flip immediately — Step 9 only writes when
                # properties/lead are present; a switch always has properties,
                # but write here too so the override never depends on that path.
                await self._conversation_manager.update_search_context(
                    session, conversation.id, search_context,
                )
                reason = derive_switch_reason(
                    (search_event.get("metadata") or {}).get("filters")
                )
                await persist_mode_switch(
                    session,
                    contact_id=contact.id,
                    conversation_id=conversation.id,
                    reason=reason,
                )
                logger.info(
                    "Mode switch — {\"from\": \"recepcionista\", \"to\": \"busqueda\", \"reason\": \"%s\"}",
                    reason,
                )

        # Step 8f: Saludo resets search_context so stale filters don't
        # bleed into future searches.
        # F-06: Only reset when there are NO pending results.  A user saying
        # "hola" mid-search should not lose their pending results — they can
        # still paginate.  If there are no pending results the reset is safe.
        if intent == "saludo" and not search_context.resultados_pendientes:
            search_context.etapa = "inicio"
            search_context.filtros = {}
            search_context.resultados_pendientes = []
            search_context.current_page_ids = []
            search_context.shown_properties = []
            search_context.search_shown_count = 0
            search_context.total_found = 0
            await self._conversation_manager.update_search_context(
                session, conversation.id, search_context,
            )

        # Step 8e: Lead events for search/detail + bot_interaction fallback.
        await persist_turn_events(
            session, contact, conversation, events_to_record,
            is_lead=is_lead, is_opt_out=is_opt_out,
        )

        # Step 9: Update search_context if we have search results
        shown_ids = [p["id"] for p in properties_collected[:2]] if properties_collected else []
        pending_ids = all_ids_collected[2:] if len(all_ids_collected) > 2 else []

        if properties_collected or is_lead:
            if is_detail and properties_collected:
                # Detail view: preserve pagination pool so user can
                # continue paginating after viewing a property detail.
                search_context.etapa = "detalle"
                search_context.last_detalle_id = properties_collected[0].get("id")
                pending_ids = search_context.resultados_pendientes
            else:
                search_context.current_page_ids = shown_ids
                search_context.shown_properties.extend(shown_ids)
                search_context.resultados_pendientes = pending_ids
                if properties_collected:
                    # Reset per-search counter on new search, set to first page count
                    search_context.search_shown_count = len(shown_ids)
                    search_context.etapa = "mostrando_resultados"
            await self._conversation_manager.update_search_context(
                session, conversation.id, search_context,
            )
        elif intent.startswith("busqueda_incompleta"):
            # Bug 6: persist etapa so the next turn's system prompt shows
            # Estado: busqueda_incompleta — Claude won't restart data-gathering
            # from scratch when the user provides the next missing field.
            search_context.etapa = "busqueda_incompleta"
            await self._conversation_manager.update_search_context(
                session, conversation.id, search_context,
            )

        # Step 10: Build BotResponse
        logger.info(
            "Decision — {\"intent\": \"%s\", \"model\": \"%s\", \"properties\": %d, \"is_lead\": %s, \"tool_iterations\": %d, \"fallback_used\": %s}",
            intent, ai_response.model,
            len(properties_collected), is_lead,
            tool_iterations, fallback_used,
        )
        response_text = ai_response.text or get_response_template(intent)
        # Iter-3: when the derivation was forced in code, the model's prose may
        # still be gathering criteria — append the deterministic note so the
        # user-visible text narrates the derivation that actually happened.
        if forced_derivation and response_text:
            response_text = f"{response_text}\n\n{FORCED_DERIVATION_NOTE}"
        # POLISH-05 (M6.3.1): guarantee a trackable LEAD_REF in the lead reply.
        # Claude's prose may omit the literal code, and RESPONSE_TEMPLATES["lead"]
        # has no {LEAD_REF}. When a lead is registered, ensure LEAD-{contact_id}
        # appears in the outbound text so asesores can track the lead.
        if is_lead:
            lead_ref = f"LEAD-{contact.id}"
            if lead_ref not in response_text:
                response_text = (
                    f"{response_text} (Código de seguimiento: {lead_ref})"
                    if response_text else lead_ref
                )
        relaxed_filters = getattr(search_context, "_last_relaxed_filters", None) or []
        bot_response = BotResponse(
            text=response_text,
            intent=intent,
            properties=properties_collected[:2],
            shown_ids=shown_ids,
            pending_ids=pending_ids,
            ai_model=ai_response.model,
            ai_tokens_in=ai_response.input_tokens,
            ai_tokens_out=ai_response.output_tokens,
            is_lead=is_lead,
            metadata={
                "tool_iterations": tool_iterations,
                "fallback_used": fallback_used,
                "llm_provider": "gemini" if fallback_used else "claude",
                "contact_id": contact.id,
                "ai_latency_ms": ai_response.latency_ms,
                "relaxed_filters": list(relaxed_filters),
            },
        )

        # Step 11: Save outbound message
        await self._conversation_manager.save_outbound_message(
            session, conversation.id, contact.id,
            bot_response.text, bot_response.intent,
            ai_model=ai_response.model,
            ai_tokens_in=ai_response.input_tokens,
            ai_tokens_out=ai_response.output_tokens,
            ai_latency_ms=ai_response.latency_ms,
            properties_shown=bot_response.shown_ids or None,
            tool_iterations=tool_iterations,
        )

        # Step 12: Auto-advance contact status
        if not is_opt_out and contact.status in ("new", "no_response"):
            await session.execute(sa_text(
                "UPDATE contacts SET status = 'bot_replied' "
                "WHERE id = :id AND status IN ('new', 'no_response')"
            ), {"id": contact.id})

        return bot_response
