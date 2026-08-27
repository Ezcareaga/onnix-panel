"""Carril A4 — parametros que los `hx-*` pierden en el camino.

Tres bugs distintos con la misma forma: el atributo HTMX no arrastra algo
que el route necesita, asi que el server lo defaultea y la pantalla cambia
sola debajo del usuario.
"""
from __future__ import annotations

import json
import re

import pytest

from app.tz import get_templates


class TestBotHealthPollKeepsDays:
    """El poll de 30 s no mandaba `days`, y el route lo defaultea a 7.

    Estando en Detalle con 90 dias, a los 30 segundos la vista volvia sola
    a 7 sin que nadie tocara nada.
    """

    @pytest.mark.parametrize("days", [30, 90])
    async def test_el_poll_arrastra_days(self, admin_client, days):
        resp = await admin_client.get(f"/stats/health?tab=detalle&days={days}")
        assert resp.status_code == 200
        html = resp.text

        start = html.find('id="health-root"')
        assert start != -1, "no se encontro el contenedor del poll"
        end = html.find(">", start)
        bloque = html[start:end]

        assert f"days={days}" in bloque, (
            f"el hx-get del poll no manda days={days}: a los 30 s vuelve a 7"
        )

    @pytest.mark.parametrize("days", [30, 90])
    async def test_el_boton_de_refrescar_arrastra_days(self, admin_client, days):
        resp = await admin_client.get(f"/stats/health?tab=detalle&days={days}")
        html = resp.text
        # El boton "Actualizar" del encabezado del Resumen.
        assert f'hx-get="/stats/health?days={days}"' in html, (
            "el boton de refrescar manual tampoco arrastra days"
        )


class TestConversationsKeepChannelFilter:
    """El filtro de canal se borraba solo al buscar o al llegar un SSE.

    conversation_list.html:46 renderiza un <input name="channel"> justamente
    para que los hx-include lo levanten, pero ninguno de los dos lo incluia.
    """

    async def test_el_buscador_incluye_el_canal(self, admin_client):
        resp = await admin_client.get("/conversations")
        assert resp.status_code == 200
        html = resp.text

        start = html.find('name="q"')
        assert start != -1
        end = html.find(">", start)
        bloque = html[start:end]
        assert "[name='channel']" in bloque, (
            "el hx-include del buscador no arrastra el canal: buscar borra el filtro"
        )

    async def test_el_refresco_por_sse_incluye_el_canal(self, admin_client):
        resp = await admin_client.get("/conversations")
        html = resp.text

        start = html.find('id="conv-list"')
        assert start != -1
        end = html.find(">", start)
        bloque = html[start:end]
        assert "[name='channel']" in bloque, (
            "el hx-include del SSE no arrastra el canal: un mensaje nuevo borra el filtro"
        )


class TestLoadMoreHxValsIsValidJson:
    """`hx-vals` de «Cargar mas» armaba el JSON a mano con {{ q }} adentro.

    Una busqueda con comilla doble producia JSON invalido y el boton dejaba
    de funcionar. Se renderiza el parcial directo: hace falta has_more, que
    por ruta pediria mas de `limit` conversaciones con esa query.
    """

    _HX_VALS = re.compile(r"hx-vals='([^']*)'")

    def _render(self, q: str, channel: str = "whatsapp") -> str:
        env = get_templates().env
        return env.get_template("partials/conversation_list.html").render(
            conversations=[],
            offset=30,
            limit=30,
            channel=channel,
            q=q,
            has_more=True,
            selected_id=None,
        )

    @pytest.mark.parametrize(
        "q",
        [
            'casa "en" venta',
            "barrio o'higgins",
            "algo\\raro",
            "<script>alert(1)</script>",
        ],
    )
    def test_el_hx_vals_sigue_siendo_json_valido(self, q):
        html = self._render(q)
        vals = self._HX_VALS.findall(html)
        assert vals, "no se encontro el hx-vals de «Cargar mas»"
        for raw in vals:
            parsed = json.loads(raw)  # revienta si el atributo quedo roto
            assert parsed["offset"] == 60
        assert json.loads(vals[-1])["q"] == q, "la query se deformo en el viaje"
