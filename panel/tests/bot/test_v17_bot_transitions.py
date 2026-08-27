"""GSD v17 — tests de transiciones bot_replied/agent_replied en orchestrator.

Tests:
1. Bot reply to 'new' contact → status update uses 'bot_replied'
2. Bot reply to 'no_response' contact → status update uses 'bot_replied'
3. Bot does NOT change status of 'interested' contact
4. Client message to 'agent_replied' contact → reactivation called
5. Reactivation creates lead_event 'client_responded_to_agent'
6. Change C: 'bot_replied' contact can advance to 'interested' via lead
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from app.bot.core.orchestrator import Orchestrator
from app.bot.state.bot_gate import reactivate_from_agent_replied
from app.bot.core.types import (
    BotRequest,
    BotResponse,
    ContactInfo,
    ConversationInfo,
    ConversationState,
)
from app.bot.ai.types import AIResponse, ToolCall


# ---------------------------------------------------------------------------
# Shared helpers (mirror test_orchestrator.py patterns)
# ---------------------------------------------------------------------------

def _make_orchestrator():
    """Create an Orchestrator with all dependencies mocked."""
    claude = AsyncMock()
    gemini = AsyncMock()
    circuit_breaker = MagicMock()
    circuit_breaker.is_open = False
    search_service = AsyncMock()
    conversation_manager = AsyncMock()
    conversation_manager.check_human_cooldown = MagicMock(return_value=False)
    response_builder = MagicMock()
    tool_executor = AsyncMock()
    tool_executor.build_tool_result_message = MagicMock()

    orch = Orchestrator(
        claude=claude,
        gemini=gemini,
        circuit_breaker=circuit_breaker,
        search_service=search_service,
        conversation_manager=conversation_manager,
        response_builder=response_builder,
        tool_executor=tool_executor,
    )

    return orch, {
        "claude": claude,
        "gemini": gemini,
        "circuit_breaker": circuit_breaker,
        "search_service": search_service,
        "conversation_manager": conversation_manager,
        "response_builder": response_builder,
        "tool_executor": tool_executor,
    }


def _make_contact(status: str = "new", is_baja: bool = False) -> ContactInfo:
    return ContactInfo(
        id=42, name="Test User", status=status, is_baja=is_baja,
        platform="whatsapp", phone="+595981000001", source_id="+595981000001",
    )


def _make_conversation(is_bot_active: bool = True) -> ConversationInfo:
    return ConversationInfo(
        id=99, contact_id=42, platform="whatsapp", chat_id="+595981000001",
        is_bot_active=is_bot_active,
    )


def _make_request(text: str = "Busco casa") -> BotRequest:
    return BotRequest(
        platform="whatsapp", chat_id="+595981000001", user_id="+595981000001",
        user_name="Test User", text=text, external_id="WAmsg_001",
    )


def _text_ai_response(text: str = "Hola!") -> AIResponse:
    return AIResponse(
        text=text, tool_calls=[], model="claude-haiku",
        input_tokens=100, output_tokens=25,
        stop_reason="end_turn", raw_content=[],
    )


def _setup_normal_flow(mocks, contact=None, conversation=None):
    """Configure mocks for a normal (non-short-circuit) flow."""
    mocks["conversation_manager"].resolve_contact.return_value = (
        contact or _make_contact()
    )
    mocks["conversation_manager"].get_or_create_conversation.return_value = (
        conversation or _make_conversation()
    )
    mocks["conversation_manager"].check_human_cooldown.return_value = False
    mocks["conversation_manager"].get_history.return_value = []
    mocks["conversation_manager"].get_search_context.return_value = ConversationState()


# ===========================================================================
# Test 1: 'new' contact → status SQL uses 'bot_replied'
# ===========================================================================

class TestBotRepliedStatusTransition:
    """GSD v17: auto-advance after bot reply uses 'bot_replied' not 'contacted'."""

    @pytest.mark.asyncio
    async def test_new_contact_gets_bot_replied(self):
        """After bot replies to a 'new' contact, SQL sets status = 'bot_replied'."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks, contact=_make_contact(status="new"))
        mocks["claude"].send_message.return_value = _text_ai_response("Hola!")

        session = AsyncMock()
        await orch.handle_message(_make_request(), session)

        # Collect all SQL executed on the session
        all_sql_calls = [str(c.args[0]) for c in session.execute.call_args_list]
        # At least one SQL call should mention 'bot_replied'
        bot_replied_updates = [s for s in all_sql_calls if "bot_replied" in s]
        assert bot_replied_updates, (
            "Expected at least one SQL UPDATE with 'bot_replied', got: "
            + str(all_sql_calls)
        )
        # Crucially, no call should still set 'contacted' (old v16 value)
        contacted_updates = [
            s for s in all_sql_calls
            if "SET status = 'contacted'" in s
        ]
        assert not contacted_updates, (
            "Found legacy 'contacted' UPDATE — should be 'bot_replied': "
            + str(contacted_updates)
        )

    @pytest.mark.asyncio
    async def test_no_response_contact_gets_bot_replied(self):
        """After bot replies to a 'no_response' contact, SQL uses 'bot_replied'."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks, contact=_make_contact(status="no_response"))
        mocks["claude"].send_message.return_value = _text_ai_response("Hola!")

        session = AsyncMock()
        await orch.handle_message(_make_request(), session)

        all_sql_calls = [str(c.args[0]) for c in session.execute.call_args_list]
        bot_replied_updates = [s for s in all_sql_calls if "bot_replied" in s]
        assert bot_replied_updates, (
            "Expected SQL with 'bot_replied' for no_response contact"
        )


# ===========================================================================
# Test 3: 'interested' contact is NOT downgraded
# ===========================================================================

class TestInterestedContactNotDowngraded:
    """Bot reply must NOT change status of 'interested' (or higher) contact."""

    @pytest.mark.asyncio
    async def test_interested_contact_not_regressed(self):
        """Status update SQL for 'interested' contact must NOT execute."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks, contact=_make_contact(status="interested"))
        mocks["claude"].send_message.return_value = _text_ai_response("Hola!")

        session = AsyncMock()
        await orch.handle_message(_make_request(), session)

        # The guard condition is: status IN ('new', 'no_response')
        # So no UPDATE to bot_replied should fire for 'interested'
        all_sql = [str(c.args[0]) for c in session.execute.call_args_list]
        auto_advance = [
            s for s in all_sql
            if "bot_replied" in s and "UPDATE contacts SET status" in s
        ]
        assert not auto_advance, (
            "Auto-advance SQL should NOT fire for 'interested' contact: "
            + str(auto_advance)
        )


# ===========================================================================
# Test 4: 'agent_replied' contact → reactivation is called
# ===========================================================================

class TestAgentRepliedReactivation:
    """GSD v17: when agent_replied contact sends a message, bot reactivates."""

    @pytest.mark.asyncio
    async def test_agent_replied_triggers_reactivation(self):
        """handle_message with agent_replied contact calls _reactivate_from_agent_replied."""
        orch, mocks = _make_orchestrator()
        contact = _make_contact(status="agent_replied")
        _setup_normal_flow(mocks, contact=contact)
        mocks["claude"].send_message.return_value = _text_ai_response("Hola!")

        with patch(
            "app.bot.core.orchestrator.reactivate_from_agent_replied",
            new_callable=AsyncMock,
        ) as mock_reactivate:
            session = AsyncMock()
            await orch.handle_message(_make_request(), session)

        mock_reactivate.assert_awaited_once()
        call_args = mock_reactivate.call_args
        # session, contact, conversation — check contact is agent_replied
        assert call_args.args[1].status == "agent_replied"

    @pytest.mark.asyncio
    async def test_non_agent_replied_skips_reactivation(self):
        """handle_message with 'new' contact does NOT call _reactivate_from_agent_replied."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks, contact=_make_contact(status="new"))
        mocks["claude"].send_message.return_value = _text_ai_response("Hola!")

        with patch(
            "app.bot.core.orchestrator.reactivate_from_agent_replied",
            new_callable=AsyncMock,
        ) as mock_reactivate:
            await orch.handle_message(_make_request(), AsyncMock())

        mock_reactivate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_agent_replied_contact_then_processes_normally(self):
        """After reactivation, bot processes message normally and returns BotResponse."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks, contact=_make_contact(status="agent_replied"))
        mocks["claude"].send_message.return_value = _text_ai_response("Te ayudo!")

        with patch("app.bot.core.orchestrator.reactivate_from_agent_replied", new_callable=AsyncMock):
            result = await orch.handle_message(_make_request(), AsyncMock())

        assert result is not None
        assert "ayudo" in result.text


# ===========================================================================
# Test 5: _reactivate_from_agent_replied — unit test for the function itself
# ===========================================================================

class TestReactivateFromAgentRepliedFunction:
    """Unit tests for the _reactivate_from_agent_replied module-level function."""

    @pytest.mark.asyncio
    async def test_creates_lead_event_client_responded_to_agent(self):
        """Reactivation creates a lead_event with type 'client_responded_to_agent'."""
        contact = _make_contact(status="agent_replied")
        conversation = _make_conversation()
        session = AsyncMock()
        # Simulate contact NOT opted out (baja_at IS NULL) so the race-#3 guard
        # in reactivate_from_agent_replied lets the reactivation proceed.
        session.execute.return_value = MagicMock(scalar=MagicMock(return_value=None))

        with patch(
            "app.bot.state.bot_gate.lead_event_repo",
        ) as mock_repo:
            mock_repo.create = AsyncMock()
            with patch("app.bot.state.bot_gate.get_admin_notifier") as mock_notifier_factory:
                mock_notifier = AsyncMock()
                mock_notifier_factory.return_value = mock_notifier

                await reactivate_from_agent_replied(session, contact, conversation)

        mock_repo.create.assert_awaited_once()
        call_kwargs = mock_repo.create.call_args.kwargs
        assert call_kwargs["event_type"] == "client_responded_to_agent"
        assert call_kwargs["old_status"] == "agent_replied"
        assert call_kwargs["new_status"] == "bot_replied"
        assert call_kwargs["triggered_by"] == "bot"

    @pytest.mark.asyncio
    async def test_updates_contact_status_in_memory(self):
        """Reactivation mutates contact.status to 'bot_replied' in memory."""
        contact = _make_contact(status="agent_replied")
        conversation = _make_conversation()
        session = AsyncMock()
        # Simulate contact NOT opted out (baja_at IS NULL) so the race-#3 guard
        # in reactivate_from_agent_replied lets the reactivation proceed.
        session.execute.return_value = MagicMock(scalar=MagicMock(return_value=None))

        with patch("app.bot.state.bot_gate.lead_event_repo") as mock_repo:
            mock_repo.create = AsyncMock()
            with patch("app.bot.state.bot_gate.get_admin_notifier") as mock_notifier_factory:
                mock_notifier_factory.return_value = AsyncMock()
                await reactivate_from_agent_replied(session, contact, conversation)

        assert contact.status == "bot_replied"

    @pytest.mark.asyncio
    async def test_updates_db_via_sql(self):
        """Reactivation executes SQL to update contacts and conversations tables."""
        contact = _make_contact(status="agent_replied")
        conversation = _make_conversation()
        session = AsyncMock()
        # Simulate contact NOT opted out (baja_at IS NULL) so the race-#3 guard
        # in reactivate_from_agent_replied lets the reactivation proceed.
        session.execute.return_value = MagicMock(scalar=MagicMock(return_value=None))

        with patch("app.bot.state.bot_gate.lead_event_repo") as mock_repo:
            mock_repo.create = AsyncMock()
            with patch("app.bot.state.bot_gate.get_admin_notifier") as mock_notifier_factory:
                mock_notifier_factory.return_value = AsyncMock()
                await reactivate_from_agent_replied(session, contact, conversation)

        assert session.execute.call_count >= 1
        all_sql = [str(c.args[0]) for c in session.execute.call_args_list]
        contact_update = [s for s in all_sql if "bot_replied" in s and "contacts" in s]
        assert contact_update, "Expected SQL UPDATE on contacts to 'bot_replied'"

    @pytest.mark.asyncio
    async def test_notifier_failure_does_not_raise(self):
        """AdminNotifier error during reactivation is swallowed — never raises."""
        contact = _make_contact(status="agent_replied")
        conversation = _make_conversation()
        session = AsyncMock()
        # Simulate contact NOT opted out (baja_at IS NULL) so the race-#3 guard
        # in reactivate_from_agent_replied lets the reactivation proceed.
        session.execute.return_value = MagicMock(scalar=MagicMock(return_value=None))

        with patch("app.bot.state.bot_gate.lead_event_repo") as mock_repo:
            mock_repo.create = AsyncMock()
            with patch("app.bot.state.bot_gate.get_admin_notifier") as mock_notifier_factory:
                mock_notifier_factory.side_effect = RuntimeError("Notifier exploded")
                # Must NOT raise
                await reactivate_from_agent_replied(session, contact, conversation)


# ===========================================================================
# Test 6: Change C — 'bot_replied' contact can advance to 'interested'
# ===========================================================================

class TestInterestedGuardIncludesBotReplied:
    """GSD v17 Change C: 'bot_replied' and 'agent_replied' contacts can become 'interested'."""

    @pytest.mark.asyncio
    async def test_bot_replied_contact_can_become_interested(self):
        """register_lead tool with 'bot_replied' contact triggers interested SQL."""
        orch, mocks = _make_orchestrator()
        _setup_normal_flow(mocks, contact=_make_contact(status="bot_replied"))

        # Claude returns register_lead tool call
        lead_tool_response = AIResponse(
            text=None,
            tool_calls=[ToolCall(id="t1", name="register_lead", input={"motivo": "Quiero visitar"})],
            model="claude-haiku",
            input_tokens=100,
            output_tokens=30,
            stop_reason="tool_use",
            raw_content=[{"type": "tool_use", "id": "t1", "name": "register_lead", "input": {}}],
        )
        text_response = _text_ai_response("Un asesor te contactará.")
        mocks["claude"].send_message.side_effect = [lead_tool_response, text_response]
        mocks["tool_executor"].execute.return_value = {
            "success": True, "motivo": "Quiero visitar", "message": "Lead registrado",
        }
        mocks["tool_executor"].build_tool_result_message.return_value = {
            "type": "tool_result", "tool_use_id": "t1", "content": "{}",
        }

        session = AsyncMock()
        result = await orch.handle_message(_make_request(), session)

        assert result is not None
        assert result.is_lead is True

        # Verify the SQL for interested transition includes 'bot_replied'
        all_sql = [str(c.args[0]) for c in session.execute.call_args_list]
        interested_sql = [
            s for s in all_sql
            if "interested" in s and "UPDATE contacts" in s
        ]
        assert interested_sql, "Expected SQL UPDATE to 'interested' for bot_replied contact"
        # The guard must include bot_replied
        assert any("bot_replied" in s for s in interested_sql), (
            "SQL for interested transition must include 'bot_replied' in guard: "
            + str(interested_sql)
        )
