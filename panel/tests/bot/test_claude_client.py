"""Tests for AIResponse types and ClaudeClient wrapper.

RED phase: all tests should FAIL against stubs.
"""
from __future__ import annotations

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.bot.ai.types import AIResponse, ToolCall, EmbeddingResult


# ---------------------------------------------------------------------------
# Helpers — mock objects that mimic Anthropic SDK Message structure
# ---------------------------------------------------------------------------

def _make_usage(input_tokens: int, output_tokens: int):
    """Create a mock Usage object."""
    return SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)


def _make_text_block(text: str):
    """Create a mock TextBlock."""
    return SimpleNamespace(type="text", text=text)


def _make_tool_use_block(tool_id: str, name: str, tool_input: dict):
    """Create a mock ToolUseBlock."""
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=tool_input)


def _make_claude_message(
    content: list,
    model: str = "claude-haiku-4-5-20251001",
    stop_reason: str = "end_turn",
    input_tokens: int = 100,
    output_tokens: int = 25,
):
    """Create a mock Anthropic Message object."""
    return SimpleNamespace(
        content=content,
        model=model,
        stop_reason=stop_reason,
        usage=_make_usage(input_tokens, output_tokens),
    )


# ===========================================================================
# TestAIResponse
# ===========================================================================

class TestAIResponse:
    """Tests for AIResponse dataclass and classmethods."""

    def test_from_claude_text_only(self):
        """Parse a text-only Claude response."""
        msg = _make_claude_message(
            content=[_make_text_block("Hola! Soy el asistente de Onnix.")],
            model="claude-haiku-4-5-20251001",
            stop_reason="end_turn",
            input_tokens=100,
            output_tokens=25,
        )
        resp = AIResponse.from_claude(msg)

        assert resp.text == "Hola! Soy el asistente de Onnix."
        assert "haiku" in resp.model
        assert resp.stop_reason == "end_turn"
        assert resp.input_tokens == 100
        assert resp.output_tokens == 25
        assert resp.tool_calls == []

    def test_from_claude_tool_use(self):
        """Parse a tool_use Claude response."""
        msg = _make_claude_message(
            content=[
                _make_tool_use_block(
                    "toolu_test_001",
                    "search_properties",
                    {
                        "operacion": "venta",
                        "tipo": "casa",
                        "ciudad": "asuncion",
                        "precio_max": 200000,
                    },
                )
            ],
            stop_reason="tool_use",
            input_tokens=150,
            output_tokens=50,
        )
        resp = AIResponse.from_claude(msg)

        assert resp.text is None
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "search_properties"
        assert resp.tool_calls[0].input["operacion"] == "venta"
        assert resp.stop_reason == "tool_use"

    def test_from_claude_mixed_content(self):
        """Parse a Claude response with BOTH text and tool_use blocks."""
        msg = _make_claude_message(
            content=[
                _make_text_block("Buscando propiedades para vos..."),
                _make_tool_use_block(
                    "toolu_test_002",
                    "search_properties",
                    {"operacion": "alquiler", "ciudad": "luque"},
                ),
            ],
            stop_reason="tool_use",
            input_tokens=200,
            output_tokens=80,
        )
        resp = AIResponse.from_claude(msg)

        assert resp.text == "Buscando propiedades para vos..."
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "search_properties"

    def test_has_tool_calls_true(self):
        """AIResponse with tool_calls returns True."""
        resp = AIResponse(
            text=None,
            tool_calls=[ToolCall(id="t1", name="search", input={})],
            model="test",
        )
        assert resp.has_tool_calls is True

    def test_has_tool_calls_false(self):
        """AIResponse without tool_calls returns False."""
        resp = AIResponse(text="Hello", tool_calls=[], model="test")
        assert resp.has_tool_calls is False

    def test_tool_call_fields(self):
        """ToolCall fields are accessible."""
        tc = ToolCall(
            id="toolu_abc",
            name="search_properties",
            input={"operacion": "venta"},
        )
        assert tc.id == "toolu_abc"
        assert tc.name == "search_properties"
        assert tc.input == {"operacion": "venta"}


# ===========================================================================
# TestClaudeClient
# ===========================================================================

class TestClaudeClient:
    """Tests for ClaudeClient wrapper."""

    def test_init_with_config(self):
        """ClaudeClient stores configuration without making API calls."""
        from app.bot.ai.claude_client import ClaudeClient

        client = ClaudeClient(
            api_key="sk-test-key",
            model="claude-haiku-4-5-20251001",
            timeout=25.0,
            max_retries=5,
        )
        assert client._model == "claude-haiku-4-5-20251001"
        assert client._client is not None

    @pytest.mark.asyncio
    async def test_send_message_text_response(self):
        """send_message returns AIResponse with correct text."""
        from app.bot.ai.claude_client import ClaudeClient

        mock_msg = _make_claude_message(
            content=[_make_text_block("Hola! Soy el asistente de Onnix.")],
            model="claude-haiku-4-5-20251001",
            stop_reason="end_turn",
            input_tokens=100,
            output_tokens=25,
        )

        client = ClaudeClient(api_key="sk-test", model="claude-haiku-4-5-20251001")
        client._client = MagicMock()
        client._client.messages = MagicMock()
        client._client.messages.create = AsyncMock(return_value=mock_msg)

        result = await client.send_message(
            system="Eres un asistente inmobiliario.",
            messages=[{"role": "user", "content": "Hola"}],
        )

        assert isinstance(result, AIResponse)
        assert result.text == "Hola! Soy el asistente de Onnix."
        assert result.stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_send_message_with_tools(self):
        """send_message returns AIResponse with tool_calls."""
        from app.bot.ai.claude_client import ClaudeClient

        mock_msg = _make_claude_message(
            content=[
                _make_tool_use_block(
                    "toolu_001",
                    "search_properties",
                    {"operacion": "venta"},
                )
            ],
            stop_reason="tool_use",
            input_tokens=150,
            output_tokens=50,
        )

        client = ClaudeClient(api_key="sk-test", model="claude-haiku-4-5-20251001")
        client._client = MagicMock()
        client._client.messages = MagicMock()
        client._client.messages.create = AsyncMock(return_value=mock_msg)

        tools = [{"name": "search_properties", "description": "Search", "input_schema": {}}]
        result = await client.send_message(
            system="Eres un asistente.",
            messages=[{"role": "user", "content": "Quiero comprar"}],
            tools=tools,
        )

        assert isinstance(result, AIResponse)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "search_properties"

    @pytest.mark.asyncio
    async def test_send_message_passes_kwargs(self):
        """send_message forwards all parameters to the SDK."""
        from app.bot.ai.claude_client import ClaudeClient

        mock_msg = _make_claude_message(
            content=[_make_text_block("OK")],
        )

        client = ClaudeClient(api_key="sk-test", model="claude-haiku-4-5-20251001")
        client._client = MagicMock()
        client._client.messages = MagicMock()
        client._client.messages.create = AsyncMock(return_value=mock_msg)

        tools = [{"name": "t1", "description": "d1", "input_schema": {}}]
        await client.send_message(
            system="System prompt",
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=2048,
            temperature=0.7,
            tools=tools,
        )

        call_kwargs = client._client.messages.create.call_args
        assert call_kwargs.kwargs["model"] == "claude-haiku-4-5-20251001"
        assert call_kwargs.kwargs["max_tokens"] == 2048
        assert call_kwargs.kwargs["temperature"] == 0.7
        assert call_kwargs.kwargs["system"] == "System prompt"
        assert call_kwargs.kwargs["messages"] == [{"role": "user", "content": "Hi"}]
        assert call_kwargs.kwargs["tools"] == tools

    @pytest.mark.asyncio
    async def test_send_message_passes_system_blocks_unchanged(self):
        """When system is a list of blocks (with cache_control), it reaches the SDK as-is."""
        from app.bot.ai.claude_client import ClaudeClient

        mock_msg = _make_claude_message(content=[_make_text_block("OK")])
        client = ClaudeClient(api_key="sk-test", model="claude-haiku-4-5-20251001")
        client._client = MagicMock()
        client._client.messages = MagicMock()
        client._client.messages.create = AsyncMock(return_value=mock_msg)

        system_blocks = [
            {"type": "text", "text": "BASE STATIC PROMPT", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "DYNAMIC SECTION"},
        ]
        await client.send_message(
            system=system_blocks,
            messages=[{"role": "user", "content": "Hi"}],
        )

        call_kwargs = client._client.messages.create.call_args
        assert call_kwargs.kwargs["system"] == system_blocks
        # Critical: cache_control on the base block reaches the SDK
        assert call_kwargs.kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}

    def test_is_transient_error_classification(self):
        """is_transient_error classifies errors correctly."""
        from app.bot.ai.claude_client import ClaudeClient
        from anthropic import (
            APIConnectionError,
            RateLimitError,
            AuthenticationError,
            BadRequestError,
            APIStatusError,
        )

        client = ClaudeClient(api_key="sk-test", model="test-model")

        # Transient errors → True
        conn_err = APIConnectionError(request=MagicMock())
        assert client.is_transient_error(conn_err) is True

        rate_err = RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429, headers={}),
            body=None,
        )
        assert client.is_transient_error(rate_err) is True

        # Non-transient errors → False
        auth_err = AuthenticationError(
            message="invalid key",
            response=MagicMock(status_code=401, headers={}),
            body=None,
        )
        assert client.is_transient_error(auth_err) is False

        bad_err = BadRequestError(
            message="bad request",
            response=MagicMock(status_code=400, headers={}),
            body=None,
        )
        assert client.is_transient_error(bad_err) is False

        # APIStatusError with 500 → True
        server_err = APIStatusError(
            message="internal server error",
            response=MagicMock(status_code=500, headers={}),
            body=None,
        )
        assert client.is_transient_error(server_err) is True

        # APIStatusError with 400 → False
        client_err = APIStatusError(
            message="bad request",
            response=MagicMock(status_code=400, headers={}),
            body=None,
        )
        assert client.is_transient_error(client_err) is False
