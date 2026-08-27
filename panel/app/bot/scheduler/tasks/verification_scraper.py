"""Midday verification scraper — re-checks active property portal URLs.

For each active property, fetches the portal URL and updates is_active / on_hold.
- 5xx / timeout / network error → error (NEVER mark_inactive — FIX 1 lesson).
- Feature flag: scheduler_verification_scraper_enabled in bot_settings.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Literal

import httpx
from sqlalchemy import select, text

from app.bot.scheduler.settings_manager import SettingsManager
from app.database import async_session_factory
from app.models.property import Property

logger = logging.getLogger(__name__)

_PSIR_SALE_RE = re.compile(r"(En Venta|En Alquiler)", re.IGNORECASE)
_PSIR_PRICE_RE = re.compile(r"(USD\s|Gs\.\s|₲)", re.IGNORECASE)

_SOURCES = ("remax", "psir", "coldwell", "onnixpy")

# Browser-like headers — Coldwell's AWS WAF/ELB returns 403 for 'python-httpx/*'
# and stops responding at TCP level under load (SYN-SENT hangs).
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-PY,es;q=0.9,en;q=0.8",
}
# Tighter connect timeout (5s) prevents WAF-induced SYN-SENT hangs from stalling
# the full verification batch (356 Coldwell props × 10 concurrent = hours otherwise).
_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=10.0)

# Defensive async timeouts — guards against tarpit-style WAFs (e.g. Coldwell) that
# accept TCP but never send an HTTP response body (httpx read-timeout won't fire on 0 bytes).
_PER_REQUEST_TIMEOUT_S: float = 20.0   # asyncio.wait_for around each _<portal>_check_one
_PER_PORTAL_CAP_S: float = 300.0       # asyncio.wait_for around gather/loop per portal


@dataclass
class VerificationResult:
    property_id: int
    action: Literal["mark_inactive", "set_on_hold", "clear_on_hold", "no_change", "error"]
    error: str | None = None


def _empty_source_stats() -> dict:
    return {"checked": 0, "marked_inactive": 0, "set_on_hold": 0, "cleared_on_hold": 0, "errors": 0}


class VerificationScraper:
    """Re-checks active property portal URLs and updates DB accordingly.

    Parameters
    ----------
    session_factory:
        Async session factory override (for testing).
    http_client_factory:
        Callable returning an httpx.AsyncClient async context manager (for testing).
    settings_manager:
        SettingsManager override (for testing).
    max_concurrent:
        Semaphore limit for concurrent HTTP requests (PSIR/Onnixpy).
    coldwell_concurrent:
        Lower limit for Coldwell — its WAF tarpits at high concurrency.
    remax_batch_size:
        Number of MLSIDs per Remax Azure search batch.
    """

    def __init__(
        self,
        session_factory=None,
        http_client_factory: Callable | None = None,
        settings_manager: SettingsManager | None = None,
        max_concurrent: int = 10,
        coldwell_concurrent: int = 2,
        remax_batch_size: int = 100,
    ) -> None:
        self._session_factory = session_factory or async_session_factory
        self._http_client_factory = http_client_factory or (
            lambda: httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT,
                follow_redirects=True,
                headers=_BROWSER_HEADERS,
            )
        )
        self._settings_manager = settings_manager or SettingsManager()
        self._max_concurrent = max_concurrent
        self._coldwell_concurrent = coldwell_concurrent
        self._remax_batch_size = remax_batch_size

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> dict:
        """Execute the verification scraper.

        Returns metrics dict. Early-returns ``{"skipped": True}`` when disabled.
        """
        if not await self._settings_manager.is_task_enabled("verification_scraper"):
            logger.info("Task verification_scraper disabled — skipping")
            return {"skipped": True}

        start = time.monotonic()

        async with self._session_factory() as session:
            props_by_source = await self._load_active_properties(session)

        _dispatch: dict[str, Callable] = {
            "remax": self._verify_remax,
            "psir": self._verify_psir,
            "coldwell": self._verify_coldwell,
            "onnixpy": self._verify_onnixpy,
        }
        portal_tasks = [
            (src, _dispatch[src](props))
            for src, props in props_by_source.items()
            if props and src in _dispatch
        ]
        outcomes = await asyncio.gather(
            *(coro for _, coro in portal_tasks), return_exceptions=True
        )

        all_results: list[VerificationResult] = []
        for (src, _), outcome in zip(portal_tasks, outcomes):
            if isinstance(outcome, Exception):
                logger.error("Portal %s raised unexpectedly: %s", src, outcome)
                all_results.extend(
                    VerificationResult(property_id=p["id"], action="error", error=str(outcome))
                    for p in props_by_source[src]
                )
            else:
                all_results.extend(outcome)

        async with self._session_factory() as session:
            stats = await self._apply_changes(session, all_results, props_by_source)
            await session.commit()

        elapsed_ms = (time.monotonic() - start) * 1000
        total_checked = sum(s["checked"] for s in stats["by_source"].values())
        logger.info(
            "Job executed — %s",
            json.dumps({
                "task": "verification_scraper",
                "duration_ms": int(elapsed_ms),
                "checked_total": total_checked,
                "by_source": stats["by_source"],
            }),
        )
        return {
            "checked_total": total_checked,
            "by_source": stats["by_source"],
        }

    # ------------------------------------------------------------------
    # Private: load
    # ------------------------------------------------------------------

    async def _load_active_properties(self, session) -> dict[str, list[dict]]:
        """Return active properties grouped by source."""
        stmt = select(
            Property.id,
            Property.source,
            Property.external_id,
            Property.url,
            Property.on_hold,
        ).where(Property.is_active == True)  # noqa: E712

        result = await session.execute(stmt)
        rows = result.fetchall()

        grouped: dict[str, list[dict]] = {src: [] for src in _SOURCES}
        for row in rows:
            src = row.source
            if src in grouped:
                grouped[src].append({
                    "id": row.id,
                    "external_id": row.external_id,
                    "url": row.url,
                    "on_hold": row.on_hold,
                })
        return grouped

    # ------------------------------------------------------------------
    # Private: verify — Remax
    # ------------------------------------------------------------------

    async def _verify_remax(self, props: list[dict]) -> list[VerificationResult]:
        results: list[VerificationResult] = []

        # Chunk into batches
        batches = [
            props[i: i + self._remax_batch_size]
            for i in range(0, len(props), self._remax_batch_size)
        ]

        try:
            async with asyncio.timeout(_PER_PORTAL_CAP_S):
                async with self._http_client_factory() as client:
                    for batch in batches:
                        batch_results = await self._remax_check_batch(client, batch)
                        results.extend(batch_results)
                        if len(batches) > 1:
                            await asyncio.sleep(0.1)
        except asyncio.TimeoutError:
            logger.warning("Portal remax exceeded %.0fs cap — partial results", _PER_PORTAL_CAP_S)
            # Determine which props haven't been processed yet
            processed_ids = {r.property_id for r in results}
            for p in props:
                if p["id"] not in processed_ids:
                    results.append(VerificationResult(property_id=p["id"], action="error", error="timeout"))

        return results

    async def _remax_check_batch(
        self, client: httpx.AsyncClient, batch: list[dict]
    ) -> list[VerificationResult]:
        mlsid_to_prop = {p["external_id"]: p for p in batch}
        mlsid_list = ",".join(mlsid_to_prop.keys())

        payload = {
            "search": "*",
            "top": self._remax_batch_size,
            "filter": (
                f"content/ListingCountryCode eq 'PY' and "
                f"search.in(content/MLSID, '{mlsid_list}', ',')"
            ),
            "select": "content/MLSID,content/IsFindable,content/OnHoldListing",
        }

        try:
            resp = await client.post(
                "https://www.remax.com.py/search/listing-search/docs/search",
                json=payload,
                timeout=20.0,
            )
        except Exception as exc:
            return [
                VerificationResult(property_id=p["id"], action="error", error=str(exc))
                for p in batch
            ]

        if resp.status_code >= 400:
            return [
                VerificationResult(property_id=p["id"], action="error", error=f"HTTP {resp.status_code}")
                for p in batch
            ]

        try:
            data = resp.json()
        except Exception as exc:
            return [
                VerificationResult(property_id=p["id"], action="error", error=f"JSON parse: {exc}")
                for p in batch
            ]

        found: dict[str, dict] = {}
        for item in data.get("value", []):
            content = item.get("content", item)
            mid = content.get("MLSID") or item.get("MLSID")
            if mid:
                found[mid] = content

        results: list[VerificationResult] = []
        for mlsid, prop in mlsid_to_prop.items():
            if mlsid not in found:
                results.append(VerificationResult(property_id=prop["id"], action="mark_inactive"))
                continue

            entry = found[mlsid]
            is_findable = entry.get("IsFindable", True)
            on_hold_listing = entry.get("OnHoldListing", False)

            if is_findable:
                action = "clear_on_hold" if prop["on_hold"] else "no_change"
            elif on_hold_listing:
                action = "set_on_hold" if not prop["on_hold"] else "no_change"
            else:
                action = "mark_inactive"

            results.append(VerificationResult(property_id=prop["id"], action=action))

        return results

    # ------------------------------------------------------------------
    # Private: helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _partial_results_from_tasks(
        tasks: list[asyncio.Task],
        props: list[dict],
    ) -> list[VerificationResult]:
        """After a portal cap TimeoutError, collect completed task results and
        add ``action='error'`` placeholders for tasks that did not finish.

        When ``asyncio.wait_for`` cancels the inner gather, the individual Task
        objects become cancelled/not-done. We walk both lists together by index
        to pair each task with its prop.
        """
        results: list[VerificationResult] = []
        for task, prop in zip(tasks, props):
            if task.done() and not task.cancelled() and task.exception() is None:
                results.append(task.result())
            else:
                results.append(
                    VerificationResult(property_id=prop["id"], action="error", error="timeout")
                )
        return results

    # ------------------------------------------------------------------
    # Private: verify — PSIR
    # ------------------------------------------------------------------

    async def _verify_psir(self, props: list[dict]) -> list[VerificationResult]:
        sem = asyncio.Semaphore(self._max_concurrent)
        async with self._http_client_factory() as client:
            tasks = [asyncio.create_task(self._psir_check_one(client, sem, p)) for p in props]
            try:
                return list(await asyncio.wait_for(
                    asyncio.gather(*tasks), timeout=_PER_PORTAL_CAP_S
                ))
            except asyncio.TimeoutError:
                logger.warning("Portal psir exceeded %.0fs cap — partial results", _PER_PORTAL_CAP_S)
                # Gather was cancelled; collect done tasks and mark rest as errors
                return self._partial_results_from_tasks(tasks, props)

    async def _psir_check_one(
        self, client: httpx.AsyncClient, sem: asyncio.Semaphore, prop: dict
    ) -> VerificationResult:
        pid = prop["id"]
        url = prop["url"] or ""
        try:
            async with sem:
                resp = await asyncio.wait_for(
                    client.get(url, timeout=15.0), timeout=_PER_REQUEST_TIMEOUT_S
                )
        except asyncio.TimeoutError:
            return VerificationResult(property_id=pid, action="error", error="timeout")
        except Exception as exc:
            return VerificationResult(property_id=pid, action="error", error=str(exc))

        if resp.status_code == 404:
            return VerificationResult(property_id=pid, action="mark_inactive")
        if resp.status_code >= 500:
            return VerificationResult(property_id=pid, action="error", error=f"HTTP {resp.status_code}")
        if resp.status_code == 200:
            body = resp.text
            has_sale = bool(_PSIR_SALE_RE.search(body))
            has_price = bool(_PSIR_PRICE_RE.search(body))
            if has_sale and has_price:
                return VerificationResult(property_id=pid, action="no_change")
            return VerificationResult(property_id=pid, action="mark_inactive")
        # Other non-200/404/5xx (e.g. 301 without follow — but we follow): treat as error
        return VerificationResult(property_id=pid, action="error", error=f"HTTP {resp.status_code}")

    # ------------------------------------------------------------------
    # Private: verify — Coldwell
    # ------------------------------------------------------------------

    async def _verify_coldwell(self, props: list[dict]) -> list[VerificationResult]:
        sem = asyncio.Semaphore(self._coldwell_concurrent)
        async with self._http_client_factory() as client:
            tasks = [asyncio.create_task(self._coldwell_check_one(client, sem, p)) for p in props]
            try:
                return list(await asyncio.wait_for(
                    asyncio.gather(*tasks), timeout=_PER_PORTAL_CAP_S
                ))
            except asyncio.TimeoutError:
                logger.warning("Portal coldwell exceeded %.0fs cap — partial results", _PER_PORTAL_CAP_S)
                return self._partial_results_from_tasks(tasks, props)

    async def _coldwell_check_one(
        self, client: httpx.AsyncClient, sem: asyncio.Semaphore, prop: dict
    ) -> VerificationResult:
        pid = prop["id"]
        url = prop["url"] or ""
        try:
            async with sem:
                resp = await asyncio.wait_for(
                    client.get(url, timeout=15.0), timeout=_PER_REQUEST_TIMEOUT_S
                )
        except asyncio.TimeoutError:
            return VerificationResult(property_id=pid, action="error", error="timeout")
        except Exception as exc:
            return VerificationResult(property_id=pid, action="error", error=str(exc))

        if resp.status_code in (404, 410):
            return VerificationResult(property_id=pid, action="mark_inactive")
        if resp.status_code >= 500:
            return VerificationResult(property_id=pid, action="error", error=f"HTTP {resp.status_code}")
        if resp.status_code == 200:
            if "publicación ha finalizado" in resp.text.lower():
                return VerificationResult(property_id=pid, action="mark_inactive")
            return VerificationResult(property_id=pid, action="no_change")
        return VerificationResult(property_id=pid, action="error", error=f"HTTP {resp.status_code}")

    # ------------------------------------------------------------------
    # Private: verify — Onnixpy
    # ------------------------------------------------------------------

    async def _verify_onnixpy(self, props: list[dict]) -> list[VerificationResult]:
        sem = asyncio.Semaphore(self._max_concurrent)
        async with self._http_client_factory() as client:
            tasks = [asyncio.create_task(self._onnixpy_check_one(client, sem, p)) for p in props]
            try:
                return list(await asyncio.wait_for(
                    asyncio.gather(*tasks), timeout=_PER_PORTAL_CAP_S
                ))
            except asyncio.TimeoutError:
                logger.warning("Portal onnixpy exceeded %.0fs cap — partial results", _PER_PORTAL_CAP_S)
                return self._partial_results_from_tasks(tasks, props)

    async def _onnixpy_check_one(
        self, client: httpx.AsyncClient, sem: asyncio.Semaphore, prop: dict
    ) -> VerificationResult:
        pid = prop["id"]
        url = prop["url"] or ""
        try:
            async with sem:
                resp = await asyncio.wait_for(
                    client.head(url, timeout=15.0), timeout=_PER_REQUEST_TIMEOUT_S
                )
        except asyncio.TimeoutError:
            return VerificationResult(property_id=pid, action="error", error="timeout")
        except Exception as exc:
            return VerificationResult(property_id=pid, action="error", error=str(exc))

        if resp.status_code == 404:
            return VerificationResult(property_id=pid, action="mark_inactive")
        if resp.status_code >= 500:
            return VerificationResult(property_id=pid, action="error", error=f"HTTP {resp.status_code}")
        return VerificationResult(property_id=pid, action="no_change")

    # ------------------------------------------------------------------
    # Private: apply changes
    # ------------------------------------------------------------------

    async def _apply_changes(
        self,
        session,
        results: list[VerificationResult],
        props_by_source: dict[str, list[dict]],
    ) -> dict:
        # Build source lookup for stats
        id_to_source: dict[int, str] = {}
        for src, props in props_by_source.items():
            for p in props:
                id_to_source[p["id"]] = src

        # Per-source counters
        by_source: dict[str, dict] = {src: _empty_source_stats() for src in _SOURCES}
        for src, props in props_by_source.items():
            if src in by_source:
                by_source[src]["checked"] = len(props)

        for result in results:
            src = id_to_source.get(result.property_id, "unknown")
            bucket = by_source.get(src, _empty_source_stats())

            if result.action == "mark_inactive":
                await session.execute(
                    text("UPDATE properties SET is_active = false, updated_at = NOW() WHERE id = :id"),
                    {"id": result.property_id},
                )
                bucket["marked_inactive"] += 1

            elif result.action == "set_on_hold":
                await session.execute(
                    text("UPDATE properties SET on_hold = true, updated_at = NOW() WHERE id = :id"),
                    {"id": result.property_id},
                )
                bucket["set_on_hold"] += 1

            elif result.action == "clear_on_hold":
                await session.execute(
                    text("UPDATE properties SET on_hold = false, updated_at = NOW() WHERE id = :id"),
                    {"id": result.property_id},
                )
                bucket["cleared_on_hold"] += 1

            elif result.action == "error":
                bucket["errors"] += 1

            # no_change → nothing

            if src in by_source:
                by_source[src] = bucket

        return {"by_source": by_source}


# ------------------------------------------------------------------
# Module-level factory
# ------------------------------------------------------------------


async def run_verification_scraper() -> dict:
    """Entry point invoked by APScheduler. Returns metrics dict."""
    return await VerificationScraper().run()
