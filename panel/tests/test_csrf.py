"""TDD — CSRF double-submit cookie implementation (D1).

Tests for:
  1. Unit: csrf_token_valid() pure predicate — safe methods, webhooks, token
     matching, missing tokens, constant-time compare path.
  2. Integration with enforcement FORCE-ENABLED (via CSRF_FORCE_ENFORCE env var):
     - POST with no token → 403
     - POST with matching cookie+header → passes CSRF gate
     - /webhook/... POST with no token → not blocked
  3. Sanity under normal pytest (enforcement off):
     - Existing POSTs are NOT 403'd
     - GET sets request.state.csrf_token + issues csrf_token cookie
"""
import os
import pytest
import secrets

# ---------------------------------------------------------------------------
# 1. UNIT TESTS — pure predicate (no HTTP, no DB)
# ---------------------------------------------------------------------------

class TestCsrfTokenValid:
    """Unit tests for the pure csrf_token_valid() predicate."""

    def _fn(self):
        from app.utils.csrf import csrf_token_valid
        return csrf_token_valid

    def test_get_always_passes_regardless_of_tokens(self):
        fn = self._fn()
        assert fn("GET", "/dashboard", "", "", "") is True
        assert fn("GET", "/dashboard", None, None, None) is True

    def test_head_always_passes(self):
        assert self._fn()("HEAD", "/anything", "", "", "") is True

    def test_options_always_passes(self):
        assert self._fn()("OPTIONS", "/anything", "", "", "") is True

    def test_trace_always_passes(self):
        assert self._fn()("TRACE", "/anything", "", "", "") is True

    def test_webhook_post_always_passes(self):
        fn = self._fn()
        assert fn("POST", "/webhook/whatsapp", "", "", "") is True
        assert fn("POST", "/webhook/telegram", "", "", "") is True
        assert fn("POST", "/webhook/anything", None, None, None) is True

    def test_post_without_cookie_token_returns_false(self):
        fn = self._fn()
        assert fn("POST", "/login", "", "some-header-token", "") is False
        assert fn("POST", "/login", None, "some-header-token", "") is False

    def test_post_with_mismatched_header_token_returns_false(self):
        fn = self._fn()
        cookie = "valid-cookie-token-abc123"
        bad_header = "wrong-token-xyz"
        assert fn("POST", "/login", cookie, bad_header, "") is False

    def test_post_with_mismatched_form_token_returns_false(self):
        fn = self._fn()
        cookie = "valid-cookie-token-abc123"
        bad_form = "wrong-token-xyz"
        assert fn("POST", "/login", cookie, "", bad_form) is False

    def test_post_with_matching_header_token_returns_true(self):
        fn = self._fn()
        token = secrets.token_urlsafe(32)
        assert fn("POST", "/login", token, token, "") is True

    def test_post_with_matching_form_token_returns_true(self):
        fn = self._fn()
        token = secrets.token_urlsafe(32)
        assert fn("POST", "/login", token, "", token) is True

    def test_post_with_both_matching_returns_true(self):
        fn = self._fn()
        token = secrets.token_urlsafe(32)
        assert fn("POST", "/login", token, token, token) is True

    def test_delete_without_token_returns_false(self):
        fn = self._fn()
        assert fn("DELETE", "/api/something", "", "", "") is False

    def test_put_with_matching_header_returns_true(self):
        fn = self._fn()
        token = secrets.token_urlsafe(32)
        assert fn("PUT", "/api/something", token, token, "") is True

    def test_patch_with_matching_form_returns_true(self):
        fn = self._fn()
        token = secrets.token_urlsafe(32)
        assert fn("PATCH", "/api/something", token, "", token) is True

    def test_empty_strings_as_both_tokens_returns_false(self):
        """Empty cookie is treated as absent — must not allow empty-equals-empty."""
        fn = self._fn()
        assert fn("POST", "/login", "", "", "") is False

    def test_none_as_all_tokens_returns_false(self):
        fn = self._fn()
        assert fn("POST", "/login", None, None, None) is False

    def test_generate_csrf_token_returns_urlsafe_string(self):
        from app.utils.csrf import generate_csrf_token
        token = generate_csrf_token()
        assert isinstance(token, str)
        assert len(token) >= 32  # token_urlsafe(32) → 43 chars base64url


# ---------------------------------------------------------------------------
# 2. INTEGRATION — enforcement FORCE-ENABLED
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=False)
def enforce_csrf(monkeypatch):
    """Force CSRF enforcement even under pytest by setting the env flag."""
    monkeypatch.setenv("CSRF_FORCE_ENFORCE", "1")
    yield
    monkeypatch.delenv("CSRF_FORCE_ENFORCE", raising=False)


class TestCsrfEnforcementForceEnabled:
    """Integration tests with enforcement force-enabled via CSRF_FORCE_ENFORCE=1."""

    @pytest.mark.usefixtures("enforce_csrf")
    async def test_post_without_token_returns_403(self, client):
        """POST to a normal endpoint with no CSRF token must return 403."""
        resp = await client.post("/login", data={
            "email": "nobody@onnixtest.com",
            "password": "whatever",
        })
        assert resp.status_code == 403

    @pytest.mark.usefixtures("enforce_csrf")
    async def test_post_with_matching_cookie_and_header_passes_csrf_gate(self, client):
        """POST with matching cookie+header must not be blocked by CSRF (not 403)."""
        token = secrets.token_urlsafe(32)
        resp = await client.post(
            "/login",
            data={"email": "nobody@onnixtest.com", "password": "whatever"},
            cookies={"csrf_token": token},
            headers={"X-CSRFToken": token},
        )
        # Must NOT be 403 — CSRF gate passes. Auth may still return 401 or 200.
        assert resp.status_code != 403

    @pytest.mark.usefixtures("enforce_csrf")
    async def test_post_with_matching_cookie_and_form_field_passes_csrf_gate(self, client):
        """POST with matching cookie+form field must not be blocked by CSRF."""
        token = secrets.token_urlsafe(32)
        resp = await client.post(
            "/login",
            data={
                "email": "nobody@onnixtest.com",
                "password": "whatever",
                "csrf_token": token,
            },
            cookies={"csrf_token": token},
        )
        assert resp.status_code != 403

    @pytest.mark.usefixtures("enforce_csrf")
    async def test_webhook_post_without_token_is_not_blocked(self, client):
        """Webhook endpoints must be exempt from CSRF enforcement.

        The webhook will likely return 403 from its own Twilio HMAC auth check
        (missing signature), but it must NOT be a CSRF 403 (which contains
        '<p>Solicitud inválida (CSRF check)</p>' in the body).
        """
        resp = await client.post(
            "/webhook/whatsapp",
            data={"Body": "test", "From": "whatsapp:+595981000000"},
        )
        # The CSRF middleware MUST NOT block webhooks. Any 403 here must come
        # from Twilio HMAC auth (body contains JSON), not from our CSRF check.
        csrf_body = "<p>Solicitud inválida (CSRF check)</p>"
        assert csrf_body not in resp.text, (
            "Webhook was blocked by CSRF middleware — webhooks must be exempt"
        )

    @pytest.mark.usefixtures("enforce_csrf")
    async def test_post_with_wrong_header_token_returns_403(self, client):
        """POST with mismatched header token must return 403."""
        cookie_token = secrets.token_urlsafe(32)
        wrong_header = secrets.token_urlsafe(32)
        resp = await client.post(
            "/login",
            data={"email": "nobody@onnixtest.com", "password": "whatever"},
            cookies={"csrf_token": cookie_token},
            headers={"X-CSRFToken": wrong_header},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 3. SANITY — normal pytest (enforcement off by default)
# ---------------------------------------------------------------------------

class TestCsrfPytestSanity:
    """Sanity tests: enforcement is off under pytest (CSRF_FORCE_ENFORCE not set)."""

    async def test_existing_post_login_not_403d_under_pytest(self, client):
        """Existing login POST must not be 403'd under normal pytest (enforcement off)."""
        resp = await client.post("/login", data={
            "email": "ez@onnix.com.py",
            "password": "wrong",
        })
        # Must not be a CSRF 403 — may be 401 (bad password) or 303 (redirect)
        assert resp.status_code != 403

    async def test_get_sets_csrf_token_on_request_state_and_cookie(self, client):
        """GET must set csrf_token cookie (JS-readable for double-submit pattern)."""
        resp = await client.get("/login")
        assert resp.status_code == 200
        # The middleware must set the csrf_token cookie on the response
        assert "csrf_token" in resp.cookies, (
            "Expected csrf_token cookie on GET /login response; "
            "middleware must issue the cookie for new sessions"
        )

    async def test_csrf_cookie_is_not_httponly(self, client):
        """csrf_token cookie must NOT be httponly — JS must be able to read it."""
        resp = await client.get("/login")
        # httpx exposes cookies as SimpleCookie; the httponly flag is in the
        # Set-Cookie header. If httponly is present it must NOT be there.
        set_cookie_header = resp.headers.get("set-cookie", "")
        if "csrf_token" in set_cookie_header:
            assert "httponly" not in set_cookie_header.lower(), (
                "csrf_token cookie must not be HttpOnly so JavaScript can read it"
            )

    async def test_login_page_html_contains_csrf_meta_or_hidden_field(self, client):
        """GET /login must return HTML with a csrf_token hidden field."""
        resp = await client.get("/login")
        body = resp.text
        assert "csrf_token" in body, (
            "Expected csrf_token hidden field in login HTML"
        )


# ---------------------------------------------------------------------------
# 4. REGRESSION — D1 body-replay bug (pure ASGI middleware must not exhaust
#    the ASGI receive stream before the downstream route reads Form(...)).
#
#    Root cause that these tests guard against: the original @app.middleware("http")
#    implementation called await request.form() inside the middleware to read the
#    csrf_token form field. BaseHTTPMiddleware does NOT replay the consumed body to
#    the downstream ASGI app, so FastAPI's Form(...) parameters arrived empty.
#    With CSRF_FORCE_ENFORCE=1 a valid POST /login with correct cookie+form-field
#    was returning 401 (empty email/password) instead of 303 -> /dashboard.
# ---------------------------------------------------------------------------

import subprocess as _subprocess


def _clear_admin_lockout() -> None:
    """Remove recent wrong_password/locked rows for the real admin AND for the
    empty-email '' sentinel.

    The body-replay bug (the regression this class guards) sends email='' to the
    route when the middleware consumes the form body; those '' rows accumulate and
    trigger the lockout gate (is_locked('') == True) on subsequent requests, which
    causes the route to return 401 before even attempting authentication — masking
    the real 401 (empty-email) with a 'locked' 401. Both email variants must be
    cleared so the regression test can reach the actual auth path.

    Mirrors the same logic used by admin_client in conftest.py.
    """
    for email in ("ez@onnix.com.py", ""):
        try:
            _subprocess.run(
                ["docker", "exec", "onnix-postgres",
                 "psql", "-U", "onnix", "-d", os.environ["POSTGRES_DB"], "-c",
                 f"DELETE FROM auth_audit WHERE email = '{email}' "
                 "AND result IN ('wrong_password', 'not_found', 'inactive', 'locked') "
                 "AND created_at > now() - interval '40 minutes'"],
                capture_output=True,
                timeout=10,
            )
        except Exception:
            pass  # Best-effort; test will fail naturally if lockout blocks login


class TestCsrfBodyReplayRegression:
    """Regression tests for the D1 body-replay bug.

    These tests MUST fail against the old @app.middleware("http") implementation
    (which consumed the ASGI receive stream without replaying it) and pass with
    the correct pure-ASGI middleware that buffers + replays the body.
    """

    @pytest.mark.usefixtures("enforce_csrf")
    async def test_valid_login_form_post_with_csrf_cookie_and_form_field_returns_303(
        self, client
    ):
        """POST /login with VALID credentials + matching csrf_token cookie+form-field
        must return 303 -> /dashboard under force-enforcement.

        This is the primary regression test for the body-replay bug:
        - The CSRF middleware must read the form token WITHOUT consuming the stream.
        - The downstream route (Form(...) params) must still receive the full body.
        - If the body is exhausted by the middleware, email/password arrive empty
          and the route returns 401 instead of 303.
        """
        _clear_admin_lockout()
        token = secrets.token_urlsafe(32)
        admin_password = os.environ.get("TEST_ADMIN_PASSWORD", "test-fallback-only")
        # Use a fresh client with the csrf_token cookie pre-set
        from httpx import AsyncClient, ASGITransport
        from app.main import app as _app
        async with AsyncClient(
            transport=ASGITransport(app=_app),
            base_url="http://test",
            follow_redirects=False,
            cookies={"csrf_token": token},
        ) as c:
            resp = await c.post(
                "/login",
                data={
                    "email": "ez@onnix.com.py",
                    "password": admin_password,
                    "csrf_token": token,
                },
            )
        assert resp.status_code == 303, (
            f"Expected 303 redirect to /dashboard; got {resp.status_code}. "
            "If 401: middleware exhausted the ASGI receive stream (body-replay bug). "
            "If 403: CSRF check failed (token mismatch). "
            f"Response body snippet: {resp.text[:200]}"
        )
        assert resp.headers.get("location") == "/dashboard", (
            f"Expected Location: /dashboard, got: {resp.headers.get('location')}"
        )

    @pytest.mark.usefixtures("enforce_csrf")
    async def test_valid_login_via_header_token_returns_303(self, client):
        """POST /login with VALID credentials + matching csrf_token via X-CSRFToken
        header (not form field) must return 303 -> /dashboard.

        This path does NOT require reading the body for the CSRF token (header
        already provides it), so body replay is not needed.  It must still work.
        """
        _clear_admin_lockout()
        token = secrets.token_urlsafe(32)
        admin_password = os.environ.get("TEST_ADMIN_PASSWORD", "test-fallback-only")
        from httpx import AsyncClient, ASGITransport
        from app.main import app as _app
        async with AsyncClient(
            transport=ASGITransport(app=_app),
            base_url="http://test",
            follow_redirects=False,
            cookies={"csrf_token": token},
        ) as c:
            resp = await c.post(
                "/login",
                data={
                    "email": "ez@onnix.com.py",
                    "password": admin_password,
                    # No csrf_token in form — header provides the token instead
                },
                headers={"X-CSRFToken": token},
            )
        assert resp.status_code == 303, (
            f"Expected 303 redirect to /dashboard via header token; got {resp.status_code}. "
            f"Response body snippet: {resp.text[:200]}"
        )
        assert resp.headers.get("location") == "/dashboard"
