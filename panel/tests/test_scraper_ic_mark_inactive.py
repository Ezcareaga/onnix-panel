"""Tests for mark_ic_inactive() and the 90% guard in the IC scraper run().

TDD: these tests must FAIL before the implementation exists, then PASS after.

All tests are fully isolated — no real DB, no network access.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup
#
# Two separate paths are needed:
#   1. scrapers/        → for `from shared.db import ...` (used by shared.db itself)
#   2. la raíz del repo → for `import scrapers.infocasas.scraper` (package import)
# ---------------------------------------------------------------------------
_home_dir = str(Path(__file__).resolve().parent.parent.parent)  # raíz del repo
_scrapers_dir = str(Path(__file__).resolve().parent.parent.parent / "scrapers")

for _p in [_home_dir, _scrapers_dir]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Helper: build a fake psycopg context-manager chain
#
# Simulates:
#   with get_connection() as conn:
#       with conn.cursor() as cur:
#           cur.execute(...)
#           count = cur.rowcount
#       conn.commit()
# ---------------------------------------------------------------------------

def _make_conn_mock(rowcount: int = 0):
    """Return (mock_get_conn, mock_conn, mock_cur) with rowcount pre-set."""
    mock_cur = MagicMock()
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.rowcount = rowcount

    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor = MagicMock(return_value=mock_cur)

    # get_connection() is a context manager itself
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    mock_get_conn = MagicMock(return_value=mock_ctx)

    return mock_get_conn, mock_conn, mock_cur


# ===========================================================================
# Tests for mark_ic_inactive()
# ===========================================================================

class TestMarkIcInactive:
    """Unit tests for shared.db.mark_ic_inactive()."""

    def test_mark_ic_inactive_skips_empty_list(self):
        """mark_ic_inactive([]) must return 0 without executing any SQL."""
        from shared.db import mark_ic_inactive

        mock_get_conn, mock_conn, mock_cur = _make_conn_mock(rowcount=5)

        with patch("shared.db.get_connection", mock_get_conn):
            result = mark_ic_inactive([])

        assert result == 0
        # SQL must NOT have been executed
        mock_cur.execute.assert_not_called()
        mock_conn.commit.assert_not_called()

    def test_mark_ic_inactive_executes_correct_sql(self):
        """mark_ic_inactive executes UPDATE with != ALL(%s) containing the given IDs."""
        from shared.db import mark_ic_inactive

        active_ids = ["IC001", "IC002"]
        mock_get_conn, mock_conn, mock_cur = _make_conn_mock(rowcount=0)

        with patch("shared.db.get_connection", mock_get_conn):
            mark_ic_inactive(active_ids)

        mock_cur.execute.assert_called_once()
        sql_called, params_called = mock_cur.execute.call_args.args
        # The SQL must target infocasas_properties, not properties
        assert "infocasas_properties" in sql_called
        # Must use != ALL pattern
        assert "!= ALL" in sql_called
        # Must pass the list as the single parameter
        assert params_called == (active_ids,)

    def test_mark_ic_inactive_returns_rowcount(self):
        """mark_ic_inactive returns the number of rows affected by the UPDATE."""
        from shared.db import mark_ic_inactive

        mock_get_conn, mock_conn, mock_cur = _make_conn_mock(rowcount=3)

        with patch("shared.db.get_connection", mock_get_conn):
            result = mark_ic_inactive(["IC001"])

        assert result == 3

    def test_mark_ic_inactive_uses_active_true_condition(self):
        """The UPDATE SQL must filter WHERE is_active = TRUE to avoid re-marking."""
        from shared.db import mark_ic_inactive

        mock_get_conn, mock_conn, mock_cur = _make_conn_mock(rowcount=1)

        with patch("shared.db.get_connection", mock_get_conn):
            mark_ic_inactive(["IC001"])

        sql_called = mock_cur.execute.call_args.args[0]
        # Must only mark currently-active rows (case-insensitive check)
        assert "is_active = TRUE" in sql_called or "is_active = true" in sql_called.lower()

    def test_mark_ic_inactive_commits_transaction(self):
        """mark_ic_inactive must commit after the UPDATE."""
        from shared.db import mark_ic_inactive

        mock_get_conn, mock_conn, mock_cur = _make_conn_mock(rowcount=2)

        with patch("shared.db.get_connection", mock_get_conn):
            mark_ic_inactive(["IC001", "IC002"])

        mock_conn.commit.assert_called_once()


# ===========================================================================
# Tests for the 90% guard in scraper run()
# ===========================================================================

def _make_page_data(ids: list[str]) -> dict:
    """Build a fake virtual-office page with the given infocasas_ids.

    Los nombres son los de la API nueva (graph.infocasas.com.uy), medidos el
    2026-08-24 — ver panel/tests/test_scraper_ic_api_nueva.py:
      - 'id'     → infocasas_id
      - 'codigo' → infocasas_ref  (NOT 'ref' — parse_property uses raw.get('codigo', ''))
    """
    return {
        "data": [
            {
                "id": ic_id,
                "codigo": f"REF-{ic_id}",  # parse_property reads 'codigo' for infocasas_ref
                "titulo": f"Prop {ic_id}",
                "price": 100000,
                "precioa": 0,
                "currency_id": 1,
                "property_type": {"name": "Casa", "plural": "Casas"},
                "operation_type": {"name": "Venta", "operation_type_id": 1},
                "operation_type_id": 1,
                "direccion": "Centro, Asunción, Paraguay",
                "location": "Asunción Asunción",
                "IDdepartamentos": 21,
                "activo": 1,
            }
            for ic_id in ids
        ]
    }


class TestScraperGuard90Percent:
    """Tests for the 90% threshold guard before calling mark_ic_inactive in run().

    Strategy for scraper tests: keep total_expected small enough that pages=1
    (at most 20 props per page), so the loop runs exactly once and the test
    completes in milliseconds.  The ratio still exercises the 90% boundary.

    Boundary cases:
      - 18 seen / 20 expected = 90.0%  → calls mark_ic_inactive   (AT boundary)
      - 17 seen / 20 expected = 85.0%  → skips mark_ic_inactive   (BELOW boundary)
      - 0 seen  /  0 expected = div0   → skips mark_ic_inactive   (zero-guard)
    """

    def _run_with_guard(
        self,
        total_expected: int,
        seen_count: int,
        *,
        call_expected: bool,
    ) -> None:
        """
        Helper: mock the scraper's internals so that:
          - fetch_page returns paginado.total = total_expected and seen_count props
          - mark_ic_inactive is patched and we verify whether it was called

        `last_page` se fija en 1 para que el bucle corra una sola vez.
        """
        import scrapers.infocasas.scraper as scraper_module

        ids = [f"IC{i:05d}" for i in range(seen_count)]
        page_data = _make_page_data(ids)

        first_page_response = {
            "total": total_expected,
            "last_page": 1,
            **page_data,
        }

        fake_conn = MagicMock()
        fake_cur = MagicMock()
        fake_cur.__enter__ = MagicMock(return_value=fake_cur)
        fake_cur.__exit__ = MagicMock(return_value=False)
        fake_conn.cursor.return_value = fake_cur

        with (
            patch("scrapers.infocasas.scraper.get_authenticated_session", return_value=MagicMock()),
            patch("scrapers.infocasas.scraper.fetch_page", return_value=first_page_response),
            patch("scrapers.infocasas.scraper.get_db_conn", return_value=fake_conn),
            patch("scrapers.infocasas.scraper.run_matching", return_value=0),
            patch("scrapers.infocasas.scraper.mark_ic_inactive") as mock_mark,
            patch("scrapers.infocasas.scraper.time.sleep"),
        ):
            scraper_module.run()

        if call_expected:
            mock_mark.assert_called_once()
            called_ids = mock_mark.call_args.args[0]
            assert isinstance(called_ids, list)
        else:
            mock_mark.assert_not_called()

    def test_guard_calls_mark_when_90_percent_seen(self):
        """18 seen of 20 expected (90%) → mark_ic_inactive IS called (at boundary)."""
        self._run_with_guard(
            total_expected=20,
            seen_count=18,   # 18/20 = 90.0% — exactly at the threshold
            call_expected=True,
        )

    def test_guard_skips_mark_when_below_90_percent(self):
        """17 seen of 20 expected (85%) → mark_ic_inactive NOT called (below boundary)."""
        self._run_with_guard(
            total_expected=20,
            seen_count=17,   # 17/20 = 85.0% — below 90%
            call_expected=False,
        )

    def test_guard_skips_mark_when_total_expected_zero(self):
        """total_expected=0 → mark_ic_inactive NOT called (division-by-zero guard)."""
        import scrapers.infocasas.scraper as scraper_module

        fake_conn = MagicMock()
        fake_cur = MagicMock()
        fake_cur.__enter__ = MagicMock(return_value=fake_cur)
        fake_cur.__exit__ = MagicMock(return_value=False)
        fake_conn.cursor.return_value = fake_cur

        with (
            patch("scrapers.infocasas.scraper.get_authenticated_session", return_value=MagicMock()),
            patch(
                "scrapers.infocasas.scraper.fetch_page",
                return_value={"total": 0, "last_page": 0, "data": []},
            ),
            patch("scrapers.infocasas.scraper.get_db_conn", return_value=fake_conn),
            patch("scrapers.infocasas.scraper.run_matching", return_value=0),
            patch("scrapers.infocasas.scraper.mark_ic_inactive") as mock_mark,
            patch("scrapers.infocasas.scraper.time.sleep"),
        ):
            scraper_module.run()

        mock_mark.assert_not_called()
