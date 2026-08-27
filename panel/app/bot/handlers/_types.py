"""Handler contract types for M4 refactor.

Each intent handler extracted from Orchestrator follows this signature:

    async def handle_<intent>(
        request: BotRequest,
        session: AsyncSession,
        contact: ContactInfo,
        conversation: ConversationInfo,
        search_context: ConversationState,
    ) -> HandlerResult:
        ...

The handler receives the current search_context and returns a new one
(via HandlerResult.search_context). It must NOT mutate the caller's
search_context dict/object. This keeps handlers pure and trivially
testable in isolation.

See PLAN_M4_REFACTOR.md §Fase 3 for the full extraction plan.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.bot.core.types import BotResponse


@dataclass
class HandlerResult:
    """Outcome of a single-intent handler.

    Attributes:
        response: BotResponse to return to the channel, or None when the
            orchestrator should fall through to the next stage (e.g. when
            a handler defers to the AI path).
        search_context: The search context to persist after this handler
            completes. Handlers may return the same instance they received
            if they didn't need to change anything, or a new instance with
            updates applied.
        events_to_record: Lead events the handler wants recorded (search,
            detail_view, etc.). The orchestrator persists them after the
            response is built.
    """

    response: "BotResponse | None"
    search_context: Any  # ConversationState, untyped here to avoid circular import
    events_to_record: list[dict] = field(default_factory=list)
