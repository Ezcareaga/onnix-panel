"""Tests for app/services/twilio_usage_service.py

All HTTP calls are intercepted via httpx.MockTransport — no real Twilio requests.
"""
from __future__ import annotations

import json
import time
from unittest.mock import patch

import httpx
import pytest

from app.schemas.metrics import TwilioUsage
from app.services.twilio_usage_service import TwilioUsageService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_transport(records: list[dict], status_code: int = 200) -> httpx.MockTransport:
    """Build an httpx.MockTransport that always returns the given records."""
    body = json.dumps({"usage_records": records}).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, content=body)

    return httpx.MockTransport(handler)


def _sample_records() -> list[dict]:
    """Three usage records: 1 whatsapp (legacy prefix), 2 messaging."""
    return [
        {"category": "whatsapp-messages-inbound", "price": "0.50", "price_unit": "usd"},
        {"category": "sms-outbound", "price": "0.10", "price_unit": "usd"},
        {"category": "sms-inbound", "price": "0.05", "price_unit": "usd"},
    ]


def _channels_whatsapp_records() -> list[dict]:
    """Three records exercising Conversations API category naming + legacy + SMS."""
    return [
        {"category": "channels-whatsapp-template-marketing", "price": "3.92", "price_unit": "usd"},
        {"category": "whatsapp-outbound-service-conversation-br", "price": "0.50", "price_unit": "usd"},
        {"category": "messaging-outbound-sms", "price": "0.10", "price_unit": "usd"},
    ]


# ---------------------------------------------------------------------------
# No credentials → zeros, no HTTP call
# ---------------------------------------------------------------------------

class TestTwilioUsageNoCredentials:

    @pytest.mark.asyncio
    async def test_empty_credentials_returns_zeros(self):
        """Empty SID + token → TwilioUsage with all zeros, no HTTP call."""
        svc = TwilioUsageService(account_sid="", auth_token="")
        usage = await svc.today_usd()
        assert isinstance(usage, TwilioUsage)
        assert usage.total_usd == 0.0
        assert usage.whatsapp_usd == 0.0
        assert usage.other_usd == 0.0

    @pytest.mark.asyncio
    async def test_no_http_call_when_no_credentials(self):
        """No httpx.AsyncClient is created when credentials are missing."""
        svc = TwilioUsageService(account_sid="", auth_token="")
        with patch("app.services.twilio_usage_service.httpx.AsyncClient") as mock_cls:
            await svc.today_usd()
        mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Correct aggregation with mock HTTP response
# ---------------------------------------------------------------------------

class TestTwilioUsageAggregation:

    @pytest.mark.asyncio
    async def test_aggregates_three_records_correctly(self):
        """1 whatsapp + 2 messaging → correct total, whatsapp, other splits."""
        transport = _make_transport(_sample_records())
        svc = TwilioUsageService(account_sid="ACfake", auth_token="tokfake")

        with patch("app.services.twilio_usage_service.httpx.AsyncClient") as mock_cls:
            mock_client = mock_cls.return_value.__aenter__.return_value
            mock_response = httpx.Response(
                200,
                content=json.dumps({"usage_records": _sample_records()}).encode(),
            )
            mock_response.request = httpx.Request("GET", "https://example.com")
            mock_client.get.return_value = mock_response

            usage = await svc.today_usd()

        # total = 0.50 + 0.10 + 0.05 = 0.65
        assert usage.total_usd == pytest.approx(0.65, abs=0.01)
        # whatsapp = 0.50
        assert usage.whatsapp_usd == pytest.approx(0.50, abs=0.01)
        # other = 0.15
        assert usage.other_usd == pytest.approx(0.15, abs=0.01)
        assert usage.currency == "usd"

    @pytest.mark.asyncio
    async def test_categories_dict_populated(self):
        """categories dict maps each category to its price sum."""
        svc = TwilioUsageService(account_sid="ACfake", auth_token="tokfake")

        with patch("app.services.twilio_usage_service.httpx.AsyncClient") as mock_cls:
            mock_client = mock_cls.return_value.__aenter__.return_value
            mock_response = httpx.Response(
                200,
                content=json.dumps({"usage_records": _sample_records()}).encode(),
            )
            mock_response.request = httpx.Request("GET", "https://example.com")
            mock_client.get.return_value = mock_response

            usage = await svc.today_usd()

        assert "whatsapp-messages-inbound" in usage.categories
        assert "sms-outbound" in usage.categories


# ---------------------------------------------------------------------------
# Substring category matching — channels-whatsapp-* vs whatsapp-* prefixes
# ---------------------------------------------------------------------------

class TestTwilioUsageCategorySubstringMatch:

    @pytest.mark.asyncio
    async def test_channels_whatsapp_categories_counted_as_whatsapp(self):
        """channels-whatsapp-* + whatsapp-* both count as whatsapp; SMS goes to other."""
        svc = TwilioUsageService(account_sid="ACfake", auth_token="tokfake")

        with patch("app.services.twilio_usage_service.httpx.AsyncClient") as mock_cls:
            mock_client = mock_cls.return_value.__aenter__.return_value
            mock_response = httpx.Response(
                200,
                content=json.dumps({"usage_records": _channels_whatsapp_records()}).encode(),
            )
            mock_response.request = httpx.Request("GET", "https://example.com")
            mock_client.get.return_value = mock_response

            usage = await svc.today_usd()

        # whatsapp = 3.92 + 0.50 = 4.42
        assert usage.whatsapp_usd == pytest.approx(4.42, abs=0.01)
        # other = 0.10 (SMS only)
        assert usage.other_usd == pytest.approx(0.10, abs=0.01)
        # total = 4.52
        assert usage.total_usd == pytest.approx(4.52, abs=0.01)


# ---------------------------------------------------------------------------
# HTTP 401 → zeros, doesn't raise
# ---------------------------------------------------------------------------

class TestTwilioUsageHttpErrors:

    @pytest.mark.asyncio
    async def test_http_401_returns_zeros(self):
        """HTTP 401 raises_for_status → caught → zeros returned."""
        svc = TwilioUsageService(account_sid="ACfake", auth_token="wrong")

        with patch("app.services.twilio_usage_service.httpx.AsyncClient") as mock_cls:
            mock_client = mock_cls.return_value.__aenter__.return_value
            error_response = httpx.Response(401, content=b'{"error":"Unauthorized"}')
            error_response.request = httpx.Request(
                "GET",
                "https://api.twilio.com/2010-04-01/Accounts/ACfake/Usage/Records/Today.json",
            )
            mock_client.get.return_value = error_response

            usage = await svc.today_usd()

        assert usage.total_usd == 0.0
        assert usage.whatsapp_usd == 0.0

    @pytest.mark.asyncio
    async def test_network_exception_returns_zeros(self):
        """Any network error → zeros returned, no exception propagated."""
        svc = TwilioUsageService(account_sid="ACfake", auth_token="tokfake")

        with patch("app.services.twilio_usage_service.httpx.AsyncClient") as mock_cls:
            mock_client = mock_cls.return_value.__aenter__.return_value
            mock_client.get.side_effect = httpx.ConnectError("unreachable")

            usage = await svc.today_usd()

        assert usage.total_usd == 0.0


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------

class TestTwilioUsageCache:

    @pytest.mark.asyncio
    async def test_two_calls_within_ttl_makes_one_http_request(self):
        """Second call within TTL returns cached result — only 1 HTTP GET."""
        svc = TwilioUsageService(account_sid="ACfake", auth_token="tokfake")
        call_count = 0

        with patch("app.services.twilio_usage_service.httpx.AsyncClient") as mock_cls:
            mock_client = mock_cls.return_value.__aenter__.return_value

            def _get_side_effect(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                response = httpx.Response(
                    200,
                    content=json.dumps({"usage_records": _sample_records()}).encode(),
                )
                response.request = httpx.Request("GET", args[0] if args else "https://example.com")
                return response

            mock_client.get.side_effect = _get_side_effect

            await svc.today_usd()
            await svc.today_usd()

        assert call_count == 1, f"Expected 1 HTTP call, got {call_count}"

    @pytest.mark.asyncio
    async def test_call_after_ttl_makes_new_http_request(self, monkeypatch):
        """After TTL expires, next call should fetch fresh data."""
        svc = TwilioUsageService(account_sid="ACfake", auth_token="tokfake")
        # Seed cache with an already-expired entry
        past_time = time.monotonic() - svc.CACHE_TTL_SECONDS - 1.0
        expired_usage = TwilioUsage(total_usd=0.0, whatsapp_usd=0.0, other_usd=0.0)
        svc._cache["Today"] = (past_time, expired_usage)

        with patch("app.services.twilio_usage_service.httpx.AsyncClient") as mock_cls:
            mock_client = mock_cls.return_value.__aenter__.return_value
            fresh_response = httpx.Response(
                200,
                content=json.dumps({"usage_records": _sample_records()}).encode(),
            )
            fresh_response.request = httpx.Request("GET", "https://example.com")
            mock_client.get.return_value = fresh_response

            usage = await svc.today_usd()

        mock_client.get.assert_called_once()
        assert usage.total_usd == pytest.approx(0.65, abs=0.01)


# ---------------------------------------------------------------------------
# Malformed price values
# ---------------------------------------------------------------------------

class TestTwilioUsageMalformedPrice:

    @pytest.mark.asyncio
    async def test_null_price_treated_as_zero(self):
        """Records with price=null are skipped (treated as 0)."""
        records = [
            {"category": "whatsapp-messages-inbound", "price": None, "price_unit": "usd"},
            {"category": "sms-outbound", "price": "0.10", "price_unit": "usd"},
        ]
        svc = TwilioUsageService(account_sid="ACfake", auth_token="tokfake")

        with patch("app.services.twilio_usage_service.httpx.AsyncClient") as mock_cls:
            mock_client = mock_cls.return_value.__aenter__.return_value
            mock_response = httpx.Response(
                200,
                content=json.dumps({"usage_records": records}).encode(),
            )
            mock_response.request = httpx.Request("GET", "https://example.com")
            mock_client.get.return_value = mock_response

            usage = await svc.today_usd()

        # Only the $0.10 sms-outbound record counted
        assert usage.total_usd == pytest.approx(0.10, abs=0.01)

    @pytest.mark.asyncio
    async def test_zero_price_skipped(self):
        """Records with price=0 (or '0') are not included in totals."""
        records = [
            {"category": "sms-outbound", "price": "0", "price_unit": "usd"},
            {"category": "whatsapp-messages-outbound", "price": "0.20", "price_unit": "usd"},
        ]
        svc = TwilioUsageService(account_sid="ACfake", auth_token="tokfake")

        with patch("app.services.twilio_usage_service.httpx.AsyncClient") as mock_cls:
            mock_client = mock_cls.return_value.__aenter__.return_value
            mock_response = httpx.Response(
                200,
                content=json.dumps({"usage_records": records}).encode(),
            )
            mock_response.request = httpx.Request("GET", "https://example.com")
            mock_client.get.return_value = mock_response

            usage = await svc.today_usd()

        assert usage.total_usd == pytest.approx(0.20, abs=0.01)


# ---------------------------------------------------------------------------
# Pagination — next_page_uri handling
# ---------------------------------------------------------------------------

class TestTwilioUsagePagination:

    @pytest.mark.asyncio
    async def test_pagination_follows_next_page_uri(self):
        """Two pages: page1 has next_page_uri, page2 has null. All 5 records aggregated."""
        page1_records = [
            {"category": "whatsapp-messages-outbound", "price": "1.00", "price_unit": "usd"},
            {"category": "whatsapp-messages-inbound", "price": "0.50", "price_unit": "usd"},
            {"category": "sms-outbound", "price": "0.10", "price_unit": "usd"},
        ]
        page2_records = [
            {"category": "sms-inbound", "price": "0.05", "price_unit": "usd"},
            {"category": "channels-whatsapp-template-marketing", "price": "3.92", "price_unit": "usd"},
        ]
        next_uri = "/2010-04-01/Accounts/ACfake/Usage/Records/ThisMonth.json?Page=1"

        page1_body = json.dumps({"usage_records": page1_records, "next_page_uri": next_uri}).encode()
        page2_body = json.dumps({"usage_records": page2_records, "next_page_uri": None}).encode()

        svc = TwilioUsageService(account_sid="ACfake", auth_token="tokfake")
        call_count = 0

        with patch("app.services.twilio_usage_service.httpx.AsyncClient") as mock_cls:
            mock_client = mock_cls.return_value.__aenter__.return_value

            def _get(url, **kwargs):
                nonlocal call_count
                call_count += 1
                body = page1_body if call_count == 1 else page2_body
                r = httpx.Response(200, content=body)
                r.request = httpx.Request("GET", url)
                return r

            mock_client.get.side_effect = _get

            usage = await svc.this_month_usd()

        assert call_count == 2
        # total = 1.00 + 0.50 + 0.10 + 0.05 + 3.92 = 5.57
        assert usage.total_usd == pytest.approx(5.57, abs=0.01)
        # whatsapp = 1.00 + 0.50 + 3.92 = 5.42
        assert usage.whatsapp_usd == pytest.approx(5.42, abs=0.01)
        # other = 0.10 + 0.05 = 0.15
        assert usage.other_usd == pytest.approx(0.15, abs=0.01)

    @pytest.mark.asyncio
    async def test_pagination_stops_on_null_next_uri(self):
        """Single page with next_page_uri=null makes exactly one HTTP call."""
        body = json.dumps({"usage_records": _sample_records(), "next_page_uri": None}).encode()
        svc = TwilioUsageService(account_sid="ACfake", auth_token="tokfake")

        with patch("app.services.twilio_usage_service.httpx.AsyncClient") as mock_cls:
            mock_client = mock_cls.return_value.__aenter__.return_value

            def _get(url, **kwargs):
                r = httpx.Response(200, content=body)
                r.request = httpx.Request("GET", url)
                return r

            mock_client.get.side_effect = _get

            await svc.this_month_usd()

        assert mock_client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_pagination_safety_guard_caps_at_2000(self):
        """Safety guard stops accumulation after 2000 records even if next_page_uri continues."""
        # Each page has 100 records; guard should stop before fetching indefinitely
        page_records = [
            {"category": "sms-outbound", "price": "0.01", "price_unit": "usd"}
        ] * 100
        always_has_next = json.dumps({
            "usage_records": page_records,
            "next_page_uri": "/2010-04-01/Accounts/ACfake/Usage/Records/ThisMonth.json?Page=1",
        }).encode()

        svc = TwilioUsageService(account_sid="ACfake", auth_token="tokfake")
        call_count = 0

        with patch("app.services.twilio_usage_service.httpx.AsyncClient") as mock_cls:
            mock_client = mock_cls.return_value.__aenter__.return_value

            def _get(url, **kwargs):
                nonlocal call_count
                call_count += 1
                r = httpx.Response(200, content=always_has_next)
                r.request = httpx.Request("GET", url)
                return r

            mock_client.get.side_effect = _get

            usage = await svc.this_month_usd()

        # 2000 records / 100 per page = 20 pages max; guard fires after page 20
        assert call_count <= 21  # at most 21 fetches before guard breaks the loop
        assert usage.total_usd > 0.0


# ---------------------------------------------------------------------------
# Rollup exclusion — the core fix for 4x-inflated totals
# ---------------------------------------------------------------------------

def _rollup_records() -> list[dict]:
    """Real-world response structure: rollups + leaves + totalprice."""
    return [
        {"category": "channels",                             "price": "7.46", "price_unit": "usd"},
        {"category": "totalprice",                           "price": "7.46", "price_unit": "usd"},
        {"category": "channels-whatsapp",                   "price": "3.92", "price_unit": "usd"},
        {"category": "channels-whatsapp-template-marketing","price": "3.92", "price_unit": "usd"},
        {"category": "channels-messaging",                  "price": "3.54", "price_unit": "usd"},
        {"category": "channels-messaging-outbound",         "price": "2.63", "price_unit": "usd"},
        {"category": "channels-messaging-inbound",          "price": "0.90", "price_unit": "usd"},
        {"category": "failed-message-processing-fee",       "price": "0.01", "price_unit": "usd"},
    ]


def _make_mock_client(mock_cls, records: list[dict]):
    """Wire up an AsyncClient mock to return the given records once."""
    mock_client = mock_cls.return_value.__aenter__.return_value
    mock_response = httpx.Response(
        200,
        content=json.dumps({"usage_records": records}).encode(),
    )
    mock_response.request = httpx.Request("GET", "https://example.com")
    mock_client.get.return_value = mock_response
    return mock_client


class TestRollupExclusion:

    @pytest.mark.asyncio
    async def test_rollup_categories_excluded_from_sum(self):
        """Rollups ('channels', 'channels-whatsapp', 'channels-messaging') must not
        be summed — only leaves contribute; totalprice overrides the leaf sum."""
        svc = TwilioUsageService(account_sid="ACfake", auth_token="tokfake")

        with patch("app.services.twilio_usage_service.httpx.AsyncClient") as mock_cls:
            _make_mock_client(mock_cls, _rollup_records())
            usage = await svc.today_usd()

        # totalprice = 7.46 (authoritative override)
        assert usage.total_usd == pytest.approx(7.46, abs=0.01)
        # whatsapp leaf only: channels-whatsapp-template-marketing = 3.92
        assert usage.whatsapp_usd == pytest.approx(3.92, abs=0.01)
        # other = 2.63 + 0.90 + 0.01 = 3.54
        assert usage.other_usd == pytest.approx(3.54, abs=0.01)
        # Rollup categories must NOT appear in the categories dict
        assert "channels" not in usage.categories
        assert "channels-whatsapp" not in usage.categories
        assert "channels-messaging" not in usage.categories
        assert "totalprice" not in usage.categories
        # Leaves must appear
        assert "channels-whatsapp-template-marketing" in usage.categories
        assert "channels-messaging-outbound" in usage.categories

    @pytest.mark.asyncio
    async def test_totalprice_takes_precedence_over_leaf_sum(self):
        """If 'totalprice' record is present and > 0, it overrides the leaves sum."""
        records = [
            {"category": "channels-messaging-outbound", "price": "3.00", "price_unit": "usd"},
            {"category": "channels-messaging-inbound",  "price": "2.00", "price_unit": "usd"},
            # totalprice deliberately differs from leaf sum to test the override
            {"category": "totalprice",                  "price": "7.46", "price_unit": "usd"},
        ]
        svc = TwilioUsageService(account_sid="ACfake", auth_token="tokfake")

        with patch("app.services.twilio_usage_service.httpx.AsyncClient") as mock_cls:
            _make_mock_client(mock_cls, records)
            usage = await svc.today_usd()

        # Leaf sum would be 5.00, but totalprice wins
        assert usage.total_usd == pytest.approx(7.46, abs=0.01)
        # whatsapp stays at 0 (no whatsapp leaves)
        assert usage.whatsapp_usd == pytest.approx(0.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_leaf_detection_single_category(self):
        """A single category with no siblings is always a leaf."""
        records = [
            {
                "category": "whatsapp-outbound-service-conversation-br",
                "price": "2.50",
                "price_unit": "usd",
            }
        ]
        svc = TwilioUsageService(account_sid="ACfake", auth_token="tokfake")

        with patch("app.services.twilio_usage_service.httpx.AsyncClient") as mock_cls:
            _make_mock_client(mock_cls, records)
            usage = await svc.today_usd()

        assert usage.total_usd == pytest.approx(2.50, abs=0.01)
        assert usage.whatsapp_usd == pytest.approx(2.50, abs=0.01)
        assert "whatsapp-outbound-service-conversation-br" in usage.categories
