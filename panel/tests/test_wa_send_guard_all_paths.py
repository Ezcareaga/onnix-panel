"""WA_SEND_ENABLED must gate EVERY path that POSTs a WhatsApp to Twilio.

Context: staging (``onnix-panel-dev``) inherits production's ``.env`` via
``env_file: .env``, so any un-gated Twilio POST sends a REAL WhatsApp from the
production number to a real contact — the 2026-04-04 incident class.

``WhatsAppSender.send()`` already had the guard, but it is not the only door.
Three distinct code paths reach ``api.twilio.com/.../Messages.json``:

1. ``twilio_post_with_retry`` — the bot sender (``WhatsAppSender._post``) and
   the four InfoCasas welcome/recurrente senders.
2. ``TemplateService.send_template`` — panel "Confirmar envío" button and the
   ``followup_sender`` scheduler task; posts on the shared ``_http_client``.
3. ``ReplyService._send_twilio_whatsapp`` — panel manual reply; same client.

Each test pins: with ``WA_SEND_ENABLED=false`` the path must NOT issue the
HTTP request, must not raise, and must return its normal success shape so the
UI/caller keeps working.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.bot.channels.twilio_retry import twilio_post_with_retry
from app.services.reply_service import ReplyService
from app.services.template_service import TemplateService


TWILIO_URL = "https://api.twilio.com/2010-04-01/Accounts/ACtest/Messages.json"


def _exploding_transport() -> httpx.MockTransport:
    """Transport that fails the test if any request reaches it."""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError(
            "httpx POST was attempted while WA_SEND_ENABLED=false — "
            f"the guard MUST short-circuit before reaching {request.url}"
        )

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# 1. twilio_post_with_retry — bot sender + the 4 InfoCasas senders
# ---------------------------------------------------------------------------


async def test_twilio_post_with_retry_disabled_does_not_post(monkeypatch):
    """WA_SEND_ENABLED=false → no POST, benign success-shaped result."""
    monkeypatch.setenv("WA_SEND_ENABLED", "false")

    client = httpx.AsyncClient(transport=_exploding_transport())
    try:
        result = await twilio_post_with_retry(
            client=client,
            url=TWILIO_URL,
            data={"From": "whatsapp:+595900000000", "To": "whatsapp:+595981000125",
                  "ContentSid": "HXtest"},
            auth=("ACtest", "token"),
        )
    finally:
        await client.aclose()

    assert result.success is True   # callers gate side effects on .success
    assert result.attempts == 0     # zero attempts == nothing left the process
    assert result.response_json is None


async def test_twilio_post_with_retry_enabled_by_default_posts(monkeypatch):
    """WA_SEND_ENABLED unset (default true) → the POST IS issued."""
    monkeypatch.delenv("WA_SEND_ENABLED", raising=False)

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"sid": "SM_RETRY_OK", "status": "queued"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await twilio_post_with_retry(
            client=client,
            url=TWILIO_URL,
            data={"From": "whatsapp:+595900000000", "To": "whatsapp:+595981000125"},
            auth=("ACtest", "token"),
        )
    finally:
        await client.aclose()

    assert result.success is True
    assert result.attempts == 1
    assert len(seen) == 1, "default-true must reach the Twilio Messages endpoint"


# ---------------------------------------------------------------------------
# 2. TemplateService.send_template — panel button + followup_sender
# ---------------------------------------------------------------------------


async def _create_contact(db, phone: str):
    from app.models.contact import Contact
    c = Contact(
        name="Guard Test",
        phone=phone,
        phone_normalized=phone,
        source="manual",
        status="new",
        created_at=datetime.now(timezone.utc),
    )
    db.add(c)
    await db.flush()
    return c


async def _create_conversation(db, contact_id: int) -> int:
    from app.models.conversation import Conversation
    conv = Conversation(
        contact_id=contact_id,
        status="active",
        channel="whatsapp",
        platform="whatsapp",
        message_count=0,
        created_at=datetime.now(timezone.utc),
    )
    db.add(conv)
    await db.flush()
    return conv.id


def _patch_conv_manager(conv_id: int):
    conv_info = MagicMock()
    conv_info.id = conv_id
    mock_mgr = MagicMock()
    mock_mgr.get_or_create_conversation = AsyncMock(return_value=conv_info)
    mock_mgr.get_search_context = AsyncMock(return_value=MagicMock())
    mock_mgr.update_search_context = AsyncMock()
    return patch(
        "app.services.template_service.ConversationManager",
        return_value=mock_mgr,
    )


async def test_send_template_disabled_does_not_post(db, monkeypatch):
    """WA_SEND_ENABLED=false → send_template posts nothing, UI still redirects."""
    monkeypatch.setenv("WA_SEND_ENABLED", "false")

    contact = await _create_contact(db, phone="+595981900201")
    conv_id = await _create_conversation(db, contact.id)

    mock_client = AsyncMock()
    setting_patcher = patch(
        "app.services.template_service.BotSettingRepository.get_value",
        new_callable=AsyncMock,
        return_value="HX1234567890abcdef",
    )

    with patch("app.services.template_service._http_client", mock_client), \
            setting_patcher, _patch_conv_manager(conv_id):
        result = await TemplateService.send_template(
            db=db,
            contact_id=contact.id,
            template_key="wa_tpl_send_generic",
        )

    mock_client.post.assert_not_called()
    # The route does RedirectResponse(f"/conversations/{result['conversation_id']}")
    assert result["conversation_id"] == conv_id


# ---------------------------------------------------------------------------
# 3. ReplyService._send_twilio_whatsapp — panel manual reply
# ---------------------------------------------------------------------------


async def test_reply_service_disabled_does_not_post(monkeypatch):
    """WA_SEND_ENABLED=false → manual reply posts nothing, returns empty body."""
    monkeypatch.setenv("WA_SEND_ENABLED", "false")

    mock_client = AsyncMock()
    with patch("app.services.reply_service._http_client", mock_client):
        result = await ReplyService._send_twilio_whatsapp(
            "+595981900202", "hola, te confirmo la visita"
        )

    mock_client.post.assert_not_called()
    assert result.get("sid", "") == ""  # no Twilio SID because nothing was sent
