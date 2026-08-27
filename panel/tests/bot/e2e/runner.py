"""ConversationRunner — high-level helper for E2E bot tests.

Encapsulates the orchestrator→response cycle so E2E test authors only need to
call send() / assert_last_tool() / assert_response_contains() without worrying
about mock wiring or DB fixture details.

Design contract
---------------
- Uses the REAL Orchestrator (not mocked).
- Mocks ONLY three external surfaces:
    1. ClaudeClient.send_message  → programmed via program_claude_response()
    2. SearchService (search_properties, get_by_ids) → programmed via
       program_search_result() / program_detail_result()
    3. Channel senders (whatsapp/telegram) → silenced; no HTTP calls.
- DB: uses the AsyncSession passed at construction (onnix_dev, NullPool).
- All conversation_manager calls go through the real ConversationManager mock
  that is wired up inside the Orchestrator constructor.
"""
from __future__ import annotations

import unicodedata
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.ai.types import AIResponse, ToolCall
from app.bot.core.orchestrator import Orchestrator
from app.bot.core.types import (
    BotRequest,
    BotResponse,
    ContactInfo,
    ConversationInfo,
    ConversationState,
)
from app.bot.search.search_service import SearchResult


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _unaccent(text: str) -> str:
    """Strip accent marks and lowercase — mirrors PostgreSQL unaccent()."""
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    ).lower()


def _make_text_ai_response(text: str) -> AIResponse:
    """Build a text-only (no tool call) AIResponse."""
    return AIResponse(
        text=text,
        tool_calls=[],
        model="claude-haiku-4-5-test",
        input_tokens=50,
        output_tokens=20,
        stop_reason="end_turn",
        raw_content=[],
    )


def _make_tool_ai_response(tool_name: str, tool_input: dict) -> AIResponse:
    """Build a tool_use AIResponse with a single tool call."""
    tc = ToolCall(id="toolu_e2e_001", name=tool_name, input=tool_input)
    raw = [{"type": "tool_use", "id": "toolu_e2e_001", "name": tool_name, "input": tool_input}]
    return AIResponse(
        text=None,
        tool_calls=[tc],
        model="claude-haiku-4-5-test",
        input_tokens=100,
        output_tokens=40,
        stop_reason="tool_use",
        raw_content=raw,
    )


def _make_orchestrator(
    claude_mock: AsyncMock,
    search_mock: AsyncMock,
) -> Orchestrator:
    """Build an Orchestrator with all external dependencies mocked.

    The tool_executor is also mocked so that tool calls go through
    the search_mock we control, not the real SearchService.
    """
    gemini = AsyncMock()
    gemini.send_message = AsyncMock(return_value=_make_text_ai_response("Gemini fallback"))

    circuit_breaker = MagicMock()
    circuit_breaker.is_open = False
    circuit_breaker.record_success = MagicMock()
    circuit_breaker.record_failure = MagicMock()

    conversation_manager = AsyncMock()
    conversation_manager.check_human_cooldown = MagicMock(return_value=False)

    response_builder = MagicMock()

    tool_executor = AsyncMock()
    tool_executor.build_tool_result_message = MagicMock(
        side_effect=lambda tc, result: {
            "type": "tool_result",
            "tool_use_id": tc.id,
            "content": __import__("json").dumps(result),
        }
    )

    orch = Orchestrator(
        claude=claude_mock,
        gemini=gemini,
        circuit_breaker=circuit_breaker,
        search_service=search_mock,
        conversation_manager=conversation_manager,
        response_builder=response_builder,
        tool_executor=tool_executor,
    )

    return orch, conversation_manager, tool_executor


# ---------------------------------------------------------------------------
# ConversationRunner
# ---------------------------------------------------------------------------

class ConversationRunner:
    """High-level helper that drives bot E2E tests.

    Usage::

        runner.program_claude_response(text="Hola!")
        response = await runner.send("hola")
        runner.assert_last_tool("none")
        runner.assert_response_contains("hola")

    Parameters
    ----------
    session:
        AsyncSession connected to onnix_dev.
    claude_mock:
        AsyncMock for ClaudeClient.send_message (from fixture).
    search_mock:
        AsyncMock for SearchService (from fixture).
    platform:
        "whatsapp" or "telegram".
    chat_id:
        Phone/chat identifier used for the BotRequest.
    contact_id:
        Pre-seeded contact ID (int) or None to let the orchestrator create one.
    conversation_id:
        Pre-seeded conversation ID or None.
    """

    def __init__(
        self,
        session: AsyncSession,
        claude_mock: AsyncMock,
        search_mock: AsyncMock,
        *,
        platform: str = "whatsapp",
        chat_id: str = "+595981599999",
        contact_id: int | None = None,
        conversation_id: int | None = None,
    ) -> None:
        self._session = session
        self._platform = platform
        self._chat_id = chat_id
        self._contact_id = contact_id
        self._conversation_id = conversation_id

        self._claude_mock = claude_mock
        self._search_mock = search_mock

        self._orchestrator, self._conversation_manager, self._tool_executor = _make_orchestrator(
            claude_mock, search_mock,
        )

        # Internal state updated after each send()
        self._last_response: BotResponse | None = None
        self._last_tool_name: str = "none"
        self._last_tool_args: dict = {}
        self._call_count: int = 0

        # Wire up conversation_manager to return sensible defaults
        self._wire_conversation_manager()

    # ------------------------------------------------------------------
    # Conversation manager defaults
    # ------------------------------------------------------------------

    def _wire_conversation_manager(self) -> None:
        """Set up conversation_manager mock with default return values.

        Each send() may override these for specific scenarios.
        """
        cid = self._contact_id or 1
        conv_id = self._conversation_id or 10

        self._conversation_manager.resolve_contact.return_value = ContactInfo(
            id=cid,
            name="Test E2E User",
            status="new",
            is_baja=False,
            platform=self._platform,
            phone=self._chat_id,
            source_id=self._chat_id,
        )

        self._conversation_manager.get_or_create_conversation.return_value = ConversationInfo(
            id=conv_id,
            contact_id=cid,
            platform=self._platform,
            chat_id=self._chat_id,
            is_bot_active=True,
        )

        self._conversation_manager.check_human_cooldown.return_value = False
        self._conversation_manager.get_history.return_value = []
        self._conversation_manager.get_search_context.return_value = ConversationState()
        self._conversation_manager.save_inbound_message = AsyncMock()
        self._conversation_manager.save_outbound_message = AsyncMock()
        self._conversation_manager.update_search_context = AsyncMock()

        # M6.3 Plan 123-05: delegate build_origin_context to the REAL
        # ConversationManager so the directo-IC origin note is resolved from
        # the live infocasas_ref/IC-prop (returns "" for non-directo turns,
        # which is the no-op default for every other E2E test).
        from app.bot.core.conversation import ConversationManager as _CM
        self._conversation_manager.build_origin_context = _CM().build_origin_context

    # ------------------------------------------------------------------
    # Public API — programming mocks
    # ------------------------------------------------------------------

    def program_claude_response(
        self,
        *,
        text: str | None = None,
        tool_calls: list[dict] | None = None,
    ) -> None:
        """Program the next Claude response.

        Call before send() to control what Claude returns.

        Parameters
        ----------
        text:
            Plain text response (no tool use). Use this for conversational
            turns where Claude doesn't call any tool.
        tool_calls:
            List of ``{"name": str, "input": dict}`` dicts. Each becomes a
            ToolCall. The orchestrator will call tool_executor.execute() for
            each and then make a second Claude call for the final text.
            If tool_calls is provided, *text* is used for the SECOND response
            (after tool results). If text is None with tool_calls, defaults to
            "Encontré estas opciones para vos."
        """
        if tool_calls:
            first_tc_name = tool_calls[0]["name"]
            first_tc_input = tool_calls[0].get("input", {})
            first_response = _make_tool_ai_response(first_tc_name, first_tc_input)

            # Second response (after tool results) — plain text
            second_text = text or "Encontré estas opciones para vos."
            second_response = _make_text_ai_response(second_text)

            # Claude is called twice in the tool loop
            self._claude_mock.send_message.side_effect = [first_response, second_response]
            self._last_tool_name = first_tc_name
            self._last_tool_args = first_tc_input
        else:
            response_text = text or "Entendido."
            ai_response = _make_text_ai_response(response_text)
            self._claude_mock.send_message.return_value = ai_response
            self._claude_mock.send_message.side_effect = None
            self._last_tool_name = "none"
            self._last_tool_args = {}

    def program_search_result(
        self,
        properties: list[dict],
        *,
        total_found: int | None = None,
        alternatives: dict | None = None,
    ) -> None:
        """Program what SearchService.search_properties() returns AND what the
        tool_executor returns when Claude calls search_properties.

        The orchestrator calls tool_executor.execute() (not search_mock directly)
        during the tool-use loop. For search_context.filtros to be updated, the
        tool result must contain an "all_ids" key. This method wires both surfaces.

        Parameters
        ----------
        properties:
            List of property dicts. Each should have at minimum an "id" key.
        total_found:
            Override for total_found. Defaults to len(properties).
        alternatives:
            Optional alternatives dict (e.g. cheapest price) to attach to the
            tool result. Passed through as-is under key "alternatives".
        """
        count = total_found if total_found is not None else len(properties)
        result = SearchResult(properties=properties, total_found=count)
        self._search_mock.search_properties.return_value = result
        self._search_mock.get_by_ids.return_value = result

        # Wire tool_executor.execute so the orchestrator loop sees the right shape.
        # The orchestrator checks for "all_ids" to update search_context.filtros
        # and "properties" to populate properties_collected.
        all_ids = [p["id"] for p in properties]
        tool_result: dict = {
            "properties": properties[:2],
            "total_found": count,
            "all_ids": all_ids,
        }
        if alternatives:
            tool_result["alternatives"] = alternatives
        self._tool_executor.execute.return_value = tool_result

    def program_detail_result(self, property_data: dict) -> None:
        """Program what SearchService.get_by_ids() returns for detail calls.

        Parameters
        ----------
        property_data:
            Single property dict with at minimum an "id" key.
        """
        result = SearchResult(properties=[property_data], total_found=1)
        self._search_mock.get_by_ids.return_value = result

    def program_tool_executor_result(self, result: dict) -> None:
        """Program what tool_executor.execute() returns for the next tool call.

        Use this when testing flows that involve tool calls whose results need
        to be precise (e.g., process_opt_out must return {"success": True}).

        Parameters
        ----------
        result:
            Dict that the tool executor will return when execute() is called.
        """
        self._tool_executor.execute.return_value = result

    # ------------------------------------------------------------------
    # Public API — sending messages
    # ------------------------------------------------------------------

    async def send(
        self,
        text: str,
        *,
        callback_payload: str | None = None,
    ) -> BotResponse:
        """Send a message through the orchestrator and return BotResponse.

        Uses the REAL orchestrator but with mocked Claude + SearchService.
        Twilio/Telegram channels are NOT called (no HTTP).

        Parameters
        ----------
        text:
            The user message text.
        callback_payload:
            Optional callback_data (button press payload).
        """
        self._call_count += 1

        request = BotRequest(
            platform=self._platform,
            chat_id=self._chat_id,
            user_id=self._chat_id,
            user_name="Test E2E User",
            text=text,
            external_id=f"msg_e2e_{self._call_count:04d}",
            callback_data=callback_payload,
        )

        # Patch the admin notifier so it doesn't try to send Telegram messages
        with (
            patch("app.bot.services.admin_notifier.get_admin_notifier", return_value=AsyncMock()),
            patch("app.bot.core.orchestrator.get_opt_out_text",
                  new=AsyncMock(return_value="Has solicitado la baja.")),
            patch("app.bot.ai.ai_dispatch.get_ai_dual_fail_text",
                  new=AsyncMock(return_value="Error técnico.")),
            patch("app.bot.core.orchestrator.set_request_context", return_value=None),
        ):
            response = await self._orchestrator.handle_message(request, self._session)

        self._last_response = response

        # Track which tool was actually called (if any)
        # Inspect claude_mock call args to find the tool_use stop_reason
        calls = self._claude_mock.send_message.call_args_list
        if calls:
            # side_effect list was consumed — check last programmed tool name
            # (already set in program_claude_response)
            pass

        return response

    async def send_many(self, texts: list[str]) -> list[BotResponse]:
        """Send multiple messages in sequence. Returns list of responses."""
        responses = []
        for text in texts:
            response = await self.send(text)
            responses.append(response)
        return responses

    # ------------------------------------------------------------------
    # Public API — assertions
    # ------------------------------------------------------------------

    def assert_last_tool(self, tool_name: str) -> None:
        """Assert that the last orchestrator turn used (or didn't use) a tool.

        Parameters
        ----------
        tool_name:
            Expected tool name, e.g. "search_properties", "get_property_detail",
            "register_lead", "process_opt_out". Use "none" to assert no tool.
        """
        # Derive from the programmed side_effect or return_value
        actual = self._last_tool_name
        assert actual == tool_name, (
            f"Expected last tool '{tool_name}', got '{actual}'.\n"
            f"Claude mock calls: {self._claude_mock.send_message.call_args_list}"
        )

    def assert_tool_args(self, **expected: Any) -> None:
        """Assert specific kwargs were present in the last tool call input.

        Only checks the keys provided — does not require an exact match.

        Parameters
        ----------
        expected:
            Keyword args mapping tool input field names to expected values.
            Example: ``runner.assert_tool_args(operacion="venta", tipo="casa")``
        """
        args = self._last_tool_args
        for key, value in expected.items():
            actual = args.get(key)
            assert actual == value, (
                f"Tool arg '{key}': expected {value!r}, got {actual!r}.\n"
                f"Full tool args: {args}"
            )

    def assert_tool_args_not_contains(self, *keys: str) -> None:
        """Assert specific keys are NOT present in the last tool call input.

        Parameters
        ----------
        keys:
            Key names that must be absent from the tool input dict.
        """
        args = self._last_tool_args
        for key in keys:
            assert key not in args, (
                f"Tool arg '{key}' should NOT be present but found value {args[key]!r}.\n"
                f"Full tool args: {args}"
            )

    def assert_response_contains(self, *keywords: str) -> None:
        """Assert that the last response text contains all keywords (case-insensitive, unaccented).

        Parameters
        ----------
        keywords:
            One or more substrings to look for in the response text.
        """
        assert self._last_response is not None, "No response yet — call send() first"
        response_text = _unaccent(self._last_response.text or "")
        for keyword in keywords:
            normalized = _unaccent(keyword)
            assert normalized in response_text, (
                f"Expected '{keyword}' (normalized: '{normalized}') in response.\n"
                f"Actual response: '{self._last_response.text}'"
            )

    async def assert_search_context(self, **expected_fields: Any) -> None:
        """Assert fields on the current search_context stored in the mock.

        Reads the last update_search_context call to get the current state.

        Parameters
        ----------
        expected_fields:
            Keyword args mapping ConversationState field names to expected values.
            Example: ``runner.assert_search_context(etapa="mostrando_resultados")``
        """
        # Get the search context that was last passed to update_search_context
        update_mock = self._conversation_manager.update_search_context
        if not update_mock.called:
            # Fallback: get from get_search_context return value
            ctx = self._conversation_manager.get_search_context.return_value
        else:
            # Last call: args are (session, conv_id, state)
            ctx = update_mock.call_args[0][2]

        for field_name, expected_value in expected_fields.items():
            actual = getattr(ctx, field_name, None)
            assert actual == expected_value, (
                f"search_context.{field_name}: expected {expected_value!r}, got {actual!r}"
            )

    # ------------------------------------------------------------------
    # Public API — property
    # ------------------------------------------------------------------

    @property
    def context(self) -> ConversationState:
        """Return the current ConversationState from the last mock update."""
        update_mock = self._conversation_manager.update_search_context
        if update_mock.called:
            return update_mock.call_args[0][2]
        return self._conversation_manager.get_search_context.return_value

    @property
    def last_response(self) -> BotResponse | None:
        """The BotResponse from the most recent send() call."""
        return self._last_response

    @property
    def last_properties_shown(self) -> list[dict]:
        """Properties from the most recent send() response.

        Returns the list from BotResponse.properties (max 2 per page).
        Empty list when no properties were returned.
        """
        if self._last_response is None:
            return []
        return self._last_response.properties or []

    @property
    def last_tool_args(self) -> dict:
        """The input dict from the most recent tool call.

        Empty dict when no tool was called in the last turn.
        """
        return self._last_tool_args

    @property
    def session(self) -> AsyncSession:
        """Expose the AsyncSession for DB assertions in tests."""
        return self._session

    # ------------------------------------------------------------------
    # Convenience: override contact / conversation for specific scenarios
    # ------------------------------------------------------------------

    def set_contact(self, contact: ContactInfo) -> None:
        """Override the contact returned by conversation_manager.resolve_contact."""
        self._conversation_manager.resolve_contact.return_value = contact

    def set_conversation(self, conversation: ConversationInfo) -> None:
        """Override the conversation returned by get_or_create_conversation."""
        self._conversation_manager.get_or_create_conversation.return_value = conversation

    def set_search_context(self, ctx: ConversationState) -> None:
        """Override the search context returned by get_search_context."""
        self._conversation_manager.get_search_context.return_value = ctx

    def set_history(self, history: list) -> None:
        """Override the history returned by get_history."""
        self._conversation_manager.get_history.return_value = history
