"""Gemini (Google) client wrapper.

Thin async wrapper around the Google GenAI SDK that returns unified
``AIResponse`` objects.  Provides text generation (fallback for Claude)
and embedding generation for semantic search.
"""
from __future__ import annotations

import logging
import time

from google import genai
from google.genai import types

from .types import AIResponse

logger = logging.getLogger(__name__)


class GeminiClient:
    """Wrapper around the Google GenAI SDK for bot usage."""

    def __init__(
        self,
        api_key: str,
        text_model: str = "gemini-2.5-flash",
        embedding_model: str = "gemini-embedding-001",
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self._text_model = text_model
        self._embedding_model = embedding_model

    async def send_message(
        self,
        system: str,
        user_content: str,
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> AIResponse:
        """Send a message to Gemini and return a unified AIResponse."""
        logger.debug(
            "Gemini request — {\"model\": \"%s\", \"content_len\": %d}",
            self._text_model, len(user_content),
        )
        start = time.monotonic()
        try:
            response = await self._client.aio.models.generate_content(
                model=self._text_model,
                contents=user_content,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                ),
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)
            ai_resp = AIResponse.from_gemini(response)
            ai_resp.latency_ms = elapsed_ms
            logger.info(
                "Gemini response (%.0fms) — {\"model\": \"%s\", \"tokens_in\": %d, \"tokens_out\": %d}",
                elapsed_ms, self._text_model,
                ai_resp.input_tokens, ai_resp.output_tokens,
            )
            return ai_resp
        except Exception as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.error(
                "Gemini error (%.0fms) — {\"model\": \"%s\", \"error\": \"%.200s\"}",
                elapsed_ms, self._text_model, exc,
            )
            raise

    async def generate_embedding(
        self,
        text: str,
        dimensionality: int = 768,
    ) -> list[float]:
        """Generate an embedding vector for a single text."""
        start = time.monotonic()
        response = await self._client.aio.models.embed_content(
            model=self._embedding_model,
            contents=text,
            config=types.EmbedContentConfig(
                output_dimensionality=dimensionality,
            ),
        )
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.debug(
            "Embedding generated (%.0fms) — {\"model\": \"%s\", \"text_len\": %d, \"dims\": %d}",
            elapsed_ms, self._embedding_model, len(text), dimensionality,
        )
        return response.embeddings[0].values

    async def generate_embeddings_batch(
        self,
        texts: list[str],
        dimensionality: int = 768,
    ) -> list[list[float]]:
        """Generate embedding vectors for multiple texts in one call."""
        start = time.monotonic()
        response = await self._client.aio.models.embed_content(
            model=self._embedding_model,
            contents=texts,
            config=types.EmbedContentConfig(
                output_dimensionality=dimensionality,
            ),
        )
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.debug(
            "Batch embeddings generated (%.0fms) — {\"model\": \"%s\", \"count\": %d}",
            elapsed_ms, self._embedding_model, len(texts),
        )
        return [emb.values for emb in response.embeddings]
