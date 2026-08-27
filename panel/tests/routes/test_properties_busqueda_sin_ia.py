"""TDD — la búsqueda de propiedades cuando la IA no puede responder.

`/api/properties/parse-query` es una llamada a Haiku que convierte texto libre
en JSON de filtros. Con la key sin renovar devuelve `{"parsed": null}` y el
front cae a `?search_text=` (ILIKE sobre título y external_id).

Ese camino degradado **ya funcionaba**; lo que faltaba es que se entienda. Sin
aviso, la administradora escribe «casa 3 dormitorios en Lambaré hasta 150 mil», recibe
una lista que no tiene nada que ver, y no tiene forma de saber por qué. El
`CLAUDE.md` prohíbe mostrarle un error técnico — pero quedarse callada no es la
alternativa.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

_GET_PROPERTIES = "app.routes.properties.property_service.get_properties"
_CHATBOT_PARSE = "app.routes.properties.property_chatbot.parse"


def _patch_service():
    return patch(_GET_PROPERTIES, new=AsyncMock(return_value=([], 0)))


class TestAvisoDeIaNoDisponible:
    async def test_ia_unavailable_muestra_el_aviso_en_palabras(self, admin_client):
        with _patch_service():
            resp = await admin_client.get(
                "/properties?search_text=casa+en+lambare&ia_unavailable=1"
            )
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "no pudo interpretar" in body
        # Y el texto que escribió, para que sepa qué se buscó en su lugar.
        assert "casa en lambare" in body

    async def test_el_aviso_usa_el_parcial_unico_de_mensajes(self, admin_client):
        """`ui.md`: un solo patrón para avisos. El parcial es
        `partials/error_message.html`, que en nivel warning marca
        `error-msg--warning`."""
        with _patch_service():
            resp = await admin_client.get(
                "/properties?search_text=casa&ia_unavailable=1"
            )
        assert "error-msg--warning" in resp.content.decode()

    async def test_sin_la_marca_no_hay_aviso(self, admin_client):
        with _patch_service():
            resp = await admin_client.get("/properties?search_text=casa")
        assert resp.status_code == 200
        assert "no pudo interpretar" not in resp.content.decode()

    async def test_el_front_marca_la_url_cuando_la_ia_falla(self, admin_client):
        """El fallback del buscador IA redirige a `?search_text=`; tiene que
        llevar la marca, si no la pantalla no puede decir nada."""
        with _patch_service():
            resp = await admin_client.get("/properties")
        assert "ia_unavailable=1" in resp.content.decode()


class TestElFormularioNoDependeDeClaude:
    async def test_todos_los_filtros_llegan_al_servicio_sin_tocar_la_ia(
        self, admin_client, monkeypatch
    ):
        """El listado es SQL puro: ni con la key vacía puede llamar a Claude.

        Es lo que hace que el modo degradado sirva de verdad y no sólo no
        rompa: tipo, operación, ciudad, barrio, precio, dormitorios, baños,
        portal, estado de obra y fecha andan igual sin token.
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        captured = {}

        async def fake_get_properties(db, filters, page, per_page):
            captured["filters"] = filters
            return ([], 0)

        def boom(*_a, **_kw):  # pragma: no cover — sólo corre si hay bug
            raise AssertionError("el listado no puede llamar al chatbot")

        with (
            patch(_GET_PROPERTIES, side_effect=fake_get_properties),
            patch(_CHATBOT_PARSE, side_effect=boom),
        ):
            resp = await admin_client.get(
                "/properties?property_type=casa&operation=venta&city=lambare"
                "&neighborhood=santa+ana&price_min=10000&price_max=150000"
                "&bedrooms_min=3&bathrooms_min=2&source=remax"
                "&construction_state=terminado&updated_within_days=30"
                "&search_text=quincho"
            )

        assert resp.status_code == 200
        f = captured["filters"]
        assert f.property_type == "casa"
        assert f.operation == "venta"
        assert f.city == "lambare"
        assert f.neighborhood == "santa ana"
        assert int(f.price_min) == 10000
        assert int(f.price_max) == 150000
        assert f.bedrooms_min == 3
        assert f.bathrooms_min == 2
        assert f.source == "remax"
        assert f.construction_state == "terminado"
        assert f.updated_within_days == 30
        assert f.search_text == "quincho"
