"""TDD — chips de filtros activos en la pagina /properties.

El modo BUSCADOR del sidebar muestra una linea de chips con los filtros
detectados, cada uno con un boton X que lo elimina. Los chips se construyen
en el backend a partir de los query params para que la URL siga siendo la
fuente de verdad.

Cada chip es un dict {"param": str, "label": str, "value": str}.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest


def _patch_service():
    """Helper: patch property_service so we never touch the DB."""
    return patch(
        "app.routes.properties.property_service.get_properties",
        new=AsyncMock(return_value=([], 0)),
    )


class TestActiveChipsBuilding:
    async def test_no_filters_yields_no_chips(self, admin_client):
        with _patch_service():
            resp = await admin_client.get("/properties")
        assert resp.status_code == 200
        # Sin filtros -> no chip element (el contenedor de chips no aparece)
        # Marker: data-chip-param atributo no debe estar
        assert b"data-chip-param" not in resp.content

    async def test_city_filter_renders_chip(self, admin_client):
        with _patch_service():
            resp = await admin_client.get("/properties?city=asuncion")
        assert resp.status_code == 200
        assert b'data-chip-param="city"' in resp.content
        # The chip label should be human-friendly ("Ciudad")
        assert "Ciudad".encode() in resp.content

    async def test_price_range_renders_two_chips(self, admin_client):
        with _patch_service():
            resp = await admin_client.get("/properties?price_min=50000&price_max=200000")
        assert resp.status_code == 200
        assert b'data-chip-param="price_min"' in resp.content
        assert b'data-chip-param="price_max"' in resp.content
        # Mismo formato que la celda Precio: USD y punto de miles
        assert b"50.000" in resp.content
        assert b"200.000" in resp.content

    async def test_state_active_default_does_not_render_chip(self, admin_client):
        # state=active es el default; no merece un chip ruidoso
        with _patch_service():
            resp = await admin_client.get("/properties?state=active")
        assert resp.status_code == 200
        # No chip for state when it is the default
        assert b'data-chip-param="state"' not in resp.content

    async def test_state_inactive_renders_chip(self, admin_client):
        with _patch_service():
            resp = await admin_client.get("/properties?state=inactive")
        assert resp.status_code == 200
        assert b'data-chip-param="state"' in resp.content

    async def test_search_text_renders_chip_with_raw_value(self, admin_client):
        with _patch_service():
            resp = await admin_client.get("/properties?search_text=143025134-72")
        assert resp.status_code == 200
        assert b'data-chip-param="search_text"' in resp.content
        assert b"143025134-72" in resp.content

    async def test_chip_has_remove_button(self, admin_client):
        """Cada chip debe tener un boton X que dispara removeChip()."""
        with _patch_service():
            resp = await admin_client.get("/properties?city=luque")
        text = resp.text
        # Busco una secuencia tipo: data-chip-param="city" ... removeChip('city')
        assert "data-chip-param=\"city\"" in text
        assert "removeChip('city')" in text


class TestActiveChipsContextShape:
    async def test_context_exposes_active_chips_list(self, admin_client):
        captured = {}

        async def mock_get_properties(db, filters, page, per_page):
            return [], 0

        with patch(
            "app.routes.properties.property_service.get_properties",
            side_effect=mock_get_properties,
        ):
            resp = await admin_client.get("/properties?neighborhood=villa+morra&bedrooms_min=2")
        assert resp.status_code == 200
        # Chip de barrio
        assert b'data-chip-param="neighborhood"' in resp.content
        # Chip de dormitorios mostrando "2+" o similar
        assert b'data-chip-param="bedrooms_min"' in resp.content


class TestChipDePrecioSigueALaMoneda:
    """El chip de precio decía `USD` siempre, aunque el rango estuviera escrito
    en guaraníes y se comparara contra `price_pyg`."""

    async def test_precio_en_pyg_no_se_rotula_usd(self, admin_client):
        with _patch_service():
            resp = await admin_client.get(
                "/properties?price_max=350000000&currency=PYG"
            )
        body = resp.content.decode()
        # El símbolo sale de utils.money.MONEDA_PYG, el mismo que usa la celda
        # de precio — no una tercera forma de escribir guaraníes.
        assert "\u20b2 350.000.000" in body
        assert "USD 350.000.000" not in body

    async def test_precio_sin_moneda_sigue_en_usd(self, admin_client):
        with _patch_service():
            resp = await admin_client.get("/properties?price_max=150000")
        assert "USD 150.000" in resp.content.decode()

    async def test_la_moneda_ya_no_tiene_chip_propio(self, admin_client):
        """Sin rango de precio la moneda no saca ninguna fila: un chip suyo
        anunciaría un filtro que no está filtrando."""
        with _patch_service():
            resp = await admin_client.get("/properties?currency=PYG")
        assert b'data-chip-param="currency"' not in resp.content


class TestChipDeBanos:
    async def test_bathrooms_min_renderiza_su_chip(self, admin_client):
        with _patch_service():
            resp = await admin_client.get("/properties?bathrooms_min=3")
        assert b'data-chip-param="bathrooms_min"' in resp.content
        assert "Baños".encode() in resp.content
