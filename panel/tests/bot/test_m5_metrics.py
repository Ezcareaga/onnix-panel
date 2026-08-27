"""Tests for M5 Fase I — lead_events metric emission.

Covers:
1. zero_results_offered emitted when _execute_search returns alternatives.
2. zero_results_accepted emitted with trigger=callback in
   handle_alternative_callback (happy path).
3. zero_results_accepted emitted with trigger=text when pending_alternatives
   match the filters Claude is sending.
4. No event emitted when search returns results without alternatives.
5. zero_results_abandoned emitted via orchestrator when TTL expires.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

_panel_dir = str(Path(__file__).resolve().parent.parent.parent)
if _panel_dir not in sys.path:
    sys.path.insert(0, _panel_dir)

from app.bot.ai.types import ToolCall
from app.bot.core.conversation import ConversationManager
from app.bot.core.tool_executor import ToolExecutor
from app.bot.core.types import BotResponse, ConversationState
from app.bot.handlers.alternatives import handle_alternative_callback
from app.bot.search.search_service import SearchResult
from app.bot.handlers._types import HandlerResult


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_zero_result() -> SearchResult:
    """SearchResult with 0 properties — triggers alternatives path."""
    return SearchResult(properties=[], total_found=0)


def _make_non_zero_result() -> SearchResult:
    return SearchResult(
        properties=[{
            "id": 1,
            "title": "Casa bonita",
            "city": "Asuncion",
            "operation": "venta",
            "property_type": "casa",
            "price_usd": 120000,
            "bedrooms": 3,
            "bathrooms": 2,
            "total_area_m2": 150,
            "source": "onnix",
            "external_id": "ext_1",
            "local_image_count": 2,
        }],
        total_found=1,
    )


def _make_alt(alt_id: str = "zona_vecina:lambare") -> dict:
    return {
        "id": alt_id,
        "label": "En Lambaré hay 8 deptos",
        "count": 8,
        "filters": {"ciudad": "lambare"},
        "reason": "zona vecina",
        "callback_payload": f"ALT:{alt_id}",
    }


def _make_executor_with_alts(
    search_result: SearchResult,
    alternatives: list | None = None,
) -> tuple[ToolExecutor, AsyncMock, AsyncMock]:
    """Return (executor, search_service_mock, bot_settings_mock)."""
    search_service = AsyncMock()
    search_service.search_properties.return_value = search_result
    search_service._geo_resolver = MagicMock()
    search_service._geo_resolver.resolve.return_value = MagicMock()

    bot_settings = AsyncMock()
    bot_settings.get_bool.return_value = True  # flag ON

    if alternatives is None:
        alternatives = [_make_alt()]

    alt_builder = AsyncMock()
    alt_result = MagicMock()
    from dataclasses import asdict
    from app.bot.search.alternatives import Alternative
    alt_dataclasses = [
        Alternative(
            id=a["id"],
            label=a["label"],
            count=a["count"],
            filters=a["filters"],
            reason=a["reason"],
            callback_payload=a["callback_payload"],
        )
        for a in alternatives
    ]
    alt_result.alternatives = alt_dataclasses
    alt_builder.build.return_value = alt_result

    executor = ToolExecutor(
        search_service=search_service,
        alternatives_builder=alt_builder,
        bot_settings_repo=bot_settings,
    )
    return executor, search_service, bot_settings


def _make_state(contact_id: int = 1, conversation_id: int = 42) -> ConversationState:
    state = ConversationState()
    state._contact_id = contact_id
    state._conversation_id = conversation_id
    return state


def _make_request(callback_data: str) -> MagicMock:
    from app.bot.core.types import BotRequest
    return BotRequest(
        platform="whatsapp",
        chat_id="+595981000001",
        user_id="+595981000001",
        user_name="Test User",
        callback_data=callback_data,
    )


def _make_contact(contact_id: int = 1) -> MagicMock:
    c = MagicMock()
    c.id = contact_id
    c.status = "bot_replied"
    return c


def _make_conversation(conv_id: int = 42) -> MagicMock:
    c = MagicMock()
    c.id = conv_id
    return c


# ---------------------------------------------------------------------------
# 1. zero_results_offered emitted when alternatives exist
# ---------------------------------------------------------------------------

class TestZeroResultsOfferedEmitted:
    @pytest.mark.asyncio
    async def test_zero_results_offered_emitted_with_alternatives(self):
        """_execute_search emits zero_results_offered when alternatives returned.

        Uses ciudad + tipo so active_keys >= 2 (operacion is excluded from count).
        """
        executor, _, _ = _make_executor_with_alts(_make_zero_result())
        state = _make_state()

        tc = ToolCall(
            id="toolu_01",
            name="search_properties",
            input={"ciudad": "Asuncion", "operacion": "venta", "tipo": "departamento"},
        )
        session = AsyncMock()

        with patch(
            "app.bot.core.tool_executor.record_event",
            new_callable=AsyncMock,
        ) as mock_record:
            result = await executor.execute(tc, session, search_context=state)

        # Verify alternatives returned in result
        assert "alternatives" in result
        assert result["total_found"] == 0

        # Verify record_event called with zero_results_offered
        offered_calls = [
            c for c in mock_record.call_args_list
            if c.kwargs.get("event_type") == "zero_results_offered"
        ]
        assert len(offered_calls) == 1, (
            f"Expected 1 zero_results_offered call, got {len(offered_calls)}"
        )
        kwargs = offered_calls[0].kwargs
        assert kwargs["contact_id"] == 1
        assert kwargs["conversation_id"] == 42
        assert kwargs["trigger"] == "zero_results"
        meta = kwargs["metadata"]
        assert "alternatives_count" in meta
        assert meta["alternatives_count"] == 1
        assert "alt_ids" in meta
        assert "filters" in meta

    @pytest.mark.asyncio
    async def test_offered_metadata_contains_alt_ids(self):
        """Metadata in zero_results_offered includes the correct alt_ids list."""
        alts = [_make_alt("zona_vecina:lambare"), _make_alt("presupuesto_20pct")]
        executor, _, _ = _make_executor_with_alts(_make_zero_result(), alternatives=alts)
        state = _make_state()

        tc = ToolCall(
            id="toolu_02",
            name="search_properties",
            input={"ciudad": "Asuncion", "operacion": "venta", "tipo": "departamento"},
        )
        session = AsyncMock()

        with patch(
            "app.bot.core.tool_executor.record_event",
            new_callable=AsyncMock,
        ) as mock_record:
            await executor.execute(tc, session, search_context=state)

        offered_calls = [
            c for c in mock_record.call_args_list
            if c.kwargs.get("event_type") == "zero_results_offered"
        ]
        assert len(offered_calls) == 1
        meta = offered_calls[0].kwargs["metadata"]
        assert meta["alternatives_count"] == 2
        assert "zona_vecina:lambare" in meta["alt_ids"]
        assert "presupuesto_20pct" in meta["alt_ids"]


# ---------------------------------------------------------------------------
# 2. zero_results_accepted — callback trigger
# ---------------------------------------------------------------------------

class TestZeroResultsAcceptedCallbackTrigger:
    @pytest.mark.asyncio
    async def test_zero_results_accepted_callback_trigger(self):
        """handle_alternative_callback emits zero_results_accepted with trigger=callback."""
        alt_id = "zona_vecina:lambare"
        state = ConversationState()
        state.pending_alternatives = [_make_alt(alt_id)]
        state.pending_alternatives_age = 0

        request = _make_request(f"ALT:{alt_id}")
        contact = _make_contact()
        conversation = _make_conversation()
        cm = ConversationManager()
        cm.update_search_context = AsyncMock()
        cm.save_outbound_message = AsyncMock()

        session = AsyncMock()
        session.execute = AsyncMock()

        fake_response = BotResponse(
            text="Encontré estas opciones",
            intent="busqueda_incompleta",
        )

        with patch(
            "app.bot.handlers.alternatives.handle_new_search",
            new_callable=AsyncMock,
            return_value=HandlerResult(response=fake_response, search_context=state),
        ), patch(
            "app.bot.handlers.alternatives.record_event",
            new_callable=AsyncMock,
        ) as mock_record:
            await handle_alternative_callback(
                request, session, contact, conversation, state,
                search_service=AsyncMock(),
                conversation_manager=cm,
            )

        accepted_calls = [
            c for c in mock_record.call_args_list
            if c.kwargs.get("event_type") == "zero_results_accepted"
        ]
        assert len(accepted_calls) == 1, (
            f"Expected 1 zero_results_accepted call, got {len(accepted_calls)}"
        )
        kwargs = accepted_calls[0].kwargs
        assert kwargs["contact_id"] == contact.id
        assert kwargs["conversation_id"] == conversation.id
        assert kwargs["trigger"] == "callback"
        assert kwargs["metadata"]["alt_id"] == alt_id
        assert kwargs["metadata"]["trigger"] == "callback"

    @pytest.mark.asyncio
    async def test_no_accepted_event_on_expired_alt(self):
        """Expired ALT callback (alt not found) must NOT emit accepted event."""
        state = ConversationState()  # no pending_alternatives

        request = _make_request("ALT:expired_alt")
        contact = _make_contact()
        conversation = _make_conversation()
        cm = ConversationManager()
        cm.update_search_context = AsyncMock()
        cm.save_outbound_message = AsyncMock()

        session = AsyncMock()
        session.execute = AsyncMock()

        with patch(
            "app.bot.handlers.alternatives.record_event",
            new_callable=AsyncMock,
        ) as mock_record:
            await handle_alternative_callback(
                request, session, contact, conversation, state,
                search_service=AsyncMock(),
                conversation_manager=cm,
            )

        accepted_calls = [
            c for c in mock_record.call_args_list
            if c.kwargs.get("event_type") == "zero_results_accepted"
        ]
        assert len(accepted_calls) == 0, (
            "Should NOT emit accepted event when alt not found"
        )


# ---------------------------------------------------------------------------
# 3. zero_results_accepted — text trigger
# ---------------------------------------------------------------------------

class TestZeroResultsAcceptedTextTrigger:
    @pytest.mark.asyncio
    async def test_zero_results_accepted_text_trigger(self):
        """_execute_search emits zero_results_accepted (text) when pending alt
        filters are a subset of the current call's filters."""
        # Pending alt: {"ciudad": "lambare"}
        # Current call: {"ciudad": "lambare", "operacion": "venta"}
        # "lambare" subset of current → accepted
        alt_id = "zona_vecina:lambare"
        state = _make_state()
        state.pending_alternatives = [{
            "id": alt_id,
            "label": "En Lambare",
            "count": 5,
            "filters": {"ciudad": "lambare"},
            "reason": "zona vecina",
            "callback_payload": f"ALT:{alt_id}",
        }]
        state.pending_alternatives_age = 1

        # Search returns results (non-zero) so no offered event fires
        search_service = AsyncMock()
        search_service.search_properties.return_value = _make_non_zero_result()

        executor = ToolExecutor(search_service=search_service)

        tc = ToolCall(
            id="toolu_text_01",
            name="search_properties",
            input={"ciudad": "lambare", "operacion": "venta"},
        )
        session = AsyncMock()

        with patch(
            "app.bot.core.tool_executor.record_event",
            new_callable=AsyncMock,
        ) as mock_record:
            await executor.execute(tc, session, search_context=state)

        accepted_calls = [
            c for c in mock_record.call_args_list
            if c.kwargs.get("event_type") == "zero_results_accepted"
        ]
        assert len(accepted_calls) == 1, (
            f"Expected 1 zero_results_accepted (text) call, got {len(accepted_calls)}"
        )
        kwargs = accepted_calls[0].kwargs
        assert kwargs["trigger"] == "text"
        assert kwargs["metadata"]["trigger"] == "text"
        assert kwargs["metadata"]["alt_id"] == alt_id
        assert kwargs["contact_id"] == 1
        assert kwargs["conversation_id"] == 42

    @pytest.mark.asyncio
    async def test_text_trigger_clears_pending_alternatives(self):
        """After text-trigger match, pending_alternatives must be cleared."""
        alt_id = "presupuesto_20pct"
        state = _make_state()
        state.pending_alternatives = [{
            "id": alt_id,
            "label": "Presupuesto 20% mas",
            "count": 4,
            "filters": {"precio_max": 120000, "moneda": "USD"},
            "reason": "presupuesto ajustado",
            "callback_payload": f"ALT:{alt_id}",
        }]
        state.pending_alternatives_age = 0

        search_service = AsyncMock()
        search_service.search_properties.return_value = _make_non_zero_result()
        executor = ToolExecutor(search_service=search_service)

        tc = ToolCall(
            id="toolu_text_02",
            name="search_properties",
            input={"precio_max": 120000, "moneda": "USD", "operacion": "venta"},
        )
        session = AsyncMock()

        with patch("app.bot.core.tool_executor.record_event", new_callable=AsyncMock):
            await executor.execute(tc, session, search_context=state)

        assert state.pending_alternatives == [], (
            "pending_alternatives must be cleared after text-trigger match"
        )
        assert state.pending_alternatives_age == 0


# ---------------------------------------------------------------------------
# 4. No event when search returns results (no alternatives path)
# ---------------------------------------------------------------------------

class TestNoEventWhenNoAlternatives:
    @pytest.mark.asyncio
    async def test_no_event_when_results_found(self):
        """When search returns properties, zero_results_offered must NOT fire."""
        search_service = AsyncMock()
        search_service.search_properties.return_value = _make_non_zero_result()
        executor = ToolExecutor(search_service=search_service)
        state = _make_state()

        tc = ToolCall(
            id="toolu_no_event_01",
            name="search_properties",
            input={"ciudad": "Asuncion", "operacion": "venta"},
        )
        session = AsyncMock()

        with patch(
            "app.bot.core.tool_executor.record_event",
            new_callable=AsyncMock,
        ) as mock_record:
            result = await executor.execute(tc, session, search_context=state)

        assert result["total_found"] == 1
        offered_calls = [
            c for c in mock_record.call_args_list
            if c.kwargs.get("event_type") == "zero_results_offered"
        ]
        assert len(offered_calls) == 0, (
            "zero_results_offered must NOT fire when results are found"
        )

    @pytest.mark.asyncio
    async def test_no_event_when_flag_off(self):
        """When feature flag is OFF, zero_results_offered must not fire even on 0 results."""
        search_service = AsyncMock()
        search_service.search_properties.return_value = _make_zero_result()
        search_service._geo_resolver = MagicMock()
        search_service._geo_resolver.resolve.return_value = MagicMock()

        bot_settings = AsyncMock()
        bot_settings.get_bool.return_value = False  # flag OFF

        alt_builder = AsyncMock()
        executor = ToolExecutor(
            search_service=search_service,
            alternatives_builder=alt_builder,
            bot_settings_repo=bot_settings,
        )
        state = _make_state()

        tc = ToolCall(
            id="toolu_flag_off_01",
            name="search_properties",
            input={"ciudad": "Asuncion", "operacion": "venta", "tipo": "departamento"},
        )
        session = AsyncMock()

        with patch(
            "app.bot.core.tool_executor.record_event",
            new_callable=AsyncMock,
        ) as mock_record:
            await executor.execute(tc, session, search_context=state)

        offered_calls = [
            c for c in mock_record.call_args_list
            if c.kwargs.get("event_type") == "zero_results_offered"
        ]
        assert len(offered_calls) == 0, (
            "zero_results_offered must NOT fire when feature flag is OFF"
        )

        # alt_builder.build should not have been called either
        alt_builder.build.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_event_when_no_contact_id(self):
        """When _contact_id is None on state, offered event is safely skipped."""
        executor, _, _ = _make_executor_with_alts(_make_zero_result())
        state = ConversationState()  # _contact_id defaults to None

        tc = ToolCall(
            id="toolu_no_contact_01",
            name="search_properties",
            input={"ciudad": "Asuncion", "operacion": "venta", "tipo": "departamento"},
        )
        session = AsyncMock()

        # Should not raise even though _contact_id is None
        with patch(
            "app.bot.core.tool_executor.record_event",
            new_callable=AsyncMock,
        ) as mock_record:
            result = await executor.execute(tc, session, search_context=state)

        assert "alternatives" in result
        offered_calls = [
            c for c in mock_record.call_args_list
            if c.kwargs.get("event_type") == "zero_results_offered"
        ]
        # No event because _contact_id is None (guard in tool_executor)
        assert len(offered_calls) == 0


# ---------------------------------------------------------------------------
# 5. zero_results_abandoned — orchestrator TTL expiry detection logic
# ---------------------------------------------------------------------------

class TestZeroResultsAbandonedOnTTLExpiry:
    @pytest.mark.asyncio
    async def test_tick_ttl_causes_clear_at_age_2(self):
        """Verify the TTL mechanics that trigger the abandoned event.

        The orchestrator captures alts_before_tick, calls tick, and emits
        the abandoned event if alts were cleared. This test verifies the
        state machine: after age=1, next tick expires and state clears.
        """
        mgr = ConversationManager()
        state = _make_state()
        alts = [_make_alt("zona_vecina:lambare")]
        mgr.set_pending_alternatives(state, alts)

        # First tick: age 0 → 1, alts still present
        _alts_before = list(state.pending_alternatives)
        mgr.tick_pending_alternatives_ttl(state)
        assert state.pending_alternatives_age == 1
        assert state.pending_alternatives == alts

        # Second tick: age 1 → 2 → clear
        _alts_before_second = list(state.pending_alternatives)
        mgr.tick_pending_alternatives_ttl(state)

        # Alts are now cleared — this is the condition for abandoned event
        assert state.pending_alternatives == []
        assert _alts_before_second  # was non-empty before tick
        # abandoned_should_fire == True
        assert _alts_before_second and not state.pending_alternatives

    @pytest.mark.asyncio
    async def test_abandoned_event_emitted_via_record_event(self):
        """Simulate the orchestrator's abandoned-event emission block.

        When _alts_before_tick is non-empty and pending_alternatives is
        cleared after tick, record_event is called with zero_results_abandoned.
        """
        from app.services.lead_event_service import record_event

        mgr = ConversationManager()
        state = _make_state()
        alts = [_make_alt("zona_vecina:lambare")]
        mgr.set_pending_alternatives(state, alts)
        state.pending_alternatives_age = 1  # force expiry on next tick

        _alts_before_tick = list(state.pending_alternatives)
        mgr.tick_pending_alternatives_ttl(state)
        assert state.pending_alternatives == [], "Pre-condition: tick should have expired alts"

        session = AsyncMock()

        with patch(
            "app.services.lead_event_service.LeadEventRepository.create",
            new_callable=AsyncMock,
        ) as mock_repo_create:
            # This is the exact block from orchestrator.handle_message
            if _alts_before_tick and not state.pending_alternatives:
                await record_event(
                    session,
                    contact_id=state._contact_id,
                    conversation_id=state._conversation_id,
                    event_type="zero_results_abandoned",
                    trigger="ttl_expired",
                    metadata={"alt_ids": [a.get("id") for a in _alts_before_tick]},
                )

        mock_repo_create.assert_called_once()
        call_kwargs = mock_repo_create.call_args
        assert call_kwargs.kwargs["event_type"] == "zero_results_abandoned"
        assert call_kwargs.kwargs["triggered_by"] == "ttl_expired"
        meta = call_kwargs.kwargs["metadata"]
        assert "zona_vecina:lambare" in meta.get("alt_ids", [])

    @pytest.mark.asyncio
    async def test_no_abandoned_if_alts_already_empty(self):
        """When state has no pending_alternatives, the abandoned guard is False."""
        mgr = ConversationManager()
        state = _make_state()
        # No alternatives set

        _alts_before = list(state.pending_alternatives)
        mgr.tick_pending_alternatives_ttl(state)

        # Guard: alts_before is empty → condition is False → no event emitted
        abandoned_should_fire = bool(_alts_before and not state.pending_alternatives)
        assert not abandoned_should_fire

    @pytest.mark.asyncio
    async def test_no_abandoned_if_alts_survive_tick(self):
        """When age goes 0 → 1 (not yet expired), alts still present, no event."""
        mgr = ConversationManager()
        state = _make_state()
        alts = [_make_alt("presupuesto_20pct")]
        mgr.set_pending_alternatives(state, alts)
        assert state.pending_alternatives_age == 0

        _alts_before = list(state.pending_alternatives)
        mgr.tick_pending_alternatives_ttl(state)

        # age=1, alts still present → guard is False (alts not cleared)
        abandoned_should_fire = bool(_alts_before and not state.pending_alternatives)
        assert not abandoned_should_fire
        assert state.pending_alternatives == alts  # still present


# ---------------------------------------------------------------------------
# 6. Text trigger — list-order-insensitive filter match
# ---------------------------------------------------------------------------

class TestTextTriggerListOrderInsensitive:
    @pytest.mark.asyncio
    async def test_text_trigger_matches_with_reordered_barrios_list(self):
        """Alt with barrios=["villa morra","carmelitas"], call with barrios=["carmelitas","villa morra"]
        → accepted event emitted (order should not matter)."""
        alt_id = "multi_barrio:vm_carmelitas"
        state = _make_state()
        state.pending_alternatives = [{
            "id": alt_id,
            "label": "Villa Morra o Carmelitas",
            "count": 6,
            "filters": {"barrios": ["villa morra", "carmelitas"]},
            "reason": "zona vecina",
            "callback_payload": f"ALT:{alt_id}",
        }]
        state.pending_alternatives_age = 1

        search_service = AsyncMock()
        search_service.search_properties.return_value = _make_non_zero_result()
        executor = ToolExecutor(search_service=search_service)

        # Claude sends barrios in reverse order
        tc = ToolCall(
            id="toolu_order_01",
            name="search_properties",
            input={"barrios": ["carmelitas", "villa morra"], "operacion": "venta"},
        )
        session = AsyncMock()

        with patch(
            "app.bot.core.tool_executor.record_event",
            new_callable=AsyncMock,
        ) as mock_record:
            await executor.execute(tc, session, search_context=state)

        accepted_calls = [
            c for c in mock_record.call_args_list
            if c.kwargs.get("event_type") == "zero_results_accepted"
        ]
        assert len(accepted_calls) == 1, (
            f"Expected 1 zero_results_accepted (reordered barrios), got {len(accepted_calls)}"
        )
        assert accepted_calls[0].kwargs["trigger"] == "text"

    @pytest.mark.asyncio
    async def test_text_trigger_no_match_when_key_missing(self):
        """Alt with barrio="lambare", call without barrio key → no accepted event."""
        alt_id = "zona_vecina:lambare"
        state = _make_state()
        state.pending_alternatives = [{
            "id": alt_id,
            "label": "En Lambaré",
            "count": 4,
            "filters": {"barrio": "lambare"},
            "reason": "zona vecina",
            "callback_payload": f"ALT:{alt_id}",
        }]
        state.pending_alternatives_age = 0

        search_service = AsyncMock()
        search_service.search_properties.return_value = _make_non_zero_result()
        executor = ToolExecutor(search_service=search_service)

        # Call without "barrio" key at all
        tc = ToolCall(
            id="toolu_missing_key_01",
            name="search_properties",
            input={"ciudad": "asuncion", "operacion": "venta"},
        )
        session = AsyncMock()

        with patch(
            "app.bot.core.tool_executor.record_event",
            new_callable=AsyncMock,
        ) as mock_record:
            await executor.execute(tc, session, search_context=state)

        accepted_calls = [
            c for c in mock_record.call_args_list
            if c.kwargs.get("event_type") == "zero_results_accepted"
        ]
        assert len(accepted_calls) == 0, (
            "Must NOT emit accepted when required key is absent from call"
        )
