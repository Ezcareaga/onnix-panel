"""AI response types for bot AI layer.

Unified types that abstract provider-specific responses (Claude, Gemini)
into a common format used by the bot engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolCall:
    """A tool invocation requested by the AI model."""

    id: str
    name: str
    input: dict


@dataclass
class EmbeddingResult:
    """Result of an embedding request."""

    values: list[float]
    model: str


@dataclass
class AIResponse:
    """Unified AI response from any provider.

    Use classmethods ``from_claude`` / ``from_gemini`` to build
    from provider-specific SDK objects.

    ``latency_ms`` is NOT set by the factory classmethods — it is measured
    by the caller (client wrapper) around the actual API call and assigned
    after construction.
    """

    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    stop_reason: str = ""
    raw_content: list = field(default_factory=list)

    @classmethod
    def from_claude(cls, response) -> "AIResponse":
        """Parse an Anthropic SDK Message into an AIResponse.

        ``response`` is an ``anthropic.types.Message`` (or any object
        with the same attribute layout: ``.content``, ``.model``,
        ``.stop_reason``, ``.usage``).
        """
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        input=block.input,
                    )
                )

        return cls(
            text=" ".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=response.stop_reason,
            raw_content=list(response.content),
        )

    @classmethod
    def from_gemini(cls, response) -> "AIResponse":
        """Parse a Gemini SDK response into an AIResponse.

        Extracts text from the response, reads ``usage_metadata`` if
        available, and normalises into the common ``AIResponse`` shape.
        """
        text: str | None = None
        input_tokens = 0
        output_tokens = 0

        # Gemini responses expose .text for the generated content
        if hasattr(response, "text"):
            text = response.text

        if hasattr(response, "usage_metadata"):
            meta = response.usage_metadata
            input_tokens = getattr(meta, "prompt_token_count", 0) or 0
            output_tokens = getattr(meta, "candidates_token_count", 0) or 0

        return cls(
            text=text,
            tool_calls=[],
            model="gemini-flash",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason="end_turn",
        )

    @property
    def has_tool_calls(self) -> bool:
        """Return True if the response contains tool calls."""
        return len(self.tool_calls) > 0
