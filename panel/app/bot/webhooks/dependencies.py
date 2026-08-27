"""Bot dependency graph — lazy singleton construction.

Builds the full bot pipeline (AI clients, search, orchestrator,
middleware, handlers) on first access. All components are wired
together here so the webhook endpoints get fully-functional
MessageHandler instances.

Plan 66-03: Task 1 — Dependency Injection.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.bot.ai.circuit_breaker import CircuitBreaker
from app.bot.ai.claude_client import ClaudeClient
from app.bot.ai.gemini_client import GeminiClient
from app.bot.services.admin_notifier import get_admin_notifier
from app.bot.channels.telegram import TelegramSender
from app.bot.channels.whatsapp import WhatsAppSender
from app.bot.config import bot_settings
from app.bot.core.conversation import ConversationManager
from app.bot.core.orchestrator import Orchestrator
from app.bot.core.response_builder import ResponseBuilder
from app.bot.core.tool_executor import ToolExecutor
from app.bot.handlers.message_handler import MessageHandler
from app.bot.middleware.idempotency import IdempotencyGuard
from app.bot.middleware.injection_guard import InjectionGuard
from app.bot.middleware.rate_limiter import RateLimiter
from app.bot.search.alternatives import AlternativesBuilder
from app.bot.search.search_service import SearchService
from app.repositories.bot_setting_repo import bot_setting_repo

logger = logging.getLogger(__name__)


@dataclass
class BotDependencies:
    """Container for the fully-wired bot pipeline components."""

    tg_handler: MessageHandler
    wa_handler: MessageHandler


# Module-level singleton
_bot_deps: BotDependencies | None = None


def build_bot_dependencies() -> BotDependencies:
    """Construct the full dependency graph from config.

    Creates all components bottom-up:
    1. AI clients (Claude, Gemini) + circuit breaker
    2. Search pipeline (SearchService with Gemini embeddings)
    3. Core (ConversationManager, ResponseBuilder, ToolExecutor, Orchestrator)
    4. Middleware (RateLimiter, IdempotencyGuard — shared across channels)
    5. Channel senders (Telegram, WhatsApp)
    6. MessageHandlers (one per channel, same orchestrator + middleware)
    """
    logger.info("Building bot dependency graph...")

    # -- 1. AI clients --
    claude = ClaudeClient(
        api_key=bot_settings.ANTHROPIC_API_KEY,
        model=bot_settings.CLAUDE_MODEL,
        timeout=float(bot_settings.BOT_TIMEOUT_SECONDS),
        max_retries=bot_settings.BOT_MAX_RETRIES,
    )

    # `GEMINI_API_KEY` vacia es un ESTADO ESPERADO en produccion, no un error:
    # esta vacia a proposito desde hace meses. Hasta el 3a75092 esto era un
    # `raise RuntimeError`, y como el grafo se arma perezosamente en la primera
    # request, ese raise se comia el mensaje entrante entero (ver la auditoria
    # del 2026-08-24).
    #
    # Gemini es OPCIONAL en los dos unicos lugares donde entra, y los dos ya
    # saben vivir sin el:
    #   - SearchService: `gemini_client=None` deja `_vector_search` en None y la
    #     busqueda queda SQL puro (search_service.py:57-60,120-125). La pierna
    #     vectorial es una de las dos del hibrido, no la busqueda.
    #   - Fallback del circuit breaker: sin cliente, `call_gemini` revienta
    #     adentro del try de `ai_dispatch` y cae al texto fijo de
    #     `wa_tpl_ai_dual_fail_text`, que es la ultima red que ya existia.
    #
    # `genai.Client(api_key="")` levanta un ValueError que solo dice «No API key
    # was provided» y no menciona ni a Gemini ni al bot; por eso el cliente no se
    # construye en vez de construirse y fallar despues.
    gemini: GeminiClient | None = None
    if bot_settings.GEMINI_API_KEY:
        gemini = GeminiClient(
            api_key=bot_settings.GEMINI_API_KEY,
            embedding_model=bot_settings.GEMINI_EMBEDDING_MODEL,
        )
    else:
        logger.warning(
            "GEMINI_API_KEY vacia — el bot arranca DEGRADADO: sin busqueda "
            "vectorial (queda SQL puro) y sin fallback de Gemini (queda el "
            "texto fijo). Ver TD-OPS-01."
        )

    # Circuit breaker with admin notification on open
    notifier = get_admin_notifier()

    def _on_circuit_open(failures: int) -> None:
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(notifier.notify_circuit_breaker_open(failures))
        except RuntimeError:
            pass  # no event loop — skip notification

    circuit_breaker = CircuitBreaker(
        failure_threshold=bot_settings.BOT_CIRCUIT_BREAKER_THRESHOLD,
        reset_timeout=bot_settings.BOT_CIRCUIT_BREAKER_RESET_SECONDS,
        on_open=_on_circuit_open,
    )

    # -- 2. Search pipeline --
    search_service = SearchService(gemini_client=gemini, bot_settings_repo=bot_setting_repo)

    # -- 3. Core --
    conversation_manager = ConversationManager()
    response_builder = ResponseBuilder()

    # M5 Fase E: AlternativesBuilder wired to the same SearchService + its GeoResolver
    alternatives_builder = AlternativesBuilder(
        search_service=search_service,
        geo_resolver=search_service._geo_resolver,
    )

    tool_executor = ToolExecutor(
        search_service=search_service,
        alternatives_builder=alternatives_builder,
        bot_settings_repo=bot_setting_repo,
    )

    orchestrator = Orchestrator(
        claude=claude,
        gemini=gemini,
        circuit_breaker=circuit_breaker,
        search_service=search_service,
        conversation_manager=conversation_manager,
        response_builder=response_builder,
        tool_executor=tool_executor,
        geo_data_path=bot_settings.GEO_DATA_PATH,
    )

    # -- 4. Middleware (shared) --
    rate_limiter = RateLimiter(
        max_messages=bot_settings.RATE_LIMIT_MAX_MESSAGES,
        window_seconds=bot_settings.RATE_LIMIT_WINDOW_SECONDS,
    )
    idempotency_guard = IdempotencyGuard()
    injection_guard = InjectionGuard()

    # -- 5. Channel senders --
    tg_sender = TelegramSender(
        bot_token=bot_settings.TELEGRAM_BOT_TOKEN,
    )

    wa_sender = WhatsAppSender(
        account_sid=bot_settings.TWILIO_ACCOUNT_SID,
        auth_token=bot_settings.TWILIO_AUTH_TOKEN,
        from_number=bot_settings.TWILIO_WHATSAPP_FROM,
        status_callback_url=bot_settings.TWILIO_STATUS_CALLBACK_URL,
    )

    # -- 6. MessageHandlers --
    tg_handler = MessageHandler(
        orchestrator=orchestrator,
        response_builder=response_builder,
        sender=tg_sender,
        rate_limiter=rate_limiter,
        idempotency_guard=idempotency_guard,
        injection_guard=injection_guard,
    )

    wa_handler = MessageHandler(
        orchestrator=orchestrator,
        response_builder=response_builder,
        sender=wa_sender,
        rate_limiter=rate_limiter,
        idempotency_guard=idempotency_guard,
        injection_guard=injection_guard,
    )

    logger.info("Bot dependency graph built successfully")

    return BotDependencies(
        tg_handler=tg_handler,
        wa_handler=wa_handler,
    )


def get_bot_dependencies() -> BotDependencies:
    """Return the singleton BotDependencies, building on first call.

    Thread-safe enough for FastAPI (single event loop). The lazy
    pattern avoids import-time side effects (API client init, file I/O).
    """
    global _bot_deps
    if _bot_deps is None:
        _bot_deps = build_bot_dependencies()
    return _bot_deps


def reset_bot_dependencies() -> None:
    """Reset the singleton (for testing)."""
    global _bot_deps
    _bot_deps = None
