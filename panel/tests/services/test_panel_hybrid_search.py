"""TDD — panel_hybrid_search service (M6.5 T2)

Modo IA del buscador del panel: SQL ids + pgvector ids fusionados con RRF,
paginación en memoria, degradación silenciosa sin Gemini.

Pure unit tests: property_repo, VectorSearch y _get_gemini se mockean,
no se toca DB ni APIs externas.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.search.hybrid_search import reciprocal_rank_fusion
from app.services import panel_hybrid_search
from app.services.property_service import PropertyFilters

_MOD = "app.services.panel_hybrid_search"


def _rows_for(ids: list[int]) -> list[dict]:
    return [{"id": i, "title": f"Prop {i}"} for i in ids]


def _patch_repo(sql_ids: list[int]):
    """Patch list_ids_with_filters + list_by_ids (echoes requested order)."""
    list_ids = AsyncMock(return_value=list(sql_ids))
    list_by_ids = AsyncMock(side_effect=lambda db, ids: _rows_for(ids))
    return (
        patch(f"{_MOD}.property_repo.list_ids_with_filters", list_ids),
        patch(f"{_MOD}.property_repo.list_by_ids", list_by_ids),
        list_ids,
        list_by_ids,
    )


class TestIaModeUsesPgvector:
    async def test_panel_search_uses_pgvector_in_ia_mode(self):
        sql_ids = [1, 2, 3]
        vector_ids = [3, 4, 5]
        p_ids, p_by_ids, _, list_by_ids = _patch_repo(sql_ids)

        vs_instance = MagicMock()
        vs_instance.search = AsyncMock(return_value=vector_ids)

        with p_ids, p_by_ids, \
                patch(f"{_MOD}._get_gemini", return_value=MagicMock()), \
                patch(f"{_MOD}.VectorSearch", return_value=vs_instance):
            rows, total = await panel_hybrid_search.search(
                AsyncMock(), PropertyFilters(), "casa luminosa con jardin"
            )

        vs_instance.search.assert_awaited_once()
        expected = reciprocal_rank_fusion(sql_ids, vector_ids)
        assert [r["id"] for r in rows] == expected
        assert total == len(expected)
        # list_by_ids recibió los ids en el orden fusionado
        assert list_by_ids.await_args.args[1] == expected


class TestPublicPathOnHybridRows:
    async def test_hybrid_rows_include_public_path(self):
        """M6.5 — las filas hidratadas con list_by_ids también pasan por
        _compute_public_path (mismo helper que el listado clásico)."""
        eligible = {
            "id": 1, "source": "remax", "is_active": True, "on_hold": False,
            "title": "Casa Linda", "city": "Luque",
        }
        ineligible = {
            "id": 2, "source": "infocasas", "is_active": True, "on_hold": False,
            "title": "Depto", "city": "Asuncion",
        }
        list_ids = AsyncMock(return_value=[1, 2])
        list_by_ids = AsyncMock(return_value=[eligible, ineligible])

        with patch(f"{_MOD}.property_repo.list_ids_with_filters", list_ids), \
                patch(f"{_MOD}.property_repo.list_by_ids", list_by_ids), \
                patch(f"{_MOD}._get_gemini", return_value=None):
            rows, total = await panel_hybrid_search.search(
                AsyncMock(), PropertyFilters(), "casa linda en luque"
            )

        assert total == 2
        assert rows[0]["public_path"] == "/prop/1-casa-linda-luque"
        assert rows[1]["public_path"] is None


class TestDegradation:
    async def test_ia_mode_degrades_without_gemini(self):
        sql_ids = [10, 20, 30]
        p_ids, p_by_ids, _, _ = _patch_repo(sql_ids)
        vs_cls = MagicMock()

        with p_ids, p_by_ids, \
                patch(f"{_MOD}._get_gemini", return_value=None), \
                patch(f"{_MOD}.VectorSearch", vs_cls):
            rows, total = await panel_hybrid_search.search(
                AsyncMock(), PropertyFilters(), "casa con quincho"
            )

        vs_cls.assert_not_called()
        assert [r["id"] for r in rows] == sql_ids
        assert total == 3

    async def test_ia_mode_degrades_on_vector_exception(self, caplog):
        sql_ids = [7, 8]
        p_ids, p_by_ids, _, _ = _patch_repo(sql_ids)

        vs_instance = MagicMock()
        vs_instance.search = AsyncMock(side_effect=RuntimeError("boom"))

        with p_ids, p_by_ids, \
                patch(f"{_MOD}._get_gemini", return_value=MagicMock()), \
                patch(f"{_MOD}.VectorSearch", return_value=vs_instance), \
                caplog.at_level(logging.WARNING, logger=_MOD):
            rows, total = await panel_hybrid_search.search(
                AsyncMock(), PropertyFilters(), "casa con quincho"
            )

        assert [r["id"] for r in rows] == sql_ids
        assert total == 2
        assert any(rec.levelno == logging.WARNING for rec in caplog.records)

    async def test_ia_mode_skips_vector_for_trivial_query(self):
        sql_ids = [1, 2]
        p_ids, p_by_ids, _, _ = _patch_repo(sql_ids)
        vs_cls = MagicMock()
        gemini = MagicMock()

        with p_ids, p_by_ids, \
                patch(f"{_MOD}._get_gemini", return_value=gemini), \
                patch(f"{_MOD}.VectorSearch", vs_cls):
            rows, total = await panel_hybrid_search.search(
                AsyncMock(), PropertyFilters(), "3d"
            )

        vs_cls.assert_not_called()
        assert [r["id"] for r in rows] == sql_ids


class TestPagination:
    async def test_ia_mode_pagination_in_memory(self):
        sql_ids = list(range(1, 121))  # 120 fused ids
        p_ids, p_by_ids, _, list_by_ids = _patch_repo(sql_ids)

        with p_ids, p_by_ids, patch(f"{_MOD}._get_gemini", return_value=None):
            rows, total = await panel_hybrid_search.search(
                AsyncMock(), PropertyFilters(), "casa amplia",
                page=2, per_page=50,
            )

        assert total == 120
        assert [r["id"] for r in rows] == list(range(51, 101))
        assert list_by_ids.await_args.args[1] == list(range(51, 101))


class TestPfPrefix:
    def test_pf_prefix_no_param_collision(self):
        filters = PropertyFilters(
            city="Asunción",
            price_min=Decimal("50000"),
            price_max=Decimal("120000"),
        )
        extra_where, extra_params, cte_sql = (
            panel_hybrid_search._build_vector_filter(filters)
        )
        assert extra_params, "filtros con city+price deben producir params"
        assert all(k.startswith("pf_") for k in extra_params)
        where_str = " ".join(extra_where)
        assert ":pf_city" in where_str
        assert ":pf_price_min" in where_str
        assert ":pf_price_max" in where_str
        # ningún placeholder sin prefijo (evita colisión con :query_embedding)
        import re
        bare = set(re.findall(r"(?<!:):([a-zA-Z_]\w*)", where_str))
        assert all(name.startswith("pf_") for name in bare)
        assert cte_sql is None  # sin barato no hay CTE

    def test_pf_prefix_barato_cte_passed_prefixed(self):
        filters = PropertyFilters(city="Luque", barato=True)
        extra_where, extra_params, cte_sql = (
            panel_hybrid_search._build_vector_filter(filters)
        )
        assert cte_sql is not None and cte_sql.startswith("WITH")
        assert ":pf_p25_city" in cte_sql
        assert "p25_city" not in {k for k in extra_params if not k.startswith("pf_")}
        assert all(k.startswith("pf_") for k in extra_params)
        where_str = " ".join(extra_where)
        assert "SELECT v FROM p25" in where_str
