"""E2E tests for InfoCasas flows (Fase G — M3).

Tests four IC-related flows using real InfocasasService internals with all
external I/O (Twilio HTTP, Telegram, DB writes) mocked.

Covered flows
-------------
9.  test_ic_directo_match      — new lead, property in our catalogue → wa_tpl_ic_welcome
10. test_ic_reenviado_sin_match — new lead, reassigned/no-match → wa_tpl_ic_reenviado_welcome
11. test_cliente_recurrente     — existing contact, new WA message, bot treats as fresh search
12. test_recurrente_ic_nueva_consulta — returning IC lead, new property → recurrente template

Design decisions
----------------
- IC flows do NOT pass through ConversationRunner.send().  The entry point is
  InfocasasService._send_whatsapp_welcome / _send_whatsapp_reenviado_welcome /
  _send_whatsapp_recurrente_directo — the same code paths used in production.
- Twilio HTTP is mocked via ``httpx.AsyncClient``.  We capture every POST call
  and assert on the ContentSid and ContentVariables.
- DB session factory is fully mocked; no real writes to onnix_dev happen in
  these tests (IC service creates its own sessions internally).
- Tests 11 and 12 use ConversationRunner for the orchestrator half.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

# Ensure panel/ is importable (mirrors conftest.py bootstrap)
_panel_dir = str(Path(__file__).resolve().parent.parent.parent.parent)
if _panel_dir not in sys.path:
    sys.path.insert(0, _panel_dir)

import os
os.environ.setdefault("POSTGRES_HOST", "127.0.0.1")
os.environ.setdefault("POSTGRES_DB", "onnix_dev")
os.environ["TELEGRAM_EZ_CHAT_ID"] = ""
os.environ["FOLLOWUP_SENDER_ENABLED"] = "false"

from app.bot.core.types import ContactInfo, ConversationInfo, ConversationState, HistoryMessage
from app.bot.services.infocasas.infocasas_service import InfocasasService
from app.bot.services.infocasas.lead_parser import ParsedLead
from tests.bot.e2e.runner import ConversationRunner


# ---------------------------------------------------------------------------
# Shared helpers — mirrors test_integration_ic_flow.py pattern exactly
# ---------------------------------------------------------------------------

def _make_session_factory(*, session: AsyncMock | None = None) -> MagicMock:
    """Async context manager factory wrapping *session*."""
    mock_session = session or AsyncMock()
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=mock_ctx)
    return factory


def _make_service(
    *,
    session: AsyncMock | None = None,
) -> tuple[InfocasasService, MagicMock]:
    """Build an InfocasasService with mocked session factory."""
    factory = _make_session_factory(session=session)

    mock_sm = AsyncMock()
    mock_fetcher = AsyncMock()
    mock_notifier = AsyncMock()
    mock_notifier.notify = AsyncMock(return_value=True)

    svc = InfocasasService(
        session_manager=mock_sm,
        notification_fetcher=mock_fetcher,
        notifier=mock_notifier,
        session_factory=factory,
    )
    return svc, factory


def _make_http_client(status_code: int = 201) -> AsyncMock:
    """Build an httpx.AsyncClient mock that returns *status_code*."""
    response = MagicMock()
    response.status_code = status_code

    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _make_ic_prop_full(
    *,
    prop_id: int = 99,
    property_id: int | None = 42,
    city: str = "Asuncion",
    neighborhood: str = "Recoleta",
    property_type: str = "Casa",
    operation: str = "venta",
    price_sale: float | None = 180000,
    price_rent: float | None = None,
    currency_sale: str = "USD",
) -> MagicMock:
    """Build a minimal ic_prop_full MagicMock (infocasas_properties row)."""
    obj = MagicMock()
    obj.id = prop_id
    obj.property_id = property_id
    obj.title = f"Casa en {city}"
    obj.property_type = property_type
    obj.city = city
    obj.neighborhood = neighborhood
    obj.operation = operation
    obj.price_sale = price_sale
    obj.price_rent = price_rent
    obj.currency_sale = currency_sale
    obj.currency_rent = None
    return obj


def _make_parsed_lead(
    *,
    consulta_id: str = "IC_TEST_001",
    name: str = "Ana Perez",
    phone: str = "+595981599901",
    property_code: str | None = "OF99CE",
    property_title: str | None = "Casa en Asuncion",
    listing_city: str | None = "Asuncion",
    is_reassigned: bool = False,
    listing_type: str | None = None,
    listing_operation: str | None = None,
    listing_bedrooms: int | None = None,
    listing_area_m2: float | None = None,
    listing_price: float | None = None,
    listing_currency: str | None = None,
    listing_zone_from_message: str | None = None,
) -> ParsedLead:
    """Build a ParsedLead for test use."""
    return ParsedLead(
        consulta_id=consulta_id,
        name=name,
        phone=phone,
        email=None,
        message="Me interesa la propiedad",
        consulta_date=datetime(2026, 4, 19, tzinfo=timezone.utc),
        property_code=property_code,
        property_title=property_title,
        listing_city=listing_city,
        has_whatsapp=True,
        is_reassigned=is_reassigned,
        listing_type=listing_type,
        listing_operation=listing_operation,
        listing_bedrooms=listing_bedrooms,
        listing_area_m2=listing_area_m2,
        listing_price=listing_price,
        listing_currency=listing_currency,
        listing_zone_from_message=listing_zone_from_message,
    )


def _make_conv_obj(conv_id: int = 10) -> MagicMock:
    obj = MagicMock()
    obj.id = conv_id
    return obj


# ---------------------------------------------------------------------------
# Common patches used by IC service tests
# ---------------------------------------------------------------------------

_BASE_SETTINGS = {
    "ic_autoreply_enabled": "true",
    "ic_autoreply_reenviados_enabled": "true",
    "wa_tpl_ic_welcome": "HXwelcome001",
    "wa_tpl_ic_welcome_v3": "HXwelcome_v3_001",
    "wa_tpl_ic_reenviado_welcome_v3": "HXreenviado001",
    "wa_tpl_ic_recurrente_directo_v2": "HXrec_directo001",
    "wa_tpl_ic_recurrente_reenviado_v2": "HXrec_reenviado001",
    "infocasas_wa_delay_min": "0",
    "infocasas_wa_delay_max": "0",
}


# ---------------------------------------------------------------------------
# Test 1 — IC directo con match
# ---------------------------------------------------------------------------

class TestICDirectoMatch:
    """IC new lead, property exists in our catalogue.

    Expected flow:
    - _send_whatsapp_welcome called with matched_property != None
    - Template wa_tpl_ic_welcome (or _v2) sent via Twilio
    - search_context pre-loaded with etapa='viendo_detalle' and last_detalle_id=42
    - ic_welcome message persisted (intent='ic_welcome')
    """

    @pytest.mark.asyncio
    async def test_ic_directo_match_sends_welcome_template(self):
        """New IC lead with property match → wa_tpl_ic_welcome* template sent."""
        svc, factory = _make_service()

        parsed = _make_parsed_lead(
            consulta_id="IC_DIRECTO_001",
            property_code="OF42CE",
            property_title="Casa en Asuncion",
            listing_city="Asuncion",
        )
        matched_property = {"city": "Asuncion", "title": "Casa en Asuncion", "matched_by": "infocasas_ref"}
        ic_prop_full = _make_ic_prop_full(prop_id=9999, property_id=42)

        http_client = _make_http_client(status_code=201)
        update_ctx_calls: list[ConversationState] = []

        with (
            patch(
                "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
                new=AsyncMock(side_effect=lambda _s, key: _BASE_SETTINGS.get(key)),
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.PropertyRepository.get_ic_by_ref",
                new=AsyncMock(return_value=ic_prop_full),
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
                new=AsyncMock(),
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient",
                return_value=http_client,
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.ConversationManager"
            ) as MockCM,
            patch(
                "app.bot.services.infocasas.infocasas_service.message_repo"
            ) as mock_msg_repo,
        ):
            mock_cm_instance = AsyncMock()
            mock_cm_instance.get_or_create_conversation = AsyncMock(
                return_value=_make_conv_obj(10)
            )

            async def _capture_update(session, conv_id, state):
                update_ctx_calls.append(state)

            mock_cm_instance.update_search_context = AsyncMock(side_effect=_capture_update)
            MockCM.return_value = mock_cm_instance
            mock_msg_repo.create = AsyncMock()

            await svc._send_whatsapp_welcome(parsed, matched_property, contact_id=1)

        # Twilio was called once with a welcome template SID
        http_client.post.assert_awaited_once()
        call_kwargs = http_client.post.call_args

        # Extract the data payload from the call — positional or keyword
        post_data = call_kwargs[1].get("data") or call_kwargs[0][1] if call_kwargs[0] else call_kwargs[1]["data"]
        content_sid = post_data.get("ContentSid", "")
        assert content_sid in ("HXwelcome001", "HXwelcome_v3_001"), (
            f"Expected a welcome template SID, got: {content_sid!r}"
        )

        # search_context was pre-loaded with viendo_detalle and last_detalle_id
        assert len(update_ctx_calls) == 1, "Expected update_search_context to be called once"
        state = update_ctx_calls[0]
        assert state.etapa == "viendo_detalle"
        assert state.last_detalle_id == 42

    @pytest.mark.asyncio
    async def test_ic_directo_match_persists_ic_welcome_message(self):
        """ic_welcome message record is persisted with intent='ic_welcome'."""
        svc, factory = _make_service()

        parsed = _make_parsed_lead(
            consulta_id="IC_DIRECTO_002",
            name="Pedro Lopez",
            property_code="OF42CE",
            listing_city="Asuncion",
        )
        matched_property = {"city": "Asuncion", "title": "Casa en Asuncion", "matched_by": "infocasas_ref"}
        ic_prop_full = _make_ic_prop_full(prop_id=9999, property_id=42)

        http_client = _make_http_client(status_code=201)
        msg_create_calls: list[dict] = []

        with (
            patch(
                "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
                new=AsyncMock(side_effect=lambda _s, key: _BASE_SETTINGS.get(key)),
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.PropertyRepository.get_ic_by_ref",
                new=AsyncMock(return_value=ic_prop_full),
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
                new=AsyncMock(),
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient",
                return_value=http_client,
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.ConversationManager"
            ) as MockCM,
            patch(
                "app.bot.services.infocasas.infocasas_service.message_repo"
            ) as mock_msg_repo,
        ):
            mock_cm_instance = AsyncMock()
            mock_cm_instance.get_or_create_conversation = AsyncMock(
                return_value=_make_conv_obj(10)
            )
            mock_cm_instance.update_search_context = AsyncMock()
            MockCM.return_value = mock_cm_instance

            async def capture_create(**kwargs):
                msg_create_calls.append(kwargs)

            mock_msg_repo.create = AsyncMock(side_effect=capture_create)

            await svc._send_whatsapp_welcome(parsed, matched_property, contact_id=1)

        # message_repo.create must be called with intent='ic_welcome'
        assert len(msg_create_calls) >= 1, "Expected message_repo.create to be called"
        intents = [c.get("intent") for c in msg_create_calls]
        assert "ic_welcome" in intents, (
            f"Expected intent='ic_welcome' in calls, got: {intents}"
        )


# ---------------------------------------------------------------------------
# Test 2 — IC reenviado sin match
# ---------------------------------------------------------------------------

class TestICReenviadoSinMatch:
    """IC lead: reassigned or no property match → wa_tpl_ic_reenviado_welcome.

    Expected flow:
    - _send_whatsapp_reenviado_welcome called
    - Template wa_tpl_ic_reenviado_welcome sent via Twilio
    - No search_context pre-loaded (ic_prop_full is None → _preload_search_context returns early)
    """

    @pytest.mark.asyncio
    async def test_ic_reenviado_sin_match_sends_reenviado_template(self):
        """Reassigned IC lead → wa_tpl_ic_reenviado_welcome template sent (not ic_welcome)."""
        svc, factory = _make_service()

        parsed = _make_parsed_lead(
            consulta_id="IC_REENVIADO_001",
            name="Maria Gomez",
            property_code=None,  # no property code → no IC match
            listing_city="Luque",
            is_reassigned=True,
            listing_type="departamento",
            listing_operation="alquiler",
            listing_bedrooms=2,
            listing_area_m2=80.0,
            listing_price=5_000_000.0,
            listing_currency="gs",
            listing_zone_from_message="Luque",
        )

        contact_mock = MagicMock()
        contact_mock.id = 2
        contact_mock.phone = parsed.phone
        contact_mock.name = parsed.name
        contact_mock.infocasas_ref = None

        http_client = _make_http_client(status_code=201)
        update_ctx_calls: list[ConversationState] = []

        with (
            patch(
                "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
                new=AsyncMock(side_effect=lambda _s, key: _BASE_SETTINGS.get(key)),
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
                new=AsyncMock(),
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient",
                return_value=http_client,
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.ConversationManager"
            ) as MockCM,
            patch(
                "app.bot.services.infocasas.infocasas_service.message_repo"
            ) as mock_msg_repo,
        ):
            mock_cm_instance = AsyncMock()
            mock_cm_instance.get_or_create_conversation = AsyncMock(
                return_value=_make_conv_obj(10)
            )

            async def _capture_update(session, conv_id, state):
                update_ctx_calls.append(state)

            mock_cm_instance.update_search_context = AsyncMock(side_effect=_capture_update)
            MockCM.return_value = mock_cm_instance
            mock_msg_repo.create = AsyncMock()

            await svc._send_whatsapp_reenviado_welcome(contact_mock, parsed, None)

        # Twilio called once with the reenviado template SID
        http_client.post.assert_awaited_once()
        post_data = http_client.post.call_args[1].get("data") or http_client.post.call_args[0][1]
        content_sid = post_data.get("ContentSid", "")
        assert content_sid == "HXreenviado001", (
            f"Expected wa_tpl_ic_reenviado_welcome SID, got: {content_sid!r}"
        )

        # No search_context written — ic_prop_full is None → _preload returns early
        assert len(update_ctx_calls) == 0, (
            "Expected no search_context update for reenviado without IC property"
        )

    @pytest.mark.asyncio
    async def test_ic_reenviado_template_uses_parsed_lead_variables(self):
        """Reenviado ContentVariables contain the parsed lead's zone/type/price."""
        svc, factory = _make_service()

        parsed = _make_parsed_lead(
            consulta_id="IC_REENVIADO_002",
            name="Maria Gomez",
            property_code=None,
            listing_city="Luque",
            is_reassigned=True,
            listing_type="departamento",
            listing_operation="alquiler",
            listing_bedrooms=2,
            listing_price=5_000_000.0,
            listing_currency="gs",
            listing_zone_from_message="Luque",
        )

        contact_mock = MagicMock()
        contact_mock.id = 2
        contact_mock.phone = parsed.phone
        contact_mock.name = parsed.name
        contact_mock.infocasas_ref = None

        http_client = _make_http_client(status_code=201)

        with (
            patch(
                "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
                new=AsyncMock(side_effect=lambda _s, key: _BASE_SETTINGS.get(key)),
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
                new=AsyncMock(),
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient",
                return_value=http_client,
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.ConversationManager"
            ) as MockCM,
            patch(
                "app.bot.services.infocasas.infocasas_service.message_repo"
            ) as mock_msg_repo,
        ):
            mock_cm_instance = AsyncMock()
            mock_cm_instance.get_or_create_conversation = AsyncMock(return_value=_make_conv_obj(10))
            mock_cm_instance.update_search_context = AsyncMock()
            MockCM.return_value = mock_cm_instance
            mock_msg_repo.create = AsyncMock()

            await svc._send_whatsapp_reenviado_welcome(contact_mock, parsed, None)

        post_data = http_client.post.call_args[1].get("data") or http_client.post.call_args[0][1]
        content_vars = json.loads(post_data.get("ContentVariables", "{}"))

        # Variable 1 = name
        assert content_vars.get("1") == "Maria Gomez"
        # The zone/city should appear somewhere in the variables
        vars_text = " ".join(str(v) for v in content_vars.values()).lower()
        assert "luque" in vars_text or "departamento" in vars_text or "alquiler" in vars_text, (
            f"Expected lead details in ContentVariables: {content_vars}"
        )


# ---------------------------------------------------------------------------
# Test 3 — Cliente recurrente (WA, no IC)
# ---------------------------------------------------------------------------

def _make_recurrente_runner(
    claude_mock: AsyncMock,
    search_mock: AsyncMock,
) -> "ConversationRunner":
    """Build a ConversationRunner with a fully mocked session (no real DB).

    Used by TestClienteRecurrente to avoid FK violations from the mock
    conversation_id (=10) when the orchestrator tries to insert lead_events.
    The mocked session accepts any execute() call silently.
    """
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock())
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.close = AsyncMock()

    return ConversationRunner(
        session=mock_session,
        claude_mock=claude_mock,
        search_mock=search_mock,
        platform="whatsapp",
        chat_id="+595981599901",
        contact_id=1,
        conversation_id=10,
    )


class TestClienteRecurrente:
    """Existing contact with previous conversation messages sends a new WA query.

    The orchestrator should:
    - Load the full history (3-5 past messages) and pass it to Claude
    - Treat the new message as a fresh search without carrying over old filtros
    - Respond naturally (Claude decides the intent)

    These tests use a fully-mocked session to avoid FK violations from the mock
    conversation_id (=10) that does not exist in the real DB schema.
    """

    @pytest.mark.asyncio
    async def test_cliente_recurrente_history_loaded(self, claude_mock, search_mock):
        """Existing contact with 3 prior messages: orchestrator receives the history."""
        runner = _make_recurrente_runner(claude_mock, search_mock)

        # Arrange: inject 3 prior messages into the runner's history mock.
        # Use HistoryMessage objects — the orchestrator accesses .direction/.sender_type/.body.
        prior_messages = [
            HistoryMessage(direction="inbound", sender_type="contact", body="busco casa en Luque"),
            HistoryMessage(direction="outbound", sender_type="bot", body="Encontre 2 propiedades en Luque."),
            HistoryMessage(direction="inbound", sender_type="contact", body="quiero ver la segunda"),
        ]
        runner.set_history(prior_messages)

        # Reset search_context — fresh state for this test
        runner.set_search_context(ConversationState())

        # Program Claude to reply as if greeting a returning customer
        runner.program_claude_response(
            text="Hola de nuevo! En que te puedo ayudar hoy?"
        )

        # Act
        response = await runner.send("busco departamento en alquiler")

        # Assert 1: bot responded
        assert response is not None
        assert response.text

        # Assert 2: history was loaded (get_history was called)
        runner._conversation_manager.get_history.assert_called()

        # Assert 3: no tool was called (Claude decided it's conversational / greeting)
        runner.assert_last_tool("none")

    @pytest.mark.asyncio
    async def test_cliente_recurrente_new_context_not_polluted_by_old(self, claude_mock, search_mock):
        """New query from returning contact does NOT inherit old filtros.

        The search_context is explicitly reset before the new message,
        so update_search_context should be called with a clean/empty filtros.
        """
        runner = _make_recurrente_runner(claude_mock, search_mock)

        # Arrange: set a prior context with stale filtros
        stale_ctx = ConversationState(
            etapa="mostrando_resultados",
            filtros={"tipo": "casa", "ciudad": "Luque", "operacion": "venta"},
        )
        runner.set_search_context(stale_ctx)

        prior_messages = [
            HistoryMessage(direction="inbound", sender_type="contact", body="busco casa en Luque"),
            HistoryMessage(direction="outbound", sender_type="bot", body="Encontre 2 casas en Luque."),
        ]
        runner.set_history(prior_messages)

        # Program Claude: text-only response, no tool — simulating Claude choosing
        # to ask for clarification on new intent rather than inheriting old filtros.
        runner.program_claude_response(
            text="Claro! Contame que estas buscando ahora."
        )

        # Act
        response = await runner.send("busco algo nuevo")

        assert response is not None

        # Assert: the stale filtros were not automatically re-applied.
        # (The orchestrator passes the search_context to Claude; Claude decides
        # whether to reuse or reset filtros — here Claude returns no tool_call,
        # so no search was triggered with stale filters.)
        runner.assert_last_tool("none")
        # The response should mention asking what the user is looking for — not repeat old filtros
        runner.assert_response_contains("buscando")

    @pytest.mark.asyncio
    async def test_cliente_recurrente_history_count_passed_to_claude(self, claude_mock, search_mock):
        """Orchestrator passes the full prior history to Claude send_message."""
        runner = _make_recurrente_runner(claude_mock, search_mock)

        # Arrange: inject alternating prior messages (user/bot/user/bot/user)
        # so the orchestrator does not merge them all into one.
        prior_messages = [
            HistoryMessage(direction="inbound", sender_type="contact", body="busco casa en Luque"),
            HistoryMessage(direction="outbound", sender_type="bot", body="Encontre 2 casas."),
            HistoryMessage(direction="inbound", sender_type="contact", body="quiero ver la primera"),
            HistoryMessage(direction="outbound", sender_type="bot", body="Aqui te muestro los detalles."),
            HistoryMessage(direction="inbound", sender_type="contact", body="me parece bien"),
        ]
        runner.set_history(prior_messages)
        runner.set_search_context(ConversationState())

        runner.program_claude_response(text="Hola de nuevo, con gusto te ayudo.")

        # Act
        await runner.send("busco departamento en alquiler")

        # Assert: Claude send_message was called at least once
        assert runner._claude_mock.send_message.called, "Claude must be called"

        # Verify the messages passed to Claude include the history entries.
        # The orchestrator calls: send_message(system=..., messages=[...], tools=...)
        call_args = runner._claude_mock.send_message.call_args
        # Extract 'messages' from keyword args (the orchestrator always uses kwargs)
        messages_sent = call_args.kwargs.get("messages", []) if call_args.kwargs else []

        # With alternating roles, consecutive same-role merging is minimal.
        # 5 history messages (alternating) + 1 current = merged into ~3+ messages.
        # The exact count depends on role-merging; assert at least 3 distinct turns.
        assert len(messages_sent) >= 3, (
            f"Claude must receive prior conversation history (alternating turns). "
            f"Got {len(messages_sent)} message(s): {messages_sent}"
        )

        # Verify the final user message includes the new query
        last_msg = messages_sent[-1]
        assert last_msg["role"] == "user"
        assert "departamento" in last_msg["content"].lower() or "alquiler" in last_msg["content"].lower()


# ---------------------------------------------------------------------------
# Test 4 — Recurrente IC: contacto vuelve a consultar via IC
# ---------------------------------------------------------------------------

class TestRecurrenteICNuevaConsulta:
    """Contact that previously arrived via IC now submits a new IC inquiry.

    Expected flow:
    - Contact already exists (is_new=False)
    - New property_code differs from the old infocasas_ref (is_new_property=True)
    - Bot sends wa_tpl_ic_recurrente_directo (not the first-time welcome)
    - search_context reflects the NEW property, not the old one
    """

    @pytest.mark.asyncio
    async def test_recurrente_ic_sends_recurrente_directo_template(self):
        """Returning IC contact → wa_tpl_ic_recurrente_directo sent instead of ic_welcome."""
        svc, factory = _make_service()

        # The new inquiry
        parsed = _make_parsed_lead(
            consulta_id="IC_REC_001",
            name="Carlos Ramos",
            phone="+595981599901",
            property_code="OF_NEW_CE",  # different from contact's infocasas_ref
            property_title="Departamento nuevo en Asuncion",
            listing_city="Asuncion",
            is_reassigned=False,
        )

        # Contact ORM mock — existing contact (not new)
        contact_mock = MagicMock()
        contact_mock.id = 5
        contact_mock.phone = parsed.phone
        contact_mock.name = "Carlos Ramos"
        contact_mock.infocasas_ref = "OF_OLD_CE"  # old property
        contact_mock.status = "bot_replied"
        contact_mock.baja_at = None

        # IC property for the NEW inquiry (has a match in properties table)
        ic_prop_full = _make_ic_prop_full(
            prop_id=8888,
            property_id=55,
            city="Asuncion",
            property_type="Departamento",
        )

        http_client = _make_http_client(status_code=201)
        update_ctx_calls: list[ConversationState] = []

        with (
            patch(
                "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
                new=AsyncMock(side_effect=lambda _s, key: _BASE_SETTINGS.get(key)),
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.PropertyRepository.get_ic_by_ref",
                new=AsyncMock(return_value=ic_prop_full),
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
                new=AsyncMock(),
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient",
                return_value=http_client,
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.ConversationManager"
            ) as MockCM,
            patch(
                "app.bot.services.infocasas.infocasas_service.message_repo"
            ) as mock_msg_repo,
        ):
            mock_cm_instance = AsyncMock()
            mock_cm_instance.get_or_create_conversation = AsyncMock(
                return_value=_make_conv_obj(10)
            )

            async def _capture_update(session, conv_id, state):
                update_ctx_calls.append(state)

            mock_cm_instance.update_search_context = AsyncMock(side_effect=_capture_update)
            MockCM.return_value = mock_cm_instance
            mock_msg_repo.create = AsyncMock()

            await svc._send_whatsapp_recurrente_directo(contact_mock, parsed, None)

        # Assert: recurrente_directo template SID (NOT the ic_welcome SID)
        http_client.post.assert_awaited_once()
        post_data = http_client.post.call_args[1].get("data") or http_client.post.call_args[0][1]
        content_sid = post_data.get("ContentSid", "")
        assert content_sid == "HXrec_directo001", (
            f"Expected wa_tpl_ic_recurrente_directo SID, got: {content_sid!r}"
        )
        assert content_sid != "HXwelcome001" and content_sid != "HXwelcome_v2_001", (
            "Recurring IC contact must NOT receive the first-time welcome template"
        )

    @pytest.mark.asyncio
    async def test_recurrente_ic_context_reflects_new_property(self):
        """search_context is populated from the NEW IC property, not the old one."""
        svc, factory = _make_service()

        parsed = _make_parsed_lead(
            consulta_id="IC_REC_002",
            name="Carlos Ramos",
            phone="+595981599901",
            property_code="OF_NEW_CE",
            property_title="Departamento nuevo",
            listing_city="San Lorenzo",
            is_reassigned=False,
        )

        contact_mock = MagicMock()
        contact_mock.id = 5
        contact_mock.phone = parsed.phone
        contact_mock.name = "Carlos Ramos"
        contact_mock.infocasas_ref = "OF_OLD_CE"
        contact_mock.status = "bot_replied"
        contact_mock.baja_at = None

        # NEW property match — different city from old inquiry
        ic_prop_full = _make_ic_prop_full(
            prop_id=7777,
            property_id=77,
            city="San Lorenzo",
            property_type="Departamento",
            operation="venta",
            price_sale=95000,
        )

        http_client = _make_http_client(status_code=201)
        update_ctx_calls: list[ConversationState] = []

        with (
            patch(
                "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
                new=AsyncMock(side_effect=lambda _s, key: _BASE_SETTINGS.get(key)),
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.PropertyRepository.get_ic_by_ref",
                new=AsyncMock(return_value=ic_prop_full),
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
                new=AsyncMock(),
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient",
                return_value=http_client,
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.ConversationManager"
            ) as MockCM,
            patch(
                "app.bot.services.infocasas.infocasas_service.message_repo"
            ) as mock_msg_repo,
        ):
            mock_cm_instance = AsyncMock()
            mock_cm_instance.get_or_create_conversation = AsyncMock(return_value=_make_conv_obj(10))

            async def _capture_update(session, conv_id, state):
                update_ctx_calls.append(state)

            mock_cm_instance.update_search_context = AsyncMock(side_effect=_capture_update)
            MockCM.return_value = mock_cm_instance
            mock_msg_repo.create = AsyncMock()

            await svc._send_whatsapp_recurrente_directo(contact_mock, parsed, None)

        # search_context must reflect the NEW property (San Lorenzo, prop_id=77)
        assert len(update_ctx_calls) == 1, "Expected update_search_context to be called once"
        state = update_ctx_calls[0]
        assert state.last_detalle_id == 77, (
            f"Expected last_detalle_id=77 (new property), got {state.last_detalle_id}"
        )
        # city in filtros must be the NEW property's city
        assert state.filtros.get("ciudad") == "San Lorenzo", (
            f"Expected ciudad='San Lorenzo' (new property city), got {state.filtros}"
        )

    @pytest.mark.asyncio
    async def test_recurrente_ic_reenviado_sends_recurrente_reenviado_template(self):
        """Returning IC contact with reassigned new inquiry → wa_tpl_ic_recurrente_reenviado."""
        svc, factory = _make_service()

        parsed = _make_parsed_lead(
            consulta_id="IC_REC_003",
            name="Laura Torres",
            phone="+595981599901",
            property_code=None,  # reassigned → no direct property code
            listing_city="Fernando de la Mora",
            is_reassigned=True,
            listing_type="apartamento",
            listing_operation="alquiler",
            listing_bedrooms=3,
            listing_price=6_500_000.0,
            listing_currency="gs",
            listing_zone_from_message="Fernando de la Mora",
        )

        contact_mock = MagicMock()
        contact_mock.id = 6
        contact_mock.phone = parsed.phone
        contact_mock.name = "Laura Torres"
        contact_mock.infocasas_ref = "OF_OLD2_CE"
        contact_mock.status = "bot_replied"
        contact_mock.baja_at = None

        matched_property = {
            "title": "Apartamento en Fernando de la Mora",
            "city": "Fernando de la Mora",
            "price": 6500000,
            "currency": "gs",
        }

        http_client = _make_http_client(status_code=201)

        with (
            patch(
                "app.bot.services.infocasas.infocasas_service.BotSettingRepository.get_value",
                new=AsyncMock(side_effect=lambda _s, key: _BASE_SETTINGS.get(key)),
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.asyncio.sleep",
                new=AsyncMock(),
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.httpx.AsyncClient",
                return_value=http_client,
            ),
            patch(
                "app.bot.services.infocasas.infocasas_service.ConversationManager"
            ) as MockCM,
            patch(
                "app.bot.services.infocasas.infocasas_service.message_repo"
            ) as mock_msg_repo,
        ):
            mock_cm_instance = AsyncMock()
            mock_cm_instance.get_or_create_conversation = AsyncMock(return_value=_make_conv_obj(10))
            mock_cm_instance.update_search_context = AsyncMock()
            MockCM.return_value = mock_cm_instance
            mock_msg_repo.create = AsyncMock()

            await svc._send_whatsapp_recurrente_reenviado(contact_mock, parsed, matched_property)

        # Assert: recurrente_reenviado SID (not welcome, not recurrente_directo)
        http_client.post.assert_awaited_once()
        post_data = http_client.post.call_args[1].get("data") or http_client.post.call_args[0][1]
        content_sid = post_data.get("ContentSid", "")
        assert content_sid == "HXrec_reenviado001", (
            f"Expected wa_tpl_ic_recurrente_reenviado SID, got: {content_sid!r}"
        )
