"""Tests for app.bot.observability.context."""
from __future__ import annotations

import asyncio
import contextvars

import pytest

from app.bot.observability.context import (
    CONTEXT_KEYS,
    clear_request_context,
    get_request_context,
    set_request_context,
)


@pytest.fixture(autouse=True)
def _reset_context():
    """Ensure context is clean before and after every test."""
    clear_request_context()
    yield
    clear_request_context()


def test_context_defaults_are_none():
    ctx = get_request_context()
    assert list(ctx.keys()) == list(CONTEXT_KEYS)
    assert all(v is None for v in ctx.values())


def test_set_and_get_roundtrip():
    set_request_context(request_id="abc", conversation_id=42, phone_e164="+595981234567")
    ctx = get_request_context()
    assert ctx["request_id"] == "abc"
    assert ctx["conversation_id"] == 42
    assert ctx["phone_e164"] == "+595981234567"
    # unset keys stay None
    assert ctx["external_id"] is None
    assert ctx["channel"] is None


def test_set_raises_on_unknown_key():
    with pytest.raises(ValueError, match="foo"):
        set_request_context(foo="bar")


def test_clear_resets_all():
    set_request_context(request_id="xyz", channel="whatsapp")
    clear_request_context()
    ctx = get_request_context()
    assert all(v is None for v in ctx.values())


def test_context_isolated_across_tasks():
    """Child task gets a copy of context at creation time; mutations in child
    do not affect the parent's context."""
    set_request_context(request_id="parent-req", channel="telegram")

    result_inner: dict = {}

    async def inner():
        # Override in the child copy
        set_request_context(request_id="child-req")
        result_inner.update(get_request_context())

    async def run():
        ctx_copy = contextvars.copy_context()
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: ctx_copy.run(asyncio.run, inner()))
        # Parent context must be unchanged
        outer = get_request_context()
        assert outer["request_id"] == "parent-req"
        assert outer["channel"] == "telegram"

    asyncio.run(run())
    assert result_inner["request_id"] == "child-req"
