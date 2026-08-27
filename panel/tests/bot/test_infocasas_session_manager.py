"""Tests for InfoCasas SessionManager.

Covers token validation, login, get_valid_token flow, DB persistence,
notification, and the module-level factory.  All tests use mocked
dependencies — no real network calls or DB required.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import httpx

from app.bot.services.infocasas.session_manager import (
    SessionManager,
    get_session_manager,
    _TOKEN_KEY,
    _last_alert_at,
    ICAuthError,
    ICServiceError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session_factory(get_value_return: str | None = None):
    """Build a mock async_session_factory for DB interactions.

    The returned factory is usable as an async context manager. The
    embedded session has ``BotSettingRepository.get_value`` wired to
    return *get_value_return*.
    """
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock()
    mock_factory.return_value = mock_ctx

    return mock_factory, mock_session


def _make_http_client(
    *,
    status_code: int = 200,
    json_body: dict | None = None,
    raise_exc: Exception | None = None,
) -> AsyncMock:
    """Return a mock httpx.AsyncClient with a pre-configured POST response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_body or {}
    mock_resp.raise_for_status = MagicMock()  # no-op by default

    client = AsyncMock()
    if raise_exc:
        client.post = AsyncMock(side_effect=raise_exc)
    else:
        client.post = AsyncMock(return_value=mock_resp)
    return client


def _make_notifier() -> AsyncMock:
    """Build a mock AdminNotifier."""
    notifier = AsyncMock()
    notifier.notify = AsyncMock(return_value=True)
    return notifier


# ---------------------------------------------------------------------------
# TestValidateSession
# ---------------------------------------------------------------------------


class TestValidateSession:
    """validate_session() checks a token against { me { id name } }."""

    @pytest.mark.asyncio
    async def test_success_returns_true(self):
        """Returns True when me.id is populated."""
        body = {"data": {"me": {"id": "42", "name": "la administradora"}}}
        client = _make_http_client(json_body=body)
        sm = SessionManager("u", "p", http_client=client)

        result = await sm.validate_session("valid-token")

        assert result is True
        client.post.assert_awaited_once()
        call_kwargs = client.post.call_args
        # Authorization header must be present
        assert "Bearer valid-token" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_null_me_returns_false(self):
        """Returns False when data.me is null."""
        body = {"data": {"me": None}}
        client = _make_http_client(json_body=body)
        sm = SessionManager("u", "p", http_client=client)

        result = await sm.validate_session("expired-token")

        assert result is False

    @pytest.mark.asyncio
    async def test_unauthenticated_error_returns_false(self):
        """Returns False when errors contain 'unauthenticated'."""
        body = {"errors": [{"message": "Unauthenticated"}], "data": None}
        client = _make_http_client(json_body=body)
        sm = SessionManager("u", "p", http_client=client)

        result = await sm.validate_session("bad-token")

        assert result is False

    @pytest.mark.asyncio
    async def test_http_401_returns_false(self):
        """Returns False on HTTP 401 (client returns None from _post_graphql)."""
        client = _make_http_client(status_code=401)
        sm = SessionManager("u", "p", http_client=client)

        result = await sm.validate_session("token")

        assert result is False

    @pytest.mark.asyncio
    async def test_http_403_returns_false(self):
        """Returns False on HTTP 403."""
        client = _make_http_client(status_code=403)
        sm = SessionManager("u", "p", http_client=client)

        result = await sm.validate_session("token")

        assert result is False

    @pytest.mark.asyncio
    async def test_network_error_raises_service_error(self):
        """Propagates ICServiceError on network exception (caller handles routing)."""
        client = _make_http_client(raise_exc=httpx.ConnectError("unreachable"))
        sm = SessionManager("u", "p", http_client=client)

        with pytest.raises(ICServiceError):
            await sm.validate_session("token")

    @pytest.mark.asyncio
    async def test_missing_me_id_returns_false(self):
        """Returns False when me exists but id is missing/empty."""
        body = {"data": {"me": {"name": "Someone"}}}  # no 'id' key
        client = _make_http_client(json_body=body)
        sm = SessionManager("u", "p", http_client=client)

        result = await sm.validate_session("token")

        assert result is False


# ---------------------------------------------------------------------------
# TestLogin
# ---------------------------------------------------------------------------


class TestLogin:
    """login() executes GraphQL mutation and returns access_token."""

    @pytest.mark.asyncio
    async def test_success_returns_token(self):
        """Returns the access_token string on successful login."""
        body = {
            "data": {
                "login": {
                    "access_token": "jwt-abc-123",
                    "refresh_token": "rt-xyz",
                    "expires_in": 31536000,
                    "token_type": "Bearer",
                    "user_md5": "abc",
                    "user": {"id": "1", "name": "la administradora"},
                }
            }
        }
        client = _make_http_client(json_body=body)
        sm = SessionManager("user@test.com", "secret", http_client=client)

        token = await sm.login()

        assert token == "jwt-abc-123"

    @pytest.mark.asyncio
    async def test_graphql_errors_returns_none(self):
        """Returns None when GraphQL returns errors."""
        body = {"errors": [{"message": "Invalid credentials"}], "data": None}
        client = _make_http_client(json_body=body)
        sm = SessionManager("user@test.com", "wrongpass", http_client=client)

        token = await sm.login()

        assert token is None

    @pytest.mark.asyncio
    async def test_network_error_raises_service_error(self):
        """Propagates ICServiceError on network exception (caller handles routing)."""
        client = _make_http_client(raise_exc=httpx.ConnectTimeout("timeout"))
        sm = SessionManager("user@test.com", "secret", http_client=client)

        with pytest.raises(ICServiceError):
            await sm.login()

    @pytest.mark.asyncio
    async def test_empty_credentials_returns_none(self):
        """Returns None immediately when credentials are empty."""
        sm = SessionManager("", "")

        token = await sm.login()

        assert token is None

    @pytest.mark.asyncio
    async def test_missing_access_token_returns_none(self):
        """Returns None when access_token is absent from response."""
        body = {"data": {"login": {"refresh_token": "rt", "user": {"id": "1"}}}}
        client = _make_http_client(json_body=body)
        sm = SessionManager("u", "p", http_client=client)

        token = await sm.login()

        assert token is None

    @pytest.mark.asyncio
    async def test_login_posts_to_graphql_url(self):
        """login() POSTs to the InfoCasas GraphQL endpoint."""
        body = {
            "data": {
                "login": {
                    "access_token": "tok",
                    "user": {"id": "1", "name": "Test"},
                }
            }
        }
        client = _make_http_client(json_body=body)
        sm = SessionManager("myuser", "mypass", http_client=client)

        await sm.login()

        call_args = client.post.call_args
        assert "graph.infocasas.com.uy" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_login_includes_required_headers(self):
        """login() includes x-origin and Content-Type headers."""
        body = {"data": {"login": {"access_token": "tok", "user": {"id": "1"}}}}
        client = _make_http_client(json_body=body)
        sm = SessionManager("u", "p", http_client=client)

        await sm.login()

        call_kwargs = client.post.call_args[1]
        headers = call_kwargs.get("headers", {})
        assert headers.get("x-origin") == "www.infocasas.com.py"
        assert headers.get("Content-Type") == "application/json"

    @pytest.mark.asyncio
    async def test_login_does_not_hardcode_credentials(self):
        """Credentials in the mutation come from constructor, not literals."""
        captured: list[dict] = []

        async def fake_post(url, *, json, headers, timeout):
            captured.append(json)
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {
                "data": {"login": {"access_token": "tok", "user": {"id": "1"}}}
            }
            return resp

        client = AsyncMock()
        client.post = AsyncMock(side_effect=fake_post)
        sm = SessionManager("dynamic_user", "dynamic_pass", http_client=client)

        await sm.login()

        assert len(captured) == 1
        query = captured[0]["query"]
        assert "dynamic_user" in query
        assert "dynamic_pass" in query


# ---------------------------------------------------------------------------
# TestGetValidToken
# ---------------------------------------------------------------------------


class TestGetValidToken:
    """get_valid_token() orchestrates load → validate → login → notify."""

    @pytest.mark.asyncio
    async def test_valid_cached_token_returned_directly(self):
        """Returns cached token without login when it passes validation."""
        factory, _ = _make_session_factory()
        notifier = _make_notifier()

        sm = SessionManager("u", "p", session_factory=factory, notifier=notifier)
        sm._load_token_from_db = AsyncMock(return_value="cached-token")
        sm.validate_session = AsyncMock(return_value=True)
        sm.login = AsyncMock(return_value=None)

        result = await sm.get_valid_token()

        assert result == "cached-token"
        sm.login.assert_not_awaited()
        notifier.notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_expired_token_triggers_login(self):
        """When cached token fails validation, login() is called."""
        factory, _ = _make_session_factory()
        sm = SessionManager("u", "p", session_factory=factory)
        sm._load_token_from_db = AsyncMock(return_value="expired-token")
        sm.validate_session = AsyncMock(return_value=False)
        sm.login = AsyncMock(return_value="fresh-token")
        sm._save_token_to_db = AsyncMock()

        result = await sm.get_valid_token()

        assert result == "fresh-token"
        sm.login.assert_awaited_once()
        sm._save_token_to_db.assert_awaited_once_with("fresh-token")

    @pytest.mark.asyncio
    async def test_no_cached_token_goes_to_login(self):
        """With no DB token, login() is attempted directly."""
        factory, _ = _make_session_factory()
        sm = SessionManager("u", "p", session_factory=factory)
        sm._load_token_from_db = AsyncMock(return_value=None)
        sm.validate_session = AsyncMock(return_value=False)
        sm.login = AsyncMock(return_value="brand-new-token")
        sm._save_token_to_db = AsyncMock()

        result = await sm.get_valid_token()

        assert result == "brand-new-token"
        sm.validate_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_all_fail_notifies_ez_and_returns_none(self, clear_alert_cooldown):
        """Returns None and notifies Ez when both cached token and login fail."""
        factory, _ = _make_session_factory()
        notifier = _make_notifier()

        sm = SessionManager("u", "p", session_factory=factory, notifier=notifier)
        sm._load_token_from_db = AsyncMock(return_value="stale-token")
        sm.validate_session = AsyncMock(return_value=False)
        sm.login = AsyncMock(return_value=None)

        result = await sm.get_valid_token()

        assert result is None
        notifier.notify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_notify_when_notifier_is_none(self, clear_alert_cooldown):
        """Notification skipped gracefully when no notifier is configured."""
        factory, _ = _make_session_factory()
        sm = SessionManager("u", "p", session_factory=factory, notifier=None)
        sm._load_token_from_db = AsyncMock(return_value=None)
        sm.login = AsyncMock(return_value=None)

        # Should not raise even with no notifier
        result = await sm.get_valid_token()
        assert result is None

    @pytest.mark.asyncio
    async def test_new_token_saved_after_login(self):
        """Newly obtained token is persisted to DB before returning."""
        factory, _ = _make_session_factory()
        sm = SessionManager("u", "p", session_factory=factory)
        sm._load_token_from_db = AsyncMock(return_value=None)
        sm.login = AsyncMock(return_value="new-tok")
        sm._save_token_to_db = AsyncMock()

        await sm.get_valid_token()

        sm._save_token_to_db.assert_awaited_once_with("new-tok")


# ---------------------------------------------------------------------------
# TestSaveLoadToken
# ---------------------------------------------------------------------------


class TestSaveLoadToken:
    """Token round-trip through bot_settings via BotSettingRepository."""

    @pytest.mark.asyncio
    async def test_load_token_returns_value_from_repo(self):
        """_load_token_from_db() reads infocasas_frontend_token from the DB."""
        factory, mock_session = _make_session_factory()

        with patch(
            "app.bot.services.infocasas.session_manager.BotSettingRepository"
        ) as mock_repo:
            mock_repo.get_value = AsyncMock(return_value="stored-jwt")

            sm = SessionManager("u", "p", session_factory=factory)
            token = await sm._load_token_from_db()

        assert token == "stored-jwt"
        mock_repo.get_value.assert_called_once_with(mock_session, _TOKEN_KEY)

    @pytest.mark.asyncio
    async def test_load_token_returns_none_when_absent(self):
        """_load_token_from_db() returns None when key is missing."""
        factory, _ = _make_session_factory()

        with patch(
            "app.bot.services.infocasas.session_manager.BotSettingRepository"
        ) as mock_repo:
            mock_repo.get_value = AsyncMock(return_value=None)

            sm = SessionManager("u", "p", session_factory=factory)
            token = await sm._load_token_from_db()

        assert token is None

    @pytest.mark.asyncio
    async def test_load_token_returns_none_for_placeholder(self):
        """_load_token_from_db() ignores 'PLACEHOLDER' values."""
        factory, _ = _make_session_factory()

        with patch(
            "app.bot.services.infocasas.session_manager.BotSettingRepository"
        ) as mock_repo:
            mock_repo.get_value = AsyncMock(return_value="PLACEHOLDER")

            sm = SessionManager("u", "p", session_factory=factory)
            token = await sm._load_token_from_db()

        assert token is None

    @pytest.mark.asyncio
    async def test_load_token_returns_none_on_db_error(self):
        """_load_token_from_db() returns None (never raises) on DB exception."""
        factory, _ = _make_session_factory()

        with patch(
            "app.bot.services.infocasas.session_manager.BotSettingRepository"
        ) as mock_repo:
            mock_repo.get_value = AsyncMock(side_effect=Exception("db down"))

            sm = SessionManager("u", "p", session_factory=factory)
            token = await sm._load_token_from_db()

        assert token is None

    @pytest.mark.asyncio
    async def test_save_token_calls_update_value(self):
        """_save_token_to_db() calls BotSettingRepository.update_value."""
        factory, mock_session = _make_session_factory()

        with patch(
            "app.bot.services.infocasas.session_manager.BotSettingRepository"
        ) as mock_repo:
            mock_repo.update_value = AsyncMock()

            sm = SessionManager("u", "p", session_factory=factory)
            await sm._save_token_to_db("my-jwt-token")

        mock_repo.update_value.assert_called_once_with(
            mock_session, _TOKEN_KEY, "my-jwt-token", 0
        )
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_save_token_swallows_db_error(self):
        """_save_token_to_db() never raises on DB exception."""
        factory, _ = _make_session_factory()

        with patch(
            "app.bot.services.infocasas.session_manager.BotSettingRepository"
        ) as mock_repo:
            mock_repo.update_value = AsyncMock(side_effect=Exception("db down"))

            sm = SessionManager("u", "p", session_factory=factory)
            # Must not raise
            await sm._save_token_to_db("tok")

    @pytest.mark.asyncio
    async def test_load_token_strips_whitespace(self):
        """_load_token_from_db() strips whitespace from stored value."""
        factory, _ = _make_session_factory()

        with patch(
            "app.bot.services.infocasas.session_manager.BotSettingRepository"
        ) as mock_repo:
            mock_repo.get_value = AsyncMock(return_value="  jwt-with-spaces  ")

            sm = SessionManager("u", "p", session_factory=factory)
            token = await sm._load_token_from_db()

        assert token == "jwt-with-spaces"


# ---------------------------------------------------------------------------
# TestNotifySessionExpired
# ---------------------------------------------------------------------------


class TestNotifySessionExpired:
    """_notify_session_expired() alerts Ez via AdminNotifier.notify."""

    @pytest.mark.asyncio
    async def test_notify_called_with_alert_message(self, clear_alert_cooldown):
        """Sends a Telegram notification containing 'InfoCasas'."""
        notifier = _make_notifier()
        sm = SessionManager("u", "p", notifier=notifier)

        await sm._notify_session_expired()

        notifier.notify.assert_awaited_once()
        msg = notifier.notify.call_args[0][0]
        assert "InfoCasas" in msg

    @pytest.mark.asyncio
    async def test_no_notifier_no_raise(self):
        """Does nothing and never raises when notifier is None."""
        sm = SessionManager("u", "p", notifier=None)
        await sm._notify_session_expired()  # must not raise

    @pytest.mark.asyncio
    async def test_notifier_exception_swallowed(self, clear_alert_cooldown):
        """Never raises even when notifier.notify raises."""
        notifier = _make_notifier()
        notifier.notify = AsyncMock(side_effect=Exception("telegram down"))
        sm = SessionManager("u", "p", notifier=notifier)

        await sm._notify_session_expired()  # must not raise


# ---------------------------------------------------------------------------
# TestFactory
# ---------------------------------------------------------------------------


class TestFactory:
    """get_session_manager() factory builds correctly from env vars."""

    def test_factory_returns_session_manager_instance(self):
        """get_session_manager() returns a SessionManager."""
        import os

        with patch.dict(
            os.environ,
            {"INFOCASAS_USER": "factory_user", "INFOCASAS_PASS": "factory_pass"},
        ), patch("app.bot.config.bot_settings") as mock_settings:
            mock_settings.TELEGRAM_EZ_CHAT_ID = "12345"
            mock_settings.TELEGRAM_BOT_TOKEN = "bot-tok-abc"

            sm = get_session_manager()

        assert isinstance(sm, SessionManager)
        assert sm._user == "factory_user"
        assert sm._pass == "factory_pass"

    def test_factory_configures_notifier(self):
        """get_session_manager() attaches an AdminNotifier."""
        import os

        from app.bot.services.admin_notifier import AdminNotifier

        with patch.dict(
            os.environ,
            {"INFOCASAS_USER": "u", "INFOCASAS_PASS": "p"},
        ), patch("app.bot.config.bot_settings") as mock_settings:
            mock_settings.TELEGRAM_EZ_CHAT_ID = "999"
            mock_settings.TELEGRAM_BOT_TOKEN = "tok-999"

            sm = get_session_manager()

        assert isinstance(sm._notifier, AdminNotifier)
        assert sm._notifier.chat_id == "999"
        assert sm._notifier.bot_token == "tok-999"

    def test_factory_uses_empty_strings_when_env_missing(self):
        """get_session_manager() uses empty strings when env vars absent."""
        import os

        env = {k: v for k, v in os.environ.items()
               if k not in ("INFOCASAS_USER", "INFOCASAS_PASS")}

        with patch.dict(os.environ, env, clear=True), patch(
            "app.bot.config.bot_settings"
        ) as mock_settings:
            mock_settings.TELEGRAM_EZ_CHAT_ID = ""
            mock_settings.TELEGRAM_BOT_TOKEN = ""

            sm = get_session_manager()

        assert sm._user == ""
        assert sm._pass == ""


# ---------------------------------------------------------------------------
# TestPostGraphqlErrorClassification
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_alert_cooldown():
    """Reset module-level rate-limit dict before and after each test."""
    _last_alert_at.clear()
    yield
    _last_alert_at.clear()


class TestPostGraphqlErrorClassification:
    """_post_graphql raises typed exceptions for HTTP and network errors."""

    @pytest.mark.asyncio
    async def test_post_graphql_502_raises_service_error(self, clear_alert_cooldown):
        """HTTP 502 must raise ICServiceError containing '502'."""
        client = _make_http_client(status_code=502)
        sm = SessionManager("u", "p", http_client=client)

        with pytest.raises(ICServiceError, match="502"):
            await sm._post_graphql({"query": "{ me { id } }"}, headers={})

    @pytest.mark.asyncio
    async def test_post_graphql_401_raises_auth_error(self, clear_alert_cooldown):
        """HTTP 401 must raise ICAuthError."""
        client = _make_http_client(status_code=401)
        sm = SessionManager("u", "p", http_client=client)

        with pytest.raises(ICAuthError):
            await sm._post_graphql({"query": "{ me { id } }"}, headers={})

    @pytest.mark.asyncio
    async def test_post_graphql_503_raises_service_error(self, clear_alert_cooldown):
        """HTTP 503 must raise ICServiceError containing '503'."""
        client = _make_http_client(status_code=503)
        sm = SessionManager("u", "p", http_client=client)

        with pytest.raises(ICServiceError, match="503"):
            await sm._post_graphql({"query": "{ me { id } }"}, headers={})

    @pytest.mark.asyncio
    async def test_post_graphql_network_error_raises_service_error(
        self, clear_alert_cooldown
    ):
        """httpx.ConnectError must be wrapped in ICServiceError."""
        client = _make_http_client(raise_exc=httpx.ConnectError("unreachable"))
        sm = SessionManager("u", "p", http_client=client)

        with pytest.raises(ICServiceError):
            await sm._post_graphql({"query": "{ me { id } }"}, headers={})

    @pytest.mark.asyncio
    async def test_post_graphql_timeout_raises_service_error(
        self, clear_alert_cooldown
    ):
        """httpx.TimeoutException must be wrapped in ICServiceError."""
        client = _make_http_client(raise_exc=httpx.TimeoutException("timed out"))
        sm = SessionManager("u", "p", http_client=client)

        with pytest.raises(ICServiceError):
            await sm._post_graphql({"query": "{ me { id } }"}, headers={})


# ---------------------------------------------------------------------------
# TestGetValidTokenErrorRouting
# ---------------------------------------------------------------------------


class TestGetValidTokenErrorRouting:
    """get_valid_token routes ICServiceError vs ICAuthError to correct notify."""

    @pytest.mark.asyncio
    async def test_get_valid_token_5xx_sends_unavailable_alert(
        self, clear_alert_cooldown
    ):
        """ICServiceError from validate_session triggers 'no disponible' alert."""
        notifier = _make_notifier()
        factory, _ = _make_session_factory()
        sm = SessionManager("u", "p", session_factory=factory, notifier=notifier)
        sm._load_token_from_db = AsyncMock(return_value="tok")
        sm.validate_session = AsyncMock(side_effect=ICServiceError("HTTP 502"))
        sm._save_token_to_db = AsyncMock()

        result = await sm.get_valid_token()

        assert result is None
        notifier.notify.assert_awaited_once()
        msg = notifier.notify.call_args[0][0]
        assert "no disponible" in msg.lower() or "No disponible" in msg
        # Message must NOT instruct user to check credentials (that's for ICAuthError)
        assert "Verificar credenciales" not in msg
        assert "INFOCASAS_USER" not in msg

    @pytest.mark.asyncio
    async def test_get_valid_token_401_sends_credentials_alert(
        self, clear_alert_cooldown
    ):
        """Auth failure (validate=False, login=None) triggers credentials alert."""
        notifier = _make_notifier()
        factory, _ = _make_session_factory()
        sm = SessionManager("u", "p", session_factory=factory, notifier=notifier)
        sm._load_token_from_db = AsyncMock(return_value="stale-tok")
        sm.validate_session = AsyncMock(return_value=False)
        sm.login = AsyncMock(return_value=None)
        sm._save_token_to_db = AsyncMock()

        result = await sm.get_valid_token()

        assert result is None
        notifier.notify.assert_awaited_once()
        msg = notifier.notify.call_args[0][0]
        assert "credenciales" in msg.lower() or "INFOCASAS_USER" in msg


# ---------------------------------------------------------------------------
# TestAlertRateLimit
# ---------------------------------------------------------------------------


class TestAlertRateLimit:
    """_send_alert suppresses repeated alerts of the same type within 1 hour."""

    @pytest.mark.asyncio
    async def test_alert_rate_limit_one_per_hour_per_type(
        self, clear_alert_cooldown
    ):
        """Three consecutive availability errors send only 1 Telegram message."""
        notifier = _make_notifier()
        sm = SessionManager("u", "p", notifier=notifier)

        await sm._send_alert("availability", "InfoCasas no disponible")
        await sm._send_alert("availability", "InfoCasas no disponible")
        await sm._send_alert("availability", "InfoCasas no disponible")

        assert notifier.notify.await_count == 1

    @pytest.mark.asyncio
    async def test_alert_rate_limit_resets_after_one_hour(
        self, clear_alert_cooldown, monkeypatch
    ):
        """After 61 minutes have elapsed the same type can fire again."""
        from datetime import datetime, timedelta, timezone
        import app.bot.services.infocasas.session_manager as sm_module

        notifier = _make_notifier()
        sm = SessionManager("u", "p", notifier=notifier)

        # First alert fires normally
        await sm._send_alert("availability", "down")
        assert notifier.notify.await_count == 1

        # Simulate 61 minutes passing by back-dating the recorded timestamp
        past = datetime.now(timezone.utc) - timedelta(minutes=61)
        _last_alert_at["availability"] = past

        # Second alert after 61 minutes should go through
        await sm._send_alert("availability", "down again")
        assert notifier.notify.await_count == 2

    @pytest.mark.asyncio
    async def test_alert_rate_limit_independent_per_type(
        self, clear_alert_cooldown
    ):
        """'availability' and 'auth' cooldowns are tracked independently."""
        notifier = _make_notifier()
        sm = SessionManager("u", "p", notifier=notifier)

        await sm._send_alert("availability", "IC down")
        await sm._send_alert("auth", "bad creds")

        # Both types fire once — they don't share a cooldown bucket
        assert notifier.notify.await_count == 2
