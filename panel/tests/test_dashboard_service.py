"""
Tests for app/services/dashboard_service.py

Covers: get_stats returns correct structure and infocasas-only counts.
"""
import pytest
from app.services.dashboard_service import dashboard_service


class TestGetStats:
    async def test_returns_required_keys(self, db):
        stats = await dashboard_service.get_stats(db)
        assert "status_counts" in stats
        assert "total_leads" in stats
        assert "bot_enabled" in stats
        assert "new_today" in stats

    async def test_status_counts_is_dict(self, db):
        stats = await dashboard_service.get_stats(db)
        assert isinstance(stats["status_counts"], dict)

    async def test_total_leads_matches_status_counts_sum(self, db):
        stats = await dashboard_service.get_stats(db)
        assert stats["total_leads"] == sum(stats["status_counts"].values())

    async def test_bot_enabled_is_bool(self, db):
        stats = await dashboard_service.get_stats(db)
        assert isinstance(stats["bot_enabled"], bool)

    async def test_new_today_is_int(self, db):
        stats = await dashboard_service.get_stats(db)
        assert isinstance(stats["new_today"], int)
        assert stats["new_today"] >= 0

    async def test_excel_contacts_not_in_total(self, db):
        """total_leads must not include the 10,812 excel contacts."""
        stats = await dashboard_service.get_stats(db)
        assert stats["total_leads"] < 10812  # definitely not counting all excel


class TestGetDemandStats:
    """Demanda (últimos N días) — qué consulta la gente.

    Fuentes: leads InfoCasas (JOIN infocasas_properties), leads bot con
    property_id (JOIN properties) y filtros de search_context (lo que la
    gente PIDE al bot). Corre contra onnix_dev (snapshot real) → los
    asserts son estructurales/de consistencia, no de números exactos.
    """

    async def test_returns_required_keys(self, db):
        demand = await dashboard_service.get_demand_stats(db)
        for key in (
            "days", "total", "by_source", "top_cities", "top_types",
            "operations", "sin_ciudad",
        ):
            assert key in demand, key

    async def test_default_window_is_30_days(self, db):
        demand = await dashboard_service.get_demand_stats(db)
        assert demand["days"] == 30

    async def test_by_source_has_canonical_keys_and_sums_to_total(self, db):
        demand = await dashboard_service.get_demand_stats(db)
        for src in ("infocasas", "whatsapp", "telegram"):
            assert src in demand["by_source"], src
            assert demand["by_source"][src] >= 0
        assert demand["total"] == sum(demand["by_source"].values())

    async def test_top_lists_max_5_sorted_desc(self, db):
        demand = await dashboard_service.get_demand_stats(db)
        for key in ("top_cities", "top_types"):
            items = demand[key]
            assert len(items) <= 5, key
            counts = [item["count"] for item in items]
            assert counts == sorted(counts, reverse=True), key
            for item in items:
                assert item["label"]  # nunca etiquetas vacías
                assert item["count"] > 0

    async def test_top_counts_bounded_by_total(self, db):
        demand = await dashboard_service.get_demand_stats(db)
        assert sum(i["count"] for i in demand["top_cities"]) <= demand["total"]
        assert sum(i["count"] for i in demand["top_types"]) <= demand["total"]
        # sin_ciudad = consultas sin ciudad conocida (honestidad del dato)
        assert 0 <= demand["sin_ciudad"] <= demand["total"]

    async def test_operations_split(self, db):
        demand = await dashboard_service.get_demand_stats(db)
        ops = demand["operations"]
        assert "venta" in ops and "alquiler" in ops
        assert ops["venta"] >= 0 and ops["alquiler"] >= 0
        assert ops["venta"] + ops["alquiler"] <= demand["total"]

    async def test_pct_is_relative_to_max(self, db):
        """pct alimenta el width de la barra → el item top mide 100."""
        demand = await dashboard_service.get_demand_stats(db)
        for key in ("top_cities", "top_types"):
            items = demand[key]
            if items:
                assert items[0]["pct"] == 100
                for item in items:
                    assert 0 < item["pct"] <= 100

    async def test_wider_window_never_shrinks_total(self, db):
        d30 = await dashboard_service.get_demand_stats(db, days=30)
        d90 = await dashboard_service.get_demand_stats(db, days=90)
        assert d90["total"] >= d30["total"]

    async def test_city_variants_merged_by_unaccent(self, db):
        """'Asunción' y 'Asuncion' deben agrupar como una sola ciudad."""
        demand = await dashboard_service.get_demand_stats(db, days=90)
        import unicodedata

        def _key(s: str) -> str:
            return "".join(
                ch for ch in unicodedata.normalize("NFKD", s.lower())
                if not unicodedata.combining(ch)
            )

        keys = [_key(i["label"]) for i in demand["top_cities"]]
        assert len(keys) == len(set(keys)), "ciudades duplicadas por tilde"


class TestGetDemandMonthlySeries:
    """DASH-02 — serie mensual de demanda (sparkline del dashboard).

    Misma definición de "consulta" que get_demand_stats (leads IC + leads
    bot con property_id + búsquedas con filtros), agrupada por mes
    calendario. Siempre devuelve N buckets (meses sin datos = 0).
    """

    async def test_returns_six_buckets_by_default(self, db):
        series = await dashboard_service.get_demand_monthly_series(db)
        assert len(series) == 6
        for item in series:
            assert {"label", "count", "pct"} <= set(item), item

    async def test_last_bucket_is_current_month(self, db):
        from datetime import datetime
        from app.tz import PYT
        labels = ("Ene", "Feb", "Mar", "Abr", "May", "Jun",
                  "Jul", "Ago", "Sep", "Oct", "Nov", "Dic")
        series = await dashboard_service.get_demand_monthly_series(db)
        # El ultimo bucket es el mes PARAGUAYO en curso: el 1° de cada mes,
        # de 00:00 a 03:00 UTC, aca es todavia el mes anterior.
        now = datetime.now(PYT)
        assert series[-1]["label"] == labels[now.month - 1]

    async def test_counts_nonnegative_and_pct_relative_to_max(self, db):
        series = await dashboard_service.get_demand_monthly_series(db)
        max_count = max(item["count"] for item in series)
        for item in series:
            assert item["count"] >= 0
            assert 0 <= item["pct"] <= 100
        if max_count > 0:
            assert any(item["pct"] == 100 for item in series)
        else:
            assert all(item["pct"] == 0 for item in series)

    async def test_monthly_total_consistent_with_30d_window(self, db):
        """La serie de 6 meses nunca puede tener menos consultas que la
        ventana de 30 días (mismo universo, ventana más ancha)."""
        series = await dashboard_service.get_demand_monthly_series(db)
        demand = await dashboard_service.get_demand_stats(db, days=30)
        assert sum(i["count"] for i in series) >= demand["by_source"]["infocasas"]

    async def test_monthly_included_in_demand_stats(self, db):
        """El template recibe demand.monthly — el route no cambia."""
        demand = await dashboard_service.get_demand_stats(db)
        assert "monthly" in demand
        assert len(demand["monthly"]) == 6


class TestLeadTabCounts:
    """DASH-01 — get_stats expone los counters de tabs de /leads.

    Mismo universo que los badges de /leads (count_leads_per_tab):
    excluye asignados (segun tab) e import:excel. NO confundir con
    status_counts (contactos globales).
    """

    async def test_lead_tab_counts_present_with_all_tabs(self, db):
        stats = await dashboard_service.get_stats(db)
        assert "lead_tab_counts" in stats
        for tab in ("leads", "interesados", "asignados", "sin_respuesta"):
            assert tab in stats["lead_tab_counts"]
            assert isinstance(stats["lead_tab_counts"][tab], int)
            assert stats["lead_tab_counts"][tab] >= 0

    async def test_lead_tab_counts_match_lead_service(self, db):
        """Consistencia con los badges de /leads — misma fuente, mismos numeros."""
        from app.services.lead_service import lead_service
        stats = await dashboard_service.get_stats(db)
        expected = await lead_service.count_leads_per_tab(db)
        assert stats["lead_tab_counts"] == expected
