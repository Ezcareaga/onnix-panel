"""Tests for GeminiClient wrapper.

RED phase: all tests should FAIL against stubs (NotImplementedError).
"""
from __future__ import annotations

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.bot.ai.types import AIResponse
from app.bot.ai.gemini_client import GeminiClient


# ---------------------------------------------------------------------------
# Helpers — mock objects that mimic Google GenAI SDK response structure
# ---------------------------------------------------------------------------

def _make_generate_response(text: str, prompt_tokens: int = 50,
                            candidates_tokens: int = 20):
    """Create a mock Gemini generate_content response."""
    return SimpleNamespace(
        text=text,
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt_tokens,
            candidates_token_count=candidates_tokens,
        ),
    )


def _make_embedding_response(embeddings: list[list[float]]):
    """Create a mock Gemini embed_content response."""
    return SimpleNamespace(
        embeddings=[
            SimpleNamespace(values=emb) for emb in embeddings
        ],
    )


# ===========================================================================
# TestGeminiClient
# ===========================================================================

@patch("app.bot.ai.gemini_client.genai")
class TestGeminiClient:
    """Tests for GeminiClient wrapper."""

    def test_init_stores_config(self, mock_genai):
        """GeminiClient stores text_model and embedding_model."""
        mock_genai.Client.return_value = MagicMock()
        client = GeminiClient("test-key", "my-text-model", "my-embed-model")

        assert client._text_model == "my-text-model"
        assert client._embedding_model == "my-embed-model"

    @pytest.mark.asyncio
    async def test_send_message_returns_ai_response(self, mock_genai):
        """send_message returns AIResponse with correct text and metadata."""
        mock_client_instance = MagicMock()
        mock_genai.Client.return_value = mock_client_instance

        mock_response = _make_generate_response(
            "Respuesta de Gemini", prompt_tokens=50, candidates_tokens=20
        )
        mock_client_instance.aio.models.generate_content = AsyncMock(
            return_value=mock_response
        )

        client = GeminiClient("test-key")
        result = await client.send_message(
            system="Eres un asistente.",
            user_content="Hola",
        )

        assert isinstance(result, AIResponse)
        assert result.text == "Respuesta de Gemini"
        assert "gemini" in result.model
        assert result.stop_reason == "end_turn"
        assert result.input_tokens == 50
        assert result.output_tokens == 20

    @pytest.mark.asyncio
    async def test_send_message_passes_system_instruction(self, mock_genai):
        """send_message passes system_instruction to the API."""
        mock_client_instance = MagicMock()
        mock_genai.Client.return_value = mock_client_instance

        mock_response = _make_generate_response("OK")
        mock_client_instance.aio.models.generate_content = AsyncMock(
            return_value=mock_response
        )

        client = GeminiClient("test-key")
        await client.send_message(
            system="System prompt here",
            user_content="Hola",
            max_tokens=512,
            temperature=0.5,
        )

        call_kwargs = mock_client_instance.aio.models.generate_content.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert config.system_instruction == "System prompt here"

    @pytest.mark.asyncio
    async def test_generate_embedding_returns_768_floats(self, mock_genai):
        """generate_embedding returns a list of 768 floats."""
        mock_client_instance = MagicMock()
        mock_genai.Client.return_value = mock_client_instance

        embedding_values = [0.1] * 768
        mock_response = _make_embedding_response([embedding_values])
        mock_client_instance.aio.models.embed_content = AsyncMock(
            return_value=mock_response
        )

        client = GeminiClient("test-key")
        result = await client.generate_embedding("test text")

        assert isinstance(result, list)
        assert len(result) == 768
        assert all(isinstance(v, float) for v in result)

    @pytest.mark.asyncio
    async def test_generate_embedding_passes_dimensionality(self, mock_genai):
        """generate_embedding passes output_dimensionality to the API."""
        mock_client_instance = MagicMock()
        mock_genai.Client.return_value = mock_client_instance

        mock_response = _make_embedding_response([[0.1] * 768])
        mock_client_instance.aio.models.embed_content = AsyncMock(
            return_value=mock_response
        )

        client = GeminiClient("test-key")
        await client.generate_embedding("test text", dimensionality=768)

        call_kwargs = mock_client_instance.aio.models.embed_content.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert config.output_dimensionality == 768

    @pytest.mark.asyncio
    async def test_generate_embeddings_batch_returns_list(self, mock_genai):
        """generate_embeddings_batch returns a list of embedding vectors."""
        mock_client_instance = MagicMock()
        mock_genai.Client.return_value = mock_client_instance

        batch_embeddings = [[0.1] * 768, [0.2] * 768, [0.3] * 768]
        mock_response = _make_embedding_response(batch_embeddings)
        mock_client_instance.aio.models.embed_content = AsyncMock(
            return_value=mock_response
        )

        client = GeminiClient("test-key")
        result = await client.generate_embeddings_batch(
            ["text1", "text2", "text3"]
        )

        assert isinstance(result, list)
        assert len(result) == 3
        assert all(len(emb) == 768 for emb in result)

    @pytest.mark.asyncio
    async def test_generate_embedding_default_dimensionality(self, mock_genai):
        """generate_embedding uses 768 as default dimensionality."""
        mock_client_instance = MagicMock()
        mock_genai.Client.return_value = mock_client_instance

        mock_response = _make_embedding_response([[0.1] * 768])
        mock_client_instance.aio.models.embed_content = AsyncMock(
            return_value=mock_response
        )

        client = GeminiClient("test-key")
        # Call without explicit dimensionality
        await client.generate_embedding("test text")

        call_kwargs = mock_client_instance.aio.models.embed_content.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert config.output_dimensionality == 768
