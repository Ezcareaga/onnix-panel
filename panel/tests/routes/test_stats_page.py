"""
Tests for GET /stats — Stats v2: la casa del análisis.

Cubre: selector de período (30/90/365 whitelisted, default 30), sección
Demanda (partial compartido con dashboard), gap oferta/demanda ("Qué piden
vs qué tenemos") y tendencia mensual (sparkline con counts visibles).
"""
import pytest


async def _page(admin_client, query=""):
    resp = await admin_client.get(f"/stats{query}")
    assert resp.status_code == 200
    return resp.text


class TestStatsAuth:
    async def test_unauthenticated_redirects_to_login(self, client):
        resp = await client.get("/stats")
        assert resp.status_code == 303
        assert "/login" in resp.headers["location"]

    async def test_admin_gets_200(self, admin_client):
        resp = await admin_client.get("/stats")
        assert resp.status_code == 200


class TestStatsPeriodSelector:
    async def test_default_is_30_days(self, admin_client):
        html = await _page(admin_client)
        assert "ltimos 30 d" in html  # demanda · últimos 30 días

    async def test_365_days_accepted(self, admin_client):
        html = await _page(admin_client, "?days=365")
        assert "ltimos 365 d" in html

    async def test_90_days_accepted(self, admin_client):
        html = await _page(admin_client, "?days=90")
        assert "ltimos 90 d" in html

    async def test_invalid_days_falls_back_to_30(self, admin_client):
        for bad in ("?days=7", "?days=50", "?days=-1"):
            html = await _page(admin_client, bad)
            assert "ltimos 30 d" in html, bad

    async def test_selector_links_present(self, admin_client):
        html = await _page(admin_client)
        for d in (30, 90, 365):
            assert f"?days={d}" in html, d


class TestStatsDemandSection:
    """Misma sección Demanda del dashboard via partial compartido."""

    async def test_demand_section_present(self, admin_client):
        html = await _page(admin_client)
        assert "data-demand-section" in html
        assert "InfoCasas" in html

    async def test_visual_shapes_present(self, admin_client):
        html = await _page(admin_client)
        assert "data-sparkline" in html
        assert "<svg" in html and "polyline" in html  # tendencia 6 meses
        assert "data-donut" in html and "conic-gradient" in html  # operación


class TestStatsGapSection:
    """Gap oferta/demanda — tabla 'Qué piden vs qué tenemos'."""

    async def test_gap_section_present(self, admin_client):
        html = await _page(admin_client)
        assert "data-gap-section" in html
        assert "piden" in html  # "Qué piden vs qué tenemos"

    async def test_gap_table_columns(self, admin_client):
        html = await _page(admin_client)
        assert "Demanda" in html
        assert "Stock activo" in html

    async def test_gap_honest_matching_note(self, admin_client):
        """Documentar el match aproximado de tipos IC ↔ slugs properties."""
        html = await _page(admin_client)
        assert "aproximaci" in html.lower() or "parcial" in html.lower()


class TestStatsHtmxPartial:
    """El refresh HTMX cada 60s sigue devolviendo solo los counters."""

    async def test_partial_returns_200_without_demand_section(self, admin_client):
        resp = await admin_client.get(
            "/stats", headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "data-demand-section" not in resp.text

    async def test_legacy_counters_still_render_in_full_page(self, admin_client):
        html = await _page(admin_client)
        assert "Leads por Fuente" in html
        assert "Tasa de Conversi" in html
