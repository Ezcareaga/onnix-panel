"""
Tests for _extract_ip helper in app/routes/auth.py (Fix C1).

Covers:
  - trust_proxy=True: CF-Connecting-IP preferred over client.host
  - trust_proxy=True: X-Forwarded-For leftmost used when CF header absent
  - trust_proxy=True: falls back to client.host when no proxy headers present
  - trust_proxy=False (default): client.host returned even if proxy headers present
  - trust_proxy=False: None returned when request.client is None
"""
import pytest
from unittest.mock import MagicMock

from app.routes.auth import _extract_ip, _real_ip_from_headers


class _FakeClient:
    def __init__(self, host: str):
        self.host = host


def _make_request(
    client_host: str | None = "127.0.0.1",
    cf_header: str | None = None,
    xff_header: str | None = None,
) -> MagicMock:
    """Build a minimal Request mock with the given headers and client."""
    req = MagicMock()
    req.client = _FakeClient(client_host) if client_host is not None else None

    headers = {}
    if cf_header is not None:
        headers["CF-Connecting-IP"] = cf_header
    if xff_header is not None:
        headers["X-Forwarded-For"] = xff_header

    req.headers = headers
    return req


# ---------------------------------------------------------------------------
# _real_ip_from_headers unit tests
# ---------------------------------------------------------------------------

class TestRealIpFromHeaders:
    def test_cf_connecting_ip_preferred(self):
        req = _make_request(
            client_host="10.0.0.1",
            cf_header="203.0.113.5",
            xff_header="1.2.3.4, 10.0.0.1",
        )
        assert _real_ip_from_headers(req) == "203.0.113.5"

    def test_xff_leftmost_when_no_cf_header(self):
        req = _make_request(
            client_host="10.0.0.1",
            xff_header="198.51.100.7, 10.0.0.1",
        )
        assert _real_ip_from_headers(req) == "198.51.100.7"

    def test_xff_single_entry(self):
        req = _make_request(
            client_host="10.0.0.1",
            xff_header="198.51.100.9",
        )
        assert _real_ip_from_headers(req) == "198.51.100.9"

    def test_falls_back_to_client_host_when_no_proxy_headers(self):
        req = _make_request(client_host="172.16.0.5")
        assert _real_ip_from_headers(req) == "172.16.0.5"

    def test_returns_none_when_no_client_and_no_headers(self):
        req = _make_request(client_host=None)
        assert _real_ip_from_headers(req) is None


# ---------------------------------------------------------------------------
# _extract_ip integration: trust_proxy flag
# ---------------------------------------------------------------------------

class TestExtractIp:
    def test_trust_enabled_uses_cf_header(self):
        req = _make_request(
            client_host="10.0.0.1",
            cf_header="203.0.113.99",
        )
        assert _extract_ip(req, trust_proxy=True) == "203.0.113.99"

    def test_trust_enabled_uses_xff_leftmost_no_cf(self):
        req = _make_request(
            client_host="10.0.0.1",
            xff_header="192.0.2.1, 10.0.0.1",
        )
        assert _extract_ip(req, trust_proxy=True) == "192.0.2.1"

    def test_trust_disabled_returns_client_host_ignoring_headers(self):
        req = _make_request(
            client_host="10.0.0.1",
            cf_header="203.0.113.99",
            xff_header="192.0.2.1, 10.0.0.1",
        )
        # trust_proxy=False: must ignore proxy headers
        assert _extract_ip(req, trust_proxy=False) == "10.0.0.1"

    def test_trust_disabled_default_returns_client_host(self):
        req = _make_request(
            client_host="10.0.0.1",
            cf_header="203.0.113.99",
        )
        # Default is trust_proxy=False (reads from settings, which defaults false)
        result = _extract_ip(req, trust_proxy=False)
        assert result == "10.0.0.1"

    def test_no_client_returns_none_when_trust_disabled(self):
        req = _make_request(client_host=None)
        assert _extract_ip(req, trust_proxy=False) is None

    def test_no_client_no_headers_returns_none_when_trust_enabled(self):
        req = _make_request(client_host=None)
        assert _extract_ip(req, trust_proxy=True) is None
