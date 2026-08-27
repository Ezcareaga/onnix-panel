"""AI layer — LLM clients, circuit breaker, tools, and prompts."""
from .types import AIResponse, ToolCall, EmbeddingResult
from .claude_client import ClaudeClient
from .gemini_client import GeminiClient
from .circuit_breaker import CircuitBreaker, CircuitState
from .tools import TOOLS, get_tools
from .prompts import get_system_prompt, get_response_template

__all__ = [
    "AIResponse", "ToolCall", "EmbeddingResult",
    "ClaudeClient", "GeminiClient",
    "CircuitBreaker", "CircuitState",
    "TOOLS", "get_tools",
    "get_system_prompt", "get_response_template",
]
