"""Tests for is_active=True filter on IC lookup methods in PropertyRepository.

Verifies that get_ic_by_infocasas_id, get_ic_by_ref, and get_ic_by_refs
only return InfocasasProperty rows where is_active=True.

Strategy: capture the SQLAlchemy Select statement passed to db.execute()
and inspect its WHERE clauses for the is_active=True condition.
Uses AsyncMock for db.execute() — no real DB required.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call
import re

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.repositories.property_repo import property_repo
from app.models.infocasas_property import InfocasasProperty


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ic_property(
    *,
    infocasas_id: str = "12345",
    infocasas_ref: str = "OF23CE",
    active: bool = True,
) -> InfocasasProperty:
    """Build an InfocasasProperty instance with sensible defaults."""
    prop = InfocasasProperty()
    prop.id = 1
    prop.infocasas_id = infocasas_id
    prop.infocasas_ref = infocasas_ref
    prop.is_active = active
    return prop


def _make_db(*, scalar_result=None, scalars_all=None) -> AsyncMock:
    """Return a mock AsyncSession that records the statement passed to execute().

    Args:
        scalar_result: Value returned by result.scalar_one_or_none().
        scalars_all: List returned by result.scalars().all().
    """
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = scalar_result

    scalars_mock = MagicMock()
    scalars_mock.all.return_value = scalars_all or []
    result_mock.scalars.return_value = scalars_mock

    db = AsyncMock()
    db.execute = AsyncMock(return_value=result_mock)
    return db


def _get_compiled_sql(db: AsyncMock) -> str:
    """Extract and compile the SELECT statement that was passed to db.execute()."""
    call_args = db.execute.call_args
    assert call_args is not None, "db.execute() was not called"
    stmt = call_args[0][0]
    # Compile with PostgreSQL dialect and literal_binds to see actual values
    compiled = stmt.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    )
    return str(compiled)


def _assert_has_active_filter(sql: str) -> None:
    """Assert that the SQL contains a WHERE clause filtering is_active = true."""
    # Match 'is_active = true' or 'is_active IS true' or 'is_active = TRUE' etc.
    pattern = re.compile(r"is_active\s*(=|IS)\s*(true|TRUE|1)", re.IGNORECASE)
    assert pattern.search(sql), (
        f"Expected is_active=true filter in SQL, but got:\n{sql}"
    )


# ---------------------------------------------------------------------------
# get_ic_by_infocasas_id
# ---------------------------------------------------------------------------


class TestGetIcByInfocasasIdActiveFilter:
    @pytest.mark.asyncio
    async def test_query_includes_active_true_filter(self):
        """SQL must contain is_active=true when looking up by infocasas_id."""
        db = _make_db(scalar_result=None)
        await property_repo.get_ic_by_infocasas_id(db, "99999")
        sql = _get_compiled_sql(db)
        _assert_has_active_filter(sql)

    @pytest.mark.asyncio
    async def test_returns_property_when_db_returns_active_row(self):
        """When DB returns a row (active=True), repo returns the object."""
        prop = _make_ic_property(infocasas_id="12345", active=True)
        db = _make_db(scalar_result=prop)
        result = await property_repo.get_ic_by_infocasas_id(db, "12345")
        assert result is prop

    @pytest.mark.asyncio
    async def test_returns_none_when_db_returns_nothing(self):
        """When DB returns None (because active=False was filtered), repo returns None."""
        db = _make_db(scalar_result=None)
        result = await property_repo.get_ic_by_infocasas_id(db, "12345")
        assert result is None

    @pytest.mark.asyncio
    async def test_short_circuits_on_empty_id(self):
        """Empty string must short-circuit before hitting DB."""
        db = _make_db(scalar_result=None)
        result = await property_repo.get_ic_by_infocasas_id(db, "")
        assert result is None
        db.execute.assert_not_called()


# ---------------------------------------------------------------------------
# get_ic_by_ref
# ---------------------------------------------------------------------------


class TestGetIcByRefActiveFilter:
    @pytest.mark.asyncio
    async def test_query_includes_active_true_filter(self):
        """SQL must contain is_active=true when looking up by infocasas_ref."""
        db = _make_db(scalar_result=None)
        await property_repo.get_ic_by_ref(db, "OF23CE")
        sql = _get_compiled_sql(db)
        _assert_has_active_filter(sql)

    @pytest.mark.asyncio
    async def test_returns_property_when_db_returns_active_row(self):
        """When DB returns a row, repo returns the object."""
        prop = _make_ic_property(infocasas_ref="OF23CE", active=True)
        db = _make_db(scalar_result=prop)
        result = await property_repo.get_ic_by_ref(db, "OF23CE")
        assert result is prop

    @pytest.mark.asyncio
    async def test_returns_none_when_db_returns_nothing(self):
        """When DB returns None, repo returns None."""
        db = _make_db(scalar_result=None)
        result = await property_repo.get_ic_by_ref(db, "OF23CE")
        assert result is None

    @pytest.mark.asyncio
    async def test_short_circuits_on_empty_ref(self):
        """Empty ref must short-circuit before hitting DB."""
        db = _make_db(scalar_result=None)
        result = await property_repo.get_ic_by_ref(db, "")
        assert result is None
        db.execute.assert_not_called()


# ---------------------------------------------------------------------------
# get_ic_by_refs
# ---------------------------------------------------------------------------


class TestGetIcByRefsActiveFilter:
    @pytest.mark.asyncio
    async def test_query_includes_active_true_filter(self):
        """SQL must contain is_active=true when looking up by list of refs."""
        db = _make_db(scalars_all=[])
        await property_repo.get_ic_by_refs(db, ["REF001", "REF002"])
        sql = _get_compiled_sql(db)
        _assert_has_active_filter(sql)

    @pytest.mark.asyncio
    async def test_excludes_inactive_from_result_dict(self):
        """Dict must only contain properties returned by DB (active ones)."""
        active1 = _make_ic_property(infocasas_ref="REF001", active=True)
        active2 = _make_ic_property(infocasas_ref="REF002", active=True)
        # Simulate DB filtering: inactive REF003 not returned
        db = _make_db(scalars_all=[active1, active2])

        result = await property_repo.get_ic_by_refs(db, ["REF001", "REF002", "REF003"])

        assert "REF001" in result
        assert "REF002" in result
        assert "REF003" not in result
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_all_filtered_returns_empty_dict(self):
        """When DB returns nothing (all inactive), result is empty dict."""
        db = _make_db(scalars_all=[])
        result = await property_repo.get_ic_by_refs(db, ["REF001", "REF002"])
        assert result == {}

    @pytest.mark.asyncio
    async def test_short_circuits_on_empty_list(self):
        """Empty refs list must short-circuit before hitting DB."""
        db = _make_db(scalars_all=[])
        result = await property_repo.get_ic_by_refs(db, [])
        assert result == {}
        db.execute.assert_not_called()
