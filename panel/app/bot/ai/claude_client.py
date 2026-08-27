"""Claude (Anthropic) client wrapper.

Thin async wrapper around the Anthropic SDK that returns unified
``AIResponse`` objects.  Handles kwarg forwarding, error classification,
and response parsing so the rest of the bot layer stays provider-agnostic.
"""
from __future__ import annotations

import logging
import time

from anthropic import (
    AsyncAnthropic,
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)

from app.bot.ai.types import AIResponse

logger = logging.getLogger(__name__)


class ClaudeClient:
    """Wrapper around the Anthropic SDK for bot usage."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self._client = AsyncAnthropic(
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
        )
        self._model = model

    async def send_message(
        self,
        system: str | list[dict],
        messages: list[dict],
        max_tokens: int = 1024,
        temperature: float = 0.3,
        tools: list[dict] | None = None,
        _tracking_source: str = "bot.orchestrator",
    ) -> AIResponse:
        """Send a message to Claude and return a unified AIResponse.

        All keyword arguments are forwarded to the Anthropic SDK's
        ``messages.create`` method.

        Args:
            _tracking_source: Dot-namespaced attribution label written to
                ``anthropic_api_calls.source``.  Override when the call is
                initiated from a different subsystem (e.g. ``"bot.lead_profiler"``).
        """
        kwargs: dict = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": messages,
        }
        if tools is not None:
            kwargs["tools"] = tools

        if isinstance(system, str):
            system_chars = len(system)
            system_blocks = 1
        else:
            system_chars = sum(len(b.get("text", "")) for b in system)
            system_blocks = len(system)
        logger.debug(
            "Claude request — {\"model\": \"%s\", \"messages\": %d, \"system_blocks\": %d, \"system_chars\": %d, \"tools\": %d}",
            self._model, len(messages), system_blocks, system_chars, len(tools) if tools else 0,
        )

        from app.bot.observability.anthropic_tracker import track_anthropic_call

        start = time.monotonic()
        async with track_anthropic_call(_tracking_source) as tracker:
            try:
                response = await self._client.messages.create(**kwargs)
                tracker.set_response(response)
            except Exception as exc:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                logger.error(
                    "Claude error (%.0fms) — {\"model\": \"%s\", \"error\": \"%.200s\", \"transient\": %s}",
                    elapsed_ms, self._model, exc, self.is_transient_error(exc),
                )
                raise

        elapsed_ms = int((time.monotonic() - start) * 1000)
        ai_resp = AIResponse.from_claude(response)
        ai_resp.latency_ms = elapsed_ms
        logger.info(
            "Claude response (%.0fms) — {\"model\": \"%s\", \"tokens_in\": %d, \"tokens_out\": %d, \"stop_reason\": \"%s\", \"tool_calls\": %d}",
            elapsed_ms, self._model,
            ai_resp.input_tokens, ai_resp.output_tokens,
            ai_resp.stop_reason, len(ai_resp.tool_calls),
        )
        return ai_resp

    def is_transient_error(self, error: Exception) -> bool:
        """Classify whether an error is transient (retryable).

        Transient errors (return True):
        - ``APIConnectionError`` — network issues
        - ``RateLimitError`` — 429 responses
        - ``APIStatusError`` with status_code >= 500 — server errors

        Non-transient errors (return False):
        - ``AuthenticationError`` — bad API key
        - ``BadRequestError`` — malformed request
        - ``APIStatusError`` with status_code < 500 — client errors
        """
        if isinstance(error, APIConnectionError):
            return True
        if isinstance(error, RateLimitError):
            return True
        if isinstance(error, (AuthenticationError, BadRequestError)):
            return False
        if isinstance(error, APIStatusError):
            return error.status_code >= 500
        return False


def is_anthropic_api_error(exc: Exception) -> bool:
    """Return True si la excepción viene del SDK/HTTP de Anthropic.

    Se usa en `orchestrator._call_claude_with_tools` para decidir si el
    circuit breaker debe registrar un fallo. Solo los errores genuinos de
    la API de Anthropic deberían tripearlo — los errores de DB, validación,
    lógica de tools o bugs de código propagan al error handler externo
    sin contaminar la métrica del breaker.

    Ver docs/AUDIT_M4_FASE0_20260419.md §4 para el contexto del bug y
    PLAN_M4_REFACTOR.md Task 2.2 para el punto de uso.
    """
    return isinstance(exc, (
        APIConnectionError,
        APIStatusError,
        RateLimitError,
        AuthenticationError,
        BadRequestError,
    ))
