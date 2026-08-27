"""Capa de IA — lo que quedó después de sacar el bot conversacional.

Se fueron `circuit_breaker`, `tools` y `prompts`: existían para que el bot le
contestara solo al cliente, y Onnix contesta a mano desde el panel.

Lo que queda NO le habla a nadie por WhatsApp:

- ``GeminiClient`` genera los embeddings de la búsqueda semántica del panel
  (`app/bot/search/vector_search.py`).
- ``ClaudeClient`` lo usa ``property_chatbot``, que traduce la búsqueda en
  lenguaje natural del panel de propiedades a filtros. Es una herramienta del
  agente, no un interlocutor del cliente.
"""
from .types import AIResponse, ToolCall, EmbeddingResult
from .claude_client import ClaudeClient
from .gemini_client import GeminiClient

__all__ = [
    "AIResponse", "ToolCall", "EmbeddingResult",
    "ClaudeClient", "GeminiClient",
]
