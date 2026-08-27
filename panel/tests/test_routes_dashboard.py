"""
Tests for app/routes/dashboard.py

Covers: authentication guard, response structure, HTMX partial,
DASH-01 actionable KPIs (links a tabs de /leads + funnel clickeable).
"""
import re

import pytest
from sqlalchemy import text


class TestDashboardAuth:
    async def test_unauthenticated_redirects_to_login(self, client):
        resp = await client.get("/dashboard")
        assert resp.status_code == 303
        assert "/login" in resp.headers["location"]

    async def test_admin_gets_200(self, admin_client):
        resp = await admin_client.get("/dashboard")
        assert resp.status_code == 200

    async def test_user_role_gets_403(self, user_client):
        """Desde el 2026-08-23 `/dashboard` pide `require_admin`, por decisión
        de Ez: los números del negocio son de la administradora, no de la cola de
        trabajo. Antes este test exigía un 200 — codificaba el permiso viejo."""
        resp = await user_client.get("/dashboard")
        assert resp.status_code == 403


class TestDashboardContent:
    async def test_contains_status_sections(self, admin_client):
        resp = await admin_client.get("/dashboard")
        content = resp.content
        # Dashboard shows status labels in Spanish, check for key sections
        text = content.decode('utf-8', errors='ignore').lower()
        assert "dashboard" in text or "panel" in text or "estadísticas" in text or "estado" in text

    async def test_no_excel_numbers_in_counters(self, admin_client):
        resp = await admin_client.get("/dashboard")
        # 10812 must not appear anywhere in the dashboard
        assert b"10812" not in resp.content

    async def test_htmx_partial_returns_200(self, admin_client):
        resp = await admin_client.get(
            "/dashboard",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200


class TestDashboardActionable:
    """DASH-01 — KPIs clickeables consistentes con los tabs de /leads.

    Todos los asserts usan el partial HTMX (HX-Request) para no matchear
    los links del sidebar de base.html por accidente.
    """

    @staticmethod
    async def _partial(admin_client) -> str:
        resp = await admin_client.get(
            "/dashboard", headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        return resp.text

    async def test_dashboard_kpis_link_to_leads_tabs(self, admin_client):
        html = await self._partial(admin_client)
        # KPIs existentes ahora clickeables
        # El primero decia «Total leads» y llevaba a /leads, que es la cola de
        # trabajo y cuenta otro universo. Es el total de contactos vivos, asi
        # que lleva a /contacts (carril I).
        assert 'href="/contacts"' in html                 # Total contactos
        assert 'href="/leads?tab=leads"' in html          # Nuevos Hoy
        assert 'href="/conversations"' in html            # Mensajes 24h
        assert 'href="/settings"' in html                 # Estado Bot
        # KPIs nuevos accionables (primera fila)
        assert 'href="/leads?tab=sin_respuesta"' in html  # Sin respuesta
        assert 'href="/leads?tab=interesados"' in html    # Interesados sin asignar

    async def test_dashboard_sin_respuesta_kpi_uses_tab_counter(
        self, admin_client, db,
    ):
        """El KPI 'Sin respuesta' muestra el counter del tab (count_leads_per_tab),
        NO el status_count global — regla anti-confusión de números."""
        from app.services.lead_service import lead_service
        expected = await lead_service.count_leads_per_tab(db)

        html = await self._partial(admin_client)
        m = re.search(
            r'data-kpi="sin_respuesta"[^>]*data-count="(\d+)"', html,
        )
        assert m, "KPI sin_respuesta con data-count no encontrado en el partial"
        assert int(m.group(1)) == expected["sin_respuesta"]

        m_int = re.search(
            r'data-kpi="interesados"[^>]*data-count="(\d+)"', html,
        )
        assert m_int, "KPI interesados con data-count no encontrado en el partial"
        assert int(m_int.group(1)) == expected["interesados"]

    async def test_dashboard_funnel_bars_clickable(self, admin_client):
        html = await self._partial(admin_client)
        for status in (
            "new", "bot_replied", "agent_replied", "interested",
            "closed", "no_response", "discarded",
        ):
            assert f'href="/contacts?status={status}"' in html, status
        # Regla anti-confusión: el funnel es universo global de contactos,
        # distinto a los tabs de /leads → retitulado.
        assert "Contactos por estado (todos)" in html
        assert "Funnel de Conversión" not in html
        # Barra no_response en ámbar (alerta accionable), no gris.
        assert "bg-amber-500" in html


class TestDashboardKpiRedesign:
    """Redesign sobrio de KPIs: urgencia con acento fino (borde izquierdo),
    nunca fondo ámbar entero ni pill gritón."""

    async def test_no_full_amber_background_card(self, admin_client):
        resp = await admin_client.get(
            "/dashboard", headers={"HX-Request": "true"},
        )
        html = resp.text
        assert "bg-amber-50 " not in html and 'bg-amber-50"' not in html
        assert "bg-amber-100" not in html  # pill 'requiere acción' eliminado
        # data-kpi attrs intactos (los usan estos mismos tests y el SSE)
        assert 'data-kpi="sin_respuesta"' in html
        assert 'data-kpi="interesados"' in html


class TestDashboardDemandSection:
    """Mini-análisis de demanda — sección 'Demanda (últimos 30 días)'.

    Server-rendered, sin chart libs: barras horizontales con width %.
    """

    @staticmethod
    async def _partial(admin_client) -> str:
        resp = await admin_client.get(
            "/dashboard", headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        return resp.text

    async def test_demand_section_present(self, admin_client):
        html = await self._partial(admin_client)
        assert "data-demand-section" in html
        assert "Demanda" in html
        assert "ltimos 30 d" in html  # "últimos 30 días" sin pelear encoding

    async def test_demand_sources_microcopy(self, admin_client):
        html = await self._partial(admin_client)
        # Total por fuente: IC vs bot — honestidad del dato
        assert "InfoCasas" in html
        assert "bot" in html

    async def test_demand_in_full_page_too(self, admin_client):
        resp = await admin_client.get("/dashboard")
        assert "data-demand-section" in resp.text


class TestDashboardVisualShapes:
    """DASH-02 — formas visuales server-rendered (sin chart libs):
    sparkline SVG de la serie mensual + donut conic-gradient Venta/Alquiler.
    """

    @staticmethod
    async def _partial(admin_client) -> str:
        resp = await admin_client.get(
            "/dashboard", headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        return resp.text

    async def test_sparkline_svg_present(self, admin_client):
        html = await self._partial(admin_client)
        assert "data-sparkline" in html
        assert "<svg" in html and "polyline" in html

    async def test_donut_conic_gradient_present(self, admin_client):
        html = await self._partial(admin_client)
        assert "data-donut" in html
        assert "conic-gradient" in html

    async def test_action_cards_white_minimal_orange_accent(self, admin_client, db):
        """Directiva: cards de acción blancas sin borde de acento ni fondo
        de color — solo 'Requiere acción' en naranja warning.

        Siembra su propio lead sin respuesta. El aviso naranja se renderiza solo
        con la cola en más de cero (`dashboard_stats.html`), así que el test
        dependía de lo que otro archivo hubiera dejado en la base del worker: si
        alguno asignaba o cambiaba de estado los `no_response`, el dashboard
        quedaba sin un solo `text-orange-` y esto fallaba por algo que no mide.
        Es el flaky TD-OPS-02, que el 20/08 dejó a staging un commit atrás.
        """
        await db.execute(
            text(
                "INSERT INTO contacts (phone, name, status, source) "
                "VALUES ('+595981777041', 'Pytest cola naranja', 'no_response', 'manual') "
                # el unique de phone es parcial (WHERE phone IS NOT NULL):
                # sin repetir el predicado, Postgres no encuentra el índice
                "ON CONFLICT (phone) WHERE phone IS NOT NULL DO UPDATE "
                "SET status = 'no_response', agent_user_id = NULL"
            )
        )
        await db.commit()

        html = await self._partial(admin_client)
        m = re.search(r'<a[^>]*data-kpi="sin_respuesta"[^>]*>', html)
        assert m, "card sin_respuesta no encontrada"
        tag = m.group(0)
        assert "border-l-amber" not in tag and "border-l-onnix-accent" not in tag
        assert "bg-white" in tag
        assert "text-orange-" in html  # 'Requiere acción' naranja warning

    async def test_funnel_semantic_green_only_closed(self, admin_client):
        """Verde SOLO para cerrados; nuevos azul; tasa de cierre neutra."""
        html = await self._partial(admin_client)

        def _bar_color(status: str) -> str:
            m = re.search(
                rf'href="/contacts\?status={status}".*?funnel-bar (bg-[\w-]+)',
                html, re.S,
            )
            assert m, f"barra del funnel para {status} no encontrada"
            return m.group(1)

        assert _bar_color("new") == "bg-blue-500"        # nuevos azul
        assert _bar_color("closed") == "bg-emerald-600"  # único verde
        for status in ("new", "bot_replied", "agent_replied", "interested",
                       "no_response", "discarded"):
            assert "emerald" not in _bar_color(status), status
        assert "text-onnix-accent num-format" not in html  # tasa de cierre no de acento


class TestDashboardNoRedundantHxGet:
    """Colateral: #stats-cards tenía hx-get sin hx-trigger → cualquier click
    dentro disparaba un GET redundante. El refresh SSE usa htmx.ajax con
    target/swap explícitos, así que el hx-get del div sobra."""

    async def test_stats_cards_has_no_bare_hx_get(self, admin_client):
        resp = await admin_client.get("/dashboard")
        html = resp.text
        assert 'id="stats-cards"' in html
        import re
        m = re.search(r'<div\s+id="stats-cards"[^>]*>', html)
        assert m, "contenedor stats-cards no encontrado"
        assert "hx-get" not in m.group(0)


class TestDashboardCacheHeaders:
    """Fix back-button roto: el partial HTMX comparte URL con la página
    completa (/dashboard). Sin Vary: HX-Request el browser cachea el
    partial para la URL y el botón Atrás muestra el partial sin layout.
    El partial además lleva Cache-Control: no-store (nunca cachearlo).
    """

    async def test_full_page_has_vary_hx_request(self, admin_client):
        resp = await admin_client.get("/dashboard")
        assert "HX-Request" in resp.headers.get("vary", "")

    async def test_partial_has_vary_hx_request(self, admin_client):
        resp = await admin_client.get(
            "/dashboard", headers={"HX-Request": "true"},
        )
        assert "HX-Request" in resp.headers.get("vary", "")

    async def test_partial_has_cache_control_no_store(self, admin_client):
        resp = await admin_client.get(
            "/dashboard", headers={"HX-Request": "true"},
        )
        assert resp.headers.get("cache-control") == "no-store"
