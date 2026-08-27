"""TDD — property_repo.list_ids_with_filters / list_by_ids (M6.5 T2)

Pure unit tests con sesión mockeada (mismo patrón que
test_property_repo_filters.py): se inspecciona el SQL pasado a execute.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.repositories.property_repo import PropertyRepository
from app.services.property_service import PropertyFilters


def _make_db(rows=None):
    db = AsyncMock()
    mock_result = MagicMock()
    mock_mappings = MagicMock()
    mock_mappings.all.return_value = rows or []
    mock_result.mappings.return_value = mock_mappings
    db.execute = AsyncMock(return_value=mock_result)
    return db


def _executed_sql(db) -> str:
    args, _ = db.execute.call_args
    return str(args[0])


def _executed_params(db) -> dict:
    args, _ = db.execute.call_args
    return args[1]


class TestListIdsWithFilters:
    async def test_list_ids_with_filters_respects_filters(self):
        db = _make_db()
        filters = PropertyFilters(city="Asunción", operation="venta")
        await PropertyRepository.list_ids_with_filters(db, filters, limit=100)
        sql = _executed_sql(db)
        params = _executed_params(db)
        assert sql.strip().startswith("SELECT id")
        assert "duplicate_of IS NULL" in sql
        assert "unaccent(city) ILIKE unaccent(:city)" in sql
        assert "operation = :operation" in sql
        assert "LIMIT :limit" in sql
        assert "OFFSET" not in sql
        assert params["limit"] == 100
        assert params["city"] == "%Asunción%"

    async def test_list_ids_with_filters_order_matches_list_with_filters(self):
        """El nombre dice «matches» y hasta el 2026-08-24 no comparaba nada.

        Assertaba dos literales —`ORDER BY updated_at DESC` y `ORDER BY
        created_at DESC`— contra `list_ids_with_filters` sola. Con eso, cambiar
        el orden de UNA de las dos funciones lo dejaba verde mientras el literal
        siguiera adentro, y cambiarlo en las dos a la vez lo ponia rojo aunque
        siguieran de acuerdo. Justo al reves de lo que su nombre promete.

        El invariante real es que las dos ordenen IGUAL: `list_ids_with_filters`
        es la pata SQL de la fusion RRF, y si ordena distinto que
        `list_with_filters` la fusion mezcla dos listas que no son la misma.
        Ahora se comparan.
        """
        def _order_by(sql: str) -> str:
            i = sql.index("ORDER BY")
            fin = sql.find(" LIMIT", i)
            return sql[i:fin if fin != -1 else len(sql)].strip()

        for state in ("inactive", "active", None):
            db_ids = _make_db()
            await PropertyRepository.list_ids_with_filters(
                db_ids, PropertyFilters(state=state), limit=50
            )
            db_rows = _make_db()
            await PropertyRepository.list_with_filters(
                db_rows, PropertyFilters(state=state), limit=50, offset=0
            )
            orden_ids = _order_by(_executed_sql(db_ids))
            orden_rows = _order_by(_executed_sql(db_rows))
            assert orden_ids == orden_rows, (
                f"state={state!r}: la pata de ids ordena `{orden_ids}` y la de "
                f"filas `{orden_rows}`. La fusion RRF mezcla dos listas que no "
                "son la misma"
            )

        # Y que el criterio de fecha siga estando, ademas de coincidir: dos
        # funciones pueden estar de acuerdo en un orden equivocado.
        db_inact = _make_db()
        await PropertyRepository.list_ids_with_filters(
            db_inact, PropertyFilters(state="inactive"), limit=50
        )
        assert "updated_at DESC" in _executed_sql(db_inact)

        db_act = _make_db()
        await PropertyRepository.list_ids_with_filters(
            db_act, PropertyFilters(state="active"), limit=50
        )
        assert "created_at DESC" in _executed_sql(db_act)

    async def test_list_ids_with_filters_applies_barato_cte(self):
        db = _make_db()
        await PropertyRepository.list_ids_with_filters(
            db, PropertyFilters(barato=True), limit=50
        )
        sql = _executed_sql(db)
        assert sql.startswith("WITH p25")
        assert "price_usd <= (SELECT v FROM p25)" in sql

    async def test_list_ids_with_filters_returns_id_list(self):
        rows = [{"id": 5}, {"id": 9}, {"id": 1}]
        db = _make_db(rows=rows)
        ids = await PropertyRepository.list_ids_with_filters(
            db, PropertyFilters(), limit=50
        )
        assert ids == [5, 9, 1]


class TestListByIds:
    async def test_list_by_ids_preserves_order(self):
        # DB devuelve en orden arbitrario; el repo debe reordenar según ids
        rows = [{"id": 2, "title": "b"}, {"id": 7, "title": "c"}, {"id": 4, "title": "a"}]
        db = _make_db(rows=rows)
        result = await PropertyRepository.list_by_ids(db, [7, 4, 2])
        assert [r["id"] for r in result] == [7, 4, 2]
        sql = _executed_sql(db)
        assert "id = ANY(:ids)" in sql

    async def test_list_by_ids_empty_returns_empty(self):
        db = _make_db()
        result = await PropertyRepository.list_by_ids(db, [])
        assert result == []
        db.execute.assert_not_awaited()
