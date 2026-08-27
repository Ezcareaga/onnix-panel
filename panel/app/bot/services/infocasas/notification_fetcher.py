"""InfoCasas NotificationFetcher — GraphQL API communication for notifications.

Handles three concerns:
  1. Polling up to 50 notifications from the InfoCasas GraphQL API.
  2. Fetching lead details by consulta_id.
  3. Marking a notification as seen via the legacy PHP endpoint.
  4. Batch dedup check against contacts and lead_events tables.

All network errors are caught and logged; callers receive None/False rather
than exceptions.  This matches the resilience contract used across the rest
of the InfoCasas subsystem (see session_manager.py).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GRAPHQL_URL = "https://graph.infocasas.com.uy/graphql"
MARK_SEEN_URL = (
    "https://www.infocasas.com.py/sitio/index.php"
    "?mid=notificaciones&func=ajax_call"
)
API_TIMEOUT = 15.0

# PHPSESSID placeholder values that indicate "no real session cookie available"
_PHPSESSID_PLACEHOLDERS = frozenset(
    {"PLACEHOLDER_NEEDS_MANUAL_UPDATE", "NOT_USED_JWT_AUTH"}
)

# Exact GetNotifications query — must match the N8N workflow definition
_QUERY_GET_NOTIFICATIONS = """
query GetNotifications {
  me {
    id
    name
    unread_notifications
    notifications(first: 50) {
      data {
        id
        created_at
        text
        url
        seen
        image
      }
    }
  }
}
""".strip()


# ---------------------------------------------------------------------------
# Graceful-degrade helper
# ---------------------------------------------------------------------------


def _get_introspected_field(
    data: dict | None,
    path: str,
    default: Any = None,
) -> Any:
    """Safely navigate a dotted *path* through nested dicts in *data*.

    This is a graceful-degrade helper for the InfoCasas GraphQL response
    payloads: when an upstream field disappears or flips shape (e.g. ``me``
    returned as a string scalar instead of an object), the helper returns
    *default* instead of raising ``AttributeError``. The IC poll pipeline
    keeps running and the caller can log + skip the field.

    Parameters
    ----------
    data:
        Root dict (typically the parsed GraphQL JSON body), or None.
    path:
        Dotted attribute path to walk, e.g. ``"data.me.notifications.data"``.
    default:
        Value returned when *data* is None, when any intermediate is missing,
        or when any intermediate is not a dict. Defaults to ``None``.

    Returns
    -------
    Any
        The leaf value at *path*, or *default* on any failure to walk.

    Examples
    --------
    >>> _get_introspected_field({"a": {"b": {"c": 42}}}, "a.b.c")
    42
    >>> _get_introspected_field({"a": {"b": {}}}, "a.b.c", default="X")
    'X'
    >>> _get_introspected_field(None, "a.b") is None
    True
    >>> _get_introspected_field({"a": "scalar"}, "a.b") is None
    True
    """
    if data is None:
        return default
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


# ---------------------------------------------------------------------------
# NotificationFetcher
# ---------------------------------------------------------------------------


class NotificationFetcher:
    """Fetch, mark-seen, and dedup-check InfoCasas notifications.

    Parameters
    ----------
    http_client:
        Optional pre-built ``httpx.AsyncClient`` injected for testing.
        When provided the client is used directly and is NOT closed by this
        class — the caller owns its lifecycle.
    """

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        # When an external client is injected (tests) we never close it.
        self._external_client = http_client

    # ------------------------------------------------------------------
    # Public API — network
    # ------------------------------------------------------------------

    async def fetch_notifications(self, token: str) -> list[dict] | None:
        """Fetch up to 50 notifications from the InfoCasas GraphQL API.

        Parameters
        ----------
        token:
            Valid JWT Bearer token for the InfoCasas GraphQL API.

        Returns
        -------
        list[dict] | None
            List of notification dicts on success, or None when the session
            is expired / unauthenticated, or on any network/parse error.
        """
        payload = {"query": _QUERY_GET_NOTIFICATIONS}
        headers = self._build_headers(token)

        try:
            data = await self._post_graphql(payload, headers=headers)
        except Exception:
            logger.warning(
                "NotificationFetcher.fetch_notifications: request failed",
                exc_info=True,
            )
            return None

        if data is None:
            return None

        # GraphQL-level auth failure
        errors = data.get("errors") or []
        for err in errors:
            msg = str(err.get("message", "")).lower()
            if "unauthenticated" in msg:
                logger.info(
                    "NotificationFetcher.fetch_notifications: "
                    "unauthenticated error — session expired"
                )
                return None

        me = _get_introspected_field(data, "data.me")
        if me is None:
            logger.info(
                "NotificationFetcher.fetch_notifications: "
                "data.me is null — session expired"
            )
            return None
        if not isinstance(me, dict):
            # Upstream schema drift: ``me`` flipped to a scalar. Log + skip so
            # the IC poll pipeline survives instead of crashing.
            logger.warning(
                "NotificationFetcher.fetch_notifications: "
                "data.me is not a dict (got %s) — schema drift, skipping",
                type(me).__name__,
            )
            return None

        notifications: list[dict] = (
            _get_introspected_field(data, "data.me.notifications.data", default=[])
            or []
        )
        logger.debug(
            "NotificationFetcher.fetch_notifications: fetched %d notifications",
            len(notifications),
        )
        return notifications

    async def fetch_lead_details(
        self, token: str, consulta_id: str
    ) -> dict | None:
        """Fetch lead details by consulta_id.

        The consulta_id is interpolated inline into the GraphQL query (not
        passed as a variable) to match the existing N8N workflow behaviour.

        Parameters
        ----------
        token:
            Valid JWT Bearer token.
        consulta_id:
            InfoCasas lead identifier (numeric string, e.g. ``"66065340"``).

        Returns
        -------
        dict | None
            The ``leadById`` dict on success, or None when the id is invalid,
            the session is expired, or a network/parse error occurs.
        """
        query = (
            "{ leadById(id: "
            + consulta_id
            + ") { id message created_at source property_id"
            " from { name email phone whatsapp_phone has_whatsapp }"
            " listing { ... on Property { id title code"
            " neighborhood { name } } } } }"
        )
        payload = {"query": query}
        headers = self._build_headers(token)

        try:
            data = await self._post_graphql(payload, headers=headers)
        except Exception:
            logger.warning(
                "NotificationFetcher.fetch_lead_details: request failed "
                "(consulta_id=%s)",
                consulta_id,
                exc_info=True,
            )
            return None

        if data is None:
            return None

        lead = _get_introspected_field(data, "data.leadById")
        if lead is None:
            logger.info(
                "NotificationFetcher.fetch_lead_details: "
                "leadById is null (consulta_id=%s) — invalid id or auth failure",
                consulta_id,
            )
            return None

        return lead

    async def mark_seen(
        self,
        token: str,
        notification_id: str,
        phpsessid: str | None = None,
    ) -> bool:
        """Mark a notification as seen via the legacy PHP endpoint.

        IMPORTANT: this MUST be called for ALL notifications, even when
        parsing failed, to prevent re-processing on the next poll.

        Parameters
        ----------
        token:
            The InfoCasas ``frontend_token`` (JWT, used as a cookie value).
        notification_id:
            Numeric notification identifier.
        phpsessid:
            Optional ``PHPSESSIDIC`` cookie value.  Omitted from the request
            when None, empty, or a known placeholder value
            (``PLACEHOLDER_NEEDS_MANUAL_UPDATE``, ``NOT_USED_JWT_AUTH``).

        Returns
        -------
        bool
            True when the server responds with HTTP 200; False otherwise.
        """
        # Build Cookie header
        cookie_parts = [f"frontend_token={token}"]
        if phpsessid and phpsessid not in _PHPSESSID_PLACEHOLDERS:
            cookie_parts.append(f"PHPSESSIDIC={phpsessid}")
        cookie = "; ".join(cookie_parts)

        headers = {
            "Cookie": cookie,
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": (
                "https://www.infocasas.com.py/sitio/index.php?mid=consultas"
            ),
        }
        body = f"func=markSeen&id={notification_id}"

        try:
            if self._external_client is not None:
                resp = await self._external_client.post(
                    MARK_SEEN_URL,
                    content=body,
                    headers=headers,
                    timeout=API_TIMEOUT,
                )
            else:
                async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
                    resp = await client.post(
                        MARK_SEEN_URL,
                        content=body,
                        headers=headers,
                    )

            if resp.status_code == 200:
                logger.debug(
                    "NotificationFetcher.mark_seen: OK (notification_id=%s)",
                    notification_id,
                )
                return True

            logger.warning(
                "NotificationFetcher.mark_seen: HTTP %d (notification_id=%s)",
                resp.status_code,
                notification_id,
            )
            return False

        except Exception:
            logger.warning(
                "NotificationFetcher.mark_seen: request failed "
                "(notification_id=%s)",
                notification_id,
                exc_info=True,
            )
            return False

    # ------------------------------------------------------------------
    # Public API — database
    # ------------------------------------------------------------------

    async def check_existing_ids(
        self, session: AsyncSession, consulta_ids: list[str]
    ) -> set[str]:
        """Check which consulta_ids have already been processed.

        Queries both ``contacts`` (via ``source_id``) and ``lead_events``
        (via ``metadata->>'consulta_id'``) and returns the union of matches.

        Parameters
        ----------
        session:
            Active async SQLAlchemy session.  The caller owns the lifecycle.
        consulta_ids:
            List of InfoCasas consulta IDs to check.

        Returns
        -------
        set[str]
            Set of already-processed IDs found in either table.
        """
        if not consulta_ids:
            return set()

        id_list = list(consulta_ids)

        # Check contacts table (ANY works with asyncpg arrays)
        contacts_result = await session.execute(
            text(
                "SELECT source_id FROM contacts "
                "WHERE source = 'infocasas' AND source_id = ANY(:id_list)"
            ),
            {"id_list": id_list},
        )
        contacts_ids: set[str] = {
            row[0] for row in contacts_result.fetchall() if row[0] is not None
        }

        # Check lead_events table
        events_result = await session.execute(
            text(
                "SELECT metadata->>'consulta_id' FROM lead_events "
                "WHERE metadata->>'consulta_id' = ANY(:id_list)"
            ),
            {"id_list": id_list},
        )
        event_ids: set[str] = {
            row[0] for row in events_result.fetchall() if row[0] is not None
        }

        existing = contacts_ids | event_ids
        logger.debug(
            "NotificationFetcher.check_existing_ids: %d/%d already processed",
            len(existing),
            len(consulta_ids),
        )
        return existing

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_headers(token: str) -> dict[str, str]:
        """Build standard headers for GraphQL requests.

        Parameters
        ----------
        token:
            JWT Bearer token.

        Returns
        -------
        dict[str, str]
            Headers dict ready for use with httpx.
        """
        return {
            "Authorization": f"Bearer {token}",
            "x-origin": "www.infocasas.com.py",
            "ic-user-agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
            ),
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Private: HTTP
    # ------------------------------------------------------------------

    async def _post_graphql(
        self,
        payload: dict,
        *,
        headers: dict[str, str],
    ) -> dict | None:
        """POST *payload* to the InfoCasas GraphQL endpoint.

        Parameters
        ----------
        payload:
            JSON-serialisable GraphQL request body.
        headers:
            HTTP headers to include in the request.

        Returns
        -------
        dict | None
            Parsed JSON response body, or None on HTTP 4xx/5xx.

        Raises
        ------
        httpx.TransportError
            Re-raised network-level errors so callers can log them.
        Exception
            Any unexpected error is re-raised for the caller to handle.
        """
        if self._external_client is not None:
            resp = await self._external_client.post(
                GRAPHQL_URL,
                json=payload,
                headers=headers,
                timeout=API_TIMEOUT,
            )
            if resp.status_code >= 400:
                logger.debug(
                    "NotificationFetcher._post_graphql: HTTP %d",
                    resp.status_code,
                )
                return None
            return resp.json()

        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            resp = await client.post(
                GRAPHQL_URL,
                json=payload,
                headers=headers,
            )
            if resp.status_code >= 400:
                logger.debug(
                    "NotificationFetcher._post_graphql: HTTP %d",
                    resp.status_code,
                )
                return None
            return resp.json()
