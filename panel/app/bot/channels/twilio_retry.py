"""Twilio retry helper with exponential backoff.

Provides ``twilio_post_with_retry``, a thin async wrapper around
``httpx.AsyncClient.post`` that retries transient errors and fires admin
alerts on permanent failures or exhausted retries.

Retry policy
------------
- **Transient (retried):** HTTP 5xx, HTTP 429, ``httpx.TransportError``
  (covers TimeoutException, NetworkError, ReadError, WriteError,
  PoolTimeout, RemoteProtocolError, etc.).
- **Permanent (no retry):** HTTP 4xx other than 429.
  - Twilio subcodes 63016 / 63003 → log only, no admin alert.
  - All other 4xx → log + admin alert.
- **Retries exhausted:** log + admin alert.

Delays between attempts: 1 s, 3 s, 9 s (× 3 factor).
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

import httpx

if TYPE_CHECKING:
    from app.bot.services.admin_notifier import AdminNotifier

logger = logging.getLogger(__name__)

# Delays before attempt 2, 3, 4 (i.e. before retry 1, 2, 3).
RETRY_DELAYS: tuple[float, ...] = (1.0, 3.0, 9.0)

# Twilio subcodes that are expected / noisy — log only, no admin alert.
NO_ALERT_TWILIO_CODES: frozenset[str] = frozenset({"63016", "63003"})

_MAX_ATTEMPTS: int = len(RETRY_DELAYS) + 1  # 4 total


def wa_send_disabled() -> bool:
    """True when ``WA_SEND_ENABLED`` is explicitly off (staging isolation).

    Single source of truth for the switch semantics (incidente 2026-04-04
    class): staging inherits prod Twilio creds via ``env_file: .env``, so
    every path that POSTs to api.twilio.com must consult this before the
    request leaves the process.  ``false`` / ``0`` / empty count as off;
    the default is ``"true"``, so production behavior is unchanged.
    """
    return os.getenv("WA_SEND_ENABLED", "true").strip().lower() in ("false", "0", "")


@dataclass(frozen=True)
class TwilioPostResult:
    """Result of a ``twilio_post_with_retry`` call.

    Attributes
    ----------
    success:
        True when Twilio returned HTTP 200 or 201.
    status_code:
        HTTP status code of the final response, or None on network failure.
    response_json:
        Parsed JSON body of the final response, or None on network failure /
        non-JSON body.
    twilio_error_code:
        String representation of the ``code`` field in Twilio's error body
        (e.g. ``"63016"``), or None when absent.
    attempts:
        Total number of POST attempts made (1 = no retries needed).
    to_number:
        Destination ``whatsapp:+NNN`` string propagated from the caller for
        use in failure-marker callbacks.  Empty string when not supplied.
    message_type:
        One of ``"text"``, ``"photo"``, ``"template"`` propagated from the
        caller for use in failure-marker callbacks.  Empty string when not
        supplied.
    """

    success: bool
    status_code: Optional[int]
    response_json: Optional[dict]
    twilio_error_code: Optional[str]
    attempts: int
    to_number: str = ""
    message_type: str = ""


async def twilio_post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    data: dict,
    auth: tuple[str, str],
    *,
    timeout: float = 15.0,
    admin_notifier: Optional["AdminNotifier"] = None,
    to_number: str = "",
    message_type: str = "text",
    on_permanent_failure: Optional[Callable[["TwilioPostResult"], Awaitable[None]]] = None,
    sleep: Optional[Callable[[float], Awaitable[None]]] = None,
) -> "TwilioPostResult":
    """POST to Twilio with up to 3 retries and exponential backoff.

    Parameters
    ----------
    client:
        Configured ``httpx.AsyncClient`` — the caller is responsible for
        lifecycle management (open/close).
    url:
        Full Twilio Messages URL (e.g. ``https://api.twilio.com/.../Messages.json``).
    data:
        Form-encoded payload dict.
    auth:
        ``(account_sid, auth_token)`` tuple for HTTP Basic Auth.
    timeout:
        Per-request timeout in seconds (default 15).  Passed to ``client.post``
        unless the client already has a timeout set at construction time.
    admin_notifier:
        Optional ``AdminNotifier`` instance.  When provided, used to fire TG
        alerts on permanent failures or exhausted retries.
    to_number:
        Destination ``whatsapp:+NNN`` string — included in log / alert context
        and propagated into the returned :class:`TwilioPostResult`.
    message_type:
        One of ``"text"``, ``"photo"``, ``"template"`` — for log context and
        propagated into the returned :class:`TwilioPostResult`.
    on_permanent_failure:
        Optional async callback invoked **once** on any non-retried permanent
        failure (exhausted retries OR non-silent 4xx).  Called *after* the
        admin alert.  Exceptions from the callback are swallowed and logged so
        they never propagate to the caller.
        Silent Twilio codes (63016, 63003) do NOT trigger this callback.
    sleep:
        Async sleep callable.  Defaults to ``asyncio.sleep``.  Override in
        tests to avoid real delays.

    Returns
    -------
    TwilioPostResult
        Immutable result dataclass — callers inspect ``.success`` to decide
        what to do next.
    """
    # Staging isolation guard — short-circuit BEFORE the first POST.  This is
    # the chokepoint for every bot-side WhatsApp: WhatsAppSender._post and the
    # four InfoCasas senders all land here.  attempts=0 marks "nothing left
    # the process"; success=True keeps callers on their happy path.
    if wa_send_disabled():
        logger.warning(
            'WA send disabled via WA_SEND_ENABLED=false — payload NOT sent '
            '{"channel": "whatsapp", "to": "%s", "type": "%s"}',
            to_number, message_type,
        )
        return TwilioPostResult(
            success=True,
            status_code=None,
            response_json=None,
            twilio_error_code=None,
            attempts=0,
            to_number=to_number,
            message_type=message_type,
        )

    _sleep = sleep if sleep is not None else asyncio.sleep
    last_status: Optional[int] = None
    last_body: Optional[dict] = None
    last_twilio_code: Optional[str] = None

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = await client.post(url, data=data, auth=auth, timeout=timeout)
        except httpx.TransportError as exc:
            last_status = None
            last_body = None
            last_twilio_code = None
            logger.warning(
                'Twilio POST transient error — {"channel": "whatsapp", "to": "%s",'
                ' "type": "%s", "attempt": %d, "error": "%.200s"}',
                to_number, message_type, attempt, str(exc),
            )
            if attempt < _MAX_ATTEMPTS:
                await _sleep(RETRY_DELAYS[attempt - 1])
                continue
            # Retries exhausted after network failure
            logger.error(
                'Twilio POST retries exhausted — {"channel": "whatsapp", "to": "%s",'
                ' "type": "%s", "attempts": %d}',
                to_number, message_type, attempt,
            )
            await _fire_alert(
                admin_notifier,
                error_code="network_error",
                error_message=str(exc),
                to_number=to_number,
            )
            result = TwilioPostResult(
                success=False,
                status_code=None,
                response_json=None,
                twilio_error_code=None,
                attempts=attempt,
                to_number=to_number,
                message_type=message_type,
            )
            await _fire_permanent_failure_callback(on_permanent_failure, result)
            return result

        # --- Parse response ---
        last_status = resp.status_code
        try:
            last_body = resp.json()
        except Exception:
            last_body = None

        twilio_code: Optional[str] = None
        if last_body and "code" in last_body:
            twilio_code = str(last_body["code"])
        last_twilio_code = twilio_code

        # --- Success ---
        if resp.status_code in (200, 201):
            logger.info(
                'Twilio POST ok — {"channel": "whatsapp", "to": "%s", "type": "%s",'
                ' "http_status": %d, "attempts": %d, "sid": "%s"}',
                to_number, message_type, resp.status_code, attempt,
                (last_body or {}).get("sid", ""),
            )
            return TwilioPostResult(
                success=True,
                status_code=resp.status_code,
                response_json=last_body,
                twilio_error_code=None,
                attempts=attempt,
                to_number=to_number,
                message_type=message_type,
            )

        # --- Transient: 5xx or 429 ---
        if resp.status_code >= 500 or resp.status_code == 429:
            logger.warning(
                'Twilio POST transient HTTP — {"channel": "whatsapp", "to": "%s",'
                ' "type": "%s", "http_status": %d, "attempt": %d, "body": "%.200s"}',
                to_number, message_type, resp.status_code, attempt,
                resp.text,
            )
            if attempt < _MAX_ATTEMPTS:
                await _sleep(RETRY_DELAYS[attempt - 1])
                continue
            # Retries exhausted
            logger.error(
                'Twilio POST retries exhausted — {"channel": "whatsapp", "to": "%s",'
                ' "type": "%s", "http_status": %d, "attempts": %d}',
                to_number, message_type, resp.status_code, attempt,
            )
            await _fire_alert(
                admin_notifier,
                error_code=str(resp.status_code),
                error_message=_extract_message(last_body),
                to_number=to_number,
            )
            result = TwilioPostResult(
                success=False,
                status_code=resp.status_code,
                response_json=last_body,
                twilio_error_code=twilio_code,
                attempts=attempt,
                to_number=to_number,
                message_type=message_type,
            )
            await _fire_permanent_failure_callback(on_permanent_failure, result)
            return result

        # --- Permanent 4xx (no retry) ---
        logger.warning(
            'Twilio POST permanent failure — {"channel": "whatsapp", "to": "%s",'
            ' "type": "%s", "http_status": %d, "twilio_code": "%s", "body": "%.200s"}',
            to_number, message_type, resp.status_code, twilio_code or "",
            resp.text,
        )
        if twilio_code not in NO_ALERT_TWILIO_CODES:
            await _fire_alert(
                admin_notifier,
                error_code=twilio_code or str(resp.status_code),
                error_message=_extract_message(last_body),
                to_number=to_number,
            )
        result = TwilioPostResult(
            success=False,
            status_code=resp.status_code,
            response_json=last_body,
            twilio_error_code=twilio_code,
            attempts=attempt,
            to_number=to_number,
            message_type=message_type,
        )
        # Only fire callback for non-silent codes (same gate as admin alert)
        if twilio_code not in NO_ALERT_TWILIO_CODES:
            await _fire_permanent_failure_callback(on_permanent_failure, result)
        return result

    # Unreachable — loop always returns — but satisfies type checker.
    return TwilioPostResult(  # pragma: no cover
        success=False,
        status_code=last_status,
        response_json=last_body,
        twilio_error_code=last_twilio_code,
        attempts=_MAX_ATTEMPTS,
        to_number=to_number,
        message_type=message_type,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _extract_message(body: Optional[dict]) -> str:
    """Pull the ``message`` field from Twilio's error body, or return empty."""
    if not body:
        return ""
    return str(body.get("message", ""))


async def _fire_alert(
    notifier: Optional["AdminNotifier"],
    error_code: str,
    error_message: str,
    to_number: str,
) -> None:
    """Call ``notifier.notify_twilio_error`` if a notifier is provided."""
    if notifier is None:
        return
    try:
        await notifier.notify_twilio_error(
            error_code,
            error_message,
            to_number=to_number,
        )
    except Exception:
        logger.warning("twilio_retry: admin alert failed (non-fatal)", exc_info=True)


async def _fire_permanent_failure_callback(
    callback: Optional[Callable[["TwilioPostResult"], Awaitable[None]]],
    result: "TwilioPostResult",
) -> None:
    """Invoke ``callback(result)`` if provided; swallow all exceptions."""
    if callback is None:
        return
    try:
        await callback(result)
    except Exception:
        logger.warning(
            "twilio_retry: on_permanent_failure callback raised (non-fatal)",
            exc_info=True,
        )
