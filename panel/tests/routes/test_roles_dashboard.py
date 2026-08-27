"""El dashboard y los stats son de la administradora, no del asesor.

Decision de Ez del 2026-08-23. El asesor trabaja su cola —Conversaciones,
Leads, Propiedades, Contactos— y los numeros del negocio no le dicen nada que
pueda accionar.

**Lo que hace que esto sea mas que esconder un link:** el login mandaba a TODOS
a `/dashboard`. Cerrar la ruta sin tocar el redirect le pone un 403 al asesor
en la cara justo despues de escribir su contraseña. Los dos cambios van juntos
o el cambio es un bug.

Y hay tres puertas a la misma pantalla —el link del sidebar, el redirect del
login y el `/` pelado—, asi que las tres tienen su test. Esconder el link sin
cerrar la ruta es una puerta con cortina.
"""
from __future__ import annotations

import pytest

RUTAS_SOLO_ADMIN = ["/dashboard", "/stats", "/stats/health"]


class TestLasRutasEstanCerradas:
    @pytest.mark.parametrize("ruta", RUTAS_SOLO_ADMIN)
    async def test_el_asesor_recibe_403(self, agent_client, ruta):
        resp = await agent_client.get(ruta)
        assert resp.status_code == 403, (
            f"{ruta} le contesto {resp.status_code} a un asesor: la ruta "
            "quedo abierta"
        )

    @pytest.mark.parametrize("ruta", RUTAS_SOLO_ADMIN)
    async def test_el_admin_sigue_entrando(self, admin_client, ruta):
        """La contracara: un guard que bloquea a todos tambien pasaria el test
        de arriba."""
        resp = await admin_client.get(ruta)
        assert resp.status_code == 200, f"{ruta} se le cerro tambien al admin"


class TestElAsesorNoLlegaPorLaPuertaDeAtras:
    async def test_el_sidebar_no_le_muestra_los_links(self, agent_client):
        html = (await agent_client.get("/leads")).text
        for ruta in RUTAS_SOLO_ADMIN:
            assert f'href="{ruta}"' not in html, (
                f"el sidebar le sigue mostrando {ruta} al asesor"
            )

    async def test_el_sidebar_si_se_los_muestra_al_admin(self, admin_client):
        html = (await admin_client.get("/leads")).text
        for ruta in RUTAS_SOLO_ADMIN:
            assert f'href="{ruta}"' in html, f"el admin perdio {ruta} del menu"

    async def test_la_raiz_manda_al_asesor_a_su_cola(self, agent_client):
        resp = await agent_client.get("/", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/leads"

    async def test_la_raiz_manda_al_admin_al_dashboard(self, admin_client):
        resp = await admin_client.get("/", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/dashboard"


class TestElDestinoDespuesDelLogin:
    """Sin esto, cerrar la ruta convierte el login del asesor en un 403."""

    def test_el_asesor_va_a_leads(self):
        from app.routes.auth import _inicio_para

        assert _inicio_para("agent") == "/leads"

    def test_el_admin_va_al_dashboard(self):
        from app.routes.auth import _inicio_para

        assert _inicio_para("admin") == "/dashboard"

    @pytest.mark.parametrize("role", [None, "", "user", "cualquier_cosa"])
    def test_lo_desconocido_no_cae_en_el_dashboard(self, role):
        """Default seguro: si el rol no es admin, no va a una pantalla que le
        va a devolver 403."""
        from app.routes.auth import _inicio_para

        assert _inicio_para(role) != "/dashboard"
