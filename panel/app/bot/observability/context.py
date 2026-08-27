"""Request-scoped context variables for the bot engine.

These ContextVars are set at the start of each inbound request (webhook
entry point) and read by JsonFormatter to enrich every log record emitted
during that request, even across awaited calls.

Usage::

    from app.bot.observability.context import set_request_context, clear_request_context

    set_request_context(request_id="abc123", channel="whatsapp", phone_e164="+595981234567")
    # ... handle request ...
    clear_request_context()
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

# ---------------------------------------------------------------------------
# Context variables
# ---------------------------------------------------------------------------

request_id: ContextVar[str | None] = ContextVar("bot_request_id", default=None)
external_id: ContextVar[str | None] = ContextVar("bot_external_id", default=None)
channel: ContextVar[str | None] = ContextVar("bot_channel", default=None)
conversation_id: ContextVar[int | None] = ContextVar("bot_conversation_id", default=None)
phone_e164: ContextVar[str | None] = ContextVar("bot_phone_e164", default=None)

# Ordered tuple used as the whitelist for set/get helpers.
CONTEXT_KEYS: tuple[str, ...] = (
    "request_id",
    "external_id",
    "channel",
    "conversation_id",
    "phone_e164",
)

_VARS: dict[str, ContextVar[Any]] = {
    "request_id": request_id,
    "external_id": external_id,
    "channel": channel,
    "conversation_id": conversation_id,
    "phone_e164": phone_e164,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def set_request_context(**kwargs: Any) -> None:
    """Set one or more context variables by name.

    Args:
        **kwargs: Keyword arguments whose keys must be members of
            ``CONTEXT_KEYS``.

    Raises:
        ValueError: If any key is not in ``CONTEXT_KEYS``.
    """
    unknown = set(kwargs) - set(CONTEXT_KEYS)
    if unknown:
        raise ValueError(
            f"Unknown context key(s): {sorted(unknown)}. "
            f"Allowed: {list(CONTEXT_KEYS)}"
        )
    for key, value in kwargs.items():
        _VARS[key].set(value)


def get_request_context() -> dict[str, Any]:
    """Return the current values of all five context variables.

    Returns:
        A dict with all five ``CONTEXT_KEYS`` as keys; unset vars have
        ``None`` as value.
    """
    return {key: _VARS[key].get() for key in CONTEXT_KEYS}


def clear_request_context() -> None:
    """Reset all five context variables to ``None``."""
    for var in _VARS.values():
        var.set(None)
