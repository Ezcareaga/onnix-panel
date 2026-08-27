"""TwilioUsageService — async read-only Twilio Usage Records API client.

Only GETs usage data; never sends messages.  Safe in staging (read-only).
Results are cached in memory for CACHE_TTL_SECONDS to avoid hammering the API
on every dashboard refresh.  Any API failure returns zeros — the dashboard
must never break because of a Twilio outage.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx

from app.schemas.metrics import TwilioUsage

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_QUANTIZE_CENTS = Decimal("0.01")


class TwilioUsageService:
    """Thin async wrapper over the Twilio Usage Records API.

    Caches each subresource window for ``CACHE_TTL_SECONDS`` (5 minutes).
    Returns zeros on missing credentials or any HTTP/network failure.
    Thread-safe: uses an asyncio.Lock for cache reads/writes.
    """

    BASE_URL = "https://api.twilio.com/2010-04-01"
    CACHE_TTL_SECONDS: float = 300.0

    def __init__(
        self,
        account_sid: str | None = None,
        auth_token: str | None = None,
    ) -> None:
        # Use explicit value if provided (even empty string), else fall back to env.
        self._sid = account_sid if account_sid is not None else os.getenv("TWILIO_ACCOUNT_SID", "")
        self._token = auth_token if auth_token is not None else os.getenv("TWILIO_AUTH_TOKEN", "")
        # cache: subresource_key -> (monotonic_time, TwilioUsage)
        self._cache: dict[str, tuple[float, TwilioUsage]] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def today_usd(self) -> TwilioUsage:
        """Fetch usage for the current calendar day (UTC) at Twilio."""
        return await self._fetch("Today")

    async def this_month_usd(self) -> TwilioUsage:
        """Fetch usage since the start of the current UTC month."""
        return await self._fetch("ThisMonth")

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _zeros(self) -> TwilioUsage:
        return TwilioUsage(
            total_usd=0.0,
            whatsapp_usd=0.0,
            other_usd=0.0,
            categories={},
            currency="usd",
        )

    async def _fetch(self, subresource: str) -> TwilioUsage:
        """Return cached result or fetch from Twilio API."""
        # --- cache check ---
        async with self._lock:
            if subresource in self._cache:
                cached_at, val = self._cache[subresource]
                if time.monotonic() - cached_at < self.CACHE_TTL_SECONDS:
                    return val

        # --- no credentials → zeros, no HTTP ---
        if not self._sid or not self._token:
            logger.debug("TwilioUsageService: no credentials, returning zeros")
            return self._zeros()

        url: str | None = (
            f"{self.BASE_URL}/Accounts/{self._sid}/Usage/Records/{subresource}.json"
            "?PageSize=100"
        )
        all_records: list[dict] = []

        async with httpx.AsyncClient(timeout=10.0) as client:
            while url:
                try:
                    resp = await client.get(url, auth=(self._sid, self._token))
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    logger.warning(
                        "TwilioUsageService: HTTP %d for %s — returning zeros",
                        exc.response.status_code,
                        subresource,
                    )
                    return self._zeros()
                except Exception:
                    logger.warning(
                        "TwilioUsageService: request failed for %s — returning zeros",
                        subresource,
                        exc_info=True,
                    )
                    return self._zeros()

                data = resp.json()
                all_records.extend(data.get("usage_records", []))

                next_uri = data.get("next_page_uri")
                url = f"https://api.twilio.com{next_uri}" if next_uri else None

                if len(all_records) > 2000:  # safety guard
                    break

        usage = self._parse_records(all_records)

        async with self._lock:
            self._cache[subresource] = (time.monotonic(), usage)

        return usage

    def _parse_records(self, records: list[dict]) -> TwilioUsage:
        """Aggregate a list of Twilio usage_records into a TwilioUsage schema.

        Twilio returns both rollup categories (e.g. "channels",
        "channels-whatsapp") AND their leaf subcategories
        ("channels-whatsapp-template-marketing"). Summing all non-zero records
        double-counts every dollar. We detect leaves by prefix inspection: a
        category X is a leaf if no other category in the response starts with
        "X-". The special "totalprice" record is the authoritative global
        total and is handled separately rather than treated as a leaf.
        """
        categories_all = [r.get("category", "") for r in records]

        def _is_leaf(cat: str) -> bool:
            if cat == "totalprice":
                return False  # always a rollup — handled separately
            prefix = cat + "-"
            return not any(
                other.startswith(prefix) for other in categories_all if other != cat
            )

        # Aggregate price only from leaf categories.
        total = Decimal("0")
        whatsapp = Decimal("0")
        categories: dict[str, float] = {}
        currency = "usd"

        for r in records:
            if not _is_leaf(r.get("category", "")):
                continue
            raw_price = r.get("price")
            if raw_price is None:
                continue
            try:
                price = Decimal(str(raw_price))
            except Exception:
                continue
            if price == 0:
                continue

            cat = r.get("category", "")
            categories[cat] = round(
                categories.get(cat, 0.0) + float(price), 6
            )
            total += price
            if "whatsapp" in cat.lower():
                whatsapp += price
            currency = r.get("price_unit", "usd") or "usd"

        # Authoritative total: prefer the explicit "totalprice" record if
        # present and non-zero — it is ground truth from Twilio's own rollup.
        totalprice_record = next(
            (r for r in records if r.get("category") == "totalprice"), None
        )
        if totalprice_record:
            try:
                totalprice_val = Decimal(str(totalprice_record.get("price") or "0"))
                if totalprice_val > 0:
                    total = totalprice_val
            except Exception:
                pass

        other = total - whatsapp
        return TwilioUsage(
            total_usd=float(total.quantize(_QUANTIZE_CENTS)),
            whatsapp_usd=float(whatsapp.quantize(_QUANTIZE_CENTS)),
            other_usd=float(other.quantize(_QUANTIZE_CENTS)),
            categories=categories,
            currency=currency,
        )
