"""Global SSE endpoint for the Onnix SA admin panel.

GET /events streams every event published to the in-process EventBus,
regardless of event type. Intended for panel pages that need real-time
updates across multiple domains (conversations, leads, contacts, etc.).

Pattern is identical to /conversations/sse — same keepalive interval,
same auth, same unsubscribe-on-disconnect discipline.
"""
import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.dependencies import get_current_user
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()


def should_forward_event(
    event: dict[str, Any],
    *,
    is_agent: bool,
    user_id: int | None,
) -> bool:
    """Return True if this SSE event should be forwarded to the connected user.

    Ownership rule for lead.created:
      - admin (is_agent=False): always forward.
      - agent: forward ONLY when event.data["agent_user_id"] == user_id.
        Unassigned contacts (agent_user_id=None) and contacts owned by other
        agents are silently dropped.

    All other event types are always forwarded (they have their own filters
    elsewhere, e.g. conversation_update checks DB ownership in conversations.py).
    """
    if event.get("type") != "lead.created":
        return True
    if not is_agent:
        return True
    data = event.get("data", {})
    return data.get("agent_user_id") == user_id


@router.get("/events")
async def global_sse(
    request: Request,
    user: User = Depends(get_current_user),
):
    """SSE endpoint that broadcasts all EventBus events to the client.

    Does NOT use get_db — long-lived connection, no DB queries.
    Sends a keepalive comment every 30 s to prevent Cloudflare timeout.
    Unsubscribes the queue on disconnect or cancellation.

    feat(authz): lead.created events are filtered by ownership.
    Agents only receive events for contacts assigned to them.
    """
    from app.services.event_bus import event_bus

    is_agent = user.role == "agent"
    agent_id = user.id if is_agent else None

    queue = event_bus.subscribe()
    logger.debug("global_sse: client connected (user=%s)", user.username)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    if not should_forward_event(event, is_agent=is_agent, user_id=agent_id):
                        continue
                    payload = json.dumps(event["data"])
                    yield f"event: {event['type']}\ndata: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.unsubscribe(queue)
            logger.debug("global_sse: client disconnected (user=%s)", user.username)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
