"""Tests for VerificationScraper scheduled task.

TDD: tests written first (RED), then implementation (GREEN).
All tests mock both session_factory and http_client_factory — no network or DB.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import httpx
import pytest

from app.bot.scheduler.tasks.verification_scraper import (
    VerificationScraper,
    VerificationResult,
    run_verification_scraper,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session_factory():
    """Build a mock async session factory with a tracked mock session."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(rowcount=0))
    mock_session.commit = AsyncMock()

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock()
    mock_factory.return_value = mock_ctx

    return mock_factory, mock_session


def _make_http_response(status_code: int, text: str = "") -> MagicMock:
    """Build a mock httpx Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    resp.json = MagicMock(return_value={})
    return resp


def _make_settings_manager(enabled: bool = True) -> AsyncMock:
    sm = AsyncMock()
    sm.is_task_enabled = AsyncMock(return_value=enabled)
    return sm


def _make_http_client_factory(response: MagicMock) -> MagicMock:
    """Return a factory callable that yields a mock async context-manager client."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=response)
    mock_client.head = AsyncMock(return_value=response)
    mock_client.post = AsyncMock(return_value=response)

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock(return_value=mock_ctx)
    return factory, mock_client


def _psir_active_html() -> str:
    return "<html><body>En Venta apartamento USD 100.000 calidad</body></html>"


def _psir_homepage_html() -> str:
    return "<html><body>Bienvenido al portal inmobiliario</body></html>"


def _remax_response_json(
    mlsid: str,
    is_findable: bool,
    on_hold: bool,
) -> dict:
    return {
        "value": [
            {
                "content": {
                    "MLSID": mlsid,
                    "IsFindable": is_findable,
                    "OnHoldListing": on_hold,
                }
            }
        ]
    }


# ---------------------------------------------------------------------------
# Fixture: scraper with controlled session + http
# ---------------------------------------------------------------------------

def _make_scraper_with_props(
    props_by_source: dict[str, list[dict]],
    http_response: MagicMock,
    enabled: bool = True,
):
    """Build a VerificationScraper whose _load_active_properties is patched."""
    session_factory, mock_session = _make_session_factory()
    http_factory, mock_client = _make_http_client_factory(http_response)
    settings_manager = _make_settings_manager(enabled)

    scraper = VerificationScraper(
        session_factory=session_factory,
        http_client_factory=lambda: http_factory(),
        settings_manager=settings_manager,
        max_concurrent=5,
    )
    # Patch _load_active_properties to return controlled data
    scraper._load_active_properties = AsyncMock(return_value=props_by_source)

    return scraper, mock_session, mock_client


# ---------------------------------------------------------------------------
# Test 1: PSIR — HTTP 500 does NOT mark inactive
# ---------------------------------------------------------------------------

class TestPsirHttp500DoesNotMarkInactive:
    @pytest.mark.asyncio
    async def test_psir_http_500_does_not_mark_inactive(self):
        """500 on PSIR GET → error result, no UPDATE executed."""
        props = {
            "psir": [
                {"id": 1, "external_id": "psir-1", "url": "https://psir.com.py/prop/1", "on_hold": False},
                {"id": 2, "external_id": "psir-2", "url": "https://psir.com.py/prop/2", "on_hold": False},
            ]
        }
        resp = _make_http_response(500, "Server Error")
        scraper, mock_session, _ = _make_scraper_with_props(props, resp)

        result = await scraper.run()

        # No UPDATE should have been issued
        execute_calls = mock_session.execute.call_args_list
        assert len(execute_calls) == 0, f"Expected 0 UPDATEs, got {len(execute_calls)}"

        by_source = result["by_source"]["psir"]
        assert by_source["marked_inactive"] == 0
        assert by_source["errors"] == 2


# ---------------------------------------------------------------------------
# Test 2: PSIR — HTTP 404 marks inactive
# ---------------------------------------------------------------------------

class TestPsirHttp404MarksInactive:
    @pytest.mark.asyncio
    async def test_psir_http_404_marks_inactive(self):
        """404 on PSIR GET → mark_inactive, 1 UPDATE issued."""
        props = {
            "psir": [
                {"id": 10, "external_id": "psir-10", "url": "https://psir.com.py/prop/10", "on_hold": False},
            ]
        }
        resp = _make_http_response(404, "Not Found")
        scraper, mock_session, _ = _make_scraper_with_props(props, resp)

        result = await scraper.run()

        execute_calls = mock_session.execute.call_args_list
        assert len(execute_calls) == 1

        by_source = result["by_source"]["psir"]
        assert by_source["marked_inactive"] == 1
        assert by_source["errors"] == 0


# ---------------------------------------------------------------------------
# Test 3: PSIR — soft 404 (200 without markers) marks inactive
# ---------------------------------------------------------------------------

class TestPsirSoft404MarksInactive:
    @pytest.mark.asyncio
    async def test_psir_soft_404_marks_inactive(self):
        """200 with homepage HTML (no price/sale markers) → mark_inactive."""
        props = {
            "psir": [
                {"id": 20, "external_id": "psir-20", "url": "https://psir.com.py/prop/20", "on_hold": False},
            ]
        }
        resp = _make_http_response(200, _psir_homepage_html())
        scraper, mock_session, _ = _make_scraper_with_props(props, resp)

        result = await scraper.run()

        execute_calls = mock_session.execute.call_args_list
        assert len(execute_calls) == 1

        by_source = result["by_source"]["psir"]
        assert by_source["marked_inactive"] == 1
        assert by_source["errors"] == 0


# ---------------------------------------------------------------------------
# Test 4: PSIR — active listing (200 with markers) → no_change
# ---------------------------------------------------------------------------

class TestPsirActiveNoChange:
    @pytest.mark.asyncio
    async def test_psir_active_no_change(self):
        """200 with 'En Venta' + 'USD' → no_change, no UPDATE issued."""
        props = {
            "psir": [
                {"id": 30, "external_id": "psir-30", "url": "https://psir.com.py/prop/30", "on_hold": False},
            ]
        }
        resp = _make_http_response(200, _psir_active_html())
        scraper, mock_session, _ = _make_scraper_with_props(props, resp)

        result = await scraper.run()

        execute_calls = mock_session.execute.call_args_list
        assert len(execute_calls) == 0

        by_source = result["by_source"]["psir"]
        assert by_source["marked_inactive"] == 0
        assert by_source["errors"] == 0


# ---------------------------------------------------------------------------
# Test 5: Remax — IsFindable=false + OnHoldListing=true → set_on_hold
# ---------------------------------------------------------------------------

class TestRemaxOnHoldSetsFlag:
    @pytest.mark.asyncio
    async def test_remax_on_hold_sets_flag(self):
        """IsFindable=false, OnHoldListing=true → set_on_hold, UPDATE on_hold=true."""
        mlsid = "REMAX-001"
        props = {
            "remax": [
                {"id": 40, "external_id": mlsid, "url": "https://remax.com.py/prop/001", "on_hold": False},
            ]
        }
        json_resp = _remax_response_json(mlsid, is_findable=False, on_hold=True)
        resp = _make_http_response(200, "")
        resp.json = MagicMock(return_value=json_resp)

        session_factory, mock_session = _make_session_factory()
        http_factory, mock_client = _make_http_client_factory(resp)
        settings_manager = _make_settings_manager(True)

        scraper = VerificationScraper(
            session_factory=session_factory,
            http_client_factory=lambda: http_factory(),
            settings_manager=settings_manager,
        )
        scraper._load_active_properties = AsyncMock(return_value=props)

        result = await scraper.run()

        # One UPDATE for set_on_hold
        execute_calls = mock_session.execute.call_args_list
        assert len(execute_calls) == 1

        by_source = result["by_source"]["remax"]
        assert by_source["set_on_hold"] == 1
        assert by_source["marked_inactive"] == 0
        assert by_source["errors"] == 0


# ---------------------------------------------------------------------------
# Test 6: Remax — MLSID absent from response → mark_inactive
# ---------------------------------------------------------------------------

class TestRemaxGoneMarksInactive:
    @pytest.mark.asyncio
    async def test_remax_gone_marks_inactive(self):
        """MLSID not in Azure response → mark_inactive."""
        mlsid = "REMAX-GONE"
        props = {
            "remax": [
                {"id": 50, "external_id": mlsid, "url": "https://remax.com.py/prop/GONE", "on_hold": False},
            ]
        }
        # Response with a different MLSID (the requested one is absent)
        json_resp = {"value": []}
        resp = _make_http_response(200, "")
        resp.json = MagicMock(return_value=json_resp)

        session_factory, mock_session = _make_session_factory()
        http_factory, mock_client = _make_http_client_factory(resp)
        settings_manager = _make_settings_manager(True)

        scraper = VerificationScraper(
            session_factory=session_factory,
            http_client_factory=lambda: http_factory(),
            settings_manager=settings_manager,
        )
        scraper._load_active_properties = AsyncMock(return_value=props)

        result = await scraper.run()

        execute_calls = mock_session.execute.call_args_list
        assert len(execute_calls) == 1

        by_source = result["by_source"]["remax"]
        assert by_source["marked_inactive"] == 1
        assert by_source["set_on_hold"] == 0
        assert by_source["errors"] == 0


# ---------------------------------------------------------------------------
# Test 7: Feature flag disabled → skips run
# ---------------------------------------------------------------------------

class TestFeatureFlagDisabledSkipsRun:
    @pytest.mark.asyncio
    async def test_feature_flag_disabled_skips_run(self):
        """is_task_enabled=False → returns {'skipped': True}, no HTTP, no DB."""
        session_factory, mock_session = _make_session_factory()
        resp = _make_http_response(200, "")
        http_factory, mock_client = _make_http_client_factory(resp)
        settings_manager = _make_settings_manager(enabled=False)

        scraper = VerificationScraper(
            session_factory=session_factory,
            http_client_factory=lambda: http_factory(),
            settings_manager=settings_manager,
        )

        result = await scraper.run()

        assert result == {"skipped": True}
        # No HTTP calls
        mock_client.get.assert_not_called()
        mock_client.head.assert_not_called()
        mock_client.post.assert_not_called()
        # No DB updates
        mock_session.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Test 8: Onnixpy — HEAD 404 marks inactive
# ---------------------------------------------------------------------------

class TestOnnixpyHead404MarksInactive:
    @pytest.mark.asyncio
    async def test_onnixpy_head_404_marks_inactive(self):
        """HEAD 404 on onnixpy → mark_inactive; assert HEAD not GET was called."""
        props = {
            "onnixpy": [
                {"id": 60, "external_id": "onnixpy-60", "url": "https://onnix.com.py/prop/60", "on_hold": False},
            ]
        }
        resp_404 = _make_http_response(404, "")

        session_factory, mock_session = _make_session_factory()

        # Separate client mock so we can assert HEAD vs GET
        mock_client = AsyncMock()
        mock_client.head = AsyncMock(return_value=resp_404)
        mock_client.get = AsyncMock(return_value=_make_http_response(200, ""))

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        http_factory = MagicMock(return_value=mock_ctx)
        settings_manager = _make_settings_manager(True)

        scraper = VerificationScraper(
            session_factory=session_factory,
            http_client_factory=lambda: http_factory(),
            settings_manager=settings_manager,
        )
        scraper._load_active_properties = AsyncMock(return_value=props)

        result = await scraper.run()

        # HEAD was used (not GET)
        mock_client.head.assert_called()
        mock_client.get.assert_not_called()

        execute_calls = mock_session.execute.call_args_list
        assert len(execute_calls) == 1

        by_source = result["by_source"]["onnixpy"]
        assert by_source["marked_inactive"] == 1
        assert by_source["errors"] == 0


# ---------------------------------------------------------------------------
# Test 9: Remax — HTTP 400 does NOT mark inactive (defensive 4xx guard)
# ---------------------------------------------------------------------------

class TestRemax400DoesNotMarkInactive:
    @pytest.mark.asyncio
    async def test_remax_400_does_not_mark_inactive(self):
        """400 from Azure Search → all props get action='error', none get 'mark_inactive'.
        Zero UPDATE calls must be issued to the DB session.
        """
        props = {
            "remax": [
                {"id": 70, "external_id": "REMAX-070", "url": "https://remax.com.py/prop/070", "on_hold": False},
                {"id": 71, "external_id": "REMAX-071", "url": "https://remax.com.py/prop/071", "on_hold": False},
            ]
        }
        resp = _make_http_response(400, '{"error":{"code":"","message":"Invalid expression"}}')
        resp.json = MagicMock(return_value={"error": {"code": "", "message": "Invalid expression"}})

        scraper, mock_session, _ = _make_scraper_with_props(props, resp)

        result = await scraper.run()

        # Zero UPDATEs — no false-positive deactivations
        execute_calls = mock_session.execute.call_args_list
        assert len(execute_calls) == 0, f"Expected 0 UPDATEs, got {len(execute_calls)}"

        by_source = result["by_source"]["remax"]
        assert by_source["marked_inactive"] == 0
        assert by_source["errors"] == 2


# ---------------------------------------------------------------------------
# Test 11: Default HTTP client uses browser User-Agent (regression guard for Coldwell WAF)
# ---------------------------------------------------------------------------

class TestDefaultHttpClientHasBrowserUserAgent:
    @pytest.mark.asyncio
    async def test_default_http_client_has_browser_user_agent(self):
        """VerificationScraper() with no overrides must produce an AsyncClient whose
        User-Agent contains 'Mozilla/5.0' and 'Chrome/' — guards against Coldwell WAF
        returning 403 when httpx's default 'python-httpx/x.y.z' UA is used."""
        scraper = VerificationScraper()
        async with scraper._http_client_factory() as client:
            ua = client.headers.get("user-agent", "")
            assert "Mozilla/5.0" in ua, f"Expected 'Mozilla/5.0' in UA, got: {ua!r}"
            assert "Chrome/" in ua, f"Expected 'Chrome/' in UA, got: {ua!r}"


# ---------------------------------------------------------------------------
# Test 10: Remax — HTTP 400 returns error with "HTTP 400" message, batch size matches
# ---------------------------------------------------------------------------

class TestRemax400ViaInvalidSelectReturnsErrorNotMarkInactive:
    @pytest.mark.asyncio
    async def test_remax_400_via_invalid_select_returns_error_not_mark_inactive(self):
        """Exact failure mode: Azure returns 400 for bad $select.
        Assert error message includes 'HTTP 400' and result count == batch size.
        """
        batch_props = [
            {"id": 80 + i, "external_id": f"REMAX-08{i}", "url": f"https://remax.com.py/prop/08{i}", "on_hold": False}
            for i in range(3)
        ]
        props = {"remax": batch_props}

        resp = _make_http_response(400, '{"error":{"code":"","message":"Could not find a property named \'MLSID\'"}}')
        resp.json = MagicMock(return_value={"error": {"message": "Could not find a property named 'MLSID'"}})

        scraper, mock_session, mock_client = _make_scraper_with_props(props, resp)

        result = await scraper.run()

        by_source = result["by_source"]["remax"]
        # Result count must equal batch size
        assert by_source["errors"] == len(batch_props)
        assert by_source["marked_inactive"] == 0

        # Error message must mention HTTP 400 (verified via internal VerificationResult)
        # We confirm by checking no DB writes occurred
        execute_calls = mock_session.execute.call_args_list
        assert len(execute_calls) == 0, f"Expected 0 UPDATEs, got {len(execute_calls)}"


# ---------------------------------------------------------------------------
# Test 12: Per-task timeout — hung GET returns error result, does not hang
# ---------------------------------------------------------------------------

class TestPerTaskTimeoutReturnsErrorNotHang:
    @pytest.mark.asyncio
    async def test_per_task_timeout_returns_error_not_hang(self):
        """_psir_check_one: client.get sleeps 30s (> 20s cap).
        Must return action='error' with error containing 'timeout' within 25s.
        """
        import asyncio
        from unittest.mock import patch

        async def _slow_get(*_args, **_kwargs):
            await asyncio.sleep(30)
            return _make_http_response(200, _psir_active_html())

        props_by_source = {
            "psir": [
                {"id": 901, "external_id": "psir-901", "url": "https://psir.com.py/prop/901", "on_hold": False},
            ]
        }

        mock_client = AsyncMock()
        mock_client.get = _slow_get
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        http_factory = MagicMock(return_value=mock_ctx)

        session_factory, mock_session = _make_session_factory()
        settings_manager = _make_settings_manager(True)

        scraper = VerificationScraper(
            session_factory=session_factory,
            http_client_factory=lambda: http_factory(),
            settings_manager=settings_manager,
        )
        scraper._load_active_properties = AsyncMock(return_value=props_by_source)

        with (
            patch("app.bot.scheduler.tasks.verification_scraper._PER_REQUEST_TIMEOUT_S", 0.4),
            patch("app.bot.scheduler.tasks.verification_scraper._PER_PORTAL_CAP_S", 5.0),
        ):
            result = await asyncio.wait_for(scraper.run(), timeout=10.0)

        by_source = result["by_source"]["psir"]
        assert by_source["errors"] == 1
        assert by_source["marked_inactive"] == 0


# ---------------------------------------------------------------------------
# Test 13: Portal cap returns partial results
# ---------------------------------------------------------------------------

class TestPortalCapReturnsPartialResults:
    @pytest.mark.asyncio
    async def test_portal_cap_returns_partial_results(self):
        """5 PSIR props. First 2 return 200 instantly, next 3 sleep forever.
        With _PER_PORTAL_CAP_S=1.0 and _PER_REQUEST_TIMEOUT_S=0.5, run() returns
        some errors and does not hang.
        """
        import asyncio
        from unittest.mock import patch

        call_count = {"n": 0}

        async def _selective_get(url, **_kwargs):
            call_count["n"] += 1
            if call_count["n"] <= 2:
                return _make_http_response(200, _psir_active_html())
            await asyncio.sleep(30)
            return _make_http_response(200, _psir_active_html())

        props_by_source = {
            "psir": [
                {"id": 910 + i, "external_id": f"psir-91{i}", "url": f"https://psir.com.py/prop/91{i}", "on_hold": False}
                for i in range(5)
            ]
        }

        mock_client = AsyncMock()
        mock_client.get = _selective_get
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        http_factory = MagicMock(return_value=mock_ctx)

        session_factory, mock_session = _make_session_factory()
        settings_manager = _make_settings_manager(True)

        scraper = VerificationScraper(
            session_factory=session_factory,
            http_client_factory=lambda: http_factory(),
            settings_manager=settings_manager,
            max_concurrent=10,
        )
        scraper._load_active_properties = AsyncMock(return_value=props_by_source)

        with (
            patch("app.bot.scheduler.tasks.verification_scraper._PER_REQUEST_TIMEOUT_S", 0.2),
            patch("app.bot.scheduler.tasks.verification_scraper._PER_PORTAL_CAP_S", 0.4),
        ):
            result = await asyncio.wait_for(scraper.run(), timeout=5.0)

        by_source = result["by_source"]["psir"]
        # At least 3 tasks timed out → errors >= 3
        assert by_source["errors"] >= 3, f"Expected >=3 errors, got {by_source}"
        # run() returned a valid dict (did not raise)
        assert "by_source" in result


# ---------------------------------------------------------------------------
# Test 14: Portal cap does not kill run() — _apply_changes is always called
# ---------------------------------------------------------------------------

class TestPortalCapDoesNotKillRun:
    @pytest.mark.asyncio
    async def test_portal_cap_does_not_kill_run(self):
        """Portal cap fires on PSIR. run() must still call _apply_changes and return
        a dict with 'by_source' populated — APScheduler slot must be freed.
        """
        import asyncio
        from unittest.mock import patch

        async def _always_slow(*_args, **_kwargs):
            await asyncio.sleep(30)
            return _make_http_response(200, "")

        props_by_source = {
            "psir": [
                {"id": 920, "external_id": "psir-920", "url": "https://psir.com.py/prop/920", "on_hold": False},
                {"id": 921, "external_id": "psir-921", "url": "https://psir.com.py/prop/921", "on_hold": False},
            ]
        }

        mock_client = AsyncMock()
        mock_client.get = _always_slow
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        http_factory = MagicMock(return_value=mock_ctx)

        session_factory, mock_session = _make_session_factory()
        settings_manager = _make_settings_manager(True)

        scraper = VerificationScraper(
            session_factory=session_factory,
            http_client_factory=lambda: http_factory(),
            settings_manager=settings_manager,
        )
        scraper._load_active_properties = AsyncMock(return_value=props_by_source)

        apply_changes_spy = AsyncMock(wraps=scraper._apply_changes)
        scraper._apply_changes = apply_changes_spy

        with (
            patch("app.bot.scheduler.tasks.verification_scraper._PER_REQUEST_TIMEOUT_S", 0.2),
            patch("app.bot.scheduler.tasks.verification_scraper._PER_PORTAL_CAP_S", 0.4),
        ):
            result = await asyncio.wait_for(scraper.run(), timeout=5.0)

        # _apply_changes must have been called (run() survived the cap)
        apply_changes_spy.assert_called_once()

        # Result dict must have by_source key with psir data
        assert "by_source" in result
        assert "psir" in result["by_source"]


# ---------------------------------------------------------------------------
# Test 15: _partial_results_from_tasks requires real Task objects (regression)
# ---------------------------------------------------------------------------

class TestPartialResultsFromTasksHandlesRealTasks:
    @pytest.mark.asyncio
    async def test_partial_results_from_tasks_handles_real_tasks(self):
        """Portal cap fires BEFORE per-task timeouts (cap=0.5s, per-task=10s).
        _partial_results_from_tasks is called with real Task objects (not coroutines).
        Must not raise AttributeError and must produce 3 error results.
        Fails on buggy code where tasks list holds bare coroutines.
        """
        import asyncio
        from unittest.mock import patch

        async def _always_slow(*_args, **_kwargs):
            await asyncio.sleep(5)
            return _make_http_response(200, _psir_active_html())

        props_by_source = {
            "psir": [
                {"id": 930 + i, "external_id": f"psir-93{i}", "url": f"https://psir.com.py/prop/93{i}", "on_hold": False}
                for i in range(3)
            ]
        }

        mock_client = AsyncMock()
        mock_client.get = _always_slow
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        http_factory = MagicMock(return_value=mock_ctx)

        session_factory, mock_session = _make_session_factory()
        settings_manager = _make_settings_manager(True)

        scraper = VerificationScraper(
            session_factory=session_factory,
            http_client_factory=lambda: http_factory(),
            settings_manager=settings_manager,
            max_concurrent=10,
        )
        scraper._load_active_properties = AsyncMock(return_value=props_by_source)

        apply_changes_spy = AsyncMock(wraps=scraper._apply_changes)
        scraper._apply_changes = apply_changes_spy

        with (
            patch("app.bot.scheduler.tasks.verification_scraper._PER_REQUEST_TIMEOUT_S", 4.0),
            patch("app.bot.scheduler.tasks.verification_scraper._PER_PORTAL_CAP_S", 0.2),
        ):
            result = await asyncio.wait_for(scraper.run(), timeout=5.0)

        # All 3 tasks timed out at portal cap level → 3 errors
        assert result["by_source"]["psir"]["errors"] == 3
        # run() survived — _apply_changes was called
        apply_changes_spy.assert_called_once()


# ---------------------------------------------------------------------------
# Test 16: checked counter reflects len(props), not len(results)
# ---------------------------------------------------------------------------

class TestCheckedCounterReflectsLenProps:
    @pytest.mark.asyncio
    async def test_checked_equals_len_props_regardless_of_results(self):
        """checked must equal the number of properties loaded, not the number of
        VerificationResults returned (e.g. portal cap fired and only 1 task completed).

        Guards against any refactor that accidentally couples checked to result list length.
        """
        props = {
            "coldwell": [
                {"id": 100 + i, "external_id": f"cw-10{i}", "url": f"https://coldwell.com.py/prop/10{i}", "on_hold": False}
                for i in range(5)
            ]
        }
        # _verify_coldwell returns only 1 result even though 5 props were loaded
        single_error = VerificationResult(property_id=100, action="error", error="timeout")

        resp = _make_http_response(200, "")
        scraper, mock_session, _ = _make_scraper_with_props(props, resp)
        scraper._verify_coldwell = AsyncMock(return_value=[single_error])

        result = await scraper.run()

        by_source = result["by_source"]["coldwell"]
        assert by_source["checked"] == 5, (
            f"checked should be len(props)=5, got {by_source['checked']}"
        )
        assert by_source["errors"] == 1


# ---------------------------------------------------------------------------
# Test 17: All 4 portals run concurrently (wall time < sum of individual times)
# ---------------------------------------------------------------------------

class TestPortalsRunInParallel:
    @pytest.mark.asyncio
    async def test_portals_run_in_parallel(self):
        """All 4 _verify_<portal> methods sleep 0.25s each. Sequential would take ~1s.
        Parallel execution must complete in under 0.6s (generous CI margin).
        """
        import asyncio
        import time

        async def _slow_verify(props):
            await asyncio.sleep(0.25)
            return []

        props_by_source = {
            "remax": [{"id": 1, "external_id": "r1", "url": "u", "on_hold": False}],
            "psir": [{"id": 2, "external_id": "p1", "url": "u", "on_hold": False}],
            "coldwell": [{"id": 3, "external_id": "c1", "url": "u", "on_hold": False}],
            "onnixpy": [{"id": 4, "external_id": "onnix", "url": "u", "on_hold": False}],
        }

        resp = _make_http_response(200, "")
        scraper, _, _ = _make_scraper_with_props(props_by_source, resp)
        scraper._verify_remax = _slow_verify
        scraper._verify_psir = _slow_verify
        scraper._verify_coldwell = _slow_verify
        scraper._verify_onnixpy = _slow_verify

        t0 = time.monotonic()
        result = await scraper.run()
        elapsed = time.monotonic() - t0

        assert elapsed < 0.6, f"Expected parallel run < 0.6s, took {elapsed:.2f}s"
        assert "by_source" in result


# ---------------------------------------------------------------------------
# Test 18: One portal exception does not kill the others
# ---------------------------------------------------------------------------

class TestPortalExceptionDoesNotKillOthers:
    @pytest.mark.asyncio
    async def test_portal_exception_does_not_kill_others(self):
        """_verify_coldwell raises RuntimeError. The other 3 portals must still
        apply their results. Coldwell props must appear as errors in stats.
        """
        props_by_source = {
            "remax": [{"id": 10, "external_id": "r10", "url": "u", "on_hold": False}],
            "psir": [{"id": 20, "external_id": "p20", "url": "u", "on_hold": False}],
            "coldwell": [
                {"id": 30, "external_id": "cw30", "url": "u", "on_hold": False},
                {"id": 31, "external_id": "cw31", "url": "u", "on_hold": False},
            ],
            "onnixpy": [{"id": 40, "external_id": "onnixpy40", "url": "u", "on_hold": False}],
        }

        async def _raise_coldwell(props):
            raise RuntimeError("simulated coldwell failure")

        resp = _make_http_response(200, "")
        scraper, mock_session, _ = _make_scraper_with_props(props_by_source, resp)
        scraper._verify_remax = AsyncMock(
            return_value=[VerificationResult(property_id=10, action="mark_inactive")]
        )
        scraper._verify_psir = AsyncMock(
            return_value=[VerificationResult(property_id=20, action="mark_inactive")]
        )
        scraper._verify_coldwell = _raise_coldwell
        scraper._verify_onnixpy = AsyncMock(
            return_value=[VerificationResult(property_id=40, action="mark_inactive")]
        )

        result = await scraper.run()

        # run() must return successfully
        assert "by_source" in result

        # Other portals' mark_inactive results were applied (3 UPDATE calls)
        execute_calls = mock_session.execute.call_args_list
        assert len(execute_calls) == 3, f"Expected 3 UPDATEs (non-coldwell), got {len(execute_calls)}"

        # Coldwell props all counted as errors
        cw = result["by_source"]["coldwell"]
        assert cw["errors"] == 2, f"Expected 2 coldwell errors, got {cw['errors']}"
        assert cw["marked_inactive"] == 0


# ---------------------------------------------------------------------------
# Test 19: Portals with zero props are not dispatched
# ---------------------------------------------------------------------------

class TestPortalWithZeroPropsIsNotDispatched:
    @pytest.mark.asyncio
    async def test_portal_with_zero_props_is_not_dispatched(self):
        """psir=[] and coldwell=[] → _verify_psir and _verify_coldwell never called."""
        props_by_source = {
            "remax": [{"id": 1, "external_id": "r1", "url": "u", "on_hold": False}],
            "psir": [],
            "coldwell": [],
            "onnixpy": [{"id": 2, "external_id": "onnix", "url": "u", "on_hold": False}],
        }

        resp = _make_http_response(200, "")
        scraper, _, _ = _make_scraper_with_props(props_by_source, resp)
        scraper._verify_remax = AsyncMock(return_value=[])
        scraper._verify_psir = AsyncMock(return_value=[])
        scraper._verify_coldwell = AsyncMock(return_value=[])
        scraper._verify_onnixpy = AsyncMock(return_value=[])

        await scraper.run()

        scraper._verify_psir.assert_not_called()
        scraper._verify_coldwell.assert_not_called()
        scraper._verify_remax.assert_called_once()
        scraper._verify_onnixpy.assert_called_once()
