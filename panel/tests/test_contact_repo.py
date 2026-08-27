"""
Tests for app/repositories/contact_repo.py

Covers: count_by_status, get_hot_leads, count_today — all filtered to
        exclude import:excel. Also covers count_by_source and weekly_evolution.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories.contact_repo import contact_repo


class TestCountByStatus:
    async def test_returns_dict(self, db):
        counts = await contact_repo.count_by_status(db)
        assert isinstance(counts, dict)

    async def test_excel_contacts_excluded(self, db):
        """import:excel contacts must NOT appear in the count."""
        from app.repositories.contact_repo import contact_repo as cr
        counts = await cr.count_by_status(db)
        sources = await cr.count_by_source(db)
        excel_count = sources.get("import:excel", 0)
        if excel_count > 0:
            # count_by_status excludes import:excel so totals must differ
            total_by_status = sum(counts.values())
            total_by_source = sum(sources.values())
            assert total_by_status < total_by_source

    async def test_statuses_are_strings(self, db):
        counts = await contact_repo.count_by_status(db)
        for status, count in counts.items():
            assert isinstance(status, str)
            assert isinstance(count, int)


class TestGetHotLeads:
    async def test_returns_list(self, db):
        leads = await contact_repo.get_hot_leads(db)
        assert isinstance(leads, list)

    async def test_no_excel_leads(self, db):
        leads = await contact_repo.get_hot_leads(db)
        for lead in leads:
            assert lead.source != "import:excel"

    async def test_all_leads_have_actionable_status(self, db):
        leads = await contact_repo.get_hot_leads(db)
        actionable = {"interested", "visit_scheduled", "new"}
        for lead in leads:
            assert lead.status in actionable

    async def test_respects_limit(self, db):
        leads = await contact_repo.get_hot_leads(db, limit=5)
        assert len(leads) <= 5


class TestCountToday:
    async def test_returns_integer(self, db):
        count = await contact_repo.count_today(db)
        assert isinstance(count, int)
        assert count >= 0

    async def test_excel_contacts_excluded(self, db):
        """The 10,812 imported excel contacts must not inflate today's count.

        If excel contacts were leaking, the count would be ~10,812.
        A threshold of 5,000 is safe and survives high-traffic days.
        """
        count = await contact_repo.count_today(db)
        assert count < 5000  # would be ~10,812 if excel contacts leaked through


class TestCountBySource:
    async def test_returns_dict(self, db):
        counts = await contact_repo.count_by_source(db)
        assert isinstance(counts, dict)

    async def test_excel_source_present(self, db):
        counts = await contact_repo.count_by_source(db)
        assert "import:excel" in counts
        assert counts.get("import:excel", 0) > 0


class TestWeeklyEvolution:
    async def test_returns_list(self, db):
        result = await contact_repo.weekly_evolution(db)
        assert isinstance(result, list)

    async def test_max_7_days(self, db):
        result = await contact_repo.weekly_evolution(db)
        assert len(result) <= 7


# ---------------------------------------------------------------------------
# CLEAN-07: SQL introspection — assert get_hot_leads IN filter no longer
# references the obsolete 'negotiation' status. Pattern cribbed from
# panel/tests/test_ic_repo_active_filter.py:62-72.
# ---------------------------------------------------------------------------


def _get_compiled_sql(db_mock: AsyncMock) -> str:
    """Extract the compiled SQL string of the last db.execute() call."""
    call = db_mock.execute.call_args
    assert call is not None, "db.execute() was not called"
    stmt = call.args[0]
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


async def test_get_hot_leads_query_excludes_negotiation():
    """get_hot_leads must NOT include 'negotiation' in its IN filter (CLEAN-07).

    'visit_scheduled' STAYS in the filter — M6.0 does not touch it (M6.2 territory).
    """
    result_mock = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = []
    result_mock.scalars.return_value = scalars_mock

    db = AsyncMock()
    db.execute = AsyncMock(return_value=result_mock)

    await contact_repo.get_hot_leads(db)

    sql = _get_compiled_sql(db).lower()
    assert "'negotiation'" not in sql            # FAIL pre-fix
    assert "'interested'" in sql
    assert "'visit_scheduled'" in sql            # SE QUEDA (M6.0 no lo toca)
    assert "'new'" in sql


# ---------------------------------------------------------------------------
# Carril I — «Total leads» contaba los eliminados y el embudo no.
#
# count_by_status alimenta los dos numeros del dashboard: el KPI de arriba
# suma el dict entero, el pie del embudo suma solo las filas que el embudo
# lista. Con `deleted` adentro del dict, el de arriba salia mas grande sin
# que nada en la pantalla lo explicara.
# ---------------------------------------------------------------------------


async def test_count_by_status_deja_afuera_a_los_eliminados():
    """`deleted` es el sentinel del borrado blando (regla 3), no un estado."""
    result_mock = MagicMock()
    result_mock.all.return_value = []

    db = AsyncMock()
    db.execute = AsyncMock(return_value=result_mock)

    await contact_repo.count_by_status(db)

    sql = _get_compiled_sql(db).lower()
    assert "contacts.status != 'deleted'" in sql, (
        f"count_by_status no excluye el sentinel de borrado. SQL: {sql}"
    )
    assert "contacts.source != 'import:excel'" in sql, (
        "se perdio el filtro de import:excel al agregar el de deleted"
    )
