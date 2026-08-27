"""WA_SEND_ENABLED staging guard for WhatsAppSender.send() (Phase 124-01).

Defense-in-depth against the 2026-04-04 incident class: staging inherits prod
Twilio creds via ``env_file: .env``, so a bot reply in staging could make a
REAL WhatsApp send. The guard short-circuits ``send()`` BEFORE any httpx POST
when ``WA_SEND_ENABLED`` is explicitly false/0/empty.

Two behaviors are pinned:
1. Disabled  → ``send()`` does NOT reach httpx and returns ``True`` (benign,
   matches the normal success contract) without raising.
2. Default   → with WA_SEND_ENABLED unset (true by default), ``send()`` reaches
   the httpx POST path (proves prod behavior is byte-unchanged).
"""
from __future__ import annotations

import httpx
import pytest

from app.bot.channels.whatsapp import WhatsAppSender
from app.bot.core.types import ChannelPayload, PayloadMessage


CHAT_ID = "+595981000124"


def _payload() -> ChannelPayload:
    return ChannelPayload(
        messages=[PayloadMessage(text="hola, te muestro una propiedad")],
        channel="whatsapp",
    )


def _exploding_transport() -> httpx.MockTransport:
    """A transport whose handler fails the test if any request reaches it."""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError(
            "httpx POST was attempted while WA_SEND_ENABLED=false — "
            f"the guard MUST short-circuit before reaching {request.url}"
        )

    return httpx.MockTransport(handler)


def _recording_transport(seen: list[httpx.Request]) -> httpx.MockTransport:
    """A transport that records each request and returns a Twilio-success body."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"sid": "SM124", "status": "queued"})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_send_disabled_does_not_call_httpx(monkeypatch):
    """WA_SEND_ENABLED=false → no httpx POST, returns True, no raise."""
    monkeypatch.setenv("WA_SEND_ENABLED", "false")

    client = httpx.AsyncClient(transport=_exploding_transport())
    sender = WhatsAppSender(
        account_sid="ACtest",
        auth_token="token",
        from_number="whatsapp:+595900000000",
        client=client,
    )
    try:
        result = await sender.send(_payload(), CHAT_ID)
    finally:
        await client.aclose()

    assert result is True  # benign success-shaped sentinel; callers don't crash


@pytest.mark.asyncio
async def test_send_enabled_by_default_reaches_httpx(monkeypatch):
    """WA_SEND_ENABLED unset (default true) → httpx POST IS attempted."""
    monkeypatch.delenv("WA_SEND_ENABLED", raising=False)

    seen: list[httpx.Request] = []
    client = httpx.AsyncClient(transport=_recording_transport(seen))
    sender = WhatsAppSender(
        account_sid="ACtest",
        auth_token="token",
        from_number="whatsapp:+595900000000",
        client=client,
    )
    try:
        result = await sender.send(_payload(), CHAT_ID)
    finally:
        await client.aclose()

    assert result is True
    assert len(seen) == 1, "default-true must reach the Twilio Messages endpoint"
    assert seen[0].url.path.endswith("/Messages.json")
