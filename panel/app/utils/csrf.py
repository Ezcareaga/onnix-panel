"""CSRF double-submit cookie utilities (D1).

Pattern: OWASP double-submit cookie.
  - Server sets a random token as a JS-readable cookie (httponly=False).
  - Client JS reads the cookie and echoes it as X-CSRFToken header (HTMX)
    or as a hidden form field (plain forms).
  - Middleware compares cookie value against header/form value using
    constant-time compare to prevent timing attacks.

References:
  - OWASP Double Submit Cookie:
    https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html#double-submit-cookie
  - Python secrets module: https://docs.python.org/3/library/secrets.html
  - Starlette middleware / request.cookies:
    https://www.starlette.io/middleware/
"""
import secrets

# HTTP methods that do NOT mutate state — safe to skip CSRF validation.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def generate_csrf_token() -> str:
    """Return a URL-safe random token suitable for use as a CSRF cookie value.

    Uses secrets.token_urlsafe(32) which produces 43 characters of base64url
    entropy (256 bits), well above the OWASP recommended minimum.
    """
    return secrets.token_urlsafe(32)


def csrf_token_valid(
    method: str,
    path: str,
    cookie_token: str | None,
    header_token: str | None,
    form_token: str | None,
) -> bool:
    """Pure CSRF validation predicate.

    Returns True (request is safe / passes CSRF check) when:
      - Method is safe (GET, HEAD, OPTIONS, TRACE), OR
      - Path starts with /webhook/ (exempt — they use Twilio HMAC auth), OR
      - cookie_token is non-empty AND equals either header_token or form_token
        (checked via secrets.compare_digest for constant-time safety).

    Returns False for all other unsafe-method requests.

    Args:
        method:       HTTP method string (e.g. "POST", "GET").
        path:         Request path (e.g. "/login", "/webhook/whatsapp").
        cookie_token: Value of the csrf_token cookie (None or "" if absent).
        header_token: Value of the X-CSRFToken request header (None or "" if absent).
        form_token:   Value of the csrf_token form field (None or "" if absent).

    Returns:
        bool — True means the request passes the CSRF gate.
    """
    # Safe HTTP methods never carry side-effects — no CSRF check needed.
    if method.upper() in _SAFE_METHODS:
        return True

    # Webhooks are exempt — they validate via Twilio HMAC or similar.
    if path.startswith("/webhook/"):
        return True

    # Normalise: treat None as empty string.
    cookie = cookie_token or ""
    header = header_token or ""
    form = form_token or ""

    # Empty cookie is treated as absent (no token issued yet).
    if not cookie:
        return False

    # Accept if header OR form token matches the cookie (constant-time).
    if header and secrets.compare_digest(cookie, header):
        return True

    if form and secrets.compare_digest(cookie, form):
        return True

    return False
