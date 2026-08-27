"""Meta-test for STAB-02 (TD-115-05): the conftest cleanup must be fail-loud.

Proves the NEW contract that conftest's cleanup mechanism:
  1. RAISES on a non-zero psql return (no silent stderr=DEVNULL swallow); and
  2. detects residual pytest rows after a partial/incomplete cleanup, then
     reports clean (0 leftovers) once the real cleanup SQL has run.

These tests target the cleanup helpers in tests/conftest.py directly. They run
against onnix_dev only (the test DB) and exclusively touch pytest-pattern
rows (phone +595981[5-9]…), never real staging/baseline data.
"""
import pytest

from tests import conftest as ct


# A phone clearly inside the +595981[5-9]% pytest range so the real cleanup
# (TEST_PHONE_PREFIX_SQL) removes it.
_PYTEST_PHONE = "+595981999000001"


def _esc(value: str) -> str:
    """Escape a SQL string literal (single quotes doubled)."""
    return value.replace("'", "''")


def test_checked_psql_raises_on_nonzero():
    """The checked psql helper must RAISE on a non-zero psql return.

    Today's `_psql` swallows stderr (DEVNULL) and returns None even when the
    SQL errors → silent incomplete cleanup. The new fail-loud variant must
    exist AND raise RuntimeError on a bad statement.
    """
    # The fail-loud helper must exist (absent today → RED).
    checked = ct._psql_checked
    assert callable(checked)
    # And it must RAISE RuntimeError on a non-zero psql return — the swallow is gone.
    with pytest.raises(RuntimeError):
        checked("SELECT * FROM __no_such_table__;")


def test_residual_assert_helper_detects_leftovers():
    """The residual-check helper must report a pytest leftover, then clean.

    Insert one pytest contact, confirm the residual check flags it as a
    leftover, then run the real per-table cleanup SQL and confirm the residual
    check reports clean (0 leftovers).
    """
    # Ensure a clean slate first (idempotent; uses the real fail-loud cleanup).
    ct._run_cleanup()

    # Insert a single pytest contact inside the pytest phone range.
    ct._psql_checked(
        f"INSERT INTO contacts (phone, name, status) "
        f"VALUES ('{_esc(_PYTEST_PHONE)}', 'Pytest Residual Probe', 'new');"
    )

    # The residual check must flag this contact as a leftover (non-empty).
    leftovers = ct._residual_pytest_rows()
    assert leftovers, "residual check should report the inserted pytest contact"
    assert any(
        tbl == "contacts" and cnt > 0 for tbl, cnt in leftovers.items()
    ), f"expected a contacts leftover, got: {leftovers}"

    # Run the real cleanup, then the residual check must report clean (0).
    ct._run_cleanup()
    assert ct._residual_pytest_rows() == {}, (
        "after cleanup the residual check must report 0 leftovers"
    )
