"""Observability package for the bot engine.

Provides structured JSON logging and request context propagation via
contextvars for correlation across async tasks.
"""
from app.bot.observability.context import (
    CONTEXT_KEYS,
    clear_request_context,
    get_request_context,
    set_request_context,
)
from app.bot.observability.json_formatter import JsonFormatter

__all__ = [
    "CONTEXT_KEYS",
    "clear_request_context",
    "get_request_context",
    "JsonFormatter",
    "set_request_context",
]
