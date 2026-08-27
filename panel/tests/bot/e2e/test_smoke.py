"""Smoke test — validates the E2E runner + fixtures end-to-end.

This is the minimal test that MUST pass before writing the full 20-30 test suite.
It verifies:
- ConversationRunner instantiates correctly
- program_claude_response() programs the mock properly
- send() invokes the orchestrator and returns a BotResponse
- assert_last_tool("none") passes when no tool was called
- assert_response_contains() works (case-insensitive, unaccented)

No real Claude/Twilio/Meta calls are made.
"""
from __future__ import annotations

import pytest

from app.bot.core.types import BotResponse


class TestSmokeHelloGreeting:
    """Basic smoke: send 'hola', get a greeting back, no tool called."""

    @pytest.mark.asyncio
    async def test_smoke_hello_greeting(self, runner):
        """Runner + mocks work: greeting response without tool call."""
        runner.program_claude_response(text="¡Hola! Soy Onnix, ¿qué buscás?")

        response = await runner.send("hola")

        # Must return a BotResponse, not None
        assert response is not None
        assert isinstance(response, BotResponse)

        # No tool was used — conversational greeting
        runner.assert_last_tool("none")

        # Response text contains the greeting keyword (case-insensitive)
        runner.assert_response_contains("hola")

    @pytest.mark.asyncio
    async def test_smoke_response_text_returned(self, runner):
        """BotResponse.text matches the programmed Claude response."""
        expected_text = "¡Hola! Soy Onnix, asistente de Onnix."
        runner.program_claude_response(text=expected_text)

        response = await runner.send("hola")

        assert response is not None
        assert response.text == expected_text

    @pytest.mark.asyncio
    async def test_smoke_runner_send_many(self, runner):
        """send_many() returns one response per message."""
        runner.program_claude_response(text="Hola!")
        responses = await runner.send_many(["hola"])

        assert len(responses) == 1
        assert responses[0] is not None
