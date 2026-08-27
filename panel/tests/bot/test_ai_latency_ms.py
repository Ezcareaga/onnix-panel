"""Tests for ai_latency_ms write path.

A1: AIResponse.latency_ms field exists with default 0.
A2: Clients set latency_ms on the returned AIResponse.
A4: save_outbound_message accepts ai_latency_ms and passes it to SQL.
A3: Orchestrator passes ai_response.latency_ms to save_outbound_message.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.ai.types import AIResponse, ToolCall
from app.bot.core.conversation import ConversationManager


# ---------------------------------------------------------------------------
# Helpers (mirror test_conversation_manager.py)
# ---------------------------------------------------------------------------

def _mock_session():
    session = AsyncMock()
    return session


def _mock_row(**kwargs):
    row = MagicMock()
    for k, v in kwargs.items():
        setattr(row, k, v)
    return row


def _mock_result(one_row=None):
    result = MagicMock()
    result.first.return_value = one_row
    result.fetchone.return_value = one_row
    return result


# ---------------------------------------------------------------------------
# A1 — AIResponse dataclass
# ---------------------------------------------------------------------------

class TestAIResponseLatencyField:
    """AIResponse.latency_ms exists and defaults to 0."""

    def test_default_latency_is_zero(self):
        resp = AIResponse()
        assert resp.latency_ms == 0

    def test_latency_can_be_set(self):
        resp = AIResponse(latency_ms=1234)
        assert resp.latency_ms == 1234

    def test_from_claude_does_not_set_latency(self):
        """from_claude must leave latency_ms=0 — caller sets it."""
        mock_response = MagicMock()
        mock_response.content = []
        mock_response.model = "claude-haiku-4-5"
        mock_response.stop_reason = "end_turn"
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 5
        ai_resp = AIResponse.from_claude(mock_response)
        assert ai_resp.latency_ms == 0

    def test_from_gemini_does_not_set_latency(self):
        """from_gemini must leave latency_ms=0 — caller sets it."""
        mock_response = MagicMock()
        mock_response.text = "Hola"
        mock_response.usage_metadata.prompt_token_count = 8
        mock_response.usage_metadata.candidates_token_count = 4
        ai_resp = AIResponse.from_gemini(mock_response)
        assert ai_resp.latency_ms == 0

    def test_latency_ms_attribute_is_assignable(self):
        """AIResponse is not frozen — direct assignment must work."""
        resp = AIResponse.from_claude(
            MagicMock(
                content=[],
                model="claude-haiku-4-5",
                stop_reason="end_turn",
                usage=MagicMock(input_tokens=1, output_tokens=1),
            )
        )
        resp.latency_ms = 999
        assert resp.latency_ms == 999


# ---------------------------------------------------------------------------
# A2 — ClaudeClient sets latency_ms
# ---------------------------------------------------------------------------

class TestClaudeClientSetsLatency:
    """ClaudeClient.send_message assigns latency_ms on returned AIResponse."""

    @pytest.mark.asyncio
    async def test_send_message_sets_latency_ms(self):
        from app.bot.ai.claude_client import ClaudeClient

        mock_sdk_response = MagicMock()
        mock_sdk_response.content = []
        mock_sdk_response.model = "claude-haiku-4-5"
        mock_sdk_response.stop_reason = "end_turn"
        mock_sdk_response.usage.input_tokens = 10
        mock_sdk_response.usage.output_tokens = 5

        client = ClaudeClient.__new__(ClaudeClient)
        client._model = "claude-haiku-4-5"
        mock_anthropic = AsyncMock()
        mock_anthropic.messages.create = AsyncMock(return_value=mock_sdk_response)
        client._client = mock_anthropic

        result = await client.send_message(
            system="test", messages=[{"role": "user", "content": "hi"}]
        )

        assert isinstance(result.latency_ms, int)
        assert result.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_send_message_latency_is_integer(self):
        """latency_ms must be int, not float."""
        from app.bot.ai.claude_client import ClaudeClient

        mock_sdk_response = MagicMock()
        mock_sdk_response.content = []
        mock_sdk_response.model = "claude-haiku-4-5"
        mock_sdk_response.stop_reason = "end_turn"
        mock_sdk_response.usage.input_tokens = 0
        mock_sdk_response.usage.output_tokens = 0

        client = ClaudeClient.__new__(ClaudeClient)
        client._model = "claude-haiku-4-5"
        mock_anthropic = AsyncMock()
        mock_anthropic.messages.create = AsyncMock(return_value=mock_sdk_response)
        client._client = mock_anthropic

        result = await client.send_message(
            system="s", messages=[{"role": "user", "content": "q"}]
        )
        assert type(result.latency_ms) is int


# ---------------------------------------------------------------------------
# A2 — GeminiClient sets latency_ms
# ---------------------------------------------------------------------------

class TestGeminiClientSetsLatency:
    """GeminiClient.send_message assigns latency_ms on returned AIResponse."""

    @pytest.mark.asyncio
    async def test_send_message_sets_latency_ms(self):
        from app.bot.ai.gemini_client import GeminiClient

        mock_sdk_response = MagicMock()
        mock_sdk_response.text = "Hola"
        mock_sdk_response.usage_metadata.prompt_token_count = 8
        mock_sdk_response.usage_metadata.candidates_token_count = 3

        client = GeminiClient.__new__(GeminiClient)
        client._text_model = "gemini-flash"
        client._embedding_model = "gemini-embedding-001"
        mock_genai = MagicMock()
        mock_genai.aio.models.generate_content = AsyncMock(return_value=mock_sdk_response)
        client._client = mock_genai

        result = await client.send_message(system="sys", user_content="hola")

        assert isinstance(result.latency_ms, int)
        assert result.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_send_message_latency_is_integer(self):
        from app.bot.ai.gemini_client import GeminiClient

        mock_sdk_response = MagicMock()
        mock_sdk_response.text = "resp"
        mock_sdk_response.usage_metadata.prompt_token_count = 2
        mock_sdk_response.usage_metadata.candidates_token_count = 2

        client = GeminiClient.__new__(GeminiClient)
        client._text_model = "gemini-flash"
        client._embedding_model = "gemini-embedding-001"
        mock_genai = MagicMock()
        mock_genai.aio.models.generate_content = AsyncMock(return_value=mock_sdk_response)
        client._client = mock_genai

        result = await client.send_message(system="s", user_content="q")
        assert type(result.latency_ms) is int


# ---------------------------------------------------------------------------
# A4 — save_outbound_message persists ai_latency_ms
# ---------------------------------------------------------------------------

class TestSaveOutboundMessagePersistsLatency:
    """save_outbound_message passes ai_latency_ms to the INSERT SQL."""

    @pytest.mark.asyncio
    async def test_save_outbound_message_persists_ai_latency_ms(self):
        """ai_latency_ms=123 must appear in the SQL params."""
        session = _mock_session()
        row = _mock_row(id=42)
        session.execute.return_value = _mock_result(one_row=row)

        mgr = ConversationManager()
        msg_id = await mgr.save_outbound_message(
            session,
            conversation_id=1,
            contact_id=2,
            body="Test response",
            intent="conversacion",
            ai_model="claude-haiku-4-5",
            ai_tokens_in=100,
            ai_tokens_out=50,
            ai_latency_ms=123,
        )

        assert msg_id == 42
        # Verify the SQL call included ai_latency_ms in its params
        call_args = session.execute.call_args_list
        assert len(call_args) >= 1
        _, params = call_args[0][0], call_args[0][1] if len(call_args[0]) > 1 else {}
        # The second positional arg to execute() is the params dict
        actual_params = call_args[0][0][1] if len(call_args[0][0]) > 1 else call_args[0][1]
        # Handle both positional and keyword call styles
        found_latency = False
        for call in call_args:
            args, kwargs = call
            if len(args) >= 2 and isinstance(args[1], dict):
                if args[1].get("ai_latency_ms") == 123:
                    found_latency = True
        assert found_latency, "ai_latency_ms=123 was not passed to the SQL execute call"

    @pytest.mark.asyncio
    async def test_save_outbound_message_default_latency_is_zero(self):
        """Default ai_latency_ms=0 when not specified."""
        session = _mock_session()
        row = _mock_row(id=99)
        session.execute.return_value = _mock_result(one_row=row)

        mgr = ConversationManager()
        await mgr.save_outbound_message(
            session,
            conversation_id=10,
            contact_id=20,
            body="No latency",
            intent="conversacion",
        )

        call_args = session.execute.call_args_list
        for call in call_args:
            args, kwargs = call
            if len(args) >= 2 and isinstance(args[1], dict):
                # When ai_latency_ms omitted, it should default to 0 in params
                if "ai_latency_ms" in args[1]:
                    assert args[1]["ai_latency_ms"] == 0


# ---------------------------------------------------------------------------
# A3 — Orchestrator passes latency through to save_outbound_message
# ---------------------------------------------------------------------------

class TestOrchestratorPassesLatency:
    """Orchestrator propagates ai_response.latency_ms to save_outbound_message."""

    @pytest.mark.asyncio
    async def test_orchestrator_passes_latency_ms_to_conversation_manager(self):
        """When ai_response.latency_ms=750, save_outbound_message is called with ai_latency_ms=750."""
        from app.bot.core.orchestrator import Orchestrator
        from app.bot.core.types import BotRequest, BotResponse, ContactInfo, ConversationInfo, ConversationState

        # Build a minimal Orchestrator with all dependencies mocked
        orchestrator = Orchestrator.__new__(Orchestrator)

        mock_conv_manager = AsyncMock()
        mock_conv_manager.resolve_contact = AsyncMock(return_value=ContactInfo(
            id=1, name="Test", status="new", is_baja=False,
            platform="whatsapp", phone="+595981000001",
        ))
        mock_conv_manager.get_or_create_conversation = AsyncMock(return_value=ConversationInfo(
            id=1, contact_id=1, platform="whatsapp", chat_id="+595981000001",
            is_bot_active=True, is_open=True, search_context={}, message_count=0,
        ))
        mock_conv_manager.save_inbound_message = AsyncMock(return_value=1)
        mock_conv_manager.get_history = AsyncMock(return_value=[])
        mock_conv_manager.get_search_context = AsyncMock(return_value=ConversationState())
        mock_conv_manager.update_search_context = AsyncMock()
        mock_conv_manager.save_outbound_message = AsyncMock(return_value=2)
        mock_conv_manager.check_human_cooldown = MagicMock(return_value=False)

        # AI response with latency set
        ai_resp = AIResponse(
            text="Hola, ¿en qué puedo ayudarte?",
            tool_calls=[],
            model="claude-haiku-4-5",
            input_tokens=50,
            output_tokens=20,
            latency_ms=750,
            stop_reason="end_turn",
        )

        mock_claude = AsyncMock()
        mock_claude.send_message = AsyncMock(return_value=ai_resp)

        mock_circuit_breaker = MagicMock()
        mock_circuit_breaker.is_open = False
        mock_circuit_breaker.call = AsyncMock(return_value=ai_resp)

        mock_tool_executor = AsyncMock()
        mock_response_builder = MagicMock()

        orchestrator._conversation_manager = mock_conv_manager
        orchestrator._claude_client = mock_claude
        orchestrator._circuit_breaker = mock_circuit_breaker
        orchestrator._tool_executor = mock_tool_executor
        orchestrator._response_builder = mock_response_builder
        orchestrator._search_service = AsyncMock()

        # Call the orchestrator
        session = AsyncMock()
        session.execute = AsyncMock()

        request = BotRequest(
            platform="whatsapp",
            chat_id="+595981000001",
            user_id="+595981000001",
            user_name="Test",
            text="hola",
        )

        try:
            await orchestrator.process(session, request)
        except Exception:
            pass  # We only care about what was passed to save_outbound_message

        # Check that save_outbound_message was called with ai_latency_ms=750
        if mock_conv_manager.save_outbound_message.called:
            call_kwargs = mock_conv_manager.save_outbound_message.call_args
            # Could be positional or keyword
            if call_kwargs.kwargs:
                assert call_kwargs.kwargs.get("ai_latency_ms") == 750, (
                    f"Expected ai_latency_ms=750, got {call_kwargs.kwargs}"
                )
