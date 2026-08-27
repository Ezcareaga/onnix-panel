"""Tests for NotificationFetcher — InfoCasas GraphQL notification polling.

Covers:
  - fetch_notifications: success, session expired, unauthenticated, timeout, HTTP 500
  - fetch_lead_details: success, null leadById, network error
  - mark_seen: success, HTTP error, network error, PHPSESSID cookie handling
  - check_existing_ids: empty input, contacts hits, lead_events hits, union, empty
  - _build_headers: correct header values

All tests mock httpx and AsyncSession; no real network calls or DB access.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bot.services.infocasas.notification_fetcher import (
    GRAPHQL_URL,
    MARK_SEEN_URL,
    NotificationFetcher,
    _get_introspected_field,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_mock_client(
    status_code: int = 200,
    json_data: dict | None = None,
    text_data: str = "",
) -> AsyncMock:
    """Build a mock httpx.AsyncClient."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = json_data or {}
    mock_response.text = text_data

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    return mock_client


def _make_mock_session(
    contact_ids: list[str] | None = None,
    event_ids: list[str] | None = None,
) -> AsyncMock:
    """Build a mock AsyncSession returning specified IDs.

    The first ``execute`` call returns contact rows; the second returns
    lead_event rows.
    """
    contact_rows = [(cid,) for cid in (contact_ids or [])]
    event_rows = [(eid,) for eid in (event_ids or [])]

    contact_result = MagicMock()
    contact_result.fetchall.return_value = contact_rows

    event_result = MagicMock()
    event_result.fetchall.return_value = event_rows

    call_count = {"n": 0}

    async def fake_execute(stmt, params=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return contact_result
        return event_result

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=fake_execute)
    return mock_session


# ---------------------------------------------------------------------------
# TestFetchNotifications
# ---------------------------------------------------------------------------


class TestFetchNotifications:
    """fetch_notifications() polls the GraphQL API and returns parsed data."""

    @pytest.mark.asyncio
    async def test_success_returns_list(self):
        """Valid response returns list of notification dicts."""
        notifications = [
            {
                "id": "101",
                "created_at": "2026-03-28T10:00:00Z",
                "text": "Nueva consulta recibida",
                "url": "/consulta/101",
                "seen": False,
                "image": None,
            }
        ]
        json_data = {
            "data": {
                "me": {
                    "id": "42",
                    "name": "Onnix",
                    "unread_notifications": 1,
                    "notifications": {"data": notifications},
                }
            }
        }
        client = _make_mock_client(status_code=200, json_data=json_data)
        fetcher = NotificationFetcher(http_client=client)

        result = await fetcher.fetch_notifications("test_token")

        assert result == notifications
        client.post.assert_awaited_once()
        call_args = client.post.call_args
        assert call_args[0][0] == GRAPHQL_URL

    @pytest.mark.asyncio
    async def test_success_empty_notifications(self):
        """Valid response with empty notification list returns empty list."""
        json_data = {
            "data": {
                "me": {
                    "id": "42",
                    "name": "Onnix",
                    "unread_notifications": 0,
                    "notifications": {"data": []},
                }
            }
        }
        client = _make_mock_client(status_code=200, json_data=json_data)
        fetcher = NotificationFetcher(http_client=client)

        result = await fetcher.fetch_notifications("test_token")

        assert result == []

    @pytest.mark.asyncio
    async def test_session_expired_me_null_returns_none(self):
        """When data.me is null, session is expired → returns None."""
        json_data = {"data": {"me": None}}
        client = _make_mock_client(status_code=200, json_data=json_data)
        fetcher = NotificationFetcher(http_client=client)

        result = await fetcher.fetch_notifications("expired_token")

        assert result is None

    @pytest.mark.asyncio
    async def test_unauthenticated_error_returns_none(self):
        """GraphQL 'unauthenticated' error in errors array → returns None."""
        json_data = {
            "errors": [{"message": "Unauthenticated.", "extensions": {}}],
            "data": None,
        }
        client = _make_mock_client(status_code=200, json_data=json_data)
        fetcher = NotificationFetcher(http_client=client)

        result = await fetcher.fetch_notifications("bad_token")

        assert result is None

    @pytest.mark.asyncio
    async def test_network_timeout_returns_none(self):
        """Network timeout exception → returns None (never raises)."""
        import httpx

        client = AsyncMock()
        client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        fetcher = NotificationFetcher(http_client=client)

        result = await fetcher.fetch_notifications("any_token")

        assert result is None

    @pytest.mark.asyncio
    async def test_http_500_returns_none(self):
        """HTTP 500 from GraphQL endpoint → returns None."""
        client = _make_mock_client(status_code=500, json_data={})
        fetcher = NotificationFetcher(http_client=client)

        result = await fetcher.fetch_notifications("token")

        assert result is None

    @pytest.mark.asyncio
    async def test_authorization_header_sent(self):
        """Bearer token is sent in Authorization header."""
        json_data = {
            "data": {
                "me": {
                    "id": "1",
                    "name": "x",
                    "unread_notifications": 0,
                    "notifications": {"data": []},
                }
            }
        }
        client = _make_mock_client(status_code=200, json_data=json_data)
        fetcher = NotificationFetcher(http_client=client)

        await fetcher.fetch_notifications("my_jwt_token")

        call_kwargs = client.post.call_args[1]
        assert call_kwargs["headers"]["Authorization"] == "Bearer my_jwt_token"


# ---------------------------------------------------------------------------
# TestFetchLeadDetails
# ---------------------------------------------------------------------------


class TestFetchLeadDetails:
    """fetch_lead_details() fetches a single lead by consulta_id."""

    @pytest.mark.asyncio
    async def test_success_returns_lead_dict(self):
        """Valid response returns the leadById dict."""
        lead = {
            "id": "66065340",
            "message": "Me interesa la propiedad",
            "created_at": "2026-03-28T09:00:00Z",
            "source": "web",
            "property_id": "12345",
            "from": {
                "name": "Juan Perez",
                "email": "juan@example.com",
                "phone": "+595981000001",
                "whatsapp_phone": "+595981000001",
                "has_whatsapp": True,
            },
            "listing": {
                "id": "12345",
                "title": "Casa en Asuncion",
                "code": "C001",
                "neighborhood": {"name": "Villa Morra"},
            },
        }
        json_data = {"data": {"leadById": lead}}
        client = _make_mock_client(status_code=200, json_data=json_data)
        fetcher = NotificationFetcher(http_client=client)

        result = await fetcher.fetch_lead_details("token", "66065340")

        assert result == lead
        # Consulta_id must be inlined in the query body, not as a variable
        call_kwargs = client.post.call_args[1]
        assert "66065340" in call_kwargs["json"]["query"]
        assert "variables" not in call_kwargs["json"]

    @pytest.mark.asyncio
    async def test_invalid_consulta_id_returns_none(self):
        """leadById null → invalid id or auth failure → returns None."""
        json_data = {"data": {"leadById": None}}
        client = _make_mock_client(status_code=200, json_data=json_data)
        fetcher = NotificationFetcher(http_client=client)

        result = await fetcher.fetch_lead_details("token", "9999999")

        assert result is None

    @pytest.mark.asyncio
    async def test_network_error_returns_none(self):
        """Network error → returns None."""
        import httpx

        client = AsyncMock()
        client.post = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )
        fetcher = NotificationFetcher(http_client=client)

        result = await fetcher.fetch_lead_details("token", "12345")

        assert result is None

    @pytest.mark.asyncio
    async def test_http_500_returns_none(self):
        """HTTP 500 → returns None."""
        client = _make_mock_client(status_code=500)
        fetcher = NotificationFetcher(http_client=client)

        result = await fetcher.fetch_lead_details("token", "12345")

        assert result is None

    @pytest.mark.asyncio
    async def test_query_inline_interpolation(self):
        """consulta_id is inlined into the query string (no variables)."""
        json_data = {"data": {"leadById": {"id": "777", "message": "test"}}}
        client = _make_mock_client(status_code=200, json_data=json_data)
        fetcher = NotificationFetcher(http_client=client)

        await fetcher.fetch_lead_details("token", "777")

        call_kwargs = client.post.call_args[1]
        query = call_kwargs["json"]["query"]
        assert "leadById(id: 777)" in query


# ---------------------------------------------------------------------------
# TestMarkSeen
# ---------------------------------------------------------------------------


class TestMarkSeen:
    """mark_seen() marks a notification as seen via the legacy PHP endpoint."""

    @pytest.mark.asyncio
    async def test_success_returns_true(self):
        """HTTP 200 → returns True."""
        client = _make_mock_client(status_code=200, text_data="ok")
        fetcher = NotificationFetcher(http_client=client)

        result = await fetcher.mark_seen("my_token", "101")

        assert result is True
        client.post.assert_awaited_once()
        call_args = client.post.call_args
        assert call_args[0][0] == MARK_SEEN_URL

    @pytest.mark.asyncio
    async def test_http_error_returns_false(self):
        """Non-200 HTTP status → returns False."""
        client = _make_mock_client(status_code=403)
        fetcher = NotificationFetcher(http_client=client)

        result = await fetcher.mark_seen("token", "101")

        assert result is False

    @pytest.mark.asyncio
    async def test_http_500_returns_false(self):
        """HTTP 500 → returns False."""
        client = _make_mock_client(status_code=500)
        fetcher = NotificationFetcher(http_client=client)

        result = await fetcher.mark_seen("token", "999")

        assert result is False

    @pytest.mark.asyncio
    async def test_network_error_returns_false(self):
        """Network exception → returns False (never raises)."""
        import httpx

        client = AsyncMock()
        client.post = AsyncMock(
            side_effect=httpx.NetworkError("connection reset")
        )
        fetcher = NotificationFetcher(http_client=client)

        result = await fetcher.mark_seen("token", "101")

        assert result is False

    @pytest.mark.asyncio
    async def test_with_phpsessid_includes_cookie(self):
        """Valid phpsessid → Cookie header includes both tokens."""
        client = _make_mock_client(status_code=200)
        fetcher = NotificationFetcher(http_client=client)

        await fetcher.mark_seen("jwt_token", "101", phpsessid="abc123session")

        call_kwargs = client.post.call_args[1]
        cookie = call_kwargs["headers"]["Cookie"]
        assert "frontend_token=jwt_token" in cookie
        assert "PHPSESSIDIC=abc123session" in cookie

    @pytest.mark.asyncio
    async def test_without_phpsessid_cookie_omits_phpsessidic(self):
        """None phpsessid → Cookie header has only frontend_token."""
        client = _make_mock_client(status_code=200)
        fetcher = NotificationFetcher(http_client=client)

        await fetcher.mark_seen("jwt_token", "101", phpsessid=None)

        call_kwargs = client.post.call_args[1]
        cookie = call_kwargs["headers"]["Cookie"]
        assert "frontend_token=jwt_token" in cookie
        assert "PHPSESSIDIC" not in cookie

    @pytest.mark.asyncio
    async def test_placeholder_phpsessid_omits_phpsessidic(self):
        """PLACEHOLDER_NEEDS_MANUAL_UPDATE → PHPSESSIDIC omitted from Cookie."""
        client = _make_mock_client(status_code=200)
        fetcher = NotificationFetcher(http_client=client)

        await fetcher.mark_seen(
            "jwt_token",
            "101",
            phpsessid="PLACEHOLDER_NEEDS_MANUAL_UPDATE",
        )

        call_kwargs = client.post.call_args[1]
        cookie = call_kwargs["headers"]["Cookie"]
        assert "PHPSESSIDIC" not in cookie

    @pytest.mark.asyncio
    async def test_not_used_jwt_auth_phpsessid_omits_phpsessidic(self):
        """NOT_USED_JWT_AUTH → PHPSESSIDIC omitted from Cookie."""
        client = _make_mock_client(status_code=200)
        fetcher = NotificationFetcher(http_client=client)

        await fetcher.mark_seen(
            "jwt_token",
            "101",
            phpsessid="NOT_USED_JWT_AUTH",
        )

        call_kwargs = client.post.call_args[1]
        cookie = call_kwargs["headers"]["Cookie"]
        assert "PHPSESSIDIC" not in cookie

    @pytest.mark.asyncio
    async def test_form_body_contains_notification_id(self):
        """Request body contains func=markSeen and the notification_id."""
        client = _make_mock_client(status_code=200)
        fetcher = NotificationFetcher(http_client=client)

        await fetcher.mark_seen("token", "42")

        call_kwargs = client.post.call_args[1]
        body = call_kwargs["content"]
        assert "func=markSeen" in body
        assert "id=42" in body

    @pytest.mark.asyncio
    async def test_correct_referer_header(self):
        """Referer header points to consultas page."""
        client = _make_mock_client(status_code=200)
        fetcher = NotificationFetcher(http_client=client)

        await fetcher.mark_seen("token", "101")

        call_kwargs = client.post.call_args[1]
        referer = call_kwargs["headers"]["Referer"]
        assert "mid=consultas" in referer


# ---------------------------------------------------------------------------
# TestCheckExistingIds
# ---------------------------------------------------------------------------


class TestCheckExistingIds:
    """check_existing_ids() returns the set of already-processed consulta_ids."""

    @pytest.mark.asyncio
    async def test_empty_input_returns_empty_set(self):
        """Empty input list → empty set, no DB queries issued."""
        session = _make_mock_session()
        fetcher = NotificationFetcher()

        result = await fetcher.check_existing_ids(session, [])

        assert result == set()
        session.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_existing_returns_empty_set(self):
        """Neither table has matching IDs → empty set."""
        session = _make_mock_session(contact_ids=[], event_ids=[])
        fetcher = NotificationFetcher()

        result = await fetcher.check_existing_ids(session, ["100", "200"])

        assert result == set()

    @pytest.mark.asyncio
    async def test_contacts_hit_returns_those_ids(self):
        """IDs found in contacts → returned in set."""
        session = _make_mock_session(contact_ids=["100", "200"], event_ids=[])
        fetcher = NotificationFetcher()

        result = await fetcher.check_existing_ids(session, ["100", "200", "300"])

        assert "100" in result
        assert "200" in result
        assert "300" not in result

    @pytest.mark.asyncio
    async def test_lead_events_hit_returns_those_ids(self):
        """IDs found only in lead_events → returned in set."""
        session = _make_mock_session(contact_ids=[], event_ids=["300", "400"])
        fetcher = NotificationFetcher()

        result = await fetcher.check_existing_ids(session, ["300", "400", "500"])

        assert "300" in result
        assert "400" in result
        assert "500" not in result

    @pytest.mark.asyncio
    async def test_both_tables_returns_union(self):
        """IDs in both tables → full union returned."""
        session = _make_mock_session(
            contact_ids=["100", "200"],
            event_ids=["200", "300"],
        )
        fetcher = NotificationFetcher()

        result = await fetcher.check_existing_ids(
            session, ["100", "200", "300", "400"]
        )

        assert result == {"100", "200", "300"}
        assert "400" not in result

    @pytest.mark.asyncio
    async def test_two_db_queries_issued(self):
        """Exactly two execute() calls are made (contacts + lead_events)."""
        session = _make_mock_session(contact_ids=[], event_ids=[])
        fetcher = NotificationFetcher()

        await fetcher.check_existing_ids(session, ["1", "2"])

        assert session.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_none_values_in_rows_ignored(self):
        """Rows with None source_id / metadata are filtered out."""
        # Simulate DB returning rows with None values
        contact_result = MagicMock()
        contact_result.fetchall.return_value = [(None,), ("100",)]

        event_result = MagicMock()
        event_result.fetchall.return_value = [(None,), ("200",)]

        call_count = {"n": 0}

        async def fake_execute(stmt, params=None):
            call_count["n"] += 1
            return contact_result if call_count["n"] == 1 else event_result

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=fake_execute)
        fetcher = NotificationFetcher()

        result = await fetcher.check_existing_ids(session, ["100", "200", "300"])

        assert result == {"100", "200"}


# ---------------------------------------------------------------------------
# TestBuildHeaders
# ---------------------------------------------------------------------------


class TestBuildHeaders:
    """_build_headers() returns the correct GraphQL headers."""

    def test_returns_authorization_bearer(self):
        """Authorization header uses Bearer scheme."""
        headers = NotificationFetcher._build_headers("my_secret_token")
        assert headers["Authorization"] == "Bearer my_secret_token"

    def test_returns_x_origin(self):
        """x-origin header is set to infocasas.com.py."""
        headers = NotificationFetcher._build_headers("tok")
        assert headers["x-origin"] == "www.infocasas.com.py"

    def test_returns_ic_user_agent(self):
        """ic-user-agent header is set."""
        headers = NotificationFetcher._build_headers("tok")
        assert "Mozilla/5.0" in headers["ic-user-agent"]

    def test_returns_content_type_json(self):
        """Content-Type is application/json."""
        headers = NotificationFetcher._build_headers("tok")
        assert headers["Content-Type"] == "application/json"

    def test_all_required_headers_present(self):
        """All four required headers are present."""
        headers = NotificationFetcher._build_headers("tok")
        assert set(headers.keys()) == {
            "Authorization",
            "x-origin",
            "ic-user-agent",
            "Content-Type",
        }

    def test_different_tokens_produce_different_auth(self):
        """Different tokens produce different Authorization values."""
        h1 = NotificationFetcher._build_headers("token_a")
        h2 = NotificationFetcher._build_headers("token_b")
        assert h1["Authorization"] != h2["Authorization"]


# ---------------------------------------------------------------------------
# TestGetIntrospectedField (CLEAN-05)
# ---------------------------------------------------------------------------


class TestGetIntrospectedField:
    """_get_introspected_field() safely walks a dotted path through nested dicts.

    Graceful-degrade helper: when an upstream IC GraphQL response is missing a
    field or a field flips shape (e.g. dict → string), the helper returns the
    caller-provided default instead of raising AttributeError. This keeps the
    IC poll pipeline alive when the upstream schema drifts.
    """

    def test_get_introspected_field_returns_value(self):
        """Happy path: nested path is walked and the leaf value is returned."""
        data = {"a": {"b": {"c": 42}}}
        assert _get_introspected_field(data, "a.b.c") == 42

    def test_get_introspected_field_returns_default_on_missing_path(self):
        """Missing leaf key → default is returned, no exception raised."""
        data = {"a": {"b": {}}}
        assert _get_introspected_field(data, "a.b.c", default="X") == "X"

    def test_get_introspected_field_handles_none_data(self):
        """None root data → default (default-default is None)."""
        assert _get_introspected_field(None, "a.b") is None

    def test_get_introspected_field_handles_non_dict_intermediate(self):
        """Intermediate value is a non-dict (str/list/int) → default returned,
        does NOT raise AttributeError.

        This is the core regression guard: pre-helper, the call sites used
        ``something.get(...)`` which crashes when ``something`` is a string.
        """
        data = {"a": "not_a_dict"}
        assert _get_introspected_field(data, "a.b", default=None) is None


# ---------------------------------------------------------------------------
# TestFetchNotificationsGracefulDegrade (CLEAN-05 — adversarial integration)
# ---------------------------------------------------------------------------


class TestFetchNotificationsGracefulDegrade:
    """fetch_notifications degrades gracefully when IC returns a non-dict 'me'.

    Pre-CLEAN-05, ``(data.get("data") or {}).get("me")`` returned the string
    intact and then ``me.get("notifications")`` crashed with AttributeError,
    killing the IC poll pipeline. Post-fix, the helper detects the non-dict
    intermediate and returns None/[] so the poller logs and moves on.
    """

    @pytest.mark.asyncio
    async def test_fetch_notifications_handles_non_dict_me(self):
        """IC returns ``data.me`` as a string scalar → fetcher does NOT raise.

        Returns None (treated as session expired) or [] (no notifications) —
        either is acceptable graceful-degrade behaviour.
        """
        json_data = {"data": {"me": "this_is_not_a_dict"}}
        client = _make_mock_client(status_code=200, json_data=json_data)
        fetcher = NotificationFetcher(http_client=client)

        # Pre-fix: AttributeError ('str' object has no attribute 'get')
        # Post-fix: returns None or []
        result = await fetcher.fetch_notifications("test_token")

        assert result is None or result == []
