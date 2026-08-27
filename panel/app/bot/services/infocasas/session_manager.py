"""InfoCasas SessionManager — authentication token lifecycle for GraphQL API.

Manages JWT Bearer tokens for the InfoCasas GraphQL API at
https://graph.infocasas.com.uy/graphql. Tokens persist in bot_settings
under key ``infocasas_frontend_token`` so they survive service restarts.

Flow:
  get_valid_token() → load from DB → validate → return if valid
                                    → login() on failure → save → return
                                    → notify Ez if all methods fail → None
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.bot.services.admin_notifier import AdminNotifier
from app.database import async_session_factory
from app.repositories.bot_setting_repo import BotSettingRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------


class ICAuthError(Exception):
    """Raised when InfoCasas rejects credentials (401/403 or unauthenticated GraphQL error)."""


class ICServiceError(Exception):
    """Raised when InfoCasas service is unavailable (5xx, network, timeout)."""


# ---------------------------------------------------------------------------
# Module-level alert rate limiting
# ---------------------------------------------------------------------------

_ALERT_COOLDOWN = timedelta(hours=1)
_last_alert_at: dict[str, datetime] = {}  # error_type -> last sent UTC

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GRAPHQL_URL = "https://graph.infocasas.com.uy/graphql"
_GRAPHQL_TIMEOUT = 15.0  # seconds, per project resilience rules
_HEADERS_BASE = {
    "Content-Type": "application/json",
    "x-origin": "www.infocasas.com.py",
}
_TOKEN_KEY = "infocasas_frontend_token"
# user_id=0 used for system writes (no human actor)
_SYSTEM_USER_ID = 0


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------


class SessionManager:
    """Manage InfoCasas authentication tokens.

    Persists the JWT Bearer token in ``bot_settings`` so it survives
    restarts.  All network errors are caught and logged; the caller
    receives ``None`` on failure rather than an exception.

    Parameters
    ----------
    infocasas_user:
        InfoCasas account username / email.
    infocasas_pass:
        InfoCasas account password.
    session_factory:
        Optional async session factory override (for testing).
    notifier:
        Optional AdminNotifier override (for testing).
    http_client:
        Optional pre-built httpx.AsyncClient (for testing).
        When provided, the client is used directly and is NOT closed by
        this class — the caller owns its lifecycle.
    """

    def __init__(
        self,
        infocasas_user: str,
        infocasas_pass: str,
        *,
        session_factory: Any = None,
        notifier: AdminNotifier | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._user = infocasas_user
        self._pass = infocasas_pass
        self._session_factory = session_factory or async_session_factory
        self._notifier = notifier
        # When an external client is injected (tests) we never close it.
        self._external_client = http_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_valid_token(self) -> str | None:
        """Return a valid InfoCasas Bearer token.

        Load the stored token from DB, validate it, refresh via fresh
        login on failure, and notify Ez if all methods fail.

        Returns
        -------
        str | None
            A valid JWT access token, or None if authentication cannot be
            established.
        """
        token = await self._load_token_from_db()

        if token:
            try:
                if await self.validate_session(token):
                    logger.debug("SessionManager: cached token is valid")
                    return token
                logger.info("SessionManager: cached token invalid, attempting login")
            except ICServiceError as exc:
                logger.warning("SessionManager: service unavailable during validate — %s", exc)
                await self._notify_session_unavailable(str(exc))
                return None

        try:
            new_token = await self.login()
        except ICServiceError as exc:
            logger.warning("SessionManager: service unavailable during login — %s", exc)
            await self._notify_session_unavailable(str(exc))
            return None

        if new_token:
            await self._save_token_to_db(new_token)
            return new_token

        logger.warning("SessionManager: all authentication methods failed")
        await self._notify_session_expired()
        return None

    async def validate_session(self, token: str) -> bool:
        """Check whether *token* is accepted by the InfoCasas GraphQL API.

        Issues a ``{ me { id name } }`` query and inspects the response.

        Parameters
        ----------
        token:
            JWT Bearer token to test.

        Returns
        -------
        bool
            True when ``data.me.id`` is populated; False on any error,
            401/403 response, or 'unauthenticated' in the GraphQL errors.
        """
        payload: dict[str, Any] = {"query": "{ me { id name } }"}
        headers = {
            **_HEADERS_BASE,
            "Authorization": f"Bearer {token}",
        }
        try:
            data = await self._post_graphql(payload, headers=headers)
        except ICServiceError:
            raise
        except ICAuthError:
            logger.debug("SessionManager: validate_session — auth rejected")
            return False
        except Exception:
            logger.warning("SessionManager: validate_session request failed", exc_info=True)
            return False

        if data is None:
            return False

        # Unauthenticated errors from GraphQL layer
        errors = data.get("errors") or []
        for err in errors:
            msg = str(err.get("message", "")).lower()
            if "unauthenticated" in msg:
                logger.debug("SessionManager: token rejected — unauthenticated error")
                return False

        me = (data.get("data") or {}).get("me")
        if not me or not me.get("id"):
            logger.debug("SessionManager: token rejected — me.id is null")
            return False

        return True

    async def login(self) -> str | None:
        """Execute the GraphQL login mutation.

        Uses the credentials supplied at construction time.

        Returns
        -------
        str | None
            The ``access_token`` JWT on success, or None on any failure.
        """
        if not self._user or not self._pass:
            logger.error("SessionManager: login aborted — credentials not configured")
            return None

        mutation = (
            "mutation {"
            f'  login(input: {{ username: "{self._user}", password: "{self._pass}" }}) {{'
            "    access_token"
            "    refresh_token"
            "    expires_in"
            "    token_type"
            "    user_md5"
            "    user { id name }"
            "  }"
            "}"
        )
        payload: dict[str, Any] = {"query": mutation}

        logger.info("SessionManager: attempting GraphQL login for user %s", self._user)
        try:
            data = await self._post_graphql(payload, headers=_HEADERS_BASE)
        except ICServiceError:
            raise
        except ICAuthError:
            logger.warning("SessionManager: login — credentials rejected (401/403)")
            return None
        except Exception:
            logger.warning("SessionManager: login request failed", exc_info=True)
            return None

        if data is None:
            return None

        errors = data.get("errors")
        if errors:
            logger.warning("SessionManager: login returned GraphQL errors: %s", errors)
            return None

        login_data = (data.get("data") or {}).get("login") or {}
        token = login_data.get("access_token")
        if not token:
            logger.warning("SessionManager: login response missing access_token")
            return None

        logger.info("SessionManager: login successful for user %s", self._user)
        return token

    # ------------------------------------------------------------------
    # Private: DB persistence
    # ------------------------------------------------------------------

    async def _load_token_from_db(self) -> str | None:
        """Read ``infocasas_frontend_token`` from bot_settings.

        Returns
        -------
        str | None
            The stored token string, or None when absent or on DB error.
        """
        try:
            async with self._session_factory() as db:
                token = await BotSettingRepository.get_value(db, _TOKEN_KEY)
                if token and token.strip() and token.upper() != "PLACEHOLDER":
                    return token.strip()
                return None
        except Exception:
            logger.warning("SessionManager: failed to load token from DB", exc_info=True)
            return None

    async def _save_token_to_db(self, token: str) -> None:
        """Write *token* to ``bot_settings`` under key ``infocasas_frontend_token``.

        Parameters
        ----------
        token:
            JWT access token to persist.
        """
        try:
            async with self._session_factory() as db:
                await BotSettingRepository.update_value(
                    db, _TOKEN_KEY, token, _SYSTEM_USER_ID
                )
                await db.commit()
            logger.debug("SessionManager: token saved to DB")
        except Exception:
            logger.warning("SessionManager: failed to save token to DB", exc_info=True)

    # ------------------------------------------------------------------
    # Private: notification
    # ------------------------------------------------------------------

    async def _notify_session_expired(self) -> None:
        """Auth genuinely failed (401/403 or invalid credentials)."""
        text = (
            "<b>ALERTA: InfoCasas sesion expirada</b>\n"
            "No fue posible autenticar con la API de InfoCasas.\n"
            "Verificar credenciales INFOCASAS_USER / INFOCASAS_PASS."
        )
        await self._send_alert("auth", text)

    async def _notify_session_unavailable(self, status_code: str) -> None:
        """Service unavailable (5xx / network) — not a credentials problem."""
        text = (
            "<b>InfoCasas No Disponible</b>\n"
            f"Error {status_code} — problema de conectividad upstream, no de credenciales.\n"
            "Se reintentará automáticamente."
        )
        await self._send_alert("availability", text)

    async def _send_alert(self, error_type: str, text: str) -> None:
        """Send *text* via notifier, honouring a 1-hour per-type cooldown.

        Uses the module-level ``_last_alert_at`` dict so the rate limit
        persists across multiple SessionManager instances within a process.
        """
        now = datetime.now(timezone.utc)
        last = _last_alert_at.get(error_type)
        if last and (now - last) < _ALERT_COOLDOWN:
            logger.debug(
                "SessionManager: alert '%s' suppressed (rate limit)", error_type
            )
            return
        _last_alert_at[error_type] = now
        if self._notifier is None:
            return
        try:
            await self._notifier.notify(text)
        except Exception:
            logger.warning(
                "SessionManager: failed to send '%s' alert", error_type, exc_info=True
            )

    # ------------------------------------------------------------------
    # Private: HTTP
    # ------------------------------------------------------------------

    async def _post_graphql(
        self,
        payload: dict[str, Any],
        *,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        """POST *payload* to the InfoCasas GraphQL endpoint.

        Parameters
        ----------
        payload:
            JSON-serialisable GraphQL request body.
        headers:
            HTTP headers to include in the request.

        Returns
        -------
        dict
            Parsed JSON response body.

        Raises
        ------
        ICAuthError
            HTTP 401/403 response.
        ICServiceError
            HTTP 5xx response, network error, or timeout.
        """
        try:
            if self._external_client is not None:
                resp = await self._external_client.post(
                    _GRAPHQL_URL,
                    json=payload,
                    headers=headers,
                    timeout=_GRAPHQL_TIMEOUT,
                )
            else:
                async with httpx.AsyncClient(timeout=_GRAPHQL_TIMEOUT) as client:
                    resp = await client.post(
                        _GRAPHQL_URL,
                        json=payload,
                        headers=headers,
                    )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ICServiceError(str(exc)) from exc

        if resp.status_code in (401, 403):
            logger.debug("SessionManager: HTTP %d from GraphQL", resp.status_code)
            raise ICAuthError(f"HTTP {resp.status_code}")

        if resp.status_code >= 500:
            logger.warning(
                "SessionManager: HTTP %d from GraphQL (upstream error)", resp.status_code
            )
            raise ICServiceError(f"HTTP {resp.status_code}")

        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Module-level factory
# ---------------------------------------------------------------------------


def get_session_manager() -> SessionManager:
    """Create a SessionManager from environment variables and defaults.

    Returns
    -------
    SessionManager
        Configured instance ready for use.
    """
    from app.bot.config import bot_settings

    return SessionManager(
        infocasas_user=os.environ.get("INFOCASAS_USER", ""),
        infocasas_pass=os.environ.get("INFOCASAS_PASS", ""),
        notifier=AdminNotifier(
            chat_id=bot_settings.TELEGRAM_EZ_CHAT_ID,
            bot_token=bot_settings.TELEGRAM_BOT_TOKEN,
        ),
    )
